"""HTTP routes for GSystemPause system events."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from engine.config.settings import load_settings, save_settings
from engine.runtime.pause_coordinator import (
    SystemEventControl,
    coordinator,
    get_system_event_control_from_settings,
)
from engine.runtime.quiesce import capture_owned_snapshot, quiesce_units, restore_units

router = APIRouter(tags=["system"])


class SystemEventBody(BaseModel):
    event_id: str
    kind: str
    occurred_at: str | None = None
    source: str = "tauri"
    generation_hint: int | None = None


class ManualPauseBody(BaseModel):
    reason: str = "manual"


class ManualResumeBody(BaseModel):
    reasons: list[str] | None = None


class SystemEventControlUpdate(BaseModel):
    enabled: bool | None = None
    pause_on_system_sleep: bool | None = None
    pause_on_power_off: bool | None = None
    pause_on_session_inactive: bool | None = None
    pause_on_screen_sleep: bool | None = None
    auto_resume_on_wake: bool | None = None
    auto_resume_on_session_active: bool | None = None
    wake_settle_seconds: int | None = Field(default=None, ge=0, le=60)
    quiesce_timeout_seconds: int | None = Field(default=None, ge=1, le=120)
    event_debounce_ms: int | None = Field(default=None, ge=0, le=5000)
    resume_requires_path_health: bool | None = None
    resume_requires_disk_health: bool | None = None


def _audit_system_sync(
    event: str,
    message: str,
    *,
    level: str = "info",
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort DB audit. Must never run on the sleep/wake HTTP hot path."""
    try:
        from engine.catalog.db import get_session
        from engine.ops.audit_log import write_log

        session = get_session()
        try:
            write_log(
                session,
                customer_id=None,
                scope="machine",
                category="system",
                event=event,
                message=message,
                level=level,
                source_type="system_event",
                source_id=str((details or {}).get("event_id") or ""),
                details=details,
                commit=True,
            )
        finally:
            session.close()
    except Exception:
        pass


def _audit_system(
    event: str,
    message: str,
    *,
    level: str = "info",
    details: dict[str, Any] | None = None,
) -> None:
    # SQLite commit on a busy montage.db can take multi-seconds; never block
    # POST /system/events (desktop ureq timeout used to be 3s → toast storm).
    threading.Thread(
        target=_audit_system_sync,
        kwargs={
            "event": event,
            "message": message,
            "level": level,
            "details": details,
        },
        daemon=True,
        name="suying-system-audit",
    ).start()


def _path_and_disk_ok() -> tuple[bool, bool]:
    try:
        from engine.config.settings import load_settings as _ls
        from engine.ops.maintenance import disk_report

        settings = _ls()
        disks = disk_report(settings)
        path_h = (disks.get("path_health") if isinstance(disks, dict) else None) or {}
        path_ok = bool(path_h.get("ok", True))
        disk_ok = True
        if isinstance(disks, dict) and disks.get("ok") is False:
            disk_ok = False
        return path_ok, disk_ok
    except Exception:  # noqa: BLE001
        return False, False


def _after_pause_async(timeout_sec: float, policy: SystemEventControl) -> None:
    generation = int(coordinator.snapshot().get("generation") or 0)
    try:
        quiesce_units(timeout_sec=timeout_sec)
        # quiesce_units may mark_paused or mark_blocked; both promote pending wake
        # to RESUMING. Call mark_paused again for the clean (no-error) path only.
        state = coordinator.mark_paused(generation=generation)
        if state.get("state") != "RESUMING" or not state.get("pending_resume"):
            state = coordinator.snapshot()
        if state.get("state") == "RESUMING" and state.get("pending_resume"):
            _after_resume_async(policy)
    except Exception as e:  # noqa: BLE001
        # Generation-scoped: ignore if wake already advanced/cleared this pause round.
        state = coordinator.mark_blocked(
            ["quiesce_failed"], error=str(e), generation=generation
        )
        if state.get("state") == "RESUMING" and state.get("pending_resume"):
            _after_resume_async(policy)


def _after_resume_async(policy: SystemEventControl) -> None:
    try:
        path_ok, disk_ok = _path_and_disk_ok()
        state = coordinator.apply_resume_after_checks(path_ok=path_ok, disk_ok=disk_ok, policy=policy)
        if state.get("restore_ready"):
            restored = restore_units()
            if not restored.get("errors"):
                coordinator.complete_resume()
    except Exception as e:  # noqa: BLE001
        coordinator.mark_blocked(["resume_failed"], error=str(e))


def _maybe_retry_health_blocked_resume() -> None:
    """If resume was blocked only by path/disk, re-check when App polls pause-state."""
    snap = coordinator.snapshot()
    if snap.get("state") != "PAUSED_BLOCKED":
        return
    blockers = set(snap.get("resume_blockers") or [])
    if not blockers.issubset({"path_health", "disk_health"}):
        return
    settings = load_settings()
    policy = get_system_event_control_from_settings(settings)
    path_ok, disk_ok = _path_and_disk_ok()
    if policy.resume_requires_path_health and not path_ok:
        return
    if policy.resume_requires_disk_health and not disk_ok:
        return
    if not coordinator.rearm_health_blocked_resume():
        return
    threading.Thread(
        target=_after_resume_async,
        args=(policy,),
        daemon=True,
        name="suying-resume-health-retry",
    ).start()


@router.get("/system/pause-state")
def get_pause_state() -> dict[str, Any]:
    _maybe_retry_health_blocked_resume()
    settings = load_settings()
    return {
        **coordinator.snapshot(),
        "policy": get_system_event_control_from_settings(settings).model_dump(),
        "token_configured": bool(coordinator.system_token()),
    }


@router.get("/system/events")
def list_system_events(limit: int = 50) -> dict[str, Any]:
    return {"events": coordinator.list_events(limit=limit)}


def _legacy_production_job_events(
    session,
    *,
    customer_id: int,
    level: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from engine.catalog.db import Job, JobEvent, RenderOutput
    from engine.catalog.serial import format_display_no

    # Mandatory auto-approve used to spam JobEvent; hide those happy-path
    # rows from the amalgamated 日志 view. Audit stays on 审片决策历史.
    _legacy_noise = {
        "审片自动通过",
        "审片一键通过",
    }
    legacy = session.execute(
        select(JobEvent, Job.customer_id)
        .join(Job, JobEvent.job_id == Job.id)
        .where(Job.customer_id == customer_id)
        .order_by(JobEvent.id.desc())
        .limit(120)
    ).all()
    # Latest output per job (one query for the page)
    job_ids = {int(event.job_id) for event, _ in legacy if event.job_id is not None}
    latest_by_job: dict[int, RenderOutput] = {}
    if job_ids:
        for out in session.scalars(
            select(RenderOutput)
            .where(RenderOutput.job_id.in_(job_ids))
            .order_by(RenderOutput.id.desc())
        ).all():
            jid = int(out.job_id) if out.job_id is not None else None
            if jid is not None and jid not in latest_by_job:
                latest_by_job[jid] = out
    rows: list[dict[str, Any]] = []
    for event, row_customer_id in legacy:
        if (event.message or "") in _legacy_noise:
            continue
        if isinstance(event.payload_json, dict) and event.payload_json.get("_structured_log"):
            continue
        if level and level != "all" and (event.level or "info") != level:
            continue
        job_id = int(event.job_id) if event.job_id is not None else None
        item: dict[str, Any] = {
            "id": f"job-{event.id}",
            "customer_id": row_customer_id,
            "scope": "customer",
            "category": "production",
            "level": event.level,
            "event": "legacy_job_event",
            "stage": "",
            "message": event.message,
            "source_type": "job",
            "source_id": str(event.job_id),
            "correlation_id": f"job:{event.job_id}",
            "details": event.payload_json or {},
            "evidence": {},
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "legacy": True,
            "job_id": job_id,
        }
        out = latest_by_job.get(job_id) if job_id is not None else None
        if out is not None:
            item["output_id"] = out.id
            item["display_no"] = out.display_no
            item["display_label"] = (
                format_display_no(out.display_no) if out.display_no is not None else ""
            )
            item["output_path"] = out.output_path or ""
        rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def _include_legacy_production_logs(
    *,
    category: str | None,
    level: str | None,
    search: str | None,
    page: int,
) -> bool:
    if page != 1 or search:
        return False
    if level and level != "all":
        # level filter still applies inside legacy loader
        pass
    cat = (category or "").strip().lower()
    if not cat or cat == "all" or cat == "production":
        return True
    return False


@router.get("/operation-logs")
def list_operation_logs(
    category: str | None = None,
    level: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    from sqlalchemy import func, or_, select

    from engine.catalog.customer_scope import require_active_customer
    from engine.catalog.db import Job, OperationLog, get_session
    from engine.ops.audit_log import log_to_dict

    session = get_session()
    try:
        customer = require_active_customer(session, load_settings())
        stmt = select(OperationLog).where(
            or_(
                OperationLog.customer_id == customer.id,
                OperationLog.scope == "machine",
            )
        )
        if category and category != "all":
            if category == "message":
                stmt = stmt.where(
                    OperationLog.category.in_(("message", "notification"))
                )
            else:
                stmt = stmt.where(OperationLog.category == category)
        if level and level != "all":
            stmt = stmt.where(OperationLog.level == level)
        if search:
            raw_search = search.strip()
            term = f"%{raw_search}%"
            display_no: int | None = None
            try:
                display_no = int(raw_search.lstrip("#"))
            except ValueError:
                pass
            output_ids: list[int] = []
            if display_no is not None:
                from engine.catalog.db import RenderOutput

                output_ids = [
                    int(value)
                    for value in session.scalars(
                        select(RenderOutput.id)
                        .join(Job, RenderOutput.job_id == Job.id)
                        .where(
                            Job.customer_id == customer.id,
                            RenderOutput.display_no == display_no,
                        )
                    ).all()
                ]
            search_parts = [
                OperationLog.message.ilike(term),
                OperationLog.event.ilike(term),
                OperationLog.source_id.ilike(term),
                OperationLog.correlation_id.ilike(term),
                OperationLog.account.ilike(term),
                OperationLog.platform.ilike(term),
            ]
            if output_ids:
                search_parts.extend(
                    [
                        OperationLog.source_id.in_([str(value) for value in output_ids]),
                        func.json_extract(
                            OperationLog.details_json, "$.output_id"
                        ).in_(output_ids),
                    ]
                )
            stmt = stmt.where(
                or_(*search_parts)
            )
        page = max(1, page)
        page_size = min(200, max(1, page_size))
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(
            stmt.order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        legacy_rows: list[dict[str, Any]] = []
        if _include_legacy_production_logs(
            category=category, level=level, search=search, page=page
        ):
            legacy_rows = _legacy_production_job_events(
                session,
                customer_id=customer.id,
                level=level,
                limit=50,
            )
        return {
            "ok": True,
            "items": [log_to_dict(row, session=session) for row in rows],
            "legacy_items": legacy_rows,
            "page": page,
            "page_size": page_size,
            "total": total,
        }
    finally:
        session.close()


@router.get("/operation-logs/{log_id}")
def get_operation_log(log_id: int) -> dict[str, Any]:
    from engine.catalog.customer_scope import require_active_customer
    from engine.catalog.db import OperationLog, get_session
    from engine.ops.audit_log import log_to_dict

    session = get_session()
    try:
        customer = require_active_customer(session, load_settings())
        row = session.get(OperationLog, log_id)
        if not row or (row.customer_id not in (None, customer.id) and row.scope != "machine"):
            raise HTTPException(404, "日志不存在")
        return {"ok": True, "item": log_to_dict(row, session=session)}
    finally:
        session.close()


@router.get("/system/event-control")
def get_event_control() -> dict[str, Any]:
    settings = load_settings()
    return get_system_event_control_from_settings(settings).model_dump()


@router.put("/system/event-control")
def put_event_control(body: SystemEventControlUpdate) -> dict[str, Any]:
    settings = load_settings()
    current = get_system_event_control_from_settings(settings)
    data = current.model_dump()
    for key, value in body.model_dump(exclude_none=True).items():
        data[key] = value
    settings.system_event_control = SystemEventControl.model_validate(data)
    save_settings(settings)
    _audit_system(
        "system_event_preferences_updated",
        "系统暂停与恢复偏好已更新",
        details=body.model_dump(exclude_none=True),
    )
    return settings.system_event_control.model_dump()


@router.post("/system/events")
def post_system_event(
    body: SystemEventBody,
    x_suying_system_token: str | None = Header(default=None, alias="X-Suying-System-Token"),
) -> dict[str, Any]:
    coordinator.require_system_token(x_suying_system_token)
    # Fast ACK for already-seen ids: skip snapshot/settings (outbox retries).
    if coordinator.has_seen_event(body.event_id):
        snap = coordinator.snapshot()
        result = {**snap, "duplicate": True, "applied": False, "acknowledged": True}
        _audit_system(
            f"system_event_{body.kind}",
            f"系统事件 {body.kind} 重复投递已确认",
            details={
                "event_id": body.event_id,
                "kind": body.kind,
                "source": body.source,
                "applied": False,
                "duplicate": True,
                "state": result.get("state"),
                "generation": result.get("generation"),
            },
        )
        return result
    settings = load_settings()
    policy = get_system_event_control_from_settings(settings)
    owned = capture_owned_snapshot()
    result = coordinator.handle_event(
        event_id=body.event_id,
        kind=body.kind,
        occurred_at=body.occurred_at,
        source=body.source,
        generation_hint=body.generation_hint,
        policy=policy,
        owned_snapshot=owned,
    )
    result = {**result, "acknowledged": True}
    _audit_system(
        f"system_event_{body.kind}",
        f"系统事件 {body.kind} {'已应用' if result.get('applied') else '已忽略'}",
        details={
            "event_id": body.event_id,
            "kind": body.kind,
            "source": body.source,
            "applied": result.get("applied"),
            "duplicate": result.get("duplicate"),
            "state": result.get("state"),
            "generation": result.get("generation"),
        },
    )
    if result.get("applied") and body.kind in (
        "will_sleep",
        "screens_sleep",
        "session_inactive",
        "will_power_off",
    ):
        timeout = float(policy.quiesce_timeout_seconds or 15)
        threading.Thread(
            target=_after_pause_async,
            args=(timeout, policy),
            daemon=True,
            name="suying-quiesce",
        ).start()
    if (
        result.get("applied")
        and result.get("state") != "PAUSING"
        and body.kind in ("did_wake", "screens_wake", "session_active")
    ):
        threading.Thread(
            target=_after_resume_async,
            args=(policy,),
            daemon=True,
            name="suying-resume",
        ).start()
    return result


@router.post("/system/pause")
def post_manual_pause(body: ManualPauseBody | None = None) -> dict[str, Any]:
    reason = (body.reason if body else None) or "manual"
    owned = capture_owned_snapshot()
    state = coordinator.pause_manual(reason=reason, owned_snapshot=owned)
    settings = load_settings()
    policy = get_system_event_control_from_settings(settings)
    threading.Thread(
        target=_after_pause_async,
        args=(float(policy.quiesce_timeout_seconds or 15), policy),
        daemon=True,
        name="suying-manual-quiesce",
    ).start()
    _audit_system(
        "manual_pause_requested",
        "已请求暂停速影任务",
        details={"reason": reason, "state": state.get("state")},
    )
    return state


@router.post("/system/resume")
def post_manual_resume(body: ManualResumeBody | None = None) -> dict[str, Any]:
    body = body or ManualResumeBody()
    if not body.reasons:
        raise HTTPException(status_code=400, detail="人工恢复必须明确指定要清除的暂停原因")
    settings = load_settings()
    policy = get_system_event_control_from_settings(settings)
    prepared = coordinator.prepare_manual_resume(reasons=body.reasons)
    if prepared.get("state") != "RESUMING":
        return prepared
    path_ok, disk_ok = _path_and_disk_ok()
    checked = coordinator.apply_resume_after_checks(
        path_ok=path_ok,
        disk_ok=disk_ok,
        policy=policy,
    )
    if not checked.get("restore_ready"):
        return checked
    restored = restore_units()
    if restored.get("errors"):
        _audit_system(
            "manual_resume_failed",
            "恢复检查未通过",
            level="error",
            details={"reasons": body.reasons, "errors": restored.get("errors")},
        )
        return coordinator.snapshot()
    result = coordinator.complete_resume()
    _audit_system(
        "manual_resume_completed",
        "速影任务已恢复",
        details={"reasons": body.reasons, "state": result.get("state")},
    )
    return result
