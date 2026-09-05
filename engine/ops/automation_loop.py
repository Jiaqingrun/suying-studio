"""Durable production → pack → publish automation orchestration.

This module only coordinates existing subsystems.  It never renders media or
drives Chrome directly, and every side effect is guarded by an occurrence
checkpoint so engine restarts cannot replay it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import (
    AutomationOccurrence,
    AutomationPlan,
    Customer,
    Job,
    ReachPublishRun,
    ReachPublishRunItem,
    ReachPublishSchedule,
    ReachPublishTrigger,
    RenderOutput,
)


TERMINAL = frozenset(
    {"completed", "failed", "cancelled", "blocked_quality", "asset_blocked"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def plan_to_dict(row: AutomationPlan) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "name": row.name,
        "enabled": row.enabled,
        "timezone": row.timezone,
        "target_ready_count": row.target_ready_count,
        "template_name": row.template_name,
        "theme": row.theme,
        "category": row.category,
        "publish_schedule_id": row.publish_schedule_id,
        "review_policy": row.review_policy,
        "max_production_attempts": row.max_production_attempts,
        "max_replacement_attempts": row.max_replacement_attempts,
        "config": row.config_json or {},
    }


def occurrence_to_dict(row: AutomationOccurrence) -> dict[str, Any]:
    return {
        "id": row.id,
        "occurrence_key": row.occurrence_key,
        "customer_id": row.customer_id,
        "plan_id": row.plan_id,
        "local_date": row.local_date,
        "status": row.status,
        "production_job_id": row.production_job_id,
        "output_id": row.output_id,
        "pack_dir": row.pack_dir,
        "queue_id": row.queue_id,
        "publish_schedule_id": row.publish_schedule_id,
        "publish_trigger_id": row.publish_trigger_id,
        "publish_run_id": row.publish_run_id,
        "replacement_of_id": row.replacement_of_id,
        "replacement_attempt": row.replacement_attempt,
        "bypass_window_reason": row.bypass_window_reason,
        "step": row.step_json or {},
        "evidence": row.evidence_json or {},
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def ensure_daily_occurrence(
    session: Session, plan: AutomationPlan, *, local_date: str | None = None
) -> AutomationOccurrence:
    day = local_date or date.today().isoformat()
    key = f"plan:{plan.id}:{day}"
    existing = session.scalar(
        select(AutomationOccurrence).where(
            AutomationOccurrence.occurrence_key == key
        )
    )
    if existing:
        return existing
    row = AutomationOccurrence(
        occurrence_key=key,
        customer_id=plan.customer_id,
        plan_id=plan.id,
        local_date=day,
        status="preflight",
        publish_schedule_id=plan.publish_schedule_id,
        step_json={"production_attempt": 0, "target_ready_count": plan.target_ready_count},
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def create_generation_occurrence(
    session: Session,
    *,
    schedule: ReachPublishSchedule,
    trigger: ReachPublishTrigger,
    job: Job,
    frozen_config: dict[str, Any],
) -> AutomationOccurrence:
    key = trigger.occurrence_key or f"trigger:{trigger.id}"
    existing = session.scalar(
        select(AutomationOccurrence).where(
            AutomationOccurrence.occurrence_key == key
        )
    )
    if existing:
        return existing
    row = AutomationOccurrence(
        occurrence_key=key,
        customer_id=schedule.customer_id,
        local_date=trigger.local_date or date.today().isoformat(),
        status="producing",
        production_job_id=job.id,
        publish_schedule_id=schedule.id,
        publish_trigger_id=trigger.id,
        step_json={"production_attempt": 1, "frozen_config": frozen_config},
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    trigger.status = "preparing"
    trigger.updated_at = _now()
    session.commit()
    session.refresh(row)
    return row


def _sync_linked_trigger(session: Session, occurrence: AutomationOccurrence) -> None:
    try:
        from engine.reach.publish_schedule import sync_trigger_with_occurrence

        sync_trigger_with_occurrence(
            session,
            occurrence_status=str(occurrence.status or ""),
            trigger_id=occurrence.publish_trigger_id,
        )
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()


def _create_job(session: Session, occurrence: AutomationOccurrence) -> Job:
    from engine.catalog.db import Customer
    from engine.jobs.queue import CreateJobRequest, create_job

    plan = session.get(AutomationPlan, occurrence.plan_id) if occurrence.plan_id else None
    frozen = dict((occurrence.step_json or {}).get("frozen_config") or {})
    customer = session.get(Customer, occurrence.customer_id)
    req = CreateJobRequest(
        mode="count",
        target_count=max(
            1,
            int(
                (plan.target_ready_count if plan else None)
                or frozen.get("target_count")
                or 1
            ),
        ),
        template_name=str(
            (plan.template_name if plan else None)
            or frozen.get("template_name")
            or "default-vertical"
        ),
        theme=str((plan.theme if plan else None) or frozen.get("theme") or "default"),
        category=str(
            (plan.category if plan else None) or frozen.get("category") or "default"
        ),
        customer_name=(customer.name if customer else None),
        use_active_rule=True,
    )
    return create_job(session, req, customer_id=occurrence.customer_id)


def _eligible_output(session: Session, job_id: int) -> RenderOutput | None:
    from engine.reach.publish_sources import (
        has_current_approved_review,
        has_verified_ready_gate,
    )

    rows = session.scalars(
        select(RenderOutput)
        .where(RenderOutput.job_id == job_id, RenderOutput.state == "ready")
        .order_by(RenderOutput.id.asc())
    ).all()
    for output in rows:
        if not has_verified_ready_gate(
            output.qc_json if isinstance(output.qc_json, dict) else None
        ):
            continue
        if not has_current_approved_review(session, output.id):
            continue
        if output.pack_status != "ready":
            continue
        return output
    return None


def _export_pack(
    session: Session, occurrence: AutomationOccurrence, output: RenderOutput
) -> str:
    from engine.catalog.review_auto import ensure_publish_pack

    customer = session.get(Customer, occurrence.customer_id)
    if not customer:
        raise ValueError("客户不存在")
    result = ensure_publish_pack(session, customer, output)
    if not result.get("ok"):
        raise ValueError(f"发布物料生成失败：{result.get('error') or 'unknown'}")
    return str(result["pack_dir"])


def _queue_and_publish(
    session: Session, occurrence: AutomationOccurrence
) -> None:
    from engine.reach.prefill import enqueue_from_pack
    from engine.reach.publish_runner import (
        create_run,
        list_run_items,
        reconcile_stale_runs,
        retry_deferred_items,
        start_run,
    )

    schedule = session.get(ReachPublishSchedule, occurrence.publish_schedule_id)
    if not schedule:
        occurrence.status = "pack_ready"
        occurrence.updated_at = _now()
        session.commit()
        return
    if occurrence.queue_id is None:
        rows = enqueue_from_pack(
            session,
            customer_id=occurrence.customer_id,
            pack_dir=Path(str(occurrence.pack_dir)),
            platforms=[schedule.platform],
            output_id=occurrence.output_id,
            mark_ready=False,
        )
        occurrence.queue_id = rows[0].id
        occurrence.updated_at = _now()
        session.commit()
    if occurrence.publish_run_id:
        reconcile_stale_runs(session)
        existing = session.scalar(
            select(ReachPublishRun).where(
                ReachPublishRun.run_id == occurrence.publish_run_id
            )
        )
        existing_items = list_run_items(session, existing) if existing else []
        if existing and existing.status in (
            "queued",
            "running",
            "paused_human",
            "outcome_unknown",
            "stopping",
        ):
            started = start_run(existing.run_id)
            if started.get("ok"):
                occurrence.status = "publishing"
                occurrence.error = ""
                occurrence.updated_at = _now()
                session.commit()
            return
        if existing and existing.status == "completed":
            if existing_items and all(item.phase == "published" for item in existing_items):
                occurrence.status = "published"
                occurrence.finished_at = _now()
                occurrence.updated_at = _now()
                session.commit()
                return
            if any(
                item.phase in ("awaiting_confirmation", "outcome_unknown")
                for item in existing_items
            ):
                occurrence.status = "waiting_human"
                occurrence.error = "存在发布结果待人工确认的条目"
                occurrence.updated_at = _now()
                session.commit()
                return
        if existing and existing.status == "interrupted_system":
            occurrence.status = "waiting_human"
            occurrence.error = "系统中断后的发布任务须人工确认，禁止自动重放"
            occurrence.updated_at = _now()
            session.commit()
            return
        retry_ids = [
            item.id
            for item in existing_items
            if item.phase in ("deferred", "failed", "skipped")
            and item.retry_run_id is None
        ]
        if retry_ids:
            try:
                retry_run = retry_deferred_items(
                    session,
                    customer_id=occurrence.customer_id,
                    item_ids=retry_ids,
                    automatic=True,
                )
            except ValueError:
                occurrence.status = "publish_scheduled"
                occurrence.updated_at = _now()
                session.commit()
                return
            if retry_run:
                occurrence.publish_run_id = retry_run.run_id
                occurrence.status = "publishing"
                occurrence.error = ""
                occurrence.updated_at = _now()
                session.commit()
                return
            occurrence.status = "waiting_human"
            occurrence.error = "存在未发布条目，等待人工补发或确认"
            occurrence.updated_at = _now()
            session.commit()
            return
        occurrence.publish_run_id = None
        occurrence.updated_at = _now()
        session.commit()
    queue = session.get(__import__("engine.catalog.db", fromlist=["ReachQueueItem"]).ReachQueueItem, occurrence.queue_id)
    if not queue:
        raise ValueError("自动发布队列项不存在")
    run = create_run(
        session,
        customer_id=occurrence.customer_id,
        items=[
            {
                "queue_id": queue.id,
                "platform": queue.platform,
                "chrome_profile": schedule.chrome_profile,
                "title": queue.title,
                "body": queue.body,
                "video_path": queue.video_path,
                "pack_dir": queue.pack_dir,
                "gate_snapshot": {
                    "automation_occurrence_id": occurrence.id,
                    "bypass_window_reason": occurrence.bypass_window_reason,
                },
            }
        ],
        source=(
            "quality_recovery"
            if occurrence.bypass_window_reason == "quality_recovery"
            else "automation"
        ),
        schedule_id=schedule.id,
        trigger_id=occurrence.publish_trigger_id,
        accept_risk=True,
    )
    occurrence.publish_run_id = run.run_id
    occurrence.status = "publishing"
    occurrence.updated_at = _now()
    session.commit()
    started = start_run(run.run_id)
    if not started.get("ok"):
        occurrence.status = "publish_scheduled"
        occurrence.error = str(started.get("error") or "publish_worker_busy")
        session.commit()


def _run_has_quality_failure(
    session: Session, run: ReachPublishRun
) -> bool:
    items = session.scalars(
        select(ReachPublishRunItem).where(ReachPublishRunItem.run_id == run.id)
    ).all()
    for item in items:
        evidence = item.evidence_json if isinstance(item.evidence_json, dict) else {}
        pub = evidence.get("publish_result") if isinstance(evidence, dict) else {}
        phase = str((pub or {}).get("phase") or "")
        error = f"{item.error} {(pub or {}).get('error', '')}".lower()
        if phase == "gate_failed" or "ready_gate" in error or "质量" in error:
            return True
    return False


def _create_replacement(
    session: Session, occurrence: AutomationOccurrence
) -> AutomationOccurrence | None:
    plan = session.get(AutomationPlan, occurrence.plan_id) if occurrence.plan_id else None
    max_attempts = int(plan.max_replacement_attempts if plan else 2)
    next_attempt = int(occurrence.replacement_attempt or 0) + 1
    if next_attempt > max_attempts:
        occurrence.status = "blocked_quality"
        occurrence.error = "质量补偿次数已用尽"
        occurrence.updated_at = _now()
        session.commit()
        return None
    key = f"{occurrence.occurrence_key}:replacement:{next_attempt}"
    replacement = session.scalar(
        select(AutomationOccurrence).where(
            AutomationOccurrence.occurrence_key == key
        )
    )
    if replacement:
        return replacement
    replacement = AutomationOccurrence(
        occurrence_key=key,
        customer_id=occurrence.customer_id,
        plan_id=occurrence.plan_id,
        local_date=occurrence.local_date,
        status="preflight",
        publish_schedule_id=occurrence.publish_schedule_id,
        publish_trigger_id=occurrence.publish_trigger_id,
        replacement_of_id=occurrence.id,
        replacement_attempt=next_attempt,
        bypass_window_reason="quality_recovery",
        step_json={
            "production_attempt": 0,
            "frozen_config": (occurrence.step_json or {}).get("frozen_config") or {},
        },
        created_at=_now(),
        updated_at=_now(),
    )
    occurrence.status = "replaced"
    occurrence.updated_at = _now()
    session.add(replacement)
    session.commit()
    session.refresh(replacement)
    return replacement


def advance_occurrence(
    session: Session, occurrence: AutomationOccurrence
) -> dict[str, Any]:
    """Advance at most one idempotent checkpoint."""
    if occurrence.status in TERMINAL:
        _sync_linked_trigger(session, occurrence)
        return occurrence_to_dict(occurrence)
    if occurrence.status == "preflight":
        job = _create_job(session, occurrence)
        steps = dict(occurrence.step_json or {})
        steps["production_attempt"] = int(steps.get("production_attempt") or 0) + 1
        occurrence.production_job_id = job.id
        occurrence.step_json = steps
        occurrence.status = "producing"
        occurrence.updated_at = _now()
        session.commit()
        return occurrence_to_dict(occurrence)
    if occurrence.status == "producing":
        job = session.get(Job, occurrence.production_job_id)
        if not job:
            occurrence.status = "failed"
            occurrence.error = "生产任务不存在"
        else:
            output = _eligible_output(session, job.id)
            if output:
                occurrence.output_id = output.id
                occurrence.status = "packing"
            elif job.status in ("failed", "circuit_open", "cancelled", "completed"):
                plan = session.get(AutomationPlan, occurrence.plan_id) if occurrence.plan_id else None
                attempts = int((occurrence.step_json or {}).get("production_attempt") or 1)
                limit = int(plan.max_production_attempts if plan else 3)
                if attempts < limit:
                    occurrence.status = "preflight"
                else:
                    occurrence.status = "blocked_quality"
                    occurrence.error = "未达到 ready 配额且补产次数已用尽"
        occurrence.updated_at = _now()
        session.commit()
        if occurrence.status in TERMINAL:
            _sync_linked_trigger(session, occurrence)
        return occurrence_to_dict(occurrence)
    if occurrence.status == "packing":
        from engine.reach.publish_sources import (
            has_current_approved_review,
            has_verified_ready_gate,
        )

        output = session.get(RenderOutput, occurrence.output_id)
        if (
            not output
            or output.state != "ready"
            or not has_verified_ready_gate(
                output.qc_json if isinstance(output.qc_json, dict) else None
            )
            or not has_current_approved_review(session, output.id)
        ):
            occurrence.status = "blocked_quality"
            occurrence.error = "成片未明确通过 READY_GATE 与审片 approved"
        else:
            try:
                occurrence.pack_dir = _export_pack(session, occurrence, output)
                occurrence.status = "pack_ready"
                occurrence.error = ""
            except ValueError as exc:
                occurrence.status = "asset_blocked"
                occurrence.error = str(exc)
        occurrence.updated_at = _now()
        session.commit()
        if occurrence.status in TERMINAL:
            _sync_linked_trigger(session, occurrence)
        return occurrence_to_dict(occurrence)
    if occurrence.status in ("pack_ready", "publish_scheduled", "waiting_human"):
        _queue_and_publish(session, occurrence)
        return occurrence_to_dict(occurrence)
    if occurrence.status == "publishing":
        run = session.scalar(
            select(ReachPublishRun).where(
                ReachPublishRun.run_id == occurrence.publish_run_id
            )
        )
        if not run:
            occurrence.status = "failed"
            occurrence.error = "发布批次不存在"
        elif run.status == "completed":
            items = session.scalars(
                select(ReachPublishRunItem).where(ReachPublishRunItem.run_id == run.id)
            ).all()
            if items and all(item.phase == "published" for item in items):
                occurrence.status = "published"
            elif any(
                item.phase in ("awaiting_confirmation", "outcome_unknown")
                for item in items
            ):
                occurrence.status = "waiting_human"
                occurrence.error = "存在发布结果待人工确认的条目"
            else:
                occurrence.status = "publish_scheduled"
                occurrence.error = "发布批次未全部核验成功"
        elif run.status in ("paused_human", "outcome_unknown", "interrupted_system"):
            occurrence.status = "waiting_human"
        elif run.status == "failed":
            if _run_has_quality_failure(session, run):
                _create_replacement(session, occurrence)
            else:
                occurrence.status = "failed"
                occurrence.error = run.error or "发布失败"
        occurrence.updated_at = _now()
        if occurrence.status == "published":
            occurrence.finished_at = _now()
        session.commit()
        # Always mirror terminal / retryable states onto linked trigger.
        # Historical bug: published left trigger stuck in preparing forever.
        if occurrence.status in (
            "published",
            "failed",
            "cancelled",
            "blocked_quality",
            "asset_blocked",
            "waiting_human",
            "publish_scheduled",
        ):
            _sync_linked_trigger(session, occurrence)
        return occurrence_to_dict(occurrence)
    return occurrence_to_dict(occurrence)


def tick_automation(session: Session, *, customer_id: int | None = None) -> list[dict[str, Any]]:
    """Advance enabled plans and all non-terminal occurrences once."""
    plans_stmt = select(AutomationPlan).where(AutomationPlan.enabled.is_(True))
    if customer_id is not None:
        plans_stmt = plans_stmt.where(AutomationPlan.customer_id == customer_id)
    plans = session.scalars(plans_stmt).all()
    for plan in plans:
        ensure_daily_occurrence(session, plan)
    stmt = select(AutomationOccurrence).where(
        AutomationOccurrence.status.not_in(tuple(TERMINAL | {"published", "replaced"}))
    )
    if customer_id is not None:
        stmt = stmt.where(AutomationOccurrence.customer_id == customer_id)
    rows = session.scalars(stmt.order_by(AutomationOccurrence.id.asc()).limit(50)).all()
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            results.append(advance_occurrence(session, row))
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            fresh = session.get(AutomationOccurrence, row.id)
            if fresh:
                fresh.status = "failed"
                fresh.error = str(exc)
                fresh.updated_at = _now()
                session.commit()
                results.append(occurrence_to_dict(fresh))
    return results
