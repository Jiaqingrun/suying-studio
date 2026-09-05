"""跟镜精品 API：简报、场景覆盖、分类、试规划、热点占位。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from engine.catalog.customer_scope import require_active_customer
from engine.catalog.db import get_session
from engine.config.settings import load_settings
from engine.pack.scene_tour import build_scene_tour_plan
from engine.pack.scene_tour_brief import compile_scene_tour_brief
from engine.pack.scene_tour_buckets import coverage_report, set_cliplet_bucket
from engine.pack.scene_tour_copy import CATEGORY_LABELS, UI, bucket_label, category_label
from engine.pack.scene_tour_hot_inbox import hot_inbox_status
from engine.pack.scene_tour_skeleton import resolve_industry_key, skeleton_for
from engine.template.engine import plan_to_dict
from engine.template.rule_store import get_active_rule

router = APIRouter(prefix="/scene-tour", tags=["scene-tour"])


class BucketIn(BaseModel):
    cliplet_id: int
    bucket: str = Field(min_length=1, max_length=64)


class BucketBatchIn(BaseModel):
    items: list[BucketIn] = Field(default_factory=list, max_length=200)


@router.get("/labels")
def labels() -> dict[str, Any]:
    return {
        "ok": True,
        "categories": CATEGORY_LABELS,
        "ui": UI,
        "buckets": {k: bucket_label(k) for k in (
            "entrance", "honor_wall", "culture_wall", "corridor", "slippers",
            "vanity", "sterilize", "treatment_bed", "treatment_room", "supply",
            "treatment_action", "tea", "other",
        )},
    }


@router.get("/hot-inbox")
def get_hot_inbox() -> dict[str, Any]:
    """第二版占位：不联网、不进旁白。"""
    return hot_inbox_status()


@router.get("/brief")
def get_brief() -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        rule = get_active_rule(
            session,
            customer_id=int(customer.id),
            content_category="scene_tour",
            orientation="portrait",
        )
        rule_industry = None
        if rule and isinstance(rule.effective_json, dict):
            rule_industry = rule.effective_json.get("industry_key")
        brief = compile_scene_tour_brief(
            customer_id=int(customer.id),
            customer_name=str(customer.name),
            profile=profile,
            rule_industry_key=str(rule_industry) if rule_industry else None,
        )
        return {"ok": bool(brief.get("ok")), "brief": brief, "label_zh": UI["brief"]}
    finally:
        session.close()


@router.get("/coverage")
def get_coverage(orientation: Literal["portrait", "landscape"] = "portrait") -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        key = resolve_industry_key(profile)
        skel = skeleton_for(key)
        if not skel:
            return {
                "ok": False,
                "label_zh": UI["coverage"],
                "reasons": ["缺少行业资料，请先配置行业类型。"],
                "buckets": [],
            }
        report = coverage_report(
            session,
            customer_id=int(customer.id),
            stages=list(skel.get("stages") or []),
            min_per_bucket=1,
            orientation=orientation,
        )
        return {"ok": bool(report.get("ok")), "label_zh": UI["coverage"], **report}
    finally:
        session.close()


@router.post("/buckets")
def post_buckets(body: BucketBatchIn) -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        updated = []
        for item in body.items:
            row = set_cliplet_bucket(
                session, int(item.cliplet_id), item.bucket, customer_id=int(customer.id)
            )
            updated.append({"cliplet_id": row.id, "bucket": item.bucket, "label_zh": bucket_label(item.bucket)})
        session.commit()
        return {"ok": True, "updated": updated, "message": f"已更新 {len(updated)} 条场景分类"}
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.post("/dry-run")
def dry_run(orientation: Literal["portrait", "landscape"] = "portrait", seed: int = 1) -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        rule = get_active_rule(
            session,
            customer_id=int(customer.id),
            content_category="scene_tour",
            orientation=orientation,
        )
        if rule is None:
            return {
                "ok": False,
                "blocked": True,
                "reasons": ["尚未启用跟镜精品规则，请先到规则实验室保存并启用。"],
                "label_zh": category_label("scene_tour"),
            }
        eff = rule.effective_json if isinstance(rule.effective_json, dict) else {}
        plan = build_scene_tour_plan(
            session,
            customer=customer,
            seed=max(1, int(seed)),
            orientation=orientation,
            production_rules=eff,
            use_ollama=False,  # dry-run 用模板句，避免拖慢
        )
        return {
            "ok": not plan.blocked,
            "blocked": bool(plan.blocked),
            "reasons": list(plan.block_reasons or []),
            "warnings": list(plan.warnings or []),
            "title": plan.title,
            "clip_count": len(plan.clips or []),
            "plan": plan_to_dict(plan),
            "label_zh": category_label("scene_tour"),
            "rule": {"id": rule.id, "name": rule.name},
        }
    finally:
        session.close()


@router.post("/bootstrap-buckets")
def bootstrap_buckets(
    orientation: Literal["portrait", "landscape"] = "portrait",
    force: bool = False,
) -> dict[str, Any]:
    """冷启动：按画面描述推断站位写入场景分类（推不出再轮转兜底）。"""
    from engine.pack.scene_tour_buckets import reclassify_cliplets_for_customer

    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        key = resolve_industry_key(profile)
        skel = skeleton_for(key)
        if not skel:
            raise HTTPException(status_code=400, detail="缺少行业资料，请先配置行业类型。")
        stages = [str(s.get("key")) for s in (skel.get("stages") or []) if s.get("key")]
        if not stages:
            raise HTTPException(status_code=400, detail="行业骨架没有可用站位。")
        result = reclassify_cliplets_for_customer(
            session,
            customer_id=int(customer.id),
            industry_key=str(key or ""),
            orientation=orientation,
            force=bool(force),
            stage_keys=stages,
        )
        session.commit()
        updated = int(result.get("updated") or 0)
        skipped = int(result.get("skipped") or 0)
        inferred = int(result.get("inferred") or 0)
        return {
            "ok": True,
            "updated": updated,
            "skipped": skipped,
            "inferred": inferred,
            "counts": result.get("counts") or {},
            "message": (
                f"已为 {updated} 条片段写入场景分类（推断 {inferred}，跳过 {skipped}）。"
                "请抽检后再生跟镜精品。"
            ),
        }
    except HTTPException:
        session.rollback()
        raise
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()
