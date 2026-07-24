from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Job, Template, get_session, log_event
from engine.template.engine import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE, TemplateDefinition


class CreateJobRequest(BaseModel):
    mode: str = Field(default="count", pattern="^(count|duration)$")
    target_count: int | None = 10
    target_duration_sec: int | None = None
    template_name: str = "default-vertical"
    theme: str = "default"
    category: str = "default"
    customer_name: str | None = None


def ensure_default_template(session: Session) -> None:
    """Upsert all built-in rhythm templates (default / fast-ship / stable-product)."""
    for name, tpl in BUILTIN_TEMPLATES.items():
        existing = session.scalar(select(Template).where(Template.name == name))
        if not existing:
            session.add(
                Template(
                    name=name,
                    definition_json=tpl.model_dump(),
                    active=True,
                )
            )
        else:
            existing.definition_json = tpl.model_dump()
            existing.active = True
    session.commit()


def create_job(session: Session, req: CreateJobRequest, *, customer_id: int | None = None) -> Job:
    ensure_default_template(session)
    tpl = session.scalar(select(Template).where(Template.name == req.template_name))
    builtin = BUILTIN_TEMPLATES.get(req.template_name)
    template_def = TemplateDefinition(
        **(tpl.definition_json if tpl else (builtin or DEFAULT_TEMPLATE).model_dump())
    )
    snap: dict[str, Any] = {
        "customer_name": req.customer_name,
        "template": template_def.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    job = Job(
        status="queued",
        customer_id=customer_id,
        mode=req.mode,
        target_count=req.target_count,
        target_duration_sec=req.target_duration_sec,
        template_name=req.template_name,
        theme=req.theme,
        category=req.category,
        config_snapshot_json=snap,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    log_event(session, job.id, "info", "任务已创建", {"mode": req.mode})
    return job


def pause_job(session: Session, job_id: int) -> Job | None:
    job = session.get(Job, job_id)
    if not job:
        return None
    job.status = "paused"
    session.commit()
    log_event(session, job.id, "info", "任务已暂停")
    return job


def resume_job(session: Session, job_id: int) -> Job | None:
    job = session.get(Job, job_id)
    if not job:
        return None
    job.status = "queued"
    job.consecutive_failures = 0
    session.commit()
    log_event(session, job.id, "info", "任务已恢复")
    return job
