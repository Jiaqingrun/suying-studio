"""Jobs + calendar API routes (extracted zero-semantics from app.py)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from engine.api.scope import active_scope, resolve_job_customer
from engine.catalog.calendar import (
    CalendarEntryIn,
    delete_entry,
    entry_to_dict,
    list_entries,
    today_plan,
    upsert_entry,
)
from engine.catalog.db import Job, get_session
from engine.config.settings import load_settings
from engine.jobs.queue import CreateJobRequest, create_job, pause_job, resume_job
from engine.jobs.worker import worker
from engine.ops.maintenance import assert_production_ready
from engine.runtime.pause_coordinator import assert_runtime_active

router = APIRouter(tags=["jobs"])


def _job_obs_fields(job: Job) -> dict[str, Any]:
    """Rule-slot / asset folder / infra counters for App pipeline observability."""
    snap = job.config_snapshot_json if isinstance(job.config_snapshot_json, dict) else {}
    pr = snap.get("production_rules") if isinstance(snap.get("production_rules"), dict) else {}
    content_category = (
        str(snap.get("content_category") or pr.get("content_category") or job.category or "default")
        .strip()
        or "default"
    )
    asset_raw = snap.get("asset_category")
    asset_category = str(asset_raw).strip() if asset_raw else None
    ready_reasons: list[str] = []
    for key in ("last_ready_gate_fails", "ready_gate_fails", "last_align_fails"):
        raw = snap.get(key)
        if isinstance(raw, list):
            ready_reasons = [str(x) for x in raw[:8]]
            break
    return {
        "content_category": content_category,
        "asset_category": asset_category,
        "rule_profile_id": pr.get("rule_profile_id"),
        "rule_name": pr.get("name"),
        "rule_revision": pr.get("revision"),
        "pipeline_phase": snap.get("_pipeline_phase"),
        "consecutive_ollama_infra": int(snap.get("consecutive_ollama_infra") or 0),
        "consecutive_non_ready": int(snap.get("consecutive_non_ready") or 0),
        "item_skip_count": int(snap.get("item_skip_count") or 0),
        "ollama_infra_pause_reason": snap.get("ollama_infra_pause_reason"),
        "ready_reasons": ready_reasons,
    }


@router.get("/jobs")
def list_jobs(limit: int = 80) -> list[dict[str, Any]]:
    """Recent jobs for the control plane (bounded).

    Full-history dumps made the Production page and 5s poll unusable once a
    customer had hundreds of cancelled/completed rows.
    Active (running/queued/pending/paused) jobs are always included.
    """
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        cap = max(1, min(int(limit or 80), 200))
        active = session.scalars(
            select(Job)
            .where(
                Job.customer_id == customer.id,
                Job.status.in_(("running", "queued", "pending", "paused")),
            )
            .order_by(Job.id.desc())
        ).all()
        recent = session.scalars(
            select(Job)
            .where(Job.customer_id == customer.id)
            .order_by(Job.id.desc())
            .limit(cap)
        ).all()
        by_id: dict[int, Job] = {}
        ordered: list[Job] = []
        for row in list(active) + list(recent):
            if row.id in by_id:
                continue
            by_id[row.id] = row
            ordered.append(row)
        ordered.sort(key=lambda j: j.id, reverse=True)
        return [
            {
                "id": j.id,
                "customer_id": j.customer_id,
                "status": j.status,
                "mode": j.mode,
                "target_count": j.target_count,
                "target_duration_sec": j.target_duration_sec,
                "produced_count": j.produced_count,
                "template_name": j.template_name,
                "theme": j.theme,
                "category": j.category,
                "orientation": j.orientation,
                "consecutive_failures": j.consecutive_failures,
                **_job_obs_fields(j),
            }
            for j in ordered[: max(cap, len(active))]
        ]
    finally:
        session.close()


@router.get("/jobs/pipeline")
def jobs_pipeline() -> dict[str, Any]:
    """Live production pipeline snapshot for the Production page banner."""
    from engine.catalog.db import JobEvent
    from engine.pack.voice_clone import clone_runtime_status
    from engine.runtime.resource_gate import gate as resource_gate

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        running = session.scalar(
            select(Job)
            .where(Job.customer_id == customer.id, Job.status == "running")
            .order_by(Job.id.asc())
            .limit(1)
        )
        queued = session.scalars(
            select(Job)
            .where(Job.customer_id == customer.id, Job.status == "queued")
            .order_by(Job.id.asc())
        ).all()
        recent = []
        phase = None
        if running:
            snap = running.config_snapshot_json if isinstance(running.config_snapshot_json, dict) else {}
            phase = snap.get("_pipeline_phase")
            recent = [
                {
                    "level": e.level,
                    "message": e.message,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in session.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == running.id)
                    .order_by(JobEvent.id.desc())
                    .limit(5)
                ).all()
            ]
        gate_snap = resource_gate.snapshot()
        clone = clone_runtime_status()
        held = worker.running_job_id()
        from engine.catalog.ollama_runtime import ollama_health_snapshot
        from engine.pack.ollama_narration import resolve_narration_model

        ollama_gw = ollama_health_snapshot()
        ollama_circuit = ollama_gw.get("circuit") if isinstance(ollama_gw.get("circuit"), dict) else {}
        running_obs = _job_obs_fields(running) if running else {}
        return {
            "ok": True,
            "running": (
                {
                    "id": running.id,
                    "status": running.status,
                    "phase": phase or running_obs.get("pipeline_phase"),
                    "theme": running.theme,
                    "template_name": running.template_name,
                    "produced_count": running.produced_count,
                    "target_count": running.target_count,
                    "consecutive_failures": running.consecutive_failures,
                    "updated_at": running.updated_at.isoformat()
                    if running.updated_at
                    else None,
                    "held_by_worker": held is not None and held == running.id,
                    **running_obs,
                }
                if running
                else None
            ),
            "queued_count": len(queued),
            "queued_ids": [j.id for j in queued[:20]],
            "recent_events": recent,
            "worker_running": worker.is_alive(),
            "worker_pid": os.getpid(),
            "held_job_id": held,
            "resource_gate": gate_snap,
            "clone_runtime": clone,
            "ollama_narration_model": resolve_narration_model(settings),
            "ollama_narration_enabled": bool(getattr(settings, "ollama_narration_enabled", False)),
            "ollama_circuit": ollama_circuit,
            "chat_probe_ok": bool(ollama_gw.get("chat_probe_ok")),
        }
    finally:
        session.close()


@router.post("/jobs/reap-stale")
def jobs_reap_stale(stale_after_sec: float = 600.0) -> dict[str, Any]:
    """Re-queue abandoned running jobs (ops / diag). Prefer this over raw SQL UPDATEs."""
    from engine.jobs.stale import reap_stale_running_jobs

    session = get_session()
    try:
        reaped = reap_stale_running_jobs(
            session,
            held_job_id=worker.running_job_id(),
            stale_after_sec=float(stale_after_sec),
        )
        return {
            "ok": True,
            "reaped": reaped,
            "count": len(reaped),
            "held_job_id": worker.running_job_id(),
            "worker_pid": os.getpid(),
        }
    finally:
        session.close()


@router.get("/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    """Single-job detail (same fields as list + snapshot highlights)."""
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        job = session.get(Job, job_id)
        if job is None or job.customer_id != customer.id:
            raise HTTPException(404, f"任务不存在: {job_id}")
        snap = job.config_snapshot_json if isinstance(job.config_snapshot_json, dict) else {}
        return {
            "ok": True,
            "id": job.id,
            "customer_id": job.customer_id,
            "status": job.status,
            "mode": job.mode,
            "target_count": job.target_count,
            "target_duration_sec": job.target_duration_sec,
            "produced_count": job.produced_count,
            "template_name": job.template_name,
            "theme": job.theme,
            "category": job.category,
            "orientation": job.orientation,
            "consecutive_failures": job.consecutive_failures,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            **_job_obs_fields(job),
            "config_snapshot": {
                "content_category": snap.get("content_category"),
                "asset_category": snap.get("asset_category"),
                "production_rules": snap.get("production_rules"),
                "_pipeline_phase": snap.get("_pipeline_phase"),
                "consecutive_ollama_infra": snap.get("consecutive_ollama_infra"),
                "consecutive_non_ready": snap.get("consecutive_non_ready"),
            },
        }
    finally:
        session.close()


@router.post("/jobs")
def post_job(body: CreateJobRequest) -> dict[str, Any]:
    assert_runtime_active("create_job")
    try:
        assert_production_ready()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    settings = load_settings()
    want_strict = bool(getattr(body, "strict_semantic_v1", False) or getattr(body, "topic_intent", None) is not None)
    if want_strict and not bool(getattr(settings, "strict_semantic_allowed", True)):
        raise HTTPException(409, "当前机型档位未开放严格语义生产（lite 请升配或补装视觉包）")
    session = get_session()
    try:
        if body.customer_name is None:
            body.customer_name = settings.active_customer
        customer = resolve_job_customer(session, settings, body.customer_name)
        body.customer_name = customer.name
        try:
            job = create_job(session, body, customer_id=customer.id)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        queued_ahead = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.customer_id == customer.id,
                Job.status == "queued",
                Job.id < job.id,
            )
        ) or 0
        running = session.scalar(
            select(Job).where(
                Job.customer_id == customer.id, Job.status == "running"
            )
        )
        return {
            "id": job.id,
            "status": job.status,
            "customer_id": job.customer_id,
            "rush": bool(body.rush),
            "queued_ahead": int(queued_ahead),
            "running_job_id": running.id if running else None,
            "message": (
                "生产任务已创建并插队" if body.rush else "生产任务已入队"
            ),
        }
    finally:
        session.close()


@router.post("/jobs/{job_id}/pause")
def post_pause(job_id: int) -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        job = session.get(Job, job_id)
        if not job or job.customer_id != customer.id:
            raise HTTPException(404, "Job not found")
        job = pause_job(session, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return {"id": job.id, "status": job.status}
    finally:
        session.close()


@router.post("/jobs/{job_id}/resume")
def post_resume(job_id: int) -> dict[str, Any]:
    assert_runtime_active("resume_job")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        job = session.get(Job, job_id)
        if not job or job.customer_id != customer.id:
            raise HTTPException(404, "Job not found")
        try:
            job = resume_job(session, job_id)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        if not job:
            raise HTTPException(404, "Job not found")
        return {"id": job.id, "status": job.status}
    finally:
        session.close()


@router.get("/calendar")
def calendar_list(from_day: str | None = None, to_day: str | None = None) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return [entry_to_dict(e) for e in list_entries(session, customer.id, from_day, to_day)]
    finally:
        session.close()


@router.get("/calendar/today")
def calendar_today(day: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        row = today_plan(session, customer.id, day)
        if not row:
            raise HTTPException(404, "今日无日历计划（或已停用）")
        return entry_to_dict(row)
    finally:
        session.close()


@router.put("/calendar/{day}")
def calendar_upsert(day: str, body: CalendarEntryIn) -> dict[str, Any]:
    settings = load_settings()
    body.day = day
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        body.customer_name = customer.name
        row = upsert_entry(session, customer.id, body)
        return entry_to_dict(row)
    finally:
        session.close()


@router.delete("/calendar/{day}")
def calendar_delete(day: str) -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        ok = delete_entry(session, customer.id, day)
        if not ok:
            raise HTTPException(404, "calendar entry not found")
        return {"deleted": day}
    finally:
        session.close()


@router.post("/jobs/from-calendar")
def post_job_from_calendar(day: str | None = None) -> dict[str, Any]:
    """Create a count job using today's (or given day's) calendar quota/theme."""
    assert_runtime_active("create_job")
    try:
        assert_production_ready()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    session = get_session()
    try:
        settings = load_settings()
        _, customer, _ = active_scope(session, settings)
        row = today_plan(session, customer.id, day)
        if not row:
            raise HTTPException(404, "无可用日历计划")
        from engine.catalog.industry_pack import pack_id_for_customer
        from engine.template.engine import template_for_theme

        # Prefer calendar template; if still default, bind by theme (Sprint B)
        pack_id = pack_id_for_customer(customer.name, customer.profile_json)
        tpl_name = row.template_name or "default-vertical"
        if tpl_name == "default-vertical":
            mapped = template_for_theme(row.theme, pack_id=pack_id)
            if mapped != tpl_name:
                tpl_name = mapped
        req = CreateJobRequest(
            mode="count",
            target_count=row.quota,
            template_name=tpl_name,
            theme=row.theme,
            category=row.category,
            customer_name=row.customer_name,
        )
        job = create_job(session, req, customer_id=customer.id)
        return {
            "id": job.id,
            "status": job.status,
            "calendar_day": row.day,
            "theme": row.theme,
            "template_name": tpl_name,
            "quota": row.quota,
        }
    finally:
        session.close()

