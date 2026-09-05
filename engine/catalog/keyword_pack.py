from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import unicodedata
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from engine.catalog.db import (
    Customer,
    KeywordPack,
    KeywordPackRevisionCandidate,
    KeywordUsage,
    OfficialObjectEvidence,
    SemanticHealthCandidate,
)


def parse_keyword_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    # Extract JSON block from markdown if present
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(text)


def _canonical_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def keyword_pack_sha256(data: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(data)).hexdigest()


def keyword_pack_revision(data: dict[str, Any]) -> int:
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    raw = meta.get("revision", meta.get("version", data.get("version", 1)))
    try:
        return max(1, int(raw or 1))
    except (TypeError, ValueError):
        return 1


def keyword_pack_schema(data: dict[str, Any]) -> str:
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return str(meta.get("schema") or data.get("schema") or "suying.customer.keyword-pack.v1")


def _normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def compile_keyword_pack(data: dict[str, Any]) -> dict[str, Any]:
    """Compile content-pack v2 into the legacy read view without dual authoring."""
    if keyword_pack_schema(data) != "suying.customer.content-pack.v2":
        return dict(data)
    out = dict(data)
    identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    company = {
        "display_name_preferred": identity.get("display_name") or identity.get("short_name") or "品牌",
        "positioning_one_liner": "",
    }
    positioning_ref = str(identity.get("positioning_fact_id") or "")
    positioning = facts.get(positioning_ref) if isinstance(facts.get(positioning_ref), dict) else {}
    company["positioning_one_liner"] = str(positioning.get("value") or "品牌实拍")
    out["company_info"] = company

    components = data.get("copy_components") if isinstance(data.get("copy_components"), dict) else {}
    themes_raw = components.get("themes") if isinstance(components.get("themes"), dict) else {}
    themes: dict[str, Any] = {}
    hooks: list[str] = []
    title_categories: dict[str, list[str]] = {}
    bindings: dict[str, list[str]] = {}
    for theme, block in themes_raw.items():
        if not isinstance(block, dict):
            continue
        keywords = block.get("keywords") if isinstance(block.get("keywords"), list) else []
        theme_hooks = [str(x).strip() for x in (block.get("hooks") or []) if str(x).strip()]
        themes[str(theme)] = {
            "label": str(block.get("label") or theme),
            "keywords": [
                {"text": str(item.get("text")), "weight": float(item.get("weight", 1))}
                if isinstance(item, dict)
                else {"text": str(item), "weight": 1}
                for item in keywords
                if (isinstance(item, dict) and item.get("text")) or str(item).strip()
            ],
            "hooks": theme_hooks,
        }
        hooks.extend(theme_hooks)
        titles = [str(x).strip() for x in (block.get("titles") or []) if str(x).strip()]
        if titles:
            cat = f"{theme}标题"
            title_categories[cat] = titles
            bindings[str(theme)] = [cat]
    default_block = components.get("default") if isinstance(components.get("default"), dict) else {}
    hooks.extend(str(x).strip() for x in (default_block.get("hooks") or []) if str(x).strip())
    default_titles = [str(x).strip() for x in (default_block.get("titles") or []) if str(x).strip()]
    if default_titles:
        title_categories["默认标题"] = default_titles
        bindings.setdefault("default", ["默认标题"])
    title_banks = components.get("title_banks") if isinstance(components.get("title_banks"), dict) else {}
    for bank_key, arr in title_banks.items():
        if not isinstance(arr, list):
            continue
        titles = [str(x).strip() for x in arr if str(x).strip()]
        if not titles:
            continue
        cat = f"{bank_key}标题"
        if cat in title_categories:
            continue
        title_categories[cat] = titles

    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            key = _normalize_text(item)
            if key and key not in seen:
                seen.add(key)
                result.append(item)
        return result

    hashtags = [str(x).strip() for x in (components.get("hashtags") or []) if str(x).strip()]
    pack_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    title_constraints = pack_meta.get("title_constraints") if isinstance(pack_meta.get("title_constraints"), dict) else {}
    out["keyword_pool"] = {
        "themes": themes or {"default": {"label": "默认", "keywords": [{"text": "现场实拍", "weight": 1}]}},
        "hooks": _dedupe(hooks),
        "hashtags": _dedupe(hashtags),
        "title_pool": {
            "meta": {
                "spoken": bool(title_constraints.get("spoken", False)),
                "max_chars_per_line": int(title_constraints.get("max_chars_per_line") or 24),
                "target_chars_per_line": int(
                    title_constraints.get("target_chars_per_line")
                    or max(12, min(16, int(title_constraints.get("max_chars_per_line") or 24)))
                ),
                "min_chars_per_line": int(title_constraints.get("min_chars") or 6),
                "min_chars_total": int(title_constraints.get("min_chars_total") or 12),
                "max_total_chars": int(title_constraints.get("max_chars") or 48),
                "purpose": "片上标题",
            },
            "theme_category_bindings": bindings,
            "categories": {k: _dedupe(v) for k, v in title_categories.items()},
        },
    }
    compliance = data.get("compliance") if isinstance(data.get("compliance"), dict) else {}
    out["compliance"] = {
        **compliance,
        "blocked_terms": list(
            dict.fromkeys(
                [
                    *[str(x) for x in (compliance.get("blocked_terms") or []) if str(x)],
                    *[str(x) for x in (compliance.get("hard_deny") or []) if str(x)],
                ]
            )
        ),
    }
    return out


def validate_keyword_pack(data: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return {"ok": False, "errors": ["root_not_object"], "warnings": []}
    schema = keyword_pack_schema(data)
    revision = keyword_pack_revision(data)
    compiled = compile_keyword_pack(data)
    pool = compiled.get("keyword_pool") if isinstance(compiled.get("keyword_pool"), dict) else {}
    themes = pool.get("themes") if isinstance(pool.get("themes"), dict) else {}
    if not themes:
        errors.append("keyword_pool.themes_empty")
    if not isinstance((compiled.get("compliance") or {}).get("blocked_terms", []), list):
        errors.append("compliance.blocked_terms_not_list")
    if schema == "suying.customer.content-pack.v2":
        for key in ("identity", "facts", "taxonomy", "copy_components", "recipes", "compliance"):
            if not isinstance(data.get(key), (dict, list)):
                errors.append(f"{key}_missing")
    elif schema not in ("suying.customer.keyword-pack.v1", ""):
        warnings.append(f"unknown_schema:{schema}")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "schema": schema,
        "revision": revision,
        "sha256": keyword_pack_sha256(data),
    }


def import_keyword_pack(
    session: Session,
    customer_name: str,
    data: dict[str, Any],
    pack_name: str = "default",
    *,
    source_path: str | None = None,
    source_kind: str = "manual",
) -> KeywordPack:
    """Import an immutable validated revision without overwriting App profile."""
    report = validate_keyword_pack(data)
    if not report["ok"]:
        raise ValueError("词池校验失败：" + "；".join(report["errors"]))
    sha = str(report["sha256"])
    revision = int(report["revision"])
    customer = session.scalar(select(Customer).where(Customer.name == customer_name))
    if not customer:
        customer = Customer(name=customer_name, profile_json={})
        session.add(customer)
        session.flush()
    existing = session.scalar(
        select(KeywordPack).where(
            KeywordPack.customer_id == customer.id,
            KeywordPack.name == pack_name,
            KeywordPack.content_sha256 == sha,
        )
    )
    if existing:
        if existing.status != "active":
            session.execute(
                update(KeywordPack)
                .where(
                    KeywordPack.customer_id == customer.id,
                    KeywordPack.name == pack_name,
                    KeywordPack.status == "active",
                )
                .values(status="archived", archived_at=datetime.now(timezone.utc))
            )
            existing.status = "active"
            existing.activated_at = datetime.now(timezone.utc)
            session.commit()
        return existing

    now = datetime.now(timezone.utc)
    session.execute(
        update(KeywordPack)
        .where(
            KeywordPack.customer_id == customer.id,
            KeywordPack.name == pack_name,
            KeywordPack.status == "active",
        )
        .values(status="archived", archived_at=now)
    )

    pack = KeywordPack(
        customer_id=customer.id,
        name=pack_name,
        data_json=compile_keyword_pack(data),
        version=revision,
        revision=revision,
        schema_version=str(report["schema"]),
        content_sha256=sha,
        source_path=source_path,
        source_kind=source_kind,
        status="active",
        validation_report=report,
        activated_at=now,
    )
    session.add(pack)
    session.commit()
    session.refresh(pack)
    return pack


def get_active_pack(session: Session, customer_name: str, pack_name: str = "default") -> KeywordPack | None:
    customer = session.scalar(select(Customer).where(Customer.name == customer_name))
    if not customer:
        return None
    return session.scalar(
        select(KeywordPack)
        .where(
            KeywordPack.customer_id == customer.id,
            KeywordPack.name == pack_name,
            KeywordPack.status == "active",
        )
        .order_by(KeywordPack.id.desc())
    )


def get_pack_by_id(
    session: Session,
    pack_id: int | None,
    *,
    customer_id: int | None = None,
) -> KeywordPack | None:
    if not pack_id:
        return None
    row = session.get(KeywordPack, int(pack_id))
    if row and customer_id is not None and row.customer_id != customer_id:
        return None
    return row


def normalize_content_facet(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text or text.lower() in {"null", "none", "default", "自动"}:
        return None
    return text[:128]


def list_content_facets(pack: KeywordPack | None) -> list[dict[str, Any]]:
    """Active pack themes with title/keyword counts for rule binding."""
    if pack is None:
        return []
    pool = pack.data_json.get("keyword_pool") if isinstance(pack.data_json, dict) else {}
    if not isinstance(pool, dict):
        return []
    themes = pool.get("themes") if isinstance(pool.get("themes"), dict) else {}
    out: list[dict[str, Any]] = []
    for key, block in themes.items():
        name = str(key).strip()
        if not name:
            continue
        slice_info = resolve_facet_slice(pack, name)
        label = name
        if isinstance(block, dict) and block.get("label"):
            label = str(block.get("label") or name)
        out.append(
            {
                "name": name,
                "label": label,
                "keyword_count": int(slice_info.get("keyword_count") or 0),
                "title_count": int(slice_info.get("title_count") or 0),
                "hook_count": int(slice_info.get("hook_count") or 0),
                "title_categories": list(slice_info.get("title_categories") or []),
                "titles_sample": list(slice_info.get("titles_sample") or []),
                "usable": bool(slice_info.get("ok")),
            }
        )
    return out


def resolve_facet_slice(pack: KeywordPack | None, facet: str | None) -> dict[str, Any]:
    """Resolve one content facet to titles/keywords/hooks. ok=False if empty or missing."""
    name = normalize_content_facet(facet)
    empty: dict[str, Any] = {
        "facet": name,
        "ok": False,
        "error": "",
        "title_categories": [],
        "titles_sample": [],
        "title_count": 0,
        "keyword_count": 0,
        "hook_count": 0,
        "hooks": [],
        "resolved_from": "missing",
    }
    if not name:
        empty["ok"] = True
        empty["resolved_from"] = "customer_default"
        empty["error"] = ""
        return empty
    if pack is None:
        empty["error"] = f"词池内容面「{name}」无法解析：客户尚未导入词池"
        return empty
    pool = pack.data_json.get("keyword_pool") if isinstance(pack.data_json, dict) else {}
    themes = pool.get("themes") if isinstance(pool, dict) and isinstance(pool.get("themes"), dict) else {}
    if name not in themes:
        empty["error"] = f"词池没有内容面「{name}」；请改词池 themes 或换一面"
        empty["resolved_from"] = "missing_theme"
        return empty
    titles = list_title_pool(pack, theme=name, prefer_rich=False, strict_theme=True)
    keywords = list_keywords_by_theme(pack, name, fallback_default=False)
    block = themes.get(name) if isinstance(themes.get(name), dict) else {}
    hooks = [str(x).strip() for x in (block.get("hooks") or []) if str(x).strip()]
    title_pool = pool.get("title_pool") if isinstance(pool.get("title_pool"), dict) else {}
    bindings = (
        title_pool.get("theme_category_bindings")
        if isinstance(title_pool.get("theme_category_bindings"), dict)
        else {}
    )
    cats = _theme_title_categories(name, bindings=bindings)
    if not cats:
        guessed = f"{name}标题"
        categories = title_pool.get("categories") if isinstance(title_pool.get("categories"), dict) else {}
        if guessed in categories:
            cats = [guessed]
    if not titles:
        empty["error"] = f"内容面「{name}」没有可用标题，禁止回退默认标题池"
        empty["title_categories"] = cats
        empty["keyword_count"] = len(keywords)
        empty["hook_count"] = len(hooks)
        empty["hooks"] = hooks
        empty["resolved_from"] = "empty_titles"
        return empty
    return {
        "facet": name,
        "ok": True,
        "error": "",
        "title_categories": cats,
        "titles_sample": titles[:12],
        "title_count": len(titles),
        "keyword_count": len(keywords),
        "hook_count": len(hooks),
        "hooks": hooks,
        "resolved_from": "rule_binding",
    }


def resolve_rule_facet(pack: KeywordPack | None, rules: dict[str, Any] | None) -> dict[str, Any]:
    """Fail-closed when a rule pins a missing/empty facet."""
    from engine.template.rule_schema import content_facet_of

    facet = content_facet_of(rules if isinstance(rules, dict) else None)
    if not facet:
        return {
            "facet": None,
            "ok": True,
            "error": "",
            "title_categories": [],
            "titles_sample": [],
            "title_count": 0,
            "keyword_count": 0,
            "hook_count": 0,
            "hooks": [],
            "resolved_from": "customer_default",
        }
    info = resolve_facet_slice(pack, facet)
    if not info.get("ok"):
        raise ValueError(info.get("error") or f"内容面「{facet}」不可用")
    return info


def install_keyword_pack(
    session: Session,
    customer: Customer,
    candidate_path: Path,
    *,
    source_kind: str = "manual",
    allow_downgrade: bool = False,
) -> dict[str, Any]:
    """Validate, archive and atomically promote one customer's canonical pack."""
    candidate_path = Path(candidate_path).expanduser().resolve()
    data = parse_keyword_file(candidate_path)
    report = validate_keyword_pack(data)
    if not report["ok"]:
        raise ValueError("词池校验失败：" + "；".join(report["errors"]))
    active = get_active_pack(session, customer.name)
    if active and active.content_sha256 == report["sha256"]:
        canonical = Path(customer.keyword_pack_path or candidate_path)
        return {"ok": True, "idempotent": True, "pack": active, "path": str(canonical), "report": report}
    if active and int(report["revision"]) < int(active.revision or active.version or 1) and not allow_downgrade:
        raise ValueError(
            f"拒绝词池降级：候选 revision={report['revision']}，当前 revision={active.revision or active.version}"
        )
    if active and int(report["revision"]) == int(active.revision or active.version or 1):
        raise ValueError("同 revision 内容不同，必须提升 meta.revision 后再更新")

    current_path = Path(customer.keyword_pack_path).expanduser() if customer.keyword_pack_path else None
    root = (
        current_path.parent
        if current_path and current_path.name == "keyword-pack.json"
        else candidate_path.parent
    )
    canonical = root / "keyword-pack.json"
    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".keyword-pack.lock"
    lock_path.touch(exist_ok=True)

    import fcntl

    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if active:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            old_data = (
                parse_keyword_file(canonical)
                if canonical.is_file() and candidate_path != canonical
                else active.data_json
            )
            old_sha = keyword_pack_sha256(old_data)
            old_rev = keyword_pack_revision(old_data)
            archived = archive / f"keyword-pack.r{old_rev}.{stamp}.{old_sha[:8]}.json"
            if not archived.exists():
                archived.write_text(
                    json.dumps(old_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        if candidate_path != canonical:
            fd, temp_name = tempfile.mkstemp(prefix=".keyword-pack.", suffix=".tmp", dir=str(root))
            try:
                with os.fdopen(fd, "wb") as tmp:
                    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
                    tmp.write(payload)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(temp_name, canonical)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        customer.keyword_pack_path = str(canonical)
        pack = import_keyword_pack(
            session,
            customer.name,
            data,
            source_path=str(canonical),
            source_kind=source_kind,
        )
        session.commit()
        return {
            "ok": True,
            "idempotent": False,
            "pack": pack,
            "path": str(canonical),
            "archive_dir": str(archive),
            "report": report,
        }


def refresh_canonical_pack(session: Session, customer: Customer) -> dict[str, Any]:
    """Low-frequency/startup reconcile of the one configured canonical path."""
    path = Path(customer.keyword_pack_path).expanduser() if customer.keyword_pack_path else None
    if not path:
        return {"ok": False, "loaded": False, "error": "canonical_path_missing"}
    if path.name != "keyword-pack.json":
        return {"ok": False, "loaded": False, "error": "canonical_filename_required"}
    if not path.is_file():
        return {"ok": False, "loaded": False, "error": "canonical_file_missing", "path": str(path)}
    try:
        result = install_keyword_pack(
            session,
            customer,
            path,
            source_kind="canonical-auto",
        )
        return {
            "ok": True,
            "loaded": True,
            "idempotent": result["idempotent"],
            "revision": result["pack"].revision,
            "sha256": result["pack"].content_sha256,
            "path": result["path"],
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        session.rollback()
        return {"ok": False, "loaded": False, "error": str(exc), "path": str(path)}


_HIGH_RISK_TOKENS = {
    "name",
    "display_name",
    "brand",
    "purpose",
    "use",
    "efficacy",
    "effect",
    "price",
    "copy",
    "title",
    "hook",
    "description",
    "hashtags",
    "claim",
    "value",
}
_V2_CANONICAL_KEYS = {
    "meta",
    "identity",
    "facts",
    "taxonomy",
    "copy_components",
    "recipes",
    "compliance",
}


def _deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        out = deepcopy(base)
        for key, value in patch.items():
            out[str(key)] = _deep_merge(out.get(str(key)), value)
        return out
    return deepcopy(patch)


def _diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        rows: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                rows.append({"path": child, "before": None, "after": after[key]})
            elif key not in after:
                rows.append({"path": child, "before": before[key], "after": None})
            else:
                rows.extend(_diff(before[key], after[key], child))
        return rows
    if before != after:
        return [{"path": path, "before": before, "after": after}]
    return []


def _change_is_low_risk(path: str) -> bool:
    parts = {part.lower() for part in re.split(r"[.\[\]]+", path) if part}
    return path.startswith("taxonomy.") and not bool(parts & _HIGH_RISK_TOKENS)


def create_revision_draft(
    session: Session,
    customer: Customer,
    *,
    patch: dict[str, Any],
    semantic_candidate_ids: list[int],
    official_evidence_ids: list[int] | None = None,
    draft_key: str,
    created_by: str = "system",
) -> KeywordPackRevisionCandidate:
    active = get_active_pack(session, customer.name)
    if active is None or active.schema_version != "suying.customer.content-pack.v2":
        raise ValueError("仅支持基于当前 schema v2 canonical 词池创建 revision")
    existing = session.scalar(
        select(KeywordPackRevisionCandidate).where(
            KeywordPackRevisionCandidate.customer_id == customer.id,
            KeywordPackRevisionCandidate.draft_key == draft_key,
        )
    )
    if existing:
        return existing
    candidates = list(
        session.scalars(
            select(SemanticHealthCandidate).where(
                SemanticHealthCandidate.customer_id == customer.id,
                SemanticHealthCandidate.id.in_(semantic_candidate_ids or [-1]),
            )
        ).all()
    )
    semantic_ok = bool(candidates) and len(candidates) == len(set(semantic_candidate_ids))
    semantic_ok = semantic_ok and all(
        row.evidence_level == "semantic.v1"
        and row.status == "accept"
        and row.severity != "error"
        for row in candidates
    )
    official_ids = list(dict.fromkeys(official_evidence_ids or []))
    official = list(
        session.scalars(
            select(OfficialObjectEvidence).where(
                OfficialObjectEvidence.customer_id == customer.id,
                OfficialObjectEvidence.id.in_(official_ids or [-1]),
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    official_ok = bool(official_ids) and len(official) == len(official_ids) and all(
        row.status == "verified"
        and not row.conflict_json
        and (row.expires_at is None or row.expires_at.replace(tzinfo=row.expires_at.tzinfo or timezone.utc) > now)
        for row in official
    )
    canonical_base = {
        key: deepcopy(value)
        for key, value in active.data_json.items()
        if key in _V2_CANONICAL_KEYS
    }
    draft = _deep_merge(canonical_base, patch)
    meta = draft.get("meta") if isinstance(draft.get("meta"), dict) else {}
    target_revision = int(active.revision or active.version or 1) + 1
    draft["meta"] = {
        **meta,
        "schema": "suying.customer.content-pack.v2",
        "revision": target_revision,
    }
    diff = _diff(canonical_base, draft)
    business_diff = [row for row in diff if row["path"] != "meta.revision"]
    low_risk = bool(business_diff) and all(_change_is_low_risk(row["path"]) for row in business_diff)
    report = validate_keyword_pack(draft)
    public_text = " ".join(
        str(row.get("after") or "") for row in business_diff if not _change_is_low_risk(row["path"])
    )
    compliance_hits = check_compliance(public_text, active) if public_text else []
    validation = {
        **report,
        "semantic_ok": semantic_ok,
        "official_ok": official_ok,
        "compliance_hits": compliance_hits,
        "auto_promotable": bool(low_risk and semantic_ok and not compliance_hits),
    }
    row = KeywordPackRevisionCandidate(
        customer_id=customer.id,
        draft_key=draft_key,
        base_pack_id=active.id,
        target_revision=target_revision,
        status="draft",
        risk_level="low" if low_risk else "high",
        draft_json=draft,
        diff_json=diff,
        evidence_json={
            "semantic_candidate_ids": [item.id for item in candidates],
            "official_evidence_ids": [item.id for item in official],
        },
        validation_json=validation,
        created_by=created_by,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def approve_revision_draft(
    session: Session,
    customer: Customer,
    *,
    draft_id: int,
    approved_by: str,
) -> KeywordPackRevisionCandidate:
    row = session.scalar(
        select(KeywordPackRevisionCandidate).where(
            KeywordPackRevisionCandidate.id == draft_id,
            KeywordPackRevisionCandidate.customer_id == customer.id,
        )
    )
    if row is None:
        raise LookupError("revision draft not found")
    row.status = "approved"
    row.approved_by = approved_by
    row.approved_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return row


def promote_revision_draft(
    session: Session,
    customer: Customer,
    *,
    draft_id: int,
    actor: str = "operator",
    auto: bool = False,
) -> KeywordPackRevisionCandidate:
    row = session.scalar(
        select(KeywordPackRevisionCandidate).where(
            KeywordPackRevisionCandidate.id == draft_id,
            KeywordPackRevisionCandidate.customer_id == customer.id,
        )
    )
    if row is None:
        raise LookupError("revision draft not found")
    validation = row.validation_json or {}
    if validation.get("ok") is not True or validation.get("semantic_ok") is not True:
        raise ValueError("revision evidence or schema validation failed")
    if validation.get("compliance_hits"):
        raise ValueError("revision contains blocked compliance terms")
    if auto:
        if row.risk_level != "low" or validation.get("auto_promotable") is not True:
            raise ValueError("该 revision 不满足低风险自动提升条件")
    elif row.status != "approved":
        raise ValueError("高风险或人工提升必须先批准")
    elif row.risk_level == "high" and validation.get("official_ok") is not True:
        raise ValueError("高风险公开字段必须绑定有效官方证据并人工批准")
    canonical = Path(customer.keyword_pack_path or "").expanduser()
    if canonical.name != "keyword-pack.json":
        raise ValueError("canonical keyword-pack.json path required")
    canonical.parent.mkdir(parents=True, exist_ok=True)
    fd, candidate_name = tempfile.mkstemp(
        prefix=".keyword-pack.revision.", suffix=".json", dir=str(canonical.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(row.draft_json, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        result = install_keyword_pack(
            session,
            customer,
            Path(candidate_name),
            source_kind="semantic-auto" if auto else f"approved:{actor}",
        )
    finally:
        if os.path.exists(candidate_name):
            os.unlink(candidate_name)
    row.status = "promoted"
    row.promoted_pack_id = result["pack"].id
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    return row


def rollback_keyword_pack(
    session: Session,
    customer: Customer,
    *,
    source_revision: int,
    actor: str = "operator",
) -> KeywordPack:
    active = get_active_pack(session, customer.name)
    source = session.scalar(
        select(KeywordPack)
        .where(
            KeywordPack.customer_id == customer.id,
            KeywordPack.revision == source_revision,
            KeywordPack.status == "archived",
        )
        .order_by(KeywordPack.id.desc())
    )
    if active is None or source is None:
        raise LookupError("rollback revision not found")
    data = {
        key: deepcopy(value)
        for key, value in source.data_json.items()
        if key in _V2_CANONICAL_KEYS
    }
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    data["meta"] = {
        **meta,
        "schema": "suying.customer.content-pack.v2",
        "revision": int(active.revision or 1) + 1,
        "rollback_of_revision": source_revision,
    }
    canonical = Path(customer.keyword_pack_path or "").expanduser()
    fd, candidate_name = tempfile.mkstemp(
        prefix=".keyword-pack.rollback.", suffix=".json", dir=str(canonical.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        result = install_keyword_pack(
            session,
            customer,
            Path(candidate_name),
            source_kind=f"rollback:{actor}:r{source_revision}",
        )
        return result["pack"]
    finally:
        if os.path.exists(candidate_name):
            os.unlink(candidate_name)


def list_keywords_by_theme(
    pack: KeywordPack,
    theme: str,
    *,
    fallback_default: bool = True,
) -> list[dict[str, Any]]:
    pool = pack.data_json.get("keyword_pool", {})
    themes = pool.get("themes", {})
    if theme in themes:
        return themes[theme].get("keywords", [])
    if fallback_default and "default" in themes:
        return themes["default"].get("keywords", [])
    core = pool.get("core_phrases", [])
    if core:
        return [{"text": k, "weight": 1} for k in core]
    flat = pool.get("flat_keywords", [])
    return [{"text": k, "weight": 1} for k in flat]


def pick_keyword(
    session: Session,
    pack: KeywordPack,
    theme: str,
    seed_rng,
    cooldown_days: int = 7,
    *,
    customer_id: int | None = None,
    theme_fallbacks: list[str] | None = None,
    exclude_phrases: set[str] | None = None,
    allow_default_fallback: bool = True,
) -> str:
    themes_try = [theme, *(theme_fallbacks or [])]
    # de-dupe preserve order
    seen_t: set[str] = set()
    ordered: list[str] = []
    for t in themes_try:
        if t and t not in seen_t:
            seen_t.add(t)
            ordered.append(t)

    candidates: list[dict[str, Any]] = []
    used_theme = theme
    for t in ordered:
        rows = list_keywords_by_theme(pack, t, fallback_default=False)
        if rows:
            candidates = rows
            used_theme = t
            break
    if not candidates and allow_default_fallback:
        candidates = list_keywords_by_theme(pack, "default", fallback_default=True)
        used_theme = "default"
    if not candidates:
        return pack.data_json.get("company_info", {}).get("positioning_one_liner", "品牌宣传")

    from engine.catalog.paper_slip import phrase_is_blocked

    cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
    stmt = select(KeywordUsage).where(KeywordUsage.used_at >= cutoff, KeywordUsage.theme == used_theme)
    if customer_id is not None:
        stmt = stmt.where(KeywordUsage.customer_id == customer_id)
    recent = {row.keyword for row in session.scalars(stmt).all()}

    weighted: list[tuple[str, float]] = []
    blocked = exclude_phrases or set()
    for item in candidates:
        text = item["text"] if isinstance(item, dict) else str(item)
        if phrase_is_blocked(text, blocked):
            continue  # PAPER_SLIP HARD — 禁止超发
        weight = float(item.get("weight", 1)) if isinstance(item, dict) else 1.0
        if text in recent:
            weight *= 0.1
        weighted.append((text, max(weight, 0.01)))

    if not weighted:
        # 无未超额词：仍返回 positioning，由上层 block_reasons 拦截
        return pack.data_json.get("company_info", {}).get("positioning_one_liner", "品牌宣传")

    total = sum(w for _, w in weighted)
    r = seed_rng.random() * total
    acc = 0.0
    for text, w in weighted:
        acc += w
        if r <= acc:
            return text
    return weighted[-1][0]


def record_keyword_usage(
    session: Session,
    keyword: str,
    theme: str,
    job_id: int | None,
    *,
    customer_id: int | None = None,
) -> None:
    session.add(KeywordUsage(keyword=keyword, theme=theme, job_id=job_id, customer_id=customer_id))
    session.commit()


def undo_keyword_usage(
    session: Session,
    job_id: int,
    *,
    customer_id: int | None = None,
) -> int:
    """Remove keyword_usage rows for a rejected job so the pool slot is free again."""
    stmt = delete(KeywordUsage).where(KeywordUsage.job_id == int(job_id))
    if customer_id is not None:
        stmt = stmt.where(KeywordUsage.customer_id == int(customer_id))
    result = session.execute(stmt)
    return int(result.rowcount or 0)


def check_compliance(text: str, pack: KeywordPack) -> list[str]:
    hits: list[str] = []
    blocked = pack.data_json.get("compliance", {}).get("blocked_terms", [])
    for term in blocked:
        if term and term in text:
            hits.append(term)
    return hits


def list_title_pool(
    pack: KeywordPack | None,
    *,
    theme: str | None = None,
    prefer_rich: bool = True,
    include_all_categories: bool = False,
    strict_theme: bool = False,
) -> list[str]:
    """On-screen title corpus from keyword_pool.title_pool (not spoken).

    When theme is set, prefer matching categories; prefer_rich boosts longer lines.
    include_all_categories merges every category for maximum title diversity.
    strict_theme keeps only the bound facet categories (no default-pool mix-in).
    """
    if pack is None:
        return []
    pool = (pack.data_json.get("keyword_pool") or {}).get("title_pool")
    if not pool:
        return []
    meta = pool.get("meta") if isinstance(pool, dict) and isinstance(pool.get("meta"), dict) else {}
    pack_meta = pack.data_json.get("meta") if isinstance(pack.data_json.get("meta"), dict) else {}
    title_constraints = (
        pack_meta.get("title_constraints")
        if isinstance(pack_meta.get("title_constraints"), dict)
        else {}
    )
    max_line = int(
        meta.get("max_chars_per_line")
        or title_constraints.get("max_chars_per_line")
        or 24
    )
    target_line = int(
        meta.get("target_chars_per_line")
        or title_constraints.get("target_chars_per_line")
        or max(12, min(16, max_line))
    )
    rank_kw = {
        "prefer_rich": prefer_rich,
        "target_chars_per_line": target_line,
        "max_chars_per_line": max_line,
    }
    if isinstance(pool, list):
        items = [str(x).strip() for x in pool if str(x).strip()]
        return _rank_titles(items, **rank_kw)

    if not isinstance(pool, dict):
        return []

    cats = pool.get("categories") if isinstance(pool.get("categories"), dict) else {}
    if include_all_categories and cats:
        merged: list[str] = []
        seen_all: set[str] = set()
        for arr in cats.values():
            if not isinstance(arr, list):
                continue
            for raw in arr:
                item = str(raw).strip()
                if item and item not in seen_all:
                    seen_all.add(item)
                    merged.append(item)
        return _rank_titles(merged, **rank_kw)
    theme_key = (theme or "").strip()
    bindings = pool.get("theme_category_bindings")
    preferred_cat_names = _theme_title_categories(theme_key, bindings=bindings)
    if strict_theme and theme_key and not preferred_cat_names:
        guessed = f"{theme_key}标题"
        if guessed in cats:
            preferred_cat_names = [guessed]
        elif theme_key in cats:
            preferred_cat_names = [theme_key]

    themed: list[str] = []
    general: list[str] = []
    seen: set[str] = set()

    def _add(bucket: list[str], raw: Any) -> None:
        t = str(raw).strip()
        if t and t not in seen:
            seen.add(t)
            bucket.append(t)

    if cats and preferred_cat_names:
        for name in preferred_cat_names:
            arr = cats.get(name)
            if isinstance(arr, list):
                for x in arr:
                    _add(themed, x)
        if not strict_theme:
            for name, arr in cats.items():
                if name in preferred_cat_names or not isinstance(arr, list):
                    continue
                for x in arr:
                    _add(general, x)
    elif cats and not strict_theme:
        for arr in cats.values():
            if isinstance(arr, list):
                for x in arr:
                    _add(general, x)

    flat = pool.get("items")
    if isinstance(flat, list) and not themed and not general and not strict_theme:
        for x in flat:
            _add(general, x)

    ordered = _rank_titles(themed, **rank_kw) + (_rank_titles(general, **rank_kw) if not strict_theme else [])
    out: list[str] = []
    seen2: set[str] = set()
    for t in ordered:
        if t not in seen2:
            seen2.add(t)
            out.append(t)
    return out


def _theme_title_categories(theme: str, *, bindings: Any = None) -> list[str]:
    """Map theme → title categories using keyword-pack data, never product code."""
    t = (theme or "").strip().lower()
    mapping = bindings if isinstance(bindings, dict) else {}
    for key, cats in mapping.items():
        key_s = str(key).strip().lower()
        if key_s and (key_s in t or t in key_s) and isinstance(cats, list):
            return [str(c) for c in cats]
    return []


def prefer_product_title_theme(pack: KeywordPack | None, fallback: str | None = None) -> str:
    """Title facet for product-closeup reels: never fall through to 装车/配送 banks."""
    fallback_s = (fallback or "").strip() or "产品"
    if pack is None:
        return fallback_s
    pool = (pack.data_json.get("keyword_pool") or {}).get("title_pool") if isinstance(pack.data_json, dict) else {}
    if not isinstance(pool, dict):
        return fallback_s
    bindings = pool.get("theme_category_bindings") if isinstance(pool.get("theme_category_bindings"), dict) else {}
    cats = pool.get("categories") if isinstance(pool.get("categories"), dict) else {}
    for cand in ("产品介绍", "产品", "产品实拍", "品类介绍", fallback_s):
        if not cand:
            continue
        if cand in bindings:
            return cand
        if f"{cand}标题" in cats or cand in cats:
            return cand
    return fallback_s


def normalize_title_layout(
    title: str,
    *,
    max_chars_per_line: int = 24,
    max_lines: int = 2,
    force_dual: bool = False,
) -> str:
    """Preserve one/two complete title sentences; never split at a character midpoint.

    Pixel width and locked font scaling are enforced by ``render_title_png``.
    """
    raw = (title or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return raw
    max_lines = max(1, int(max_lines))

    if "\n" in raw:
        parts = [p.strip() for p in raw.split("\n") if p.strip()][:max_lines]
        return "\n".join(parts)

    compact = raw.replace(" ", "").replace("　", "")
    return compact


# Back-compat alias
def ensure_full_dual_title(
    title: str,
    *,
    max_chars_per_line: int = 24,
    max_lines: int = 2,
) -> str:
    return normalize_title_layout(
        title,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        force_dual=False,
    )


def _rank_titles(
    titles: list[str],
    *,
    prefer_rich: bool,
    target_chars_per_line: int | None = None,
    max_chars_per_line: int | None = None,
) -> list[str]:
    if not prefer_rich or not titles:
        return list(titles)

    target = int(target_chars_per_line or 0) or None
    line_cap = int(max_chars_per_line or 0) or 24

    def score(t: str) -> tuple[int, int]:
        compact = t.replace("\n", "").replace(" ", "")
        n = len(compact)
        rich = 0
        # Prefer filling more of the raised 24-char budget; don't reward ultra-short dual chips.
        if n < 8:
            rich -= 3
        elif n < 12:
            rich -= 1
        elif 12 <= n <= min(24, line_cap * 2):
            rich += 4
        if target:
            # Closer to target*lines (dual ≈ 2 lines) scores higher
            ideal = target * (2 if "\n" in t else 1)
            rich += max(0, 6 - abs(n - ideal) // 2)
        if "\n" in t and n >= 12:
            rich += 1
        elif "\n" in t and n <= 8:
            rich -= 2
        return (rich, n)

    return sorted(titles, key=score, reverse=True)


def title_pool_meta(pack: KeywordPack | None) -> dict[str, Any]:
    if pack is None:
        return {}
    pool = (pack.data_json.get("keyword_pool") or {}).get("title_pool")
    if not isinstance(pool, dict):
        return {}
    meta = pool.get("meta") if isinstance(pool.get("meta"), dict) else {}
    cats = pool.get("categories") if isinstance(pool.get("categories"), dict) else {}
    return {
        "max_chars_per_line": int(meta.get("max_chars_per_line") or 24),
        "spoken": bool(meta.get("spoken", False)),
        "purpose": meta.get("purpose"),
        "category_counts": {k: len(v) if isinstance(v, list) else 0 for k, v in cats.items()},
    }


def pick_title(
    seed_rng,
    titles: list[str],
    *,
    exclude: set[str] | None = None,
    pack: KeywordPack | None = None,
    max_chars_per_line: int = 24,
    theme: str | None = None,
    exclude_phrases: set[str] | None = None,
    strict_theme: bool = False,
) -> str | None:
    """Pick a compliant on-screen title; prefer theme-matched + richer unused lines."""
    from engine.catalog.paper_slip import phrase_is_blocked

    # Re-list with theme bias when pack available
    if pack is not None and theme:
        themed = list_title_pool(
            pack, theme=theme, prefer_rich=True, strict_theme=strict_theme
        )
        if themed:
            titles = themed
        elif strict_theme:
            titles = []
    pool = [t for t in titles if t]
    blocked = exclude_phrases or set()
    if blocked:
        pool = [t for t in pool if not phrase_is_blocked(t, blocked)]
    if exclude:
        filtered = [t for t in pool if t not in exclude]
        if filtered:
            pool = filtered
    if not pool:
        return None

    max_total = max(24, int(max_chars_per_line) * 2)
    compliant: list[str] = []
    for t in pool:
        if pack and check_compliance(t, pack):
            continue
        if phrase_is_blocked(t, blocked):
            continue
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        if not lines or len(lines) > 2:
            continue
        compact = re.sub(r"[\s\r\n　]+", "", t)
        if len(compact) < 6 or len(compact) > max_total:
            continue
        if any(len(re.sub(r"\s+", "", ln)) > max_chars_per_line for ln in lines):
            continue
        compliant.append("\n".join(lines))
    if not compliant:
        # PAPER_SLIP / filter exhausted — 禁止静默回退到超额标题
        return None

    # Prefer fuller titles under the raised 24-char budget; sample from top-ranked.
    scored = _rank_titles(
        compliant,
        prefer_rich=True,
        target_chars_per_line=max(12, min(16, int(max_chars_per_line))),
        max_chars_per_line=int(max_chars_per_line),
    )
    # Prefer ≥12-char titles when available; otherwise top-ranked only.
    mid = [t for t in scored if len(re.sub(r"[\s\r\n　]+", "", t)) >= 12]
    pool_pick = mid if len(mid) >= 3 else scored
    top = pool_pick[: max(8, min(24, len(pool_pick)))]
    picked = seed_rng.choice(top)
    return normalize_title_layout(picked, max_chars_per_line=max_chars_per_line)


def pick_routed_on_screen_title(
    seed_rng,
    pack: KeywordPack | None,
    *,
    theme: str | None = None,
    content_theme: str | None = None,
    recent: set[str] | None = None,
    exclude_phrases: set[str] | None = None,
    max_chars_per_line: int = 24,
    max_lines: int = 2,
    strict_theme: bool = False,
) -> str | None:
    """Pick a diverse on-screen title after semantic routing.

    Pulls from the compiled title pool with theme bias and paper-slip filtering.
    Never synthesizes fixed ``{taxonomy_label}现场实拍`` patterns.
    """
    if pack is None:
        return None

    candidates: list[str] = []
    seen: set[str] = set()
    for bias in (theme, content_theme if not strict_theme else None):
        key = (bias or "").strip()
        if not key:
            continue
        for item in list_title_pool(
            pack, theme=key, prefer_rich=True, strict_theme=strict_theme
        ):
            if item not in seen:
                seen.add(item)
                candidates.append(item)
    if not strict_theme and len(candidates) < 8:
        for item in list_title_pool(
            pack,
            theme=None,
            prefer_rich=True,
            include_all_categories=True,
        ):
            if item not in seen:
                seen.add(item)
                candidates.append(item)
    if not candidates:
        return None

    if exclude_phrases:
        blocked = {str(x).strip() for x in exclude_phrases if str(x).strip()}
        filtered = [
            t
            for t in candidates
            if not any(p in t.replace("\n", "") for p in blocked)
        ]
        if filtered:
            candidates = filtered

    return pick_title(
        seed_rng,
        candidates,
        exclude=recent,
        pack=pack,
        max_chars_per_line=max_chars_per_line,
        theme=theme,
        exclude_phrases=exclude_phrases,
        strict_theme=strict_theme,
    )
