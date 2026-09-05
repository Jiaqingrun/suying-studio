"""Persist customer-scoped production rule versions (GVideoRules)."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Customer, Job, ProductionRuleProfile
from engine.template.rule_schema import SCHEMA_VERSION, empty_rules, validate_and_clamp


def _effective_v2(row: ProductionRuleProfile) -> dict[str, Any]:
    """Read v1/v2 persisted rules through the current fail-closed defaults."""
    raw = row.effective_json if isinstance(row.effective_json, dict) else {}
    return validate_and_clamp(raw)["effective_rules"]


def rule_to_dict(row: ProductionRuleProfile) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "name": row.name,
        "content_category": row.content_category or "default",
        "orientation": row.orientation or "portrait",
        "source_text": row.source_text,
        "requested_rules": row.requested_json if isinstance(row.requested_json, dict) else {},
        "effective_rules": _effective_v2(row),
        "schema_version": row.schema_version,
        "effective_schema_version": SCHEMA_VERSION,
        "status": row.status,
        "revision": row.revision,
        "model": row.model or "",
        "parse_warnings": row.parse_warnings_json if isinstance(row.parse_warnings_json, list) else [],
        "rejected": row.rejected_json if isinstance(row.rejected_json, list) else [],
        "clamped": row.clamped_json if isinstance(row.clamped_json, list) else [],
        "approved_by": row.approved_by or "",
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "rotation_enabled": bool(getattr(row, "rotation_enabled", True)),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_rules(
    session: Session,
    *,
    customer_id: int,
    content_category: str | None = None,
    orientation: str | None = None,
    include_archived: bool = False,
) -> list[ProductionRuleProfile]:
    q = select(ProductionRuleProfile).where(ProductionRuleProfile.customer_id == customer_id)
    if content_category:
        q = q.where(ProductionRuleProfile.content_category == content_category)
    if orientation in {"portrait", "landscape"}:
        q = q.where(ProductionRuleProfile.orientation == orientation)
    if not include_archived:
        q = q.where(ProductionRuleProfile.status != "archived")
    q = q.order_by(ProductionRuleProfile.id.desc())
    return list(session.scalars(q).all())


def get_rule(session: Session, *, customer_id: int, rule_id: int) -> ProductionRuleProfile | None:
    return session.scalar(
        select(ProductionRuleProfile).where(
            ProductionRuleProfile.id == rule_id,
            ProductionRuleProfile.customer_id == customer_id,
        )
    )


def get_active_rule(
    session: Session,
    *,
    customer_id: int,
    content_category: str = "default",
    orientation: str = "portrait",
) -> ProductionRuleProfile | None:
    customer = session.get(Customer, customer_id)
    profile = customer.profile_json if customer and isinstance(customer.profile_json, dict) else {}
    active_map = profile.get("active_production_rules")
    orientation = orientation if orientation in {"portrait", "landscape"} else "portrait"
    active_key = f"{orientation}:{content_category}"
    active_id = active_map.get(active_key) if isinstance(active_map, dict) else None
    if active_id is None and orientation == "portrait" and isinstance(active_map, dict):
        active_id = active_map.get(content_category)
    if active_id is None and orientation == "portrait" and content_category == "default":
        active_id = profile.get("active_production_rule_id")
    if active_id is not None:
        try:
            rid = int(active_id)
        except (TypeError, ValueError):
            rid = None
        if rid is not None:
            row = get_rule(session, customer_id=customer_id, rule_id=rid)
            if (
                row
                and row.status == "approved"
                and (row.content_category or "default") == content_category
                and (row.orientation or "portrait") == orientation
            ):
                return row
    # Once a v2 map exists, a missing category key explicitly means inactive.
    # Do not resurrect an older activated revision after the current one is archived.
    if isinstance(active_map, dict):
        return None
    return session.scalar(
        select(ProductionRuleProfile)
        .where(
            ProductionRuleProfile.customer_id == customer_id,
            ProductionRuleProfile.content_category == content_category,
            ProductionRuleProfile.orientation == orientation,
            ProductionRuleProfile.status == "approved",
            ProductionRuleProfile.activated_at.is_not(None),
        )
        .order_by(ProductionRuleProfile.activated_at.desc())
        .limit(1)
    )


def _next_revision(
    session: Session,
    *,
    customer_id: int,
    content_category: str,
    orientation: str,
) -> int:
    rows = session.scalars(
        select(ProductionRuleProfile.revision).where(
            ProductionRuleProfile.customer_id == customer_id,
            ProductionRuleProfile.orientation == orientation,
        )
    ).all()
    return (max(rows) if rows else 0) + 1


def create_draft(
    session: Session,
    *,
    customer_id: int,
    content_category: str = "default",
    orientation: str = "portrait",
    name: str,
    source_text: str,
    rules: dict[str, Any] | None,
    model: str = "",
    extra_warnings: list[str] | None = None,
    rotation_enabled: bool = True,
) -> ProductionRuleProfile:
    report = validate_and_clamp(rules)
    warnings = list(report.get("warnings") or [])
    if extra_warnings:
        warnings.extend(str(w) for w in extra_warnings if str(w).strip())
    now = datetime.now(timezone.utc)
    clean_name = (name or "未命名规则").strip()[:128] or "未命名规则"
    clean_category = (content_category or "default").strip()[:128] or "default"
    clean_orientation = orientation if orientation in {"portrait", "landscape"} else "portrait"
    row = ProductionRuleProfile(
        customer_id=customer_id,
        content_category=clean_category,
        orientation=clean_orientation,
        name=clean_name,
        source_text=(source_text or "")[:8000],
        requested_json=report["requested_rules"],
        effective_json=report["effective_rules"],
        schema_version=SCHEMA_VERSION,
        status="draft",
        revision=_next_revision(
            session,
            customer_id=customer_id,
            content_category=clean_category,
            orientation=clean_orientation,
        ),
        model=(model or "")[:128],
        parse_warnings_json=warnings[:40],
        rejected_json=list(report.get("rejected") or []),
        clamped_json=list(report.get("clamped") or []),
        rotation_enabled=bool(rotation_enabled),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def ensure_default_rule(
    session: Session,
    *,
    customer: Customer,
    orientation: str,
    content_category: str = "default",
) -> ProductionRuleProfile:
    existing = get_active_rule(
        session,
        customer_id=customer.id,
        content_category=content_category,
        orientation=orientation,
    )
    if existing:
        return existing
    orientation = orientation if orientation in {"portrait", "landscape"} else "portrait"
    rules = empty_rules()
    rules["orientation"] = orientation
    if orientation == "landscape":
        rules.update(
            {
                "title_font_size": 76,
                "title_glyph_top_px": 120,
                "subtitle_font_size": 52,
                "subtitle_glyph_bottom_px": 180,
            }
        )
    profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
    expression = profile.get("expression") if isinstance(profile.get("expression"), dict) else {}
    for key in ("voice_lang", "subtitle_lang", "subtitle_burn", "dual_secondary_lang"):
        if expression.get(key) is not None:
            rules[key] = expression[key]
    try:
        from engine.pack.video_lock import load_video_lock

        lock = load_video_lock(
            customer.name,
            output_root=customer.output_root,
            profile=profile,
        )
        title = lock.get("title") if isinstance(lock.get("title"), dict) else {}
        subtitle = lock.get("subtitle") if isinstance(lock.get("subtitle"), dict) else {}
        voice = lock.get("voice") if isinstance(lock.get("voice"), dict) else {}
        rules.update(
            {
                "title_color": title.get("color") or rules["title_color"],
                "title_stroke_color": title.get("stroke_color")
                or rules["title_stroke_color"],
                "title_stroke_width": title.get("stroke_width")
                if title.get("stroke_width") is not None
                else rules["title_stroke_width"],
                "subtitle_color": subtitle.get("color") or rules["subtitle_color"],
                "subtitle_stroke_color": subtitle.get("outline")
                or rules["subtitle_stroke_color"],
                "tts_provider": voice.get("provider") or rules["tts_provider"],
                "tts_voice": voice.get("voice") or rules["tts_voice"],
                "voice_pack": voice.get("voice_pack") or rules["voice_pack"],
                "narration_rate": voice.get("rate") or rules["narration_rate"],
                "narration_volume": voice.get("volume")
                or rules["narration_volume"],
                "narration_pitch": voice.get("pitch") or rules["narration_pitch"],
            }
        )
    except Exception:
        pass
    row = create_draft(
        session,
        customer_id=customer.id,
        content_category=content_category,
        orientation=orientation,
        name="推荐横屏" if orientation == "landscape" else "推荐竖屏",
        source_text="系统推荐设置",
        rules=rules,
    )
    row = approve_rule(session, row, approved_by="system_default")
    return activate_rule(session, row, customer)


def update_draft(
    session: Session,
    row: ProductionRuleProfile,
    *,
    name: str | None = None,
    source_text: str | None = None,
    rules: dict[str, Any] | None = None,
) -> ProductionRuleProfile:
    if row.status != "draft":
        raise ValueError("已批准或归档的规则版本不可编辑；请新建草稿版本")
    if name is not None:
        row.name = (name or row.name).strip()[:128] or row.name
    if source_text is not None:
        row.source_text = source_text[:8000]
    if rules is not None:
        report = validate_and_clamp(rules)
        row.requested_json = report["requested_rules"]
        row.effective_json = report["effective_rules"]
        row.rejected_json = list(report.get("rejected") or [])
        row.clamped_json = list(report.get("clamped") or [])
        row.parse_warnings_json = list(report.get("warnings") or [])
        row.schema_version = SCHEMA_VERSION
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row


def approve_rule(
    session: Session,
    row: ProductionRuleProfile,
    *,
    approved_by: str = "operator",
) -> ProductionRuleProfile:
    if row.status == "archived":
        raise ValueError("已归档规则不可确认")
    report = validate_and_clamp(row.requested_json if isinstance(row.requested_json, dict) else {})
    row.requested_json = report["requested_rules"]
    row.effective_json = report["effective_rules"]
    row.rejected_json = list(report.get("rejected") or [])
    row.clamped_json = list(report.get("clamped") or [])
    from engine.catalog.keyword_pack import get_active_pack, resolve_rule_facet

    customer = session.get(Customer, row.customer_id)
    pack = get_active_pack(session, customer.name) if customer else None
    resolve_rule_facet(pack, row.effective_json)
    row.status = "approved"
    row.approved_by = (approved_by or "operator")[:128]
    row.approved_at = datetime.now(timezone.utc)
    row.updated_at = row.approved_at
    session.commit()
    session.refresh(row)
    return row


def _grvl_effect_flags(rules: dict[str, Any] | None) -> dict[str, Any]:
    """Detect GRVL text-effect combinations that need align precheck / slot guard."""
    data = rules if isinstance(rules, dict) else {}
    effect = str(data.get("narration_text_effect") or "none").strip().lower()
    layout = str(data.get("subtitle_layout") or "horizontal").strip().lower()
    risky = effect in {"karaoke", "marquee"} or layout == "vertical"
    return {
        "risky": risky,
        "narration_text_effect": effect,
        "subtitle_layout": layout,
        "experimental": risky,
    }


def activate_rule(session: Session, row: ProductionRuleProfile, customer: Customer) -> ProductionRuleProfile:
    if row.status != "approved":
        raise ValueError("仅已确认规则可启用；请先人工确认")
    if row.customer_id != customer.id:
        raise ValueError("客户域不匹配")
    eff = row.effective_json if isinstance(row.effective_json, dict) else {}
    fx = _grvl_effect_flags(eff)
    slot = (row.content_category or "default").strip() or "default"
    # Daily default slot must stay effect=none (align/产能主线)；特效只走 premium/实验。
    if slot == "default" and fx["risky"]:
        raise ValueError(
            "日更 default 槽禁止启用 karaoke/marquee/竖排字幕特效；"
            "请改回 none/horizontal，或启用到精品（premium）槽并标为实验"
        )
    now = datetime.now(timezone.utc)
    # Clear other activations marker timestamps optional — profile pointer is source of truth
    row.activated_at = now
    row.updated_at = now
    if fx["experimental"]:
        eff = dict(eff)
        eff["grvl_effect_experimental"] = True
        row.effective_json = eff
    profile = dict(customer.profile_json) if isinstance(customer.profile_json, dict) else {}
    active_map = dict(profile.get("active_production_rules") or {})
    active_key = f"{row.orientation or 'portrait'}:{row.content_category or 'default'}"
    active_map[active_key] = row.id
    profile["active_production_rules"] = active_map
    # Newly enabled saved rules join the rotation pool; operator can turn the switch off.
    row.rotation_enabled = True
    # Keep the v1 pointer readable for old clients.
    if (row.orientation or "portrait") == "portrait" and (row.content_category or "default") == "default":
        profile["active_production_rule_id"] = row.id
        profile["active_production_rule_revision"] = row.revision
    customer.profile_json = profile
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(customer, "profile_json")
    if fx["experimental"]:
        flag_modified(row, "effective_json")
    session.commit()
    session.refresh(row)
    return row


def archive_rule(session: Session, row: ProductionRuleProfile, customer: Customer) -> ProductionRuleProfile:
    row.status = "archived"
    row.updated_at = datetime.now(timezone.utc)
    profile = dict(customer.profile_json) if isinstance(customer.profile_json, dict) else {}
    active_map = dict(profile.get("active_production_rules") or {})
    category = row.content_category or "default"
    active_key = f"{row.orientation or 'portrait'}:{category}"
    try:
        category_active_id = int(active_map.get(active_key) or 0)
    except (TypeError, ValueError):
        category_active_id = 0
    if category_active_id == row.id:
        active_map.pop(active_key, None)
        profile["active_production_rules"] = active_map
    try:
        legacy_active_id = int(profile.get("active_production_rule_id") or 0)
    except (TypeError, ValueError):
        legacy_active_id = 0
    if legacy_active_id == row.id:
        profile.pop("active_production_rule_id", None)
        profile.pop("active_production_rule_revision", None)
    customer.profile_json = profile
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(customer, "profile_json")
    session.commit()
    session.refresh(row)
    return row


def is_job_referenced(session: Session, row: ProductionRuleProfile) -> bool:
    for snap in session.scalars(
        select(Job.config_snapshot_json).where(Job.customer_id == row.customer_id)
    ).all():
        frozen = snap.get("production_rules") if isinstance(snap, dict) else None
        if isinstance(frozen, dict) and int(frozen.get("rule_profile_id") or 0) == row.id:
            return True
    return False


def delete_draft(session: Session, row: ProductionRuleProfile) -> None:
    if row.status != "draft" or row.approved_at or row.activated_at or is_job_referenced(session, row):
        raise ValueError("仅未批准、未启用且未被 Job 引用的草稿可物理删除；其他版本只能归档")
    session.delete(row)
    session.commit()


def copy_to_draft(
    session: Session,
    row: ProductionRuleProfile,
    *,
    name: str | None = None,
    content_category: str | None = None,
) -> ProductionRuleProfile:
    """Clone to a new draft. Optional content_category enables cross-category copy (GRuleLabOpt)."""
    target_category = (content_category if content_category is not None else row.content_category) or "default"
    clean_category = str(target_category).strip()[:128] or "default"
    default_name = f"{row.name} 副本"
    if clean_category != (row.content_category or "default") and name is None:
        default_name = f"{row.name}·精品" if clean_category == "premium" else f"{row.name} · {clean_category}"
    return create_draft(
        session,
        customer_id=row.customer_id,
        content_category=clean_category,
        orientation=row.orientation or "portrait",
        name=name or default_name,
        source_text=row.source_text,
        rules=row.requested_json if isinstance(row.requested_json, dict) else {},
        model=row.model or "",
        extra_warnings=["复制自规则 #%s r%s → %s" % (row.id, row.revision, clean_category)],
        rotation_enabled=bool(getattr(row, "rotation_enabled", True)),
    )


def _facet_semantic_labels(facet: str) -> list[str]:
    mapping = {
        "门店形象": ["store", "interior", "reception", "welcome"],
        "服务沟通": ["consultant", "service", "welcome", "team"],
        "品牌故事": ["store", "overview"],
        "护肤护理": ["care", "facial", "product"],
        "身体与形体": ["body", "care"],
    }
    return list(mapping.get(facet, []))


def drafts_from_pack(
    session: Session,
    *,
    customer: Customer,
    orientation: str = "portrait",
    content_category: str = "default",
    facets: list[str] | None = None,
    force: bool = False,
) -> list[ProductionRuleProfile]:
    """Create one draft per usable keyword-pack facet. Never auto-activates."""
    from engine.catalog.keyword_pack import get_active_pack, list_content_facets, resolve_facet_slice
    from engine.template.rule_schema import content_facet_of

    pack = get_active_pack(session, customer.name)
    if pack is None:
        raise ValueError("客户尚未导入词池，无法从内容面生成规则")
    orient = orientation if orientation in {"portrait", "landscape"} else "portrait"
    category = (content_category or "default").strip() or "default"
    wanted = {str(x).strip() for x in (facets or []) if str(x).strip()}
    available = list_content_facets(pack)
    base = get_active_rule(
        session,
        customer_id=customer.id,
        content_category=category,
        orientation=orient,
    )
    base_rules = empty_rules()
    if base and isinstance(base.effective_json, dict):
        base_rules = dict(base.effective_json)
    existing_facets: set[str] = set()
    if not force:
        for row in list_rules(
            session,
            customer_id=customer.id,
            orientation=orient,
            include_archived=False,
        ):
            pinned = content_facet_of(
                row.effective_json if isinstance(row.effective_json, dict) else {}
            )
            if pinned:
                existing_facets.add(pinned)

    created: list[ProductionRuleProfile] = []
    for item in available:
        name = str(item.get("name") or "").strip()
        if not name or name == "default":
            continue
        if wanted and name not in wanted:
            continue
        if not item.get("usable"):
            continue
        if name in existing_facets:
            continue
        slice_info = resolve_facet_slice(pack, name)
        if not slice_info.get("ok"):
            continue
        rules = dict(base_rules)
        rules["content_facet"] = name
        rules["theme"] = name
        labels = _facet_semantic_labels(name)
        if labels and not rules.get("prefer_semantic_labels"):
            rules["prefer_semantic_labels"] = labels
        row = create_draft(
            session,
            customer_id=customer.id,
            content_category=category,
            orientation=orient,
            name=f"{item.get('label') or name} · 日更",
            source_text=f"由词池内容面「{name}」生成，文案以词池为准。",
            rules=rules,
            extra_warnings=[
                f"content_facet={name}",
                f"titles={slice_info.get('title_count')}",
            ],
        )
        created.append(row)
        existing_facets.add(name)
    return created


def customer_rotation_enabled(customer: Customer) -> bool:
    profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
    raw = profile.get("rule_rotation_enabled")
    if raw is None:
        return True
    return bool(raw)


def set_customer_rotation_enabled(session: Session, customer: Customer, *, enabled: bool) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    profile = dict(customer.profile_json) if isinstance(customer.profile_json, dict) else {}
    profile["rule_rotation_enabled"] = bool(enabled)
    customer.profile_json = profile
    flag_modified(customer, "profile_json")
    session.commit()


def set_rule_rotation(session: Session, row: ProductionRuleProfile, *, enabled: bool) -> ProductionRuleProfile:
    if row.status == "archived":
        raise ValueError("已归档规则不能参加轮换")
    row.rotation_enabled = bool(enabled)
    row.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(row)
    return row


def list_rotation_pool(
    session: Session,
    *,
    customer_id: int,
    orientation: str,
    content_category: str | None = None,
) -> list[ProductionRuleProfile]:
    """Approved + switch on + same orientation (+ same rule slot when given).

    Drafts/archived never included. When content_category is set, only that
    slot participates so daily jobs cannot rotate into premium/scene_tour rules.
    """
    orient = orientation if orientation in {"portrait", "landscape"} else "portrait"
    clauses = [
        ProductionRuleProfile.customer_id == customer_id,
        ProductionRuleProfile.orientation == orient,
        ProductionRuleProfile.status == "approved",
        ProductionRuleProfile.rotation_enabled.is_(True),
    ]
    slot = (content_category or "").strip()
    if slot:
        clauses.append(ProductionRuleProfile.content_category == slot)
    rows = list(
        session.scalars(
            select(ProductionRuleProfile)
            .where(*clauses)
            .order_by(ProductionRuleProfile.id.asc())
        ).all()
    )
    return rows


def pick_rule_for_job(
    session: Session,
    *,
    customer: Customer,
    content_category: str,
    orientation: str,
    rotation: bool | None = None,
) -> tuple[ProductionRuleProfile, str]:
    """Return (rule, picked_by). picked_by is rotation | active_slot.

    Rotation default ON (customer profile). Pool = approved rules in the same
    content_category slot with per-rule switch on. Empty pool falls back to
    that slot's active rule.
    """
    use_rotation = customer_rotation_enabled(customer) if rotation is None else bool(rotation)
    orient = orientation if orientation in {"portrait", "landscape"} else "portrait"
    category = (content_category or "default").strip() or "default"
    if use_rotation:
        pool = list_rotation_pool(
            session,
            customer_id=customer.id,
            orientation=orient,
            content_category=category,
        )
        if pool:
            return secrets.choice(pool), "rotation"
    row = get_active_rule(
        session,
        customer_id=customer.id,
        content_category=category,
        orientation=orient,
    )
    if row is None or row.status != "approved":
        from engine.pack.scene_tour_copy import category_label

        cat_zh = category_label(category)
        raise ValueError(
            f"尚未启用「{cat_zh}」规则（画幅={'横屏' if orient == 'landscape' else '竖屏'}）；"
            "请先到规则实验室保存并启用"
        )
    return row, "active_slot"


def freeze_for_job(
    row: ProductionRuleProfile | None,
    *,
    picked_by: str | None = None,
    rotation_pool_size: int | None = None,
    facet_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not row:
        return None
    effective = _effective_v2(row)
    payload = {
        "rule_profile_id": row.id,
        "revision": row.revision,
        "name": row.name,
        "content_category": row.content_category or "default",
        "orientation": row.orientation or "portrait",
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": row.schema_version,
        "source_text": row.source_text,
        "requested_rules": row.requested_json if isinstance(row.requested_json, dict) else {},
        "effective_rules": effective,
        "model": row.model or "",
        "picked_by": picked_by or "active_slot",
        "rotation_pool_size": int(rotation_pool_size or 0),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
    }
    if isinstance(facet_meta, dict):
        payload["content_facet"] = facet_meta.get("facet")
        payload["title_categories_resolved"] = list(facet_meta.get("title_categories") or [])
        payload["facet_resolved_from"] = str(facet_meta.get("resolved_from") or "")
    payload["contract_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload


def dump_compact(row: ProductionRuleProfile) -> str:
    return json.dumps(
        {"id": row.id, "rev": row.revision, "status": row.status, "name": row.name},
        ensure_ascii=False,
    )
