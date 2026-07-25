"""Semantic annotation for cliplets: scene / objects / actions + embed text.

Rules load from industry pack (scene_rules / object_rules / action_rules),
falling back to theme_rules keywords when specialized rules are absent.
"""

from __future__ import annotations

from typing import Any

from engine.catalog.db import Asset, Cliplet
from engine.catalog.industry_pack import load_industry_pack
from engine.catalog.theme_tags import apply_theme_to_cliplet

_DEFAULT_PACK = "_blank"

# Product-neutral fallbacks when industry pack omits specialized rules
_FALLBACK_SCENE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("装车卸货", ("装车", "卸货", "货车", "卡车", "配送", "发货", "送货", "车斗", "三轮车")),
    ("仓内货架", ("仓库", "货架", "堆放", "库存", "分拣", "配货", "码垛", "仓内", "库房")),
    ("门店门头", ("门店", "门头", "展厅", "展示区", "店面", "招牌", "收银台")),
    ("产品特写", ("特写", "产品", "配件", "细节", "特写镜头")),
    ("工地现场", ("工地", "现场", "施工", "钢筋", "浇筑")),
    ("人物作业", ("工人", "员工", "作业", "操作", "搬运")),
]

_FALLBACK_ACTION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("装车", ("装车", "装货", "上车")),
    ("卸货", ("卸货", "卸下")),
    ("分拣", ("分拣", "拣货", "配货")),
    ("堆放", ("堆放", "码垛", "堆叠")),
    ("演示", ("演示", "开机", "试用", "操作设备")),
]


def _rules_from_pack(pack: dict[str, Any], key: str) -> list[tuple[str, tuple[str, ...]]]:
    raw = pack.get(key) or []
    out: list[tuple[str, tuple[str, ...]]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        label = str(row.get("theme") or row.get("code") or row.get("label") or "").strip()
        kws = row.get("keywords") or []
        if label and isinstance(kws, list) and kws:
            out.append((label, tuple(str(k) for k in kws if k)))
    return out


def _score_labels(text: str, rules: list[tuple[str, tuple[str, ...]]]) -> list[tuple[str, float]]:
    text_l = text.lower()
    scored: list[tuple[str, float]] = []
    for label, keywords in rules:
        hits = sum(1 for k in keywords if k.lower() in text_l or k in text)
        if hits:
            scored.append((label, float(hits)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def classify_scene(text: str, *, pack_id: str | None = None) -> str:
    pack = load_industry_pack(pack_id or _DEFAULT_PACK)
    rules = _rules_from_pack(pack, "scene_rules") or _FALLBACK_SCENE_RULES
    scored = _score_labels(text, rules)
    return scored[0][0] if scored else "default"


def classify_objects(text: str, *, pack_id: str | None = None, limit: int = 5) -> list[str]:
    pack = load_industry_pack(pack_id or _DEFAULT_PACK)
    rules = _rules_from_pack(pack, "object_rules")
    if not rules:
        # Derive coarse objects from theme_rules labels that look like products
        for row in pack.get("theme_rules") or []:
            if isinstance(row, dict) and row.get("theme") and row.get("theme") != "default":
                rules.append((str(row["theme"]), tuple(str(k) for k in (row.get("keywords") or []) if k)))
    scored = _score_labels(text, rules)
    return [lab for lab, _ in scored[:limit]]


def classify_actions(text: str, *, pack_id: str | None = None, limit: int = 4) -> list[str]:
    pack = load_industry_pack(pack_id or _DEFAULT_PACK)
    rules = _rules_from_pack(pack, "action_rules") or _FALLBACK_ACTION_RULES
    scored = _score_labels(text, rules)
    return [lab for lab, _ in scored[:limit]]


def compose_embed_text(cliplet: Cliplet) -> str:
    """Text fed into embedding — richer than description alone."""
    parts: list[str] = []
    if cliplet.theme:
        parts.append(f"theme={cliplet.theme}")
    if cliplet.scene:
        parts.append(f"scene={cliplet.scene}")
    objs = cliplet.objects_json or []
    if objs:
        parts.append("物品=" + "、".join(objs))
    acts = cliplet.actions_json or []
    if acts:
        parts.append("动作=" + "、".join(acts))
    if cliplet.description:
        parts.append(cliplet.description)
    return "；".join(parts) if parts else (cliplet.description or "")


def annotate_cliplet(
    cliplet: Cliplet,
    asset: Asset | None = None,
    *,
    pack_id: str | None = None,
) -> dict[str, Any]:
    """Write theme + scene + objects + actions onto cliplet."""
    apply_theme_to_cliplet(cliplet, asset, pack_id=pack_id)
    name = ""
    cat = cliplet.category or ""
    if asset is not None:
        from pathlib import Path

        name = Path(asset.source_path or "").stem
        cat = cat or (asset.category or "")
    blob = f"{cliplet.description or ''} {name} {cat}"
    cliplet.scene = classify_scene(blob, pack_id=pack_id)
    cliplet.objects_json = classify_objects(blob, pack_id=pack_id)
    cliplet.actions_json = classify_actions(blob, pack_id=pack_id)
    return {
        "theme": cliplet.theme,
        "scene": cliplet.scene,
        "objects": list(cliplet.objects_json or []),
        "actions": list(cliplet.actions_json or []),
    }
