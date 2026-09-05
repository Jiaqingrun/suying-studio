"""Normalize CreateJob category fields: rule slot vs asset folder.

content_category → production rule slot (default/premium/scene_tour/…)
asset_category → optional Asset.category filter (Camera/DJI Album/…)

Legacy ``category`` must not mean both at once.
"""

from __future__ import annotations

from typing import Any

# Keep in sync with rule_schema recommended_content_categories + App CONTENT_CATEGORY_LABELS.
RULE_CONTENT_CATEGORIES: frozenset[str] = frozenset(
    {
        "default",
        "premium",
        "scene_tour",
        "store_culture",
    }
)


def is_rule_content_category(value: str | None) -> bool:
    key = (value or "").strip()
    return bool(key) and key in RULE_CONTENT_CATEGORIES


def normalize_job_categories(
    *,
    category: str | None = None,
    content_category: str | None = None,
    asset_category: str | None = None,
) -> dict[str, str]:
    """Return content_category + asset_category (asset may be empty = no folder filter).

    Rules:
    - Explicit content_category / asset_category win when provided.
    - Legacy ``category`` if it is a rule-slot name → content_category only
      (never used as Asset.category).
    - Legacy ``category`` if it looks like a media folder name → asset_category only;
      content_category stays default.
    - Empty / default legacy category → content default, no asset filter.
    """
    legacy = (category or "").strip()
    explicit_content = (content_category or "").strip()
    explicit_asset = (asset_category or "").strip()

    if explicit_content:
        content = explicit_content
    elif is_rule_content_category(legacy):
        content = legacy
    elif not legacy or legacy == "default":
        content = "default"
    else:
        # Custom / folder-like legacy value: do not treat as rule slot.
        content = "default"

    if explicit_asset:
        asset = explicit_asset
    elif legacy and not is_rule_content_category(legacy) and legacy != "default":
        asset = legacy
    else:
        asset = ""

    # Never let a rule-slot name leak into asset filter.
    if is_rule_content_category(asset):
        asset = ""

    return {
        "content_category": content or "default",
        "asset_category": asset,
    }


def plan_category_for_cliplet_filter(content_category: str, asset_category: str) -> str:
    """Value passed to build_plan ``category`` for Cliplet.category filtering.

    Rule slots must not filter cliplets by name (no Cliplet.category=premium).
    When the operator set a real asset folder, we still do not map that onto
    Cliplet.category (folders live on Asset); return default.
    """
    _ = (content_category, asset_category)
    return "default"


def snapshot_category_fields(norm: dict[str, str]) -> dict[str, Any]:
    return {
        "content_category": norm["content_category"],
        "asset_category": norm["asset_category"] or None,
    }
