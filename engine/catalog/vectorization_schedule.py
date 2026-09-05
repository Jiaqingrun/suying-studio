"""Daily local-time window for vectorization enable/disable (edge-triggered)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("montage.vectorization.schedule")

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: str) -> time:
    text = str(value or "").strip()
    m = _HHMM.match(text)
    if not m:
        raise ValueError(f"invalid_hhmm:{value!r}")
    return time(hour=int(m.group(1)), minute=int(m.group(2)))


def normalize_hhmm(value: str) -> str:
    t = parse_hhmm(value)
    return f"{t.hour:02d}:{t.minute:02d}"


def in_local_window(
    now: datetime,
    start_hhmm: str,
    end_hhmm: str,
) -> bool:
    """True if local wall-clock is inside [start, end).

    When start > end the window crosses midnight (e.g. 22:00–07:00).
    When start == end the window is invalid / empty (always False).
    """
    start = parse_hhmm(start_hhmm)
    end = parse_hhmm(end_hhmm)
    if start == end:
        return False
    local = now.astimezone(LOCAL_TZ) if now.tzinfo else now.replace(tzinfo=LOCAL_TZ)
    cur = local.timetz().replace(tzinfo=None)
    if start < end:
        return start <= cur < end
    # overnight
    return cur >= start or cur < end


def next_edge_hint(
    now: datetime,
    start_hhmm: str,
    end_hhmm: str,
    *,
    currently_in: bool | None = None,
) -> str:
    """Human-readable next open/close local datetime string."""
    try:
        start = parse_hhmm(start_hhmm)
        end = parse_hhmm(end_hhmm)
    except ValueError:
        return ""
    if start == end:
        return "时段起止不能相同"
    local = now.astimezone(LOCAL_TZ) if now.tzinfo else now.replace(tzinfo=LOCAL_TZ)
    in_win = currently_in if currently_in is not None else in_local_window(
        local, start_hhmm, end_hhmm
    )
    target = end if in_win else start
    # Walk up to 2 days to find next occurrence of target HH:MM
    for day_off in range(0, 3):
        cand_date = (local + timedelta(days=day_off)).date()
        cand = datetime(
            cand_date.year,
            cand_date.month,
            cand_date.day,
            target.hour,
            target.minute,
            tzinfo=LOCAL_TZ,
        )
        if cand > local:
            label = "结束" if in_win else "开启"
            return f"下次定时{label} {cand.strftime('%m-%d %H:%M')}"
    return ""


def schedule_policy_fields(settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Snapshot fields for status API."""
    now = now or datetime.now(tz=LOCAL_TZ)
    start = str(getattr(settings, "vectorization_schedule_start", None) or "22:00")
    end = str(getattr(settings, "vectorization_schedule_end", None) or "07:00")
    enabled = bool(getattr(settings, "vectorization_schedule_enabled", False))
    try:
        in_window = in_local_window(now, start, end) if enabled else False
    except ValueError:
        in_window = False
    auto_stop = bool(getattr(settings, "vectorization_auto_stop_when_done", True))
    return {
        "schedule_enabled": enabled,
        "schedule_start": start,
        "schedule_end": end,
        "auto_stop_when_done": auto_stop,
        "in_window": in_window,
        "schedule_last_in_window": getattr(
            settings, "vectorization_schedule_last_in_window", None
        ),
        "next_edge_hint": (
            next_edge_hint(now, start, end, currently_in=in_window) if enabled else ""
        ),
        "schedule_timezone": "Asia/Shanghai",
    }


def library_gap_totals(session: Any, customer_id: int) -> dict[str, int]:
    """Pending assets for both orientations (true embedding gaps)."""
    from engine.catalog.vectorization_runtime import status_snapshot

    portrait = status_snapshot(session, customer_id=customer_id, orientation="portrait")
    landscape = status_snapshot(session, customer_id=customer_id, orientation="landscape")
    p_pending = int(portrait.get("pending_assets") or 0)
    l_pending = int(landscape.get("pending_assets") or 0)
    return {
        "portrait_pending": p_pending,
        "landscape_pending": l_pending,
        "portrait_completed": int(portrait.get("completed_assets") or 0),
        "landscape_completed": int(landscape.get("completed_assets") or 0),
        "total_pending": p_pending + l_pending,
        "total_completed": int(portrait.get("completed_assets") or 0)
        + int(landscape.get("completed_assets") or 0),
    }


def _active_work(session: Any, customer_id: int) -> bool:
    from sqlalchemy import func, select

    from engine.catalog.db import VectorizationQueue, VectorizationRun
    from engine.catalog.vectorization_runtime import ACTIVE_STATES

    active_run = session.scalar(
        select(func.count())
        .select_from(VectorizationRun)
        .where(
            VectorizationRun.customer_id == customer_id,
            VectorizationRun.status.in_(("IDLE", "RUNNING", "PAUSE_REQUESTED")),
        )
    )
    if int(active_run or 0) > 0:
        return True
    claimed = session.scalar(
        select(func.count())
        .select_from(VectorizationQueue)
        .where(
            VectorizationQueue.customer_id == customer_id,
            VectorizationQueue.status == "CLAIMED",
        )
    )
    return int(claimed or 0) > 0


def _audit_auto(
    session: Any,
    *,
    customer_id: int | None,
    event: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        from engine.ops.audit_log import write_log

        with session.begin_nested():
            write_log(
                session,
                customer_id=customer_id,
                category="vector",
                event=event,
                message=message,
                level="info",
                stage="schedule",
                source_type="vector_run",
                source_id="",
                correlation_id="vector:schedule",
                details=details or {},
            )
    except Exception:
        log.exception("vector schedule audit failed event=%s", event)


def apply_schedule_edges(
    *,
    now: datetime | None = None,
    force_evaluate: bool = False,
) -> dict[str, Any]:
    """Edge-trigger enable on enter window, pause+disable on leave.

    Does NOT re-enable every tick while already in-window (so auto-stop stays off).
    """
    from engine.config.settings import (
        disable_vectorization,
        enable_vectorization,
        load_settings,
        save_settings,
    )

    now = now or datetime.now(tz=LOCAL_TZ)
    settings = load_settings()
    out: dict[str, Any] = {
        "ok": True,
        "schedule_enabled": bool(settings.vectorization_schedule_enabled),
        "actions": [],
    }
    if not settings.vectorization_schedule_enabled:
        # still keep last_in_window current if we want clean future enable
        return out

    start = str(settings.vectorization_schedule_start or "22:00")
    end = str(settings.vectorization_schedule_end or "07:00")
    try:
        in_window = in_local_window(now, start, end)
    except ValueError as exc:
        out["ok"] = False
        out["error"] = str(exc)
        return out

    last = settings.vectorization_schedule_last_in_window
    out["in_window"] = in_window
    out["last_in_window"] = last

    # First observation after enabling schedule: only seed edge state unless force.
    if last is None and not force_evaluate:
        settings.vectorization_schedule_last_in_window = in_window
        save_settings(settings)
        out["actions"].append("seed_edge")
        # If already inside window when schedule first turns on, open once.
        if in_window and not settings.vectorization_enabled:
            enable_vectorization(settings)
            out["actions"].append("enable_on_seed_in")
            try:
                from engine.catalog.vectorization_runtime import executor

                executor.wake()
            except Exception:
                pass
        out["vectorization_enabled"] = bool(load_settings().vectorization_enabled)
        return out

    entering = in_window and last is not True
    leaving = (not in_window) and last is True

    if entering:
        enable_vectorization(settings)
        out["actions"].append("enable_on_enter")
        try:
            from engine.catalog.vectorization_runtime import executor

            executor.wake()
        except Exception:
            pass
        try:
            from engine.catalog.db import get_session
            from engine.catalog.customer_scope import require_active_customer

            session = get_session()
            try:
                cust = require_active_customer(session, settings)
                _audit_auto(
                    session,
                    customer_id=cust.id,
                    event="vector_schedule_entered",
                    message="定时窗口开始，已开启增量向量化",
                    details={"start": start, "end": end},
                )
                session.commit()
            finally:
                session.close()
        except Exception:
            log.exception("schedule enter audit failed")

    if leaving:
        try:
            from engine.catalog.vectorization_runtime import executor

            executor.pause(owner="schedule", timeout=2.0)
            out["actions"].append("pause_on_leave")
        except Exception:
            log.exception("schedule leave pause failed")
        disable_vectorization()
        out["actions"].append("disable_on_leave")
        try:
            from engine.catalog.db import get_session
            from engine.catalog.customer_scope import require_active_customer

            session = get_session()
            try:
                settings2 = load_settings()
                cust = require_active_customer(session, settings2)
                _audit_auto(
                    session,
                    customer_id=cust.id,
                    event="vector_schedule_left",
                    message="定时窗口结束，已暂停并关闭向量化",
                    details={"start": start, "end": end},
                )
                session.commit()
            finally:
                session.close()
        except Exception:
            log.exception("schedule leave audit failed")

    settings = load_settings()
    settings.vectorization_schedule_last_in_window = in_window
    save_settings(settings)
    out["vectorization_enabled"] = bool(settings.vectorization_enabled)
    return out


def maybe_auto_stop_when_done() -> dict[str, Any]:
    """If enabled and both orientations have zero gaps (no active work), disable."""
    from engine.config.settings import disable_vectorization, load_settings

    settings = load_settings()
    out: dict[str, Any] = {"ok": True, "stopped": False}
    if not settings.vectorization_enabled:
        out["skipped"] = "disabled"
        return out
    if not bool(getattr(settings, "vectorization_auto_stop_when_done", True)):
        out["skipped"] = "auto_stop_off"
        return out

    from engine.catalog.customer_scope import require_active_customer
    from engine.catalog.db import get_session

    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        gaps = library_gap_totals(session, int(customer.id))
        out["gaps"] = gaps
        if int(gaps["total_pending"]) > 0:
            out["skipped"] = "pending"
            return out
        if _active_work(session, int(customer.id)):
            out["skipped"] = "active_work"
            return out
        try:
            from engine.catalog.vectorization_runtime import executor

            executor.pause(owner="schedule", timeout=2.0)
        except Exception:
            log.exception("auto-stop pause failed")
        disable_vectorization()
        _audit_auto(
            session,
            customer_id=int(customer.id),
            event="vector_auto_stopped_complete",
            message="向量缺口已清空，自动结束增量向量化",
            details=gaps,
        )
        session.commit()
        out["stopped"] = True
        out["actions"] = ["disable_when_done"]
        return out
    except Exception as exc:
        log.exception("auto-stop failed")
        out["ok"] = False
        out["error"] = str(exc)
        return out
    finally:
        session.close()


def maybe_vectorization_policy_tick(*, now: datetime | None = None) -> dict[str, Any]:
    """Scheduler entry: edges first, then auto-stop, then report whether work may run."""
    now = now or datetime.now(tz=LOCAL_TZ)
    edge = apply_schedule_edges(now=now)
    stop = maybe_auto_stop_when_done()
    settings = __import__(
        "engine.config.settings", fromlist=["load_settings"]
    ).load_settings()
    schedule_on = bool(settings.vectorization_schedule_enabled)
    try:
        in_window = (
            in_local_window(
                now,
                str(settings.vectorization_schedule_start or "22:00"),
                str(settings.vectorization_schedule_end or "07:00"),
            )
            if schedule_on
            else True
        )
    except ValueError:
        in_window = not schedule_on
    # When schedule is on, only allow auto incremental while inside window.
    allow_work = bool(settings.vectorization_enabled) and (
        (not schedule_on) or in_window
    )
    return {
        "ok": True,
        "edge": edge,
        "auto_stop": stop,
        "allow_work": allow_work,
        "vectorization_enabled": bool(settings.vectorization_enabled),
        "in_window": in_window if schedule_on else None,
    }
