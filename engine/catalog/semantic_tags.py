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
    rules = _rules_from_pack(pack, "scene_rules")
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
    rules = _rules_from_pack(pack, "action_rules")
    scored = _score_labels(text, rules)
    return [lab for lab, _ in scored[:limit]]


def compose_embed_text(cliplet: Cliplet) -> str:
    """Text fed into embedding — richer than description alone."""
    parts: list[str] = []
    semantic = cliplet.semantic_json if isinstance(getattr(cliplet, "semantic_json", None), dict) else {}
    if semantic:
        scenes = [str(v.get("label")) for v in semantic.get("scenes") or [] if isinstance(v, dict) and v.get("label")]
        interactions = [
            str(v.get("label"))
            for v in semantic.get("interactions") or []
            if isinstance(v, dict) and v.get("label")
        ]
        products = [
            " / ".join(
                filter(
                    None,
                    [
                        str(v.get("name") or ""),
                        str(v.get("category") or ""),
                        str(v.get("use") or ""),
                        "、".join(str(a) for a in (v.get("key_attributes") or []) if a),
                    ],
                )
            )
            for v in semantic.get("products") or []
            if isinstance(v, dict)
        ]
        people = semantic.get("people") if isinstance(semantic.get("people"), dict) else {}
        people_labels = [
            str(v.get("label"))
            for v in people.get("promotion_labels") or []
            if isinstance(v, dict) and v.get("label")
        ]
        facts = [
            str(fact)
            for frame in semantic.get("frames") or []
            if isinstance(frame, dict)
            for fact in frame.get("visible_facts") or []
            if fact
        ]
        if scenes:
            parts.append("场景分类=" + "、".join(scenes))
        if products:
            parts.append("产品=" + "；".join(products))
        if interactions:
            parts.append("互动=" + "、".join(interactions))
        if people_labels:
            parts.append(
                "人物宣传="
                + "、".join(people_labels)
                + f"；精神面貌={people.get('appearance') or '未知'}；衣着={people.get('clothing') or '未知'}"
            )
        if facts:
            parts.append("逐帧可见事实=" + "；".join(facts))
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


def apply_structured_semantics(cliplet: Cliplet, data: dict[str, Any]) -> dict[str, Any]:
    """Persist v1 structured labels into both JSON and legacy query columns."""
    cliplet.semantic_json = data
    cliplet.semantic_schema_version = str(data.get("schema_version") or "")
    cliplet.description = str(data.get("description") or "").strip()
    scenes = [
        str(row.get("label"))
        for row in data.get("scenes") or []
        if isinstance(row, dict) and row.get("label")
    ]
    interactions = [
        str(row.get("label"))
        for row in data.get("interactions") or []
        if isinstance(row, dict) and row.get("label")
    ]
    products = [
        str(row.get("name"))
        for row in data.get("products") or []
        if isinstance(row, dict) and row.get("name")
    ]
    cliplet.scene = scenes[0] if scenes else None
    cliplet.objects_json = products
    cliplet.actions_json = [v for v in interactions if v != "none_visible"]
    return {
        "scenes": scenes,
        "products": products,
        "interactions": interactions,
        "people": data.get("people") or {},
    }


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
