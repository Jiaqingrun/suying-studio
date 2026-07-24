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
    "other": "其他",
}

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


def create_rerender_job(
    session: Session,
    out: RenderOutput,
    *,
    reason: str = "other",
) -> Job:
    """Queue a count=1 job that excludes cliplets/assets from the rejected output."""
    if not out.job_id:
        raise ValueError("成片无关联任务，无法一键重渲")
    parent = session.get(Job, out.job_id)
    if not parent:
        raise ValueError("原任务不存在，无法一键重渲")

    cliplet_ids = cliplet_ids_from_output(session, out)
    asset_uuids = asset_uuids_from_output(session, out)
    # Prefer excluding whole assets so re-render cannot reuse the same takes
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
    }

    job = Job(
        status="queued",
        customer_id=parent.customer_id,
        mode="count",
        target_count=1,
        template_name=parent.template_name,
        theme=parent.theme,
        category=parent.category,
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
