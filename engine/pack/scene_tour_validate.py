"""跟镜精品 · 成片检查（不过检不成片）。"""

from __future__ import annotations

import re
from typing import Any

from engine.pack.scene_tour_copy import INDUSTRY_CROSS_BANS, bucket_label, reason_zh


_EMPTY_PADS = ()  # 空壳营销改由 scene_tour_diction 统一裁决
_ORAL = ("咱们", "啦", "哈哈哈", "绝绝子", "yyds", "冲鸭")


def validate_scene_tour_shots(
    shots: list[dict[str, Any]],
    *,
    brief: dict[str, Any],
) -> dict[str, Any]:
    """检查篇章结构、画面要点、禁用词、串味、雅述、声画锚定。"""
    from engine.pack.scene_tour_diction import diction_fail_reasons
    from engine.pack.scene_tour_grounding import narration_conflicts_evidence

    reasons: list[str] = []
    industry_key = str(brief.get("industry_key") or "")
    banned = [str(t) for t in (brief.get("banned_terms") or []) if str(t).strip()]
    cross = INDUSTRY_CROSS_BANS.get(industry_key, [])
    min_shots = int(brief.get("min_shots") or 8)

    # 品牌别名（与写词侧一致的轻量拆分）
    brand_names: list[str] = []
    display = str(brief.get("brand_display") or "").strip()
    if display:
        brand_names.append(display)
        if "（" in display and "）" in display:
            outer, rest = display.split("（", 1)
            inner = rest.split("）", 1)[0].strip()
            if outer.strip():
                brand_names.append(outer.strip())
            if inner:
                brand_names.append(inner)
    cust = str(brief.get("customer_name") or "").strip()
    if cust:
        brand_names.append(cust)
    brand_names = sorted({n for n in brand_names if n and n not in ("本店",)}, key=len, reverse=True)

    if len(shots) < max(4, min_shots - 4):
        reasons.append(reason_zh("too_few_shots"))

    stage_order = {
        str(s.get("key")): i for i, s in enumerate(brief.get("stages") or []) if s.get("key")
    }
    last_idx = -1
    roles_seen: list[str] = []
    brand_hits_total = 0
    bad_cliplet_ids: list[int] = []

    for i, shot in enumerate(shots):
        text = str(shot.get("text") or "").strip()
        bucket = str(shot.get("bucket") or "").strip()
        anchors = [str(a) for a in (shot.get("anchors") or []) if str(a).strip()]
        role = str(shot.get("role") or "body")
        roles_seen.append(role)
        cid = shot.get("cliplet_id")

        if not text:
            reasons.append(reason_zh("empty_script"))
            if cid:
                bad_cliplet_ids.append(int(cid))
            continue

        idx = stage_order.get(bucket, last_idx)
        if bucket in stage_order and idx < last_idx:
            reasons.append(reason_zh("order_regress"))
        if bucket in stage_order:
            last_idx = max(last_idx, idx)

        if not anchors or not any(a and a in text for a in anchors):
            if not anchors:
                reasons.append(reason_zh("no_anchor"))
            elif not any(a in text for a in anchors):
                reasons.append(reason_zh("no_anchor"))

        for term in banned:
            if term and term in text:
                reasons.append(reason_zh("banned_hit", term=term))
                break
        for term in cross:
            if term and term in text:
                reasons.append(reason_zh("cross_industry", term=term))
                break

        for dr in diction_fail_reasons(
            text,
            available_sec=float(shot.get("available_sec") or shot.get("duration_sec") or 0) or None,
            visual_description=str(shot.get("visual_description") or "") or None,
        ):
            reasons.append(reason_zh("elegance_fail") + f"：{dr}")

        remain = text
        for b in brand_names:
            c = remain.count(b)
            if c:
                brand_hits_total += c
                remain = remain.replace(b, "")

        vis_reasons = narration_conflicts_evidence(
            text,
            bucket=bucket,
            tokens=list(shot.get("visual_tokens") or []),
            stub=bool(shot.get("visual_stub")),
        )
        for vr in vis_reasons:
            reasons.append(reason_zh("visual_mismatch", detail=vr))
            if cid:
                bad_cliplet_ids.append(int(cid))

    if brand_hits_total > 1:
        reasons.append("品牌名在旁白中重复过多（全片最多一次），未通过。")

    if roles_seen and roles_seen[0] not in ("open", "body"):
        reasons.append("开场段落缺失或位置不当。")
    if roles_seen and roles_seen[-1] not in ("close", "body"):
        reasons.append("收束段落缺失或位置不当。")

    norms = [re.sub(r"\s+", "", str(s.get("text") or "")) for s in shots]
    for i in range(len(norms)):
        for j in range(i + 1, len(norms)):
            if norms[i] and norms[i] == norms[j]:
                reasons.append("同片出现完全重复旁白，未通过。")
                break

    from engine.pack.scene_tour_diction import cross_shot_promo_repeat_reasons

    for rr in cross_shot_promo_repeat_reasons(
        [str(s.get("text") or "") for s in shots]
    ):
        reasons.append(rr)

    uniq: list[str] = []
    seen: set[str] = set()
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)

    return {
        "ok": len(uniq) == 0,
        "reasons": uniq,
        "shot_count": len(shots),
        "label_zh": "成片检查报告",
        "buckets": [bucket_label(str(s.get("bucket"))) for s in shots],
        "bad_cliplet_ids": sorted(set(bad_cliplet_ids)),
    }
