"""Reap production jobs stuck in status=running without a live worker holder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Job, JobEvent, log_event

# No fresh job_events for this long while status=running and worker does not hold it.
DEFAULT_STALE_AFTER_SEC = 600


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def last_activity_at(session: Session, job: Job) -> datetime | None:
    row = session.scalar(
        select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.id.desc()).limit(1)
    )
    if row and row.created_at is not None:
        return _aware(row.created_at)
    return _aware(job.updated_at) or _aware(job.created_at)


def reap_stale_running_jobs(
    session: Session,
    *,
    held_job_id: int | None,
    stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Re-queue running jobs this process does not hold and that look abandoned.

    A job is reaped when:
    - status == running
    - it is not the job currently held by this worker (held_job_id)
    - last job_event / updated_at is older than stale_after_sec
    """
    now = _aware(now) or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max(30.0, float(stale_after_sec)))
    reaped: list[dict[str, Any]] = []
    rows = session.scalars(select(Job).where(Job.status == "running").order_by(Job.id)).all()
    for job in rows:
        if held_job_id is not None and int(job.id) == int(held_job_id):
            continue
        last = last_activity_at(session, job)
        if last is not None and last > cutoff:
            continue
        job.status = "queued"
        snap = dict(job.config_snapshot_json or {})
        snap["_pipeline_phase"] = "queued"
        snap.pop("_system_pause_hold", None)
        job.config_snapshot_json = snap
        job.updated_at = now
        log_event(
            session,
            job.id,
            "info",
            "回收陈旧 running：无持有者且超时无事件，已重新入队",
            {
                "stale_after_sec": float(stale_after_sec),
                "last_event_at": last.isoformat() if last else None,
                "held_job_id": held_job_id,
            },
        )
        reaped.append(
            {
                "id": job.id,
                "previous_status": "running",
                "reason": "stale_running_requeued",
                "last_event_at": last.isoformat() if last else None,
            }
        )
    if reaped:
        session.commit()
    return reaped
