"""Sprint C: reject reason codes → cliplet downweight → one-click re-render."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Cliplet, ClipletUsage, Job, RenderOutput, Template, log_event
from engine.template.engine import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE, TemplateDefinition

# Stable reason codes for App + reports (zh label for UI)
REJECT_REASONS: dict[str, str] = {
    "weak_hook": "开场弱/无钩子感",
    "off_theme": "跑题/与主题不符",
    "bad_subtitle": "字幕挡主体或难读",
    "audio_bad": "声音糊/吵/无声感",
    "reuse": "素材复用难看",
    "dark_blur": "画面暗糊",
    "ops_voice_subtitle": "补旁白/字幕",
    "ops_tts_noncompliant": "音色不合规(非Edge晓晓)",
    "tts_lock_say": "音色不合规(macOS say)",
    "other": "其他",
}

# Ops re-render should keep the same footage and only redo VO/subs/render
KEEP_FOOTAGE_REASONS = frozenset(
    {"ops_voice_subtitle", "ops_tts_noncompliant", "tts_lock_say"}
)

DEFAULT_DOWNWEIGHT = 0.12
MIN_SCORE = 0.05


def parse_reject_reason(note: str, reason: str | None = None) -> str:
    if reason and reason in REJECT_REASONS:
        return reason
    note_l = (note or "").strip().lower()
    for code in REJECT_REASONS:
        if note_l.startswith(code) or f"[{code}]" in note_l:
            return code
    return "other"


def cliplet_ids_from_output(session: Session, out: RenderOutput) -> list[int]:
    ids: list[int] = []
    # Prefer usage table
    usages = list(
        session.scalars(select(ClipletUsage).where(ClipletUsage.render_output_id == out.id)).all()
    )
    for u in usages:
        if u.cliplet_id:
            ids.append(int(u.cliplet_id))
    if ids:
        return ids
    # Fallback: sidecar
    if not out.sidecar_path:
        return []
    path = Path(out.sidecar_path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    for c in data.get("clips") or []:
        cid = c.get("cliplet_id")
        if cid is not None:
            ids.append(int(cid))
    return ids


def asset_uuids_from_output(session: Session, out: RenderOutput) -> list[str]:
    uuids: list[str] = []
    usages = list(
        session.scalars(select(ClipletUsage).where(ClipletUsage.render_output_id == out.id)).all()
    )
    for u in usages:
        if u.asset_uuid:
            uuids.append(str(u.asset_uuid))
    if uuids:
        return list(dict.fromkeys(uuids))
    if not out.sidecar_path:
        return []
    path = Path(out.sidecar_path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    for c in data.get("clips") or []:
        au = c.get("asset_uuid")
        if au:
            uuids.append(str(au))
    return list(dict.fromkeys(uuids))


def downweight_cliplets_for_reject(
    session: Session,
    out: RenderOutput,
    *,
    reason: str,
    amount: float = DEFAULT_DOWNWEIGHT,
) -> dict[str, Any]:
    """Lower visual/quality score on cliplets used in a rejected output."""
    ids = cliplet_ids_from_output(session, out)
    touched: list[dict[str, Any]] = []
    for cid in ids:
        row = session.get(Cliplet, cid)
        if not row:
            continue
        before = float(row.score if row.score is not None else 1.0)
        # Extra penalty for dark_blur / weak_hook on quality score
        pen = amount
        if reason in {"dark_blur", "bad_subtitle"}:
            pen = amount * 1.25
        after = max(MIN_SCORE, before - pen)
        row.score = round(after, 4)
        touched.append({"cliplet_id": cid, "before": before, "after": row.score})
    session.commit()
    return {
        "reason": reason,
        "reason_label": REJECT_REASONS.get(reason, reason),
        "cliplet_ids": ids,
        "downweighted": touched,
    }


MAX_AUTO_RERENDER = 2


def rerender_chain_depth(session: Session, out: RenderOutput) -> int:
    """How many rerender hops lead to this output (0 = original)."""
    depth = 0
    seen: set[int] = set()
    current: RenderOutput | None = out
    while current is not None and current.job_id and current.id not in seen:
        seen.add(int(current.id))
        parent_job = session.get(Job, current.job_id)
        if not parent_job:
            break
        snap = parent_job.config_snapshot_json if isinstance(parent_job.config_snapshot_json, dict) else {}
        src_id = snap.get("rerender_of_output_id")
        if src_id is None:
            break
        try:
            src_id_int = int(src_id)
        except (TypeError, ValueError):
            break
        depth += 1
        if depth > 32:
            break
        current = session.get(RenderOutput, src_id_int)
    return depth


def create_rerender_job(
    session: Session,
    out: RenderOutput,
    *,
    reason: str = "other",
    force: bool = False,
) -> Job:
    """Queue a count=1 re-render. Reject reasons exclude old footage; ops TTS reasons keep it."""
    if not out.job_id:
        raise ValueError("成片无关联任务，无法一键重渲")
    parent = session.get(Job, out.job_id)
    if not parent:
        raise ValueError("原任务不存在，无法一键重渲")
    depth = rerender_chain_depth(session, out)
    if not force and depth >= MAX_AUTO_RERENDER:
        raise ValueError(
            f"已自动重渲 {MAX_AUTO_RERENDER} 次仍未通过，请进入人工审片（勿再自动重渲）"
        )

    keep_footage = reason in KEEP_FOOTAGE_REASONS
    if keep_footage:
        # Voice/subtitle ops: same takes, new Edge VO (do not exclude assets)
        cliplet_ids: list[int] = []
        exclude_assets: list[str] = []
    else:
        cliplet_ids = cliplet_ids_from_output(session, out)
        asset_uuids = asset_uuids_from_output(session, out)
        # Prefer excluding whole assets so reject re-render cannot reuse the same takes
        exclude_assets = list(asset_uuids)

    tpl = session.scalar(select(Template).where(Template.name == parent.template_name))
    parent_snap = parent.config_snapshot_json or {}
    template_dump = parent_snap.get("template")
    if not template_dump:
        builtin = BUILTIN_TEMPLATES.get(parent.template_name, DEFAULT_TEMPLATE)
        template_dump = tpl.definition_json if tpl else builtin.model_dump()
    template_def = TemplateDefinition(**template_dump)

    customer_name = parent_snap.get("customer_name") or ""
    snap: dict[str, Any] = {
        "customer_name": customer_name,
        "template": template_def.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rerender_of_output_id": out.id,
        "rerender_of_job_id": parent.id,
        "reject_reason": reason,
        "exclude_cliplet_ids": cliplet_ids,
        "exclude_asset_uuids": exclude_assets,
        "keep_footage": keep_footage,
    }
    if isinstance(parent_snap, dict):
        for key in (
            "production_rules",
            "production_rules_resolved",
            "strict_semantic_v1",
            "orientation",
            "canvas",
            "keyword_pack",
            "topic_intent",
            "seed",
        ):
            if parent_snap.get(key) is not None:
                snap[key] = parent_snap[key]

    job = Job(
        status="queued",
        customer_id=parent.customer_id,
        mode="count",
        target_count=1,
        template_name=parent.template_name,
        theme=parent.theme,
        category=parent.category,
        orientation=parent.orientation or str(parent_snap.get("orientation") or "portrait"),
        config_snapshot_json=snap,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    log_event(
        session,
        job.id,
        "info",
        "打回一键重渲已入队",
        {
            "source_output_id": out.id,
            "reason": reason,
            "exclude_cliplet_ids": cliplet_ids,
            "exclude_asset_count": len(exclude_assets),
        },
    )
    return job


def montage_plan_dict_from_output(session: Session, out: RenderOutput) -> dict[str, Any]:
    """Load freezeable MontagePlan dict from sidecar (GVisualPack2 V5)."""
    if not out.sidecar_path:
        raise ValueError("成片无 sidecar，无法复用画面轨")
    path = Path(out.sidecar_path)
    if not path.is_file():
        raise ValueError(f"sidecar 不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"sidecar 无法解析: {exc}") from exc
    clips = data.get("clips")
    if not isinstance(clips, list) or not clips:
        raise ValueError("sidecar 无 clips，无法复用画面轨")
    return {
        "seed": data.get("seed") or 0,
        "template_name": data.get("template_name") or "",
        "theme": data.get("theme") or "default",
        "category": data.get("category") or "default",
        "orientation": data.get("orientation") or "portrait",
        "title": data.get("title") or "",
        "clips": clips,
        "warnings": list(data.get("warnings") or []),
        "blocked": False,
        "block_reasons": [],
        "consistency_score": float(data.get("consistency_score") or 0),
        "needs_review": False,
        "title_style": (data.get("meta") or {}).get("title_style")
        if isinstance(data.get("meta"), dict)
        else {},
        "content_theme": data.get("content_theme") or "default",
        "content_fingerprint": data.get("content_fingerprint") or {},
        "recipe_id": data.get("recipe_id") or "",
        "component_ids": list(data.get("component_ids") or []),
        "allowed_facts": list(data.get("allowed_facts") or []),
        "forbidden_claims": list(data.get("forbidden_claims") or []),
        "copy_components": list((data.get("copywriting") or {}).get("component_ids") or []),
    }


def create_expression_variant_job(
    session: Session,
    out: RenderOutput,
    *,
    expression: dict[str, Any],
    force_plan_reuse: bool = True,
) -> Job:
    """Queue count=1 job that reuses picture slots and only re-forks title/VO/subs.

    Requires effective rule or pass-through: `plan_lang_reuse=on` when force is false.
    """
    if not out.job_id:
        raise ValueError("成片无关联任务，无法做多语言复用")
    parent = session.get(Job, out.job_id)
    if not parent:
        raise ValueError("原任务不存在")
    plan_dict = montage_plan_dict_from_output(session, out)

    parent_snap = parent.config_snapshot_json if isinstance(parent.config_snapshot_json, dict) else {}
    frozen_pr = parent_snap.get("production_rules")
    if not isinstance(frozen_pr, dict):
        frozen_pr = {}
    eff = dict(frozen_pr.get("effective_rules") or {})
    reuse_flag = str(eff.get("plan_lang_reuse") or "off").strip().lower()
    if not force_plan_reuse and reuse_flag != "on":
        raise ValueError("规则 plan_lang_reuse 未开启，拒绝创建多语言复用任务")

    from engine.pack.expression import resolve_expression_prefs

    prefs = resolve_expression_prefs({"expression": {}}, overrides=expression or {})
    # Express langs into frozen production rules (job-level expression disabled).
    for key in ("voice_lang", "subtitle_lang", "subtitle_burn", "dual_secondary_lang"):
        if prefs.get(key):
            eff[key] = prefs[key]
    # Always mark reuse on for this variant job's frozen rules.
    eff["plan_lang_reuse"] = "on"
    new_pr = dict(frozen_pr)
    new_pr["effective_rules"] = eff

    tpl = session.scalar(select(Template).where(Template.name == parent.template_name))
    template_dump = parent_snap.get("template")
    if not template_dump:
        builtin = BUILTIN_TEMPLATES.get(parent.template_name, DEFAULT_TEMPLATE)
        template_dump = tpl.definition_json if tpl else builtin.model_dump()
    template_def = TemplateDefinition(**template_dump)

    snap: dict[str, Any] = {
        "customer_name": parent_snap.get("customer_name") or "",
        "template": template_def.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_output_id": out.id,
        "source_job_id": parent.id,
        "reuse_montage_plan": plan_dict,
        "expression_variant": True,
        "keep_footage": True,
        "exclude_cliplet_ids": [],
        "exclude_asset_uuids": [],
        "production_rules": new_pr,
        "production_rules_resolved": parent_snap.get("production_rules_resolved"),
        "strict_semantic_v1": parent_snap.get("strict_semantic_v1"),
        "orientation": parent.orientation or parent_snap.get("orientation") or "portrait",
        "canvas": parent_snap.get("canvas"),
        "keyword_pack": parent_snap.get("keyword_pack"),
        "topic_intent": parent_snap.get("topic_intent"),
        "seed": parent_snap.get("seed") or plan_dict.get("seed"),
    }

    job = Job(
        status="queued",
        customer_id=parent.customer_id,
        mode="count",
        target_count=1,
        template_name=parent.template_name,
        theme=parent.theme,
        category=parent.category,
        orientation=parent.orientation or str(snap.get("orientation") or "portrait"),
        config_snapshot_json=snap,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    log_event(
        session,
        job.id,
        "info",
        "多语言 Plan 复用任务已入队",
        {
            "source_output_id": out.id,
            "clips": len(plan_dict.get("clips") or []),
            "expression": prefs,
        },
    )
    return job
