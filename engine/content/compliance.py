"""Compliance gates for SEO/GEO articles."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from engine.content.platforms import get_platform

_EXTREME = re.compile(
    r"(最[优佳强低高]|第一|独家|100%|绝对|永久|国家级|全网最低|包治|根治)"
)
_AI_LABEL = re.compile(r"(AI\s*生成|人工智能生成|含AI合成|AI创作声明)", re.I)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_CREDIT_CODE = re.compile(r"(?<![A-Z0-9])[0-9A-Z]{18}(?![A-Z0-9])", re.I)
_EXTERNAL_CONTACT = re.compile(r"(加微|微信[:：]?\w+|私信.*(?:号码|微信)|扫码联系|站外联系)", re.I)
_DEFAULT_HARD_DENY = (
    "第一", "最好", "最强", "绝对", "100%", "国家级", "央企指定", "全网最低",
    "厂家直销", "核心代理", "授权总代", "保证送达", "必达", "秒达", "包赔", "假一赔十",
)
_DEFAULT_EVIDENCE = {
    "现货": "inventory_snapshot",
    "库存": "inventory_snapshot",
    "送达": "delivery_commitment",
    "经营年限": "business_registry",
    "仓库面积": "verified_scale",
    "品牌授权": "authorization_document",
    "授权经销": "authorization_document",
    "规格": "product_specification",
    "型号": "product_specification",
    "价格": "current_price",
}


def normalize_for_compliance(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)


def _contains(text: str, term: str) -> bool:
    return bool(term and normalize_for_compliance(term) in normalize_for_compliance(text))


def scan_terms(text: str, terms: list[str]) -> list[str]:
    return list(dict.fromkeys(term for term in terms if _contains(text, str(term))))


def check_output_fields(
    fields: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    evidence_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Unified legal/platform gate for every generated public text field."""
    policy = policy if isinstance(policy, dict) else {}
    evidence = set(evidence_ids or set())
    hard_deny = [
        *[str(x) for x in _DEFAULT_HARD_DENY],
        *[str(x) for x in (policy.get("hard_deny") or [])],
        *[str(x) for x in (policy.get("blocked_terms") or [])],
    ]
    required = dict(_DEFAULT_EVIDENCE)
    required.update(
        {
            str(term): str(evidence_id)
            for term, evidence_id in (policy.get("evidence_required") or {}).items()
        }
        if isinstance(policy.get("evidence_required"), dict)
        else {}
    )
    caution = [str(x) for x in (policy.get("caution_terms") or ["更快", "更稳", "更省", "放心", "靠谱"])]
    rewrites = policy.get("safe_rewrites") if isinstance(policy.get("safe_rewrites"), dict) else {}
    issues: list[dict[str, Any]] = []

    def visit(path: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(f"{path}.{key}" if path else str(key), child)
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(f"{path}[{index}]", child)
            return
        text = str(value or "")
        if not text:
            return
        for term in scan_terms(text, hard_deny):
            issues.append(
                {
                    "field": path,
                    "rule_id": "claim.hard_deny",
                    "level": "error",
                    "term": term,
                    "message": f"命中禁止声明：{term}",
                    "replacement": rewrites.get(term) or "改为当前画面可见事实",
                }
            )
        for term, evidence_id in required.items():
            disclaimer = any(
                marker in text
                for marker in ("以实际确认", "请先确认", "进一步确认", "以咨询为准", "以实拍为准")
            )
            if _contains(text, term) and evidence_id not in evidence and not disclaimer:
                issues.append(
                    {
                        "field": path,
                        "rule_id": "claim.evidence_required",
                        "level": "error",
                        "term": term,
                        "evidence_missing": evidence_id,
                        "message": f"声明“{term}”缺少证据 {evidence_id}",
                        "replacement": rewrites.get(term) or f"{term}信息以实际确认结果为准",
                    }
                )
        for term in scan_terms(text, caution):
            issues.append(
                {
                    "field": path,
                    "rule_id": "claim.caution",
                    "level": "warning",
                    "term": term,
                    "message": f"建议将“{term}”改为具体过程事实",
                    "replacement": rewrites.get(term) or "描述画面可见的具体过程",
                }
            )
        if _PHONE.search(text) or _CREDIT_CODE.search(text):
            issues.append(
                {
                    "field": path,
                    "rule_id": "privacy.internal_identifier",
                    "level": "error",
                    "message": "公开字段包含手机号或统一社会信用代码",
                    "replacement": "删除内部身份信息",
                }
            )
        if _EXTERNAL_CONTACT.search(text):
            issues.append(
                {
                    "field": path,
                    "rule_id": "platform.external_diversion",
                    "level": "error",
                    "message": "疑似站外导流",
                    "replacement": "使用平台内合规互动方式",
                }
            )

    visit("", fields)
    errors = [item for item in issues if item["level"] == "error"]
    return {
        "passed": not errors,
        "issues": issues,
        "error_count": len(errors),
        "warn_count": len(issues) - len(errors),
        "policy_version": str(policy.get("policy_version") or "builtin-v1"),
    }


# Short on-screen safe substitutes when evidence is missing (titles must stay compact).
_SHORT_EVIDENCE_REWRITES = {
    "现货": "在架",
    "库存": "仓内",
    "送达": "配送",
    "经营年限": "经营情况",
    "仓库面积": "仓储能力",
    "品牌授权": "品牌合作",
    "授权经销": "品牌合作",
    "规格": "品类",
    "型号": "品类",
    "价格": "报价",
}

# Soft marketing / demoted hard_deny terms that may be fail-soft rewritten.
_SHORT_SOFT_DENY_REWRITES = {
    "放心": "踏实",
    "靠谱": "踏实",
    "更省": "省心",
    "不耽误": "跟得上节奏",
    "一站配齐": "现场货品可看",
    "品类齐全": "品类丰富",
    "发货快": "发货节奏清楚",
    "快速发货": "发货节奏清楚",
    "配货快": "配货节奏清楚",
    "发货稳": "发货节奏清楚",
    "及时": "按节奏推进",
    "当天": "按现场节奏",
    "送到": "配送",
    # Never rewrite into warehouse-speak “仓内可看” (bleeds into product tabletop VO)
    "常备": "用得上",
    "稳当": "节奏清楚",
    "顺畅": "流程清楚",
    "利落": "动作清楚",
    "不乱": "整齐有序",
    "少跑": "流程更短",
    "跟得上": "节奏清楚",
    "更快": "节奏清楚",
    "更稳": "节奏清楚",
    # Longer compound claims (override dirty pack safe_rewrites that inject 库存)
    "现货充足": "在架情况请以实际确认为准",
    "保证送达": "交付安排请以实际确认为准",
}

_SOFT_REWRITE_RULES = frozenset({"claim.evidence_required", "claim.hard_deny"})


def _policy_sensitive_terms(policy: dict[str, Any] | None) -> set[str]:
    """Terms that must not appear inside a soft-rewrite replacement string."""
    policy = policy if isinstance(policy, dict) else {}
    out: set[str] = set()
    for key in ("hard_deny", "blocked_terms"):
        for item in policy.get(key) or []:
            t = str(item or "").strip()
            if t:
                out.add(t)
    evidence = policy.get("evidence_required")
    if isinstance(evidence, dict):
        for item in evidence:
            t = str(item or "").strip()
            if t:
                out.add(t)
    # Builtin evidence keys stay sensitive even when pack omits them.
    out.update(_SHORT_EVIDENCE_REWRITES.keys())
    return out


def _replacement_introduces_sensitive(replacement: str, sensitive: set[str]) -> bool:
    text = str(replacement or "")
    if not text:
        return True
    for term in sorted((t for t in sensitive if t), key=len, reverse=True):
        if term in text:
            return True
    return False


def _resolve_soft_replacement(
    term: str,
    *,
    custom: dict[str, Any],
    rule_id: str,
    policy: dict[str, Any] | None = None,
) -> str:
    key = str(term or "").strip()
    if not key:
        return ""
    sensitive = _policy_sensitive_terms(policy)
    for table in (custom, _SHORT_EVIDENCE_REWRITES, _SHORT_SOFT_DENY_REWRITES):
        value = str(table.get(key) or "").strip()
        if not value or value == key:
            continue
        if _replacement_introduces_sensitive(value, sensitive):
            continue
        return value
    if rule_id == "claim.evidence_required":
        fallback = "以实拍为准"
        if not _replacement_introduces_sensitive(fallback, sensitive):
            return fallback
    return ""


def _rewrite_term_in_text(before: str, term: str, replacement: str) -> str:
    if term not in before:
        return before
    if replacement and replacement in before:
        after = before.replace(term, "")
    else:
        after = before.replace(term, replacement)
    after = re.sub(r"[ \t]{2,}", " ", after).strip()
    return re.sub(r"\n{3,}", "\n\n", after)


def apply_claim_soft_rewrites(
    fields: dict[str, Any],
    compliance: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    allow_soft_deny: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fail-soft rewrite for evidence gaps and soft marketing denies.

    True hard denies without a known short replacement, privacy, and diversion
    errors are left untouched so the caller still blocks.
    """
    policy = policy if isinstance(policy, dict) else {}
    custom = policy.get("safe_rewrites") if isinstance(policy.get("safe_rewrites"), dict) else {}
    applied: list[dict[str, Any]] = []
    errors = [
        item
        for item in (compliance.get("issues") or [])
        if isinstance(item, dict) and item.get("level") == "error"
    ]
    if not errors:
        return dict(fields), applied

    allowed_rules = {"claim.evidence_required"}
    if allow_soft_deny:
        allowed_rules |= {"claim.hard_deny"}

    # Privacy / diversion / unknown rule_ids cannot soft-pass this layer.
    if any(str(item.get("rule_id") or "") not in allowed_rules for item in errors):
        # Still try partial rewrites for the allowed subset when mixed with soft deny;
        # only abort early when a non-rewritable hard error is present AND soft deny
        # is disabled (legacy evidence-only behavior).
        if not allow_soft_deny:
            return dict(fields), applied
        if any(
            str(item.get("rule_id") or "")
            not in {"claim.evidence_required", "claim.hard_deny"}
            for item in errors
        ):
            # Keep going only for evidence + hard_deny mix; privacy/diversion abort.
            non_soft = [
                item
                for item in errors
                if str(item.get("rule_id") or "")
                not in {"claim.evidence_required", "claim.hard_deny"}
            ]
            if non_soft:
                return dict(fields), applied

    out = dict(fields)

    def _apply_to_value(value: Any, term: str, replacement: str) -> Any:
        if isinstance(value, dict):
            return {k: _apply_to_value(v, term, replacement) for k, v in value.items()}
        if isinstance(value, list):
            return [_apply_to_value(v, term, replacement) for v in value]
        if not isinstance(value, str):
            return value
        return _rewrite_term_in_text(value, term, replacement)

    for item in errors:
        rule_id = str(item.get("rule_id") or "")
        if rule_id not in allowed_rules:
            continue
        term = str(item.get("term") or "").strip()
        if not term:
            continue
        replacement = _resolve_soft_replacement(
            term, custom=custom, rule_id=rule_id, policy=policy
        )
        if not replacement:
            continue
        field = str(item.get("field") or "")
        if field and field in out:
            before = out.get(field)
            after = _apply_to_value(before, term, replacement)
            if after != before:
                out[field] = after
                applied.append(
                    {
                        "field": field,
                        "term": term,
                        "replacement": replacement,
                        "rule_id": rule_id,
                    }
                )
            continue
        # Nested / multi-field: scan all top-level string-ish values.
        changed_any = False
        for key, value in list(out.items()):
            after = _apply_to_value(value, term, replacement)
            if after != value:
                out[key] = after
                changed_any = True
                applied.append(
                    {
                        "field": key if not field else field,
                        "term": term,
                        "replacement": replacement,
                        "rule_id": rule_id,
                    }
                )
        if not changed_any and field:
            # Path like platforms.douyin.body — walk nested.
            parts = field.split(".")
            if len(parts) >= 2 and parts[0] in out:
                cursor: Any = out
                ok = True
                for part in parts[:-1]:
                    if not isinstance(cursor, dict) or part not in cursor:
                        ok = False
                        break
                    cursor = cursor[part]
                leaf = parts[-1]
                if ok and isinstance(cursor, dict) and isinstance(cursor.get(leaf), str):
                    before = cursor[leaf]
                    after = _rewrite_term_in_text(before, term, replacement)
                    if after != before:
                        cursor[leaf] = after
                        applied.append(
                            {
                                "field": field,
                                "term": term,
                                "replacement": replacement,
                                "rule_id": rule_id,
                            }
                        )
    return out, applied


def sanitize_script_claims(
    script: str,
    *,
    policy: dict[str, Any] | None = None,
    extra_terms: list[str] | None = None,
) -> str:
    """Deterministic post-pass for narration scripts before TTS / gate / SRT pack."""
    text = str(script or "")
    if not text:
        return text
    policy = policy if isinstance(policy, dict) else {}
    custom = policy.get("safe_rewrites") if isinstance(policy.get("safe_rewrites"), dict) else {}
    # Longer keys first so 「放心靠谱」 beats 「放心」.
    terms = list(
        dict.fromkeys(
            [
                *list(custom.keys()),
                *[str(x) for x in (policy.get("hard_deny") or []) if str(x).strip()],
                *[str(x) for x in (policy.get("blocked_terms") or []) if str(x).strip()],
                *list(_SHORT_SOFT_DENY_REWRITES.keys()),
                *list(_SHORT_EVIDENCE_REWRITES.keys()),
                *[str(x) for x in (extra_terms or []) if str(x).strip()],
            ]
        )
    )
    terms.sort(key=len, reverse=True)
    for term in terms:
        replacement = _resolve_soft_replacement(
            term, custom=custom, rule_id="claim.hard_deny", policy=policy
        )
        if not replacement:
            continue
        if term in text:
            text = _rewrite_term_in_text(text, term, replacement)
    return text


def apply_rewrites_to_text(
    text: str,
    applied: list[dict[str, Any]] | None,
) -> str:
    """Re-apply a list of {term, replacement} soft rewrites to an arbitrary string."""
    out = str(text or "")
    if not out or not applied:
        return out
    # Longer terms first.
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in applied:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        replacement = str(item.get("replacement") or "").strip()
        if not term or not replacement or term in seen:
            continue
        seen.add(term)
        pairs.append((term, replacement))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    for term, replacement in pairs:
        if term in out:
            out = _rewrite_term_in_text(out, term, replacement)
    return out


def apply_evidence_claim_rewrites(
    fields: dict[str, Any],
    compliance: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Rewrite missing-evidence claim terms so production can continue fail-soft.

    Hard-deny / privacy / diversion errors are not rewritten here.
    """
    return apply_claim_soft_rewrites(
        fields,
        compliance,
        policy=policy,
        allow_soft_deny=False,
    )


def check_article_compliance(
    *,
    title: str,
    body_md: str,
    fact_ids: list[int],
    citations: list[Any],
    banned_terms: list[str] | None = None,
    ai_labeled: bool = True,
    platform: str | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    text = f"{title}\n{body_md}"
    if not (title or "").strip():
        issues.append({"code": "empty_title", "level": "error", "message": "标题为空"})
    if len((body_md or "").strip()) < 200:
        issues.append({"code": "body_too_short", "level": "error", "message": "正文过短（至少约200字）"})
    if not fact_ids:
        issues.append({"code": "no_facts", "level": "error", "message": "未绑定已确认事实"})
    if not citations:
        issues.append({"code": "no_citations", "level": "warn", "message": "缺少引用来源"})
    hits = scan_terms(text, banned_terms or [])
    for term in hits:
        issues.append({"code": "banned_term", "level": "error", "message": f"命中禁词：{term}"})
    for m in _EXTREME.finditer(text):
        issues.append(
            {
                "code": "extreme_claim",
                "level": "warn",
                "message": f"疑似极限词：{m.group(0)}",
            }
        )
    if ai_labeled and not _AI_LABEL.search(text):
        issues.append(
            {
                "code": "ai_label_missing",
                "level": "error",
                "message": "已启用 AI 生成标识，正文须含显式 AI 声明",
            }
        )
    if platform:
        try:
            spec = get_platform(platform)
        except ValueError as exc:
            issues.append({"code": "unknown_platform", "level": "error", "message": str(exc)})
        else:
            tmax = int(spec.get("title_max") or 0)
            tmin = int(spec.get("title_min") or 0)
            bmax = int(spec.get("body_max") or 0)
            if tmax and len(title) > tmax:
                issues.append(
                    {
                        "code": "title_too_long",
                        "level": "error",
                        "message": f"{spec.get('short')}标题超过 {tmax} 字",
                    }
                )
            if tmin and len(title) < tmin:
                issues.append(
                    {
                        "code": "title_too_short",
                        "level": "error",
                        "message": f"{spec.get('short')}标题少于 {tmin} 字",
                    }
                )
            if bmax and len(body_md) > bmax:
                issues.append(
                    {
                        "code": "body_too_long",
                        "level": "error",
                        "message": f"{spec.get('short')}正文超过 {bmax} 字",
                    }
                )
            if spec.get("allow_external_links") is False and re.search(
                r"https?://", body_md
            ):
                issues.append(
                    {
                        "code": "external_link_blocked",
                        "level": "warn",
                        "message": f"{spec.get('short')}通常限制外链，请人工确认",
                    }
                )
    errors = [i for i in issues if i["level"] == "error"]
    return {
        "passed": len(errors) == 0,
        "issues": issues,
        "error_count": len(errors),
        "warn_count": len(issues) - len(errors),
    }
