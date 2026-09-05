"""Evidence-only content fingerprinting and recipe routing."""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from typing import Any, Iterable


def _strict_rows(rows: Iterable[Any]) -> list[Any]:
    """Return only rows that still satisfy the persisted strict gate."""
    from engine.ingest.semantic_gate import semantic_gate_passed

    return [row for row in rows if semantic_gate_passed(row)]


def _labels(rows: Iterable[Any], key: str, value_key: str = "label") -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for row in rows:
        semantic = row.semantic_json if isinstance(getattr(row, "semantic_json", None), dict) else {}
        values = semantic.get(key) or []
        if isinstance(values, dict):
            values = values.get("promotion_labels") or []
        for value in values:
            if not isinstance(value, dict) or not value.get(value_key):
                continue
            result.append((str(value[value_key]), float(value.get("confidence") or 1.0)))
    return result


def build_content_fingerprint(
    cliplets: Iterable[Any],
    pack_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Aggregate only persisted strict-semantic labels; never infer from filenames."""
    input_rows = list(cliplets)
    rows = _strict_rows(input_rows)
    scenes = _labels(rows, "scenes")
    products = _labels(rows, "products", "name")
    interactions = _labels(rows, "interactions")
    people = _labels(rows, "people")
    visible_facts: list[str] = []
    unknowns: list[str] = []
    evidence_frames: list[dict[str, Any]] = []
    for row in rows:
        semantic = row.semantic_json if isinstance(getattr(row, "semantic_json", None), dict) else {}
        for frame in semantic.get("frames") or []:
            if not isinstance(frame, dict):
                continue
            facts = [str(x).strip() for x in (frame.get("visible_facts") or []) if str(x).strip()]
            frame_unknowns = [
                str(x).strip() for x in (frame.get("unknowns") or []) if str(x).strip()
            ]
            visible_facts.extend(facts)
            unknowns.extend(frame_unknowns)
            if facts or frame_unknowns:
                evidence_frames.append(
                    {
                        "cliplet_id": getattr(row, "id", None),
                        "timestamp_sec": frame.get("timestamp_sec"),
                        "facts": facts,
                        "unknowns": frame_unknowns,
                    }
                )
        unknowns.extend(str(x).strip() for x in (semantic.get("unknowns") or []) if str(x).strip())

    scores: Counter[str] = Counter()
    taxonomy = pack_data.get("taxonomy") if isinstance((pack_data or {}).get("taxonomy"), dict) else {}
    content_types = taxonomy.get("content_types") if isinstance(taxonomy.get("content_types"), dict) else {}
    evidence_labels = [value.lower() for value, _ in scenes + products + interactions + people]
    for content_type, rule in content_types.items():
        if not isinstance(rule, dict):
            continue
        for token in rule.get("match_any") or []:
            token_l = str(token).lower()
            scores[str(content_type)] += sum(
                confidence
                for (label, confidence) in scenes + products + interactions + people
                if token_l in label.lower() or label.lower() in token_l
            )
    if not scores:
        joined = " ".join(evidence_labels)
        fallback = (
            "loading_handling"
            if any(x in joined for x in ("loading", "unloading", "搬运", "装车"))
            else "warehouse_display"
            if any(x in joined for x in ("warehouse", "仓库", "陈列", "stacking"))
            else "product_closeup"
            if products
            else "daily_overview"
        )
        scores[fallback] = 1.0
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    signature = {
        "cliplet_ids": [getattr(row, "id", None) for row in rows],
        "excluded_non_strict_cliplet_ids": [
            getattr(row, "id", None) for row in input_rows if row not in rows
        ],
        "scenes": sorted({value for value, _ in scenes}),
        "products": sorted({value for value, _ in products}),
        "interactions": sorted({value for value, _ in interactions}),
        "people": sorted({value for value, _ in people}),
        "visible_facts": list(dict.fromkeys(visible_facts)),
        "unknowns": list(dict.fromkeys(unknowns)),
    }
    digest = hashlib.sha256(
        json.dumps(signature, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "schema": "suying.content-fingerprint.v1",
        "id": digest[:20],
        "primary_type": ranked[0][0],
        "secondary_types": [name for name, score in ranked[1:4] if score > 0],
        **signature,
        "evidence_frames": evidence_frames,
        "evidence_level": "semantic.v1" if rows else "none",
        "strict_semantic_only": bool(rows) and len(rows) == len(input_rows),
    }


def _library_slot_items(library: dict[str, Any], slot: str) -> list[Any]:
    """Resolve copy-library items for a recipe slot (hook/hooks alias)."""
    raw = library.get(slot)
    if isinstance(raw, list) and raw:
        return raw
    # Packs commonly store openers under plural ``hooks`` while recipes say ``hook``.
    aliases = {
        "hook": "hooks",
        "hooks": "hook",
        "visible_fact": "visible_facts",
        "visible_facts": "visible_fact",
    }
    alt = aliases.get(slot)
    if alt:
        raw = library.get(alt)
        if isinstance(raw, list) and raw:
            return raw
    return []


def _weighted_pick(rng: random.Random, eligible: list[dict[str, Any]]) -> dict[str, Any]:
    if len(eligible) == 1:
        return eligible[0]
    weights = [max(float(item.get("weight") or 1.0), 0.01) for item in eligible]
    return rng.choices(eligible, weights=weights, k=1)[0]


def select_recipe_components(
    fingerprint: dict[str, Any],
    pack_data: dict[str, Any] | None,
    *,
    seed: int,
    exclude_phrases: set[str] | None = None,
) -> dict[str, Any]:
    from engine.catalog.paper_slip import phrase_is_blocked

    recipes = (pack_data or {}).get("recipes") if isinstance((pack_data or {}).get("recipes"), dict) else {}
    primary = str(fingerprint.get("primary_type") or "daily_overview")
    recipe = recipes.get(primary) if isinstance(recipes.get(primary), dict) else {}
    components = (pack_data or {}).get("copy_components")
    library = components.get("library") if isinstance(components, dict) and isinstance(components.get("library"), dict) else {}
    rng = random.Random(seed)
    evidence_available = bool(fingerprint.get("visible_facts")) or bool(fingerprint.get("scenes"))
    blocked = exclude_phrases or set()
    selected: list[dict[str, Any]] = []
    for slot in recipe.get("sequence") or ("hook", "visible_facts", "process", "value", "cta"):
        candidates = [
            x
            for x in _library_slot_items(library, str(slot))
            if isinstance(x, dict) and x.get("id") and x.get("text")
        ]
        eligible = [
            item for item in candidates
            if (not item.get("requires_evidence") or evidence_available)
            and not phrase_is_blocked(str(item.get("text") or ""), blocked)
        ]
        if eligible:
            selected.append(_weighted_pick(rng, eligible))
    return {
        "recipe_id": str(recipe.get("id") or f"recipe.{primary}"),
        "component_ids": [str(item["id"]) for item in selected],
        "component_texts": [str(item["text"]) for item in selected],
        "allowed_facts": list(fingerprint.get("visible_facts") or []),
        "forbidden_claims": list(fingerprint.get("unknowns") or []),
    }
