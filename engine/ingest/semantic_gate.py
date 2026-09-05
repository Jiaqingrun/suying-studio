"""Strict, auditable semantic admission gate for cliplets.

New cliplets must pass this gate after visual quality checks and before an
embedding can be created.  The gate is deliberately fail-closed: unavailable
or ambiguous vision analysis is not replaced with guessed metadata.
"""

from __future__ import annotations

import re
from typing import Any

SEMANTIC_SCHEMA_VERSION = "suying.cliplet.semantic.v1"
COARSE_SEMANTIC_SCHEMA_VERSION = "suying.cliplet.coarse.v1"
STRICT_EMBEDDING_SCHEMA_VERSION = "suying.cliplet.embedding.v1"
STRICT_EMBEDDING_BACKEND = "ollama"
STRICT_EMBEDDING_MODEL = "nomic-embed-text"
MAX_SEMANTIC_ATTEMPTS = 3
MIN_OVERALL_CONFIDENCE = 0.72
MIN_LABEL_CONFIDENCE = 0.65

SCENE_LABELS = {
    "delivery",
    "product_use",
    "loading",
    "stacking",
    "inventory_full",
    "company_image",
    "warehouse",
    "storefront",
    "production",
    "installation_site",
    "office",
    "transport",
    "product_closeup",
    "people_activity",
    "other_visible",
}
INTERACTION_LABELS = {
    "delivery",
    "installation",
    "repair",
    "product_use",
    "loading",
    "unloading",
    "sorting",
    "stacking",
    "demonstration",
    "inspection",
    "consultation",
    "teamwork",
    "none_visible",
}
PEOPLE_PROMOTION_LABELS = {
    "person_visible_unclassified",
    "employee_individual",
    "employee_group",
    "customer_interaction",
    "work_portrait",
    "team_image",
    "none_visible",
}


def semantic_response_json_schema() -> dict[str, Any]:
    """Full JSON Schema for Ollama `format=` — aligned with evaluate_semantic_gate."""
    labeled = {
        "type": "object",
        "additionalProperties": False,
        "required": ["label", "confidence", "evidence_frame_ids"],
        "properties": {
            "label": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_frame_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
        },
    }
    scene_item = {
        **labeled,
        "properties": {
            **labeled["properties"],
            "label": {"type": "string", "enum": sorted(SCENE_LABELS)},
        },
    }
    interaction_item = {
        **labeled,
        "properties": {
            **labeled["properties"],
            "label": {"type": "string", "enum": sorted(INTERACTION_LABELS)},
        },
    }
    people_label_item = {
        **labeled,
        "properties": {
            **labeled["properties"],
            "label": {"type": "string", "enum": sorted(PEOPLE_PROMOTION_LABELS)},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "source_backend",
            "description",
            "frames",
            "scenes",
            "products",
            "interactions",
            "people",
            "consistency",
            "overall_confidence",
        ],
        "properties": {
            "schema_version": {"type": "string", "const": SEMANTIC_SCHEMA_VERSION},
            "source_backend": {"type": "string", "const": "vision"},
            "description": {"type": "string", "minLength": 20, "maxLength": 400},
            "frames": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["frame_id", "timestamp_sec", "visible_facts", "unknowns"],
                    "properties": {
                        "frame_id": {"type": "string"},
                        "timestamp_sec": {"type": "number"},
                        "visible_facts": {
                            "type": "array",
                            "minItems": 2,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "unknowns": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "scenes": {"type": "array", "minItems": 1, "items": scene_item},
            "products": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "name",
                        "category",
                        "use",
                        "key_attributes",
                        "confidence",
                        "evidence_frame_ids",
                    ],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "category": {"type": "string", "minLength": 1},
                        "use": {"type": "string", "minLength": 1},
                        "key_attributes": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "evidence_frame_ids": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "interactions": {"type": "array", "minItems": 1, "items": interaction_item},
            "people": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "present",
                    "count",
                    "appearance",
                    "clothing",
                    "promotion_labels",
                ],
                "properties": {
                    "present": {"type": "boolean"},
                    "count": {"type": "integer", "minimum": 0},
                    "appearance": {"type": "string", "minLength": 1},
                    "clothing": {"type": "string", "minLength": 1},
                    "promotion_labels": {
                        "type": "array",
                        "minItems": 1,
                        "items": people_label_item,
                    },
                },
            },
            "consistency": {
                "type": "object",
                "additionalProperties": False,
                "required": ["consistent", "issues"],
                "properties": {
                    "consistent": {"type": "boolean"},
                    "issues": {"type": "array", "items": {"type": "string"}},
                },
            },
            "overall_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }

_SPECULATION = re.compile(
    r"(可能|应该|大概|似乎|推测|猜测|看起来像|预计|显然是|一定是|"
    r"用于|用途是|身份是|情绪|精神饱满|热情|专业|彰显|体现)"
)
_VAGUE = re.compile(r"^(实拍业务素材|一段视频|一个画面|现场画面|相关场景|物品和人物)[。；\s]*$")
_UNKNOWN_IN_FACT = re.compile(r"(未知|无法判断|无法辨认|看不清|不确定|难以确认)")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _label_values(rows: Any, allowed: set[str], path: str, reasons: list[str]) -> list[str]:
    if not isinstance(rows, list) or not rows:
        reasons.append(f"{path}_missing")
        return []
    labels: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            reasons.append(f"{path}[{i}]_invalid")
            continue
        label = str(row.get("label") or "").strip()
        confidence = _number(row.get("confidence"))
        if label not in allowed:
            reasons.append(f"{path}[{i}]_unknown_label")
        elif label not in labels:
            labels.append(label)
        if confidence is None or not 0 <= confidence <= 1:
            reasons.append(f"{path}[{i}]_invalid_confidence")
        elif confidence < MIN_LABEL_CONFIDENCE:
            reasons.append(f"{path}[{i}]_low_confidence")
        evidence = row.get("evidence_frame_ids")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(v, str) and v for v in evidence):
            reasons.append(f"{path}[{i}]_missing_evidence")
    return labels


def evaluate_semantic_gate(data: Any) -> dict[str, Any]:
    """Validate semantic analysis and return stable, machine-readable reasons."""
    reasons: list[str] = []
    if not isinstance(data, dict):
        return {"passed": False, "reasons": ["schema_not_object"]}
    if data.get("schema_version") != SEMANTIC_SCHEMA_VERSION:
        reasons.append("schema_version_mismatch")
    if data.get("source_backend") != "vision":
        reasons.append("vision_backend_required")

    description = str(data.get("description") or "").strip()
    if not 20 <= len(description) <= 400:
        reasons.append("description_length")
    if _VAGUE.fullmatch(description):
        reasons.append("description_vague")
    if _SPECULATION.search(description):
        reasons.append("description_speculative")

    frames = data.get("frames")
    frame_ids: set[str] = set()
    fact_count = 0
    if not isinstance(frames, list) or not 2 <= len(frames) <= 4:
        reasons.append("frame_count")
    else:
        for i, frame in enumerate(frames):
            if not isinstance(frame, dict):
                reasons.append(f"frames[{i}]_invalid")
                continue
            frame_id = str(frame.get("frame_id") or "")
            if not frame_id or frame_id in frame_ids:
                reasons.append(f"frames[{i}]_frame_id")
            frame_ids.add(frame_id)
            if _number(frame.get("timestamp_sec")) is None:
                reasons.append(f"frames[{i}]_timestamp")
            facts = frame.get("visible_facts")
            if not isinstance(facts, list) or len(facts) < 2:
                reasons.append(f"frames[{i}]_facts_incomplete")
            else:
                clean = [str(v).strip() for v in facts if isinstance(v, str) and str(v).strip()]
                fact_count += len(clean)
                if len(clean) != len(facts) or any(
                    _SPECULATION.search(v) or _UNKNOWN_IN_FACT.search(v) for v in clean
                ):
                    reasons.append(f"frames[{i}]_fact_invalid")
            unknowns = frame.get("unknowns")
            if not isinstance(unknowns, list):
                reasons.append(f"frames[{i}]_unknowns_missing")
            elif not all(isinstance(v, str) and v.strip() for v in unknowns):
                reasons.append(f"frames[{i}]_unknowns_invalid")
    if fact_count < 4:
        reasons.append("visible_facts_insufficient")

    # Every asserted label/product must point to a frame actually extracted for
    # this attempt; invented/stale evidence identifiers fail the schema.
    for section in ("scenes", "products", "interactions"):
        for i, row in enumerate(data.get(section) or []):
            if not isinstance(row, dict):
                continue
            evidence = row.get("evidence_frame_ids")
            if isinstance(evidence, list) and any(v not in frame_ids for v in evidence):
                reasons.append(f"{section}[{i}]_unknown_evidence")
    people_raw = data.get("people")
    if isinstance(people_raw, dict):
        for i, row in enumerate(people_raw.get("promotion_labels") or []):
            if isinstance(row, dict) and isinstance(row.get("evidence_frame_ids"), list):
                if any(v not in frame_ids for v in row["evidence_frame_ids"]):
                    reasons.append(f"people[{i}]_unknown_evidence")

    scene_labels = _label_values(data.get("scenes"), SCENE_LABELS, "scenes", reasons)
    interaction_labels = _label_values(
        data.get("interactions"), INTERACTION_LABELS, "interactions", reasons
    )

    products = data.get("products")
    if not isinstance(products, list):
        reasons.append("products_not_list")
    else:
        for i, product in enumerate(products):
            if not isinstance(product, dict):
                reasons.append(f"products[{i}]_invalid")
                continue
            for key in ("name", "category", "use", "key_attributes"):
                value = product.get(key)
                if key == "key_attributes":
                    valid = isinstance(value, list) and bool(value) and all(
                        isinstance(v, str) and v.strip() for v in value
                    )
                else:
                    valid = isinstance(value, str) and bool(value.strip())
                if not valid:
                    reasons.append(f"products[{i}]_{key}_missing")
            confidence = _number(product.get("confidence"))
            if confidence is None or not 0 <= confidence <= 1:
                reasons.append(f"products[{i}]_invalid_confidence")
            elif confidence < MIN_LABEL_CONFIDENCE:
                reasons.append(f"products[{i}]_low_confidence")
            evidence = product.get("evidence_frame_ids")
            if not isinstance(evidence, list) or not evidence:
                reasons.append(f"products[{i}]_missing_evidence")

    people = data.get("people")
    if not isinstance(people, dict) or not isinstance(people.get("present"), bool):
        reasons.append("people_invalid")
    else:
        present = people["present"]
        labels = _label_values(people.get("promotion_labels"), PEOPLE_PROMOTION_LABELS, "people", reasons)
        if present:
            if "none_visible" in labels:
                reasons.append("people_presence_contradiction")
            if not isinstance(people.get("count"), int) or people["count"] < 1:
                reasons.append("people_count_missing")
            if not str(people.get("appearance") or "").strip():
                reasons.append("people_appearance_missing")
            if not str(people.get("clothing") or "").strip():
                reasons.append("people_clothing_missing")
        elif labels != ["none_visible"]:
            reasons.append("people_absence_contradiction")

    consistency = data.get("consistency")
    if not isinstance(consistency, dict) or consistency.get("consistent") is not True:
        reasons.append("frame_inconsistent")
    elif not isinstance(consistency.get("issues"), list):
        reasons.append("consistency_issues_missing")

    overall = _number(data.get("overall_confidence"))
    if overall is None or overall < MIN_OVERALL_CONFIDENCE:
        reasons.append("overall_low_confidence")
    if not scene_labels:
        reasons.append("scene_coverage_missing")
    if not interaction_labels:
        reasons.append("interaction_coverage_missing")

    return {"passed": not reasons, "reasons": list(dict.fromkeys(reasons))}


def semantic_analysis_passed(cliplet: Any) -> bool:
    """True only for a persisted v1 visual analysis with a passing audit."""
    audit = getattr(cliplet, "semantic_gate_json", None)
    data = getattr(cliplet, "semantic_json", None)
    return (
        getattr(cliplet, "semantic_schema_version", None) == SEMANTIC_SCHEMA_VERSION
        and isinstance(audit, dict)
        and audit.get("passed") is True
        and isinstance(data, dict)
        and evaluate_semantic_gate(data)["passed"] is True
    )


def strict_embedding_provenance_valid(cliplet: Any) -> bool:
    """Require a real, fully identified Ollama embedding for strict use."""
    embedding = getattr(cliplet, "embedding_json", None)
    return (
        isinstance(embedding, list)
        and bool(embedding)
        and getattr(cliplet, "embedding_backend", None) == STRICT_EMBEDDING_BACKEND
        and getattr(cliplet, "embedding_model", None) == STRICT_EMBEDDING_MODEL
        and getattr(cliplet, "embedding_schema_version", None)
        == STRICT_EMBEDDING_SCHEMA_VERSION
    )


def semantic_gate_passed(cliplet: Any) -> bool:
    """True only when strict visual analysis and embedding provenance both pass."""
    return semantic_analysis_passed(cliplet) and strict_embedding_provenance_valid(cliplet)


def semantic_index_admissible(cliplet: Any) -> bool:
    """Allow strict v1 or an explicitly audited coarse record into the general index.

    Coarse records never satisfy ``semantic_gate_passed`` and therefore remain
    excluded from strict-semantic production. They only keep normal production
    available on low-memory Macs without running a VLM over the whole library.
    """
    if semantic_analysis_passed(cliplet):
        return True
    audit = getattr(cliplet, "semantic_gate_json", None)
    description = str(getattr(cliplet, "description", None) or "").strip()
    return (
        getattr(cliplet, "semantic_schema_version", None) == COARSE_SEMANTIC_SCHEMA_VERSION
        and isinstance(audit, dict)
        and audit.get("mode") == "coarse"
        and audit.get("passed") is True
        and bool(description)
    )
