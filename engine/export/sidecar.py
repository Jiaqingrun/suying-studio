from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.template.engine import MontagePlan


def write_sidecar(
    output_path: Path,
    plan: MontagePlan,
    *,
    job_id: int | None,
    qc: dict[str, Any],
    copywriting: dict[str, Any],
    covers: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> Path:
    sidecar = output_path.with_suffix(".json")
    payload = {
        "version": "0.3.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "seed": plan.seed,
        "template_name": plan.template_name,
        "theme": plan.theme,
        "category": plan.category,
        "title": plan.title,
        "clips": [c.__dict__ for c in plan.clips],
        "qc": qc,
        "copywriting": copywriting,
        "covers": covers or [],
        "meta": meta or {},
        "warnings": list(plan.warnings),
        "block_reasons": list(plan.block_reasons),
        "consistency_score": plan.consistency_score,
        "content_fingerprint": plan.content_fingerprint,
        "recipe_id": plan.recipe_id,
        "component_ids": plan.component_ids,
        "allowed_facts": plan.allowed_facts,
        "forbidden_claims": plan.forbidden_claims,
    }
    sidecar.write_text(__import__("json").dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return sidecar


def build_copywriting(
    plan: MontagePlan,
    pack_data: dict[str, Any] | None = None,
    *,
    music_credit: str | None = None,
) -> dict[str, Any]:
    brand = "品牌"
    if pack_data:
        brand = pack_data.get("company_info", {}).get("display_name_preferred", brand)
    hashtags = []
    if pack_data:
        hashtags = pack_data.get("keyword_pool", {}).get("hashtags", [])[:5]
    credit = music_credit or "Music: Bensound.com"
    component_lines = [str(x).strip() for x in plan.copy_components if str(x).strip()]
    fact_lines = [str(x).strip() for x in plan.allowed_facts if str(x).strip()]
    body = component_lines or fact_lines or ["本条内容按当前画面记录"]
    desc = "。".join(body[:4]).rstrip("。") + f"。\n🎵 {credit}"
    return {
        "title": plan.title,
        "description": desc,
        "hashtags": hashtags or [f"#{plan.theme}", "#短视频"],
        "music_credit": credit,
        "content_fingerprint_id": plan.content_fingerprint.get("id"),
        "content_type": plan.content_fingerprint.get("primary_type"),
        "recipe_id": plan.recipe_id,
        "component_ids": plan.component_ids,
    }
