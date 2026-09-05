"""Explicit-time publish schedule materialization."""

from __future__ import annotations

import logging
import random
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from engine.catalog.db import (
    ReachPublishReservation,
    ReachPublishRun,
    ReachPublishRunItem,
    ReachPublishSchedule,
    ReachPublishTrigger,
)
from engine.reach.publish_runner import (
    create_run,
    get_active_run,
    retry_deferred_items,
    start_run,
)
from engine.reach.publish_sources import materialize_from_schedule

log = logging.getLogger("montage.publish_schedule")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite may return persisted UTC datetimes without tzinfo."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _append_trigger_note(trigger: ReachPublishTrigger, note: str) -> None:
    prev = (trigger.note or "").strip()
    trigger.note = f"{prev}\n{note}".strip() if prev else note
    trigger.updated_at = _now_utc()


def claim_trigger_pending(
    session: Session,
    trigger_id: int,
    *,
    now: datetime | None = None,
) -> bool:
    """Atomically claim a pending trigger into ``preparing``.

    Background tick and manual tick race on the same row: only one UPDATE
    that matches ``status='pending'`` succeeds. Returns True iff this caller
    won the claim.
    """
    stamp = now or _now_utc()
    result = session.execute(
        update(ReachPublishTrigger)
        .where(
            ReachPublishTrigger.id == int(trigger_id),
            ReachPublishTrigger.status == "pending",
        )
        .values(status="preparing", claimed_at=stamp, updated_at=stamp)
    )
    session.commit()
    return int(result.rowcount or 0) > 0


_TERMINAL_OCC_OK = frozenset({"published", "completed"})
_TERMINAL_OCC_BAD = frozenset(
    {"failed", "cancelled", "blocked_quality", "asset_blocked"}
)
_TERMINAL_OCC_ALL = _TERMINAL_OCC_OK | _TERMINAL_OCC_BAD


def sync_trigger_with_occurrence(
    session: Session,
    *,
    occurrence_status: str,
    trigger_id: int | None,
) -> None:
    """Mirror occurrence terminal states onto the linked schedule trigger."""
    if not trigger_id:
        return
    trig = session.get(ReachPublishTrigger, int(trigger_id))
    if trig is None:
        return
    occ_st = str(occurrence_status or "")
    if occ_st in _TERMINAL_OCC_OK:
        new_status = "completed"
    elif occ_st in _TERMINAL_OCC_BAD:
        # Keep a retriable pending while window still open; otherwise fail closed.
        now = _now_utc()
        end = (
            _as_utc(trig.window_end)
            if trig.window_end
            else (
                _as_utc(trig.planned_at) + timedelta(minutes=10)
                if trig.planned_at
                else now
            )
        )
        if now <= end and trig.status in ("preparing", "pending"):
            new_status = "pending"
        else:
            new_status = "failed"
    else:
        mapping = {
            "blocked_quality": "failed",
            "failed": "failed",
            "cancelled": "cancelled",
            "completed": "completed",
            "published": "completed",
            "materialized": "materialized",
        }
        new_status = mapping.get(occ_st)
    if not new_status or trig.status == new_status:
        return
    if new_status == "pending":
        trig.claimed_at = None
        trig.run_id = None
    trig.status = new_status
    trig.updated_at = _now_utc()
    _append_trigger_note(trig, f"occurrence→{occurrence_status} ⇒ trigger={new_status}")


def heal_stuck_triggers(
    session: Session,
    *,
    customer_id: int,
    orphan_age_sec: float = 90.0,
) -> list[dict[str, Any]]:
    """Fix stuck preparing triggers every tick (no 1h wait).

    Root causes historically:
    - occurrence already published/failed but trigger left ``preparing``
    - reconcile required 3600s age → whole evening lost
    - success occ wrongly reopened to pending (double-send risk)
    """
    from engine.catalog.db import AutomationOccurrence, Job

    now = _now_utc()
    orphan_cut = now - timedelta(seconds=max(15.0, float(orphan_age_sec)))
    rows = session.scalars(
        select(ReachPublishTrigger).where(
            ReachPublishTrigger.customer_id == customer_id,
            ReachPublishTrigger.status == "preparing",
        )
    ).all()
    actions: list[dict[str, Any]] = []
    for trig in rows:
        scheduled = session.get(ReachPublishSchedule, trig.schedule_id)
        end = (
            _as_utc(trig.window_end)
            if trig.window_end
            else (
                _as_utc(trig.planned_at) + timedelta(minutes=10)
                if trig.planned_at
                else now
            )
        )
        window_open = now <= end
        occ = None
        if trig.occurrence_key:
            occ = session.scalar(
                select(AutomationOccurrence).where(
                    AutomationOccurrence.occurrence_key == trig.occurrence_key
                )
            )
        if occ is None and trig.id:
            occ = session.scalar(
                select(AutomationOccurrence)
                .where(AutomationOccurrence.publish_trigger_id == int(trig.id))
                .order_by(AutomationOccurrence.id.desc())
            )

        occ_st = str(getattr(occ, "status", "") or "")
        job_id = int(getattr(occ, "production_job_id", 0) or 0) if occ else 0
        job = session.get(Job, job_id) if job_id else None
        job_st = str(getattr(job, "status", "") or "") if job else ""

        # 1) Terminal success — close trigger (never re-open → no double publish).
        if occ_st in _TERMINAL_OCC_OK:
            trig.status = "completed"
            if getattr(occ, "publish_run_id", None):
                trig.run_id = str(occ.publish_run_id)
            trig.claimed_at = None
            _append_trigger_note(
                trig, f"heal: occ={occ_st} ⇒ completed (was preparing)"
            )
            actions.append(
                {
                    "trigger_id": trig.id,
                    "action": "completed",
                    "profile": scheduled.chrome_profile if scheduled else "",
                    "occ": occ_st,
                }
            )
            continue

        # Linked run already finished all published.
        if trig.run_id:
            run = session.scalar(
                select(ReachPublishRun).where(
                    ReachPublishRun.run_id == str(trig.run_id)
                )
            )
            if run and str(run.status or "") == "completed":
                items_ok = list(
                    session.scalars(
                        select(ReachPublishRunItem).where(
                            ReachPublishRunItem.run_id == run.id
                        )
                    ).all()
                )
                if items_ok and all(
                    str(i.phase or "") == "published" for i in items_ok
                ):
                    trig.status = "completed"
                    trig.claimed_at = None
                    _append_trigger_note(
                        trig, "heal: linked run completed+all published"
                    )
                    actions.append(
                        {
                            "trigger_id": trig.id,
                            "action": "completed_via_run",
                            "run_id": run.run_id,
                        }
                    )
                    continue

        # 2) Terminal failure / stuck production after job done.
        terminal_bad = occ_st in _TERMINAL_OCC_BAD
        stuck_prod = job_st == "completed" and occ_st in (
            "producing",
            "pack_ready",
            "preflight",
            "",
        )
        if terminal_bad or stuck_prod:
            reopen_budget = (trig.note or "").count("heal: reopened_after_fail")
            if window_open and reopen_budget < 2:
                trig.status = "pending"
                trig.claimed_at = None
                _append_trigger_note(
                    trig,
                    f"heal: reopened_after_fail occ={occ_st or '-'} job={job_st or '-'}",
                )
                actions.append(
                    {
                        "trigger_id": trig.id,
                        "action": "reopen_pending",
                        "occ": occ_st,
                        "job": job_st,
                        "profile": scheduled.chrome_profile if scheduled else "",
                    }
                )
                continue
            if not window_open:
                trig.status = "missed_human_confirm"
                _append_trigger_note(
                    trig,
                    f"heal: window over preparing+occ={occ_st or '-'} → missed",
                )
                try:
                    _alert_window_missed(session, trig, scheduled)
                except Exception:
                    log.exception("heal missed alert failed trigger=%s", trig.id)
                actions.append(
                    {
                        "trigger_id": trig.id,
                        "action": "missed_human_confirm",
                        "occ": occ_st,
                    }
                )
                continue
            trig.status = "failed"
            _append_trigger_note(
                trig, f"heal: preparing → failed occ={occ_st} job={job_st}"
            )
            actions.append(
                {"trigger_id": trig.id, "action": "failed", "occ": occ_st}
            )
            continue

        # 3) Orphan preparing: no live progress past orphan_age.
        updated = _as_utc(trig.updated_at) if trig.updated_at else None
        claimed = _as_utc(trig.claimed_at) if trig.claimed_at else None
        anchor = updated or claimed or (
            _as_utc(trig.created_at) if trig.created_at else now
        )
        # Live only when a production job is actively working, or publish
        # human/run hold is still fresh — empty job_st must NOT freeze forever.
        live = job_st in ("queued", "running", "stopping")
        if (
            not live
            and occ_st in ("publishing", "publish_scheduled", "waiting_human")
            and anchor >= orphan_cut
        ):
            live = True
        if live:
            continue
        if anchor < orphan_cut:
            if window_open:
                trig.status = "pending"
                trig.claimed_at = None
                _append_trigger_note(
                    trig,
                    "heal: orphan preparing (%ss) reopened"
                    % int((now - anchor).total_seconds()),
                )
                actions.append(
                    {
                        "trigger_id": trig.id,
                        "action": "orphan_reopen",
                        "profile": scheduled.chrome_profile if scheduled else "",
                    }
                )
            else:
                trig.status = "missed_human_confirm"
                _append_trigger_note(
                    trig, "heal: orphan preparing past window → missed"
                )
                try:
                    _alert_window_missed(session, trig, scheduled)
                except Exception:
                    log.exception("heal orphan missed alert failed")
                actions.append(
                    {"trigger_id": trig.id, "action": "orphan_missed"}
                )
    if actions:
        session.commit()
    return actions


def reconcile_stale_preparing_triggers(
    session: Session,
    *,
    customer_id: int,
    max_age_sec: float = 90.0,
) -> int:
    """Compat entry: heal stuck preparing (defaults to fast 90s orphan age)."""
    actions = heal_stuck_triggers(
        session, customer_id=customer_id, orphan_age_sec=max_age_sec
    )
    return len(actions)

def schedule_to_dict(row: ReachPublishSchedule) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "name": row.name,
        "enabled": row.enabled,
        "timezone": row.timezone,
        "chrome_profile": row.chrome_profile,
        "platform": row.platform,
        "content_source": row.content_source,
        "source_config": row.source_config_json or {},
        "times": row.times_json or [],
        "windows": row.windows_json or [],
        "random_algorithm": row.random_algorithm,
        "items_per_trigger": row.items_per_trigger,
        "repeat_count": row.repeat_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def trigger_to_dict(row: ReachPublishTrigger) -> dict[str, Any]:
    return {
        "id": row.id,
        "schedule_id": row.schedule_id,
        "customer_id": row.customer_id,
        "planned_at": _iso_utc(row.planned_at),
        "status": row.status,
        "run_id": row.run_id,
        "occurrence_key": row.occurrence_key,
        "local_date": row.local_date,
        "window_start": _iso_utc(row.window_start),
        "window_end": _iso_utc(row.window_end),
        "seed": row.random_seed,
        "random_algorithm": row.random_algorithm,
        "ordinal": row.ordinal,
        "conflict_adjustment": row.conflict_adjustment_json or {},
        "claimed_at": _iso_utc(row.claimed_at),
        "bypass_window_reason": row.bypass_window_reason,
        "note": row.note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _weekday_ok(entry: dict[str, Any], local_dt: datetime) -> bool:
    weekdays = entry.get("weekdays")
    if not weekdays:
        return True
    return int(local_dt.weekday()) in {int(x) for x in weekdays}


def _matches_time(entry: dict[str, Any], local_dt: datetime, *, tolerance_min: int = 2) -> bool:
    hour = int(entry.get("hour", entry.get("h", -1)))
    minute = int(entry.get("minute", entry.get("m", 0)))
    if hour < 0:
        return False
    target = local_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = abs((local_dt - target).total_seconds())
    return delta <= tolerance_min * 60 and _weekday_ok(entry, local_dt)


def next_trigger_at(schedule: ReachPublishSchedule, *, after: datetime | None = None) -> datetime | None:
    """Compute next explicit trigger in schedule timezone."""
    after = after or _now_utc()
    try:
        tz = ZoneInfo(schedule.timezone or "Asia/Shanghai")
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    times = schedule.times_json if isinstance(schedule.times_json, list) else []
    if not times:
        return None
    local_after = after.astimezone(tz)
    candidates: list[datetime] = []
    for day_offset in range(0, 8):
        base = (local_after + timedelta(days=day_offset)).replace(second=0, microsecond=0)
        for entry in times:
            if not isinstance(entry, dict):
                continue
            hour = int(entry.get("hour", entry.get("h", -1)))
            minute = int(entry.get("minute", entry.get("m", 0)))
            if hour < 0:
                continue
            cand = base.replace(hour=hour, minute=minute)
            if day_offset == 0 and cand <= local_after:
                continue
            if not _weekday_ok(entry, cand):
                continue
            candidates.append(cand.astimezone(timezone.utc))
    if not candidates:
        return None
    return min(candidates)


def ensure_pending_triggers(session: Session, schedule: ReachPublishSchedule) -> ReachPublishTrigger | None:
    """Legacy exact-time compatibility: create the next immutable trigger."""
    nxt = next_trigger_at(schedule)
    if not nxt:
        return None
    existing = session.scalar(
        select(ReachPublishTrigger).where(
            ReachPublishTrigger.schedule_id == schedule.id,
            ReachPublishTrigger.status == "pending",
        )
    )
    if existing:
        return existing
    trig = ReachPublishTrigger(
        schedule_id=schedule.id,
        customer_id=schedule.customer_id,
        planned_at=nxt,
        status="pending",
        occurrence_key=f"schedule:{schedule.id}:legacy:{nxt.isoformat()}",
        local_date=nxt.astimezone(ZoneInfo(schedule.timezone or "Asia/Shanghai")).date().isoformat(),
        window_start=nxt,
        window_end=nxt + timedelta(minutes=10),
        random_algorithm="legacy_explicit_v1",
        ordinal=0,
        created_at=_now_utc(),
        updated_at=_now_utc(),
    )
    session.add(trig)
    session.commit()
    session.refresh(trig)
    return trig


def _clock(value: Any) -> tuple[int, int, int]:
    raw = str(value or "").strip()
    if ":" not in raw:
        raise ValueError(f"无效窗口时间: {raw}")
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"无效窗口时间: {raw}")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    if (
        hour not in range(24)
        or minute not in range(60)
        or second not in range(60)
    ):
        raise ValueError(f"无效窗口时间: {raw}")
    return hour, minute, second


def _window_bounds(
    entry: dict[str, Any], local_day: datetime, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    sh, sm, ss = _clock(entry.get("start"))
    eh, em, es = _clock(entry.get("end"))
    start = local_day.replace(hour=sh, minute=sm, second=ss, microsecond=0, tzinfo=tz)
    end = local_day.replace(hour=eh, minute=em, second=es, microsecond=0, tzinfo=tz)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _select_window_time(
    *,
    start: datetime,
    end: datetime,
    seed: str,
    occupied: list[datetime],
    min_gap_minutes: int,
) -> tuple[datetime, dict[str, Any]]:
    total_seconds = max(1, int((end - start).total_seconds()))
    rng = random.Random(int(seed, 16))
    gap = max(0, int(min_gap_minutes)) * 60
    first: datetime | None = None
    for attempt in range(64):
        candidate = start + timedelta(seconds=rng.randrange(total_seconds))
        first = first or candidate
        if all(abs((candidate - other).total_seconds()) >= gap for other in occupied):
            adjustment = {}
            if attempt:
                adjustment = {
                    "reason": "min_gap",
                    "attempts": attempt,
                    "initial_candidate": first.astimezone(timezone.utc).isoformat(),
                }
            return candidate, adjustment
    # A window too small for the requested count is still materialized
    # deterministically and flagged for preview/human correction.
    return start + timedelta(seconds=rng.randrange(total_seconds)), {
        "reason": "min_gap_unsatisfied",
        "attempts": 64,
    }


def ensure_window_triggers(
    session: Session,
    schedule: ReachPublishSchedule,
    *,
    local_dates: list[datetime] | None = None,
) -> list[ReachPublishTrigger]:
    """Materialize one immutable, independently seeded trigger per occurrence."""
    windows = schedule.windows_json if isinstance(schedule.windows_json, list) else []
    if not windows:
        legacy = ensure_pending_triggers(session, schedule)
        return [legacy] if legacy else []
    try:
        tz = ZoneInfo(schedule.timezone or "Asia/Shanghai")
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")
    now_utc = _now_utc()
    now_local = now_utc.astimezone(tz)
    explicit_dates = local_dates is not None
    days = local_dates or [
        now_local - timedelta(days=1),
        now_local,
        now_local + timedelta(days=1),
    ]
    created: list[ReachPublishTrigger] = []
    for day in days:
        local_day = day.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        for window_index, entry in enumerate(windows):
            if not isinstance(entry, dict) or not _weekday_ok(entry, local_day):
                continue
            start, end = _window_bounds(entry, local_day, tz)
            if not explicit_dates and end <= now_local:
                continue
            count = max(1, int(entry.get("count") or schedule.repeat_count or 1))
            min_gap = max(0, int(entry.get("min_gap_minutes") or 0))
            occupied = [
                _as_utc(value).astimezone(tz)
                for value in session.scalars(
                    select(ReachPublishTrigger.planned_at).where(
                        ReachPublishTrigger.customer_id == schedule.customer_id,
                        ReachPublishTrigger.status.in_(
                            ("pending", "blocked_reservation", "preparing", "materialized")
                        ),
                        ReachPublishTrigger.planned_at
                        >= start.astimezone(timezone.utc),
                        ReachPublishTrigger.planned_at < end.astimezone(timezone.utc),
                    )
                ).all()
            ]
            schedule_config = (
                schedule.source_config_json
                if isinstance(schedule.source_config_json, dict)
                else {}
            )
            revision = schedule_config.get("schedule_revision")
            for ordinal in range(count):
                key_parts = [
                    f"schedule:{schedule.id}",
                    local_day.date().isoformat(),
                ]
                if revision is not None:
                    key_parts.append(f"revision:{int(revision)}")
                key_parts.extend(
                    [f"window:{window_index}", f"ordinal:{ordinal}"]
                )
                key = ":".join(key_parts)
                existing = session.scalar(
                    select(ReachPublishTrigger).where(
                        ReachPublishTrigger.occurrence_key == key
                    )
                )
                if existing:
                    occupied.append(_as_utc(existing.planned_at).astimezone(tz))
                    created.append(existing)
                    continue
                seed = secrets.token_hex(8)
                selectable_start = (
                    max(start, now_local + timedelta(seconds=1))
                    if not explicit_dates
                    else start
                )
                if selectable_start >= end:
                    continue
                selected, adjustment = _select_window_time(
                    start=selectable_start,
                    end=end,
                    seed=seed,
                    occupied=occupied,
                    min_gap_minutes=min_gap,
                )
                trigger = ReachPublishTrigger(
                    schedule_id=schedule.id,
                    customer_id=schedule.customer_id,
                    planned_at=selected.astimezone(timezone.utc),
                    status="pending",
                    occurrence_key=key,
                    local_date=local_day.date().isoformat(),
                    window_start=start.astimezone(timezone.utc),
                    window_end=end.astimezone(timezone.utc),
                    random_seed=seed,
                    random_algorithm=schedule.random_algorithm or "window_v1",
                    ordinal=ordinal,
                    conflict_adjustment_json=adjustment,
                    created_at=_now_utc(),
                    updated_at=_now_utc(),
                )
                # Concurrent create vs publish-clock tick both materialize the
                # same occurrence_key. Use a savepoint and accept the winner.
                try:
                    with session.begin_nested():
                        session.add(trigger)
                        session.flush()
                except IntegrityError:
                    session.expunge(trigger)
                    existing = session.scalar(
                        select(ReachPublishTrigger).where(
                            ReachPublishTrigger.occurrence_key == key
                        )
                    )
                    if not existing:
                        raise
                    log.info(
                        "window trigger race absorbed key=%s schedule=%s",
                        key,
                        schedule.id,
                    )
                    occupied.append(_as_utc(existing.planned_at).astimezone(tz))
                    created.append(existing)
                    continue
                occupied.append(selected)
                created.append(trigger)
    session.commit()
    return created


def release_trigger_reservations(
    session: Session,
    trigger: ReachPublishTrigger,
    *,
    status: str = "released",
) -> int:
    rows = session.scalars(
        select(ReachPublishReservation).where(
            ReachPublishReservation.trigger_id == trigger.id,
            ReachPublishReservation.status == "reserved",
        )
    ).all()
    now = _now_utc()
    for reservation in rows:
        reservation.status = status
        reservation.updated_at = now
    return len(rows)


def _reconcile_materialized_triggers(
    session: Session, *, customer_id: int
) -> None:
    triggers = session.scalars(
        select(ReachPublishTrigger).where(
            ReachPublishTrigger.customer_id == customer_id,
            ReachPublishTrigger.status.in_(
                ("materialized", "makeup_pending", "awaiting_confirmation")
            ),
        )
    ).all()
    changed = False
    for trigger in triggers:
        if not trigger.run_id:
            continue
        run = session.scalar(
            select(ReachPublishRun).where(ReachPublishRun.run_id == trigger.run_id)
        )
        if not run:
            continue
        items = session.scalars(
            select(ReachPublishRunItem).where(ReachPublishRunItem.run_id == run.id)
        ).all()
        if any(item.phase == "awaiting_confirmation" for item in items):
            trigger.status = "awaiting_confirmation"
        elif any(item.phase in ("deferred", "failed", "skipped") for item in items):
            retry_ids = [item.retry_run_id for item in items if item.retry_run_id]
            retry_runs = (
                session.scalars(
                    select(ReachPublishRun).where(
                        ReachPublishRun.run_id.in_(retry_ids)
                    )
                ).all()
                if retry_ids
                else []
            )
            if retry_runs and all(row.status == "completed" for row in retry_runs):
                trigger.status = "completed"
            else:
                trigger.status = "makeup_pending"
        elif run.status == "completed":
            trigger.status = "completed"
        elif run.status in ("failed", "cancelled", "interrupted_system"):
            trigger.status = "skipped"
        else:
            continue
        reservations = session.scalars(
            select(ReachPublishReservation).where(
                ReachPublishReservation.trigger_id == trigger.id,
                ReachPublishReservation.status == "reserved",
            )
        ).all()
        if trigger.status == "completed":
            for reservation in reservations:
                reservation.status = "consumed"
                reservation.updated_at = _now_utc()
        elif trigger.status == "skipped":
            for reservation in reservations:
                reservation.status = "released"
                reservation.updated_at = _now_utc()
        trigger.updated_at = _now_utc()
        changed = True
    if changed:
        session.commit()


def _has_due_triggers(
    session: Session,
    *,
    customer_id: int,
    now: datetime,
) -> bool:
    """True when a pending trigger is already due (planned_at <= now) and still in window.

    Intentionally does **not** block deferred_auto for future-only planned times:
    a 5-minute "imminent" yield created a dead idle gap (no deferred, not yet claimable)
    that made makeup appear stalled before evening points.
    """
    schedules = session.scalars(
        select(ReachPublishSchedule).where(
            ReachPublishSchedule.customer_id == customer_id,
            ReachPublishSchedule.enabled.is_(True),
        )
    ).all()
    schedule_ids = [s.id for s in schedules]
    if not schedule_ids:
        return False
    rows = session.scalars(
        select(ReachPublishTrigger).where(
            ReachPublishTrigger.schedule_id.in_(schedule_ids),
            ReachPublishTrigger.status.in_(("pending", "blocked_reservation")),
        )
    ).all()
    for trig in rows:
        planned = _as_utc(trig.planned_at)
        end = (
            _as_utc(trig.window_end)
            if trig.window_end
            else planned + timedelta(minutes=10)
        )
        if now > end:
            continue
        if planned <= now:
            return True
    return False


def _has_due_or_imminent_triggers(
    session: Session,
    *,
    customer_id: int,
    now: datetime,
    horizon_sec: float = 0.0,
) -> bool:
    """Compat alias: default horizon 0 → due-only (no idle dead-zone)."""
    if float(horizon_sec or 0.0) <= 0.0:
        return _has_due_triggers(session, customer_id=customer_id, now=now)
    horizon_end = now + timedelta(seconds=max(0.0, float(horizon_sec)))
    schedules = session.scalars(
        select(ReachPublishSchedule).where(
            ReachPublishSchedule.customer_id == customer_id,
            ReachPublishSchedule.enabled.is_(True),
        )
    ).all()
    schedule_ids = [s.id for s in schedules]
    if not schedule_ids:
        return False
    rows = session.scalars(
        select(ReachPublishTrigger).where(
            ReachPublishTrigger.schedule_id.in_(schedule_ids),
            ReachPublishTrigger.status.in_(("pending", "blocked_reservation")),
        )
    ).all()
    for trig in rows:
        planned = _as_utc(trig.planned_at)
        end = (
            _as_utc(trig.window_end)
            if trig.window_end
            else planned + timedelta(minutes=10)
        )
        if now > end:
            continue
        if planned <= horizon_end:
            return True
    return False


def _alert_window_missed(
    session: Session,
    trigger: ReachPublishTrigger,
    schedule: ReachPublishSchedule | None,
) -> None:
    try:
        from engine.ops.human_alerts import create_alert, dispatch_due_alerts

        profile = (schedule.chrome_profile if schedule else "") or "账号"
        name = (schedule.name if schedule else "") or "定时发布"
        planned = _iso_utc(trigger.planned_at) or ""
        create_alert(
            session,
            alert_key=f"publish_window_missed:{trigger.id}:{trigger.local_date or ''}",
            customer_id=int(trigger.customer_id),
            kind="publish_window_missed",
            source_type="publish_trigger",
            source_id=str(trigger.id),
            summary=(
                f"错过发布窗口 · {name} · {profile} · 计划 {planned} · "
                "需人工确认后补发（禁止静默自动补开新窗）"
            ),
            deep_link="suying://publish",
        )
        dispatch_due_alerts(session, customer_id=int(trigger.customer_id))
    except Exception:
        log.exception(
            "window missed alert failed trigger_id=%s", getattr(trigger, "id", None)
        )


def list_makeup_work(
    session: Session,
    *,
    customer_id: int,
    limit: int = 100,
) -> dict[str, Any]:
    """Aggregate pending makeup: deferred items + missed/makeup triggers (no auto re-fire)."""
    from engine.reach.publish_runner import list_deferred_items, run_item_to_dict

    deferred_rows = list_deferred_items(session, customer_id=customer_id)
    deferred = [
        run_item_to_dict(row, include_evidence=False) for row in deferred_rows[:limit]
    ]
    triggers = session.scalars(
        select(ReachPublishTrigger)
        .where(
            ReachPublishTrigger.customer_id == customer_id,
            ReachPublishTrigger.status.in_(
                ("missed_human_confirm", "makeup_pending", "awaiting_confirmation")
            ),
        )
        .order_by(ReachPublishTrigger.planned_at.desc())
        .limit(limit)
    ).all()
    trigger_out: list[dict[str, Any]] = []
    for t in triggers:
        sched = session.get(ReachPublishSchedule, t.schedule_id) if t.schedule_id else None
        trigger_out.append(
            {
                "id": t.id,
                "schedule_id": t.schedule_id,
                "status": t.status,
                "planned_at": _iso_utc(t.planned_at),
                "local_date": t.local_date,
                "window_start": _iso_utc(t.window_start),
                "window_end": _iso_utc(t.window_end),
                "run_id": t.run_id,
                "occurrence_key": t.occurrence_key,
                "note": t.note,
                "task_name": sched.name if sched else "",
                "chrome_profile": sched.chrome_profile if sched else "",
                "platform": sched.platform if sched else "",
            }
        )
    return {
        "ok": True,
        "deferred": deferred,
        "triggers": trigger_out,
        "counts": {
            "deferred": len(deferred_rows),
            "triggers": len(trigger_out),
            "missed_human_confirm": sum(
                1 for t in triggers if t.status == "missed_human_confirm"
            ),
            "makeup_pending": sum(1 for t in triggers if t.status == "makeup_pending"),
        },
    }


def tick_schedules(session: Session, *, customer_id: int) -> list[dict[str, Any]]:
    """Materialize due occurrences; offline/sleep misses are audit-only."""
    results: list[dict[str, Any]] = []
    heal_actions = heal_stuck_triggers(session, customer_id=customer_id)
    if heal_actions:
        results.append({"status": "triggers_healed", "actions": heal_actions})
    _reconcile_materialized_triggers(session, customer_id=customer_id)
    # Free zombie ACTIVE first so create_run / due triggers are not blocked.
    try:
        from engine.reach.publish_runner import reconcile_stale_runs

        n = reconcile_stale_runs(session)
        if n:
            results.append({"status": "stale_runs_reconciled", "count": n})
    except Exception:
        log.exception("reconcile_stale_runs in tick_schedules failed")

    now = _now_utc()
    # Fairness: already-due window triggers beat deferred_auto.
    # Do not yield for future-only planned times (idle dead-zone banished).
    try:
        from engine.reach.publish_runner import repair_deferred_auto_eligibility

        repaired = repair_deferred_auto_eligibility(
            session, customer_id=customer_id
        )
        if repaired:
            results.append({"status": "deferred_auto_repaired", "count": repaired})
            session.commit()
    except Exception:
        log.exception("repair_deferred_auto_eligibility failed")

    allow_deferred = not _has_due_triggers(
        session, customer_id=customer_id, now=now
    )
    if allow_deferred:
        try:
            retry_run = retry_deferred_items(
                session, customer_id=customer_id, automatic=True
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("deferred auto retry failed")
            results.append(
                {"status": "deferred_retry_error", "error": str(exc)[:200]}
            )
            retry_run = None
        if retry_run is not None:
            return [
                {
                    "status": "deferred_retry_started",
                    "run_id": retry_run.run_id,
                }
            ]
    else:
        results.append(
            {
                "status": "deferred_yield_to_window",
                "reason": "due_triggers_present",
            }
        )

    schedules = session.scalars(
        select(ReachPublishSchedule).where(
            ReachPublishSchedule.customer_id == customer_id,
            ReachPublishSchedule.enabled.is_(True),
        )
    ).all()
    for sched in schedules:
        ensure_window_triggers(session, sched)
        pending = session.scalars(
            select(ReachPublishTrigger)
            .where(
                ReachPublishTrigger.schedule_id == sched.id,
                ReachPublishTrigger.status.in_(("pending", "blocked_reservation")),
            )
            .order_by(ReachPublishTrigger.planned_at.asc())
        ).all()
        for trig in pending:
            # Legacy blocked_reservation triggers are reopened as pending (no reserve).
            if trig.status == "blocked_reservation":
                trig.status = "pending"
                _append_trigger_note(trig, "policy_no_reserve: unblocked")
                trig.updated_at = now
                session.commit()
            planned_at = _as_utc(trig.planned_at)
            end = (
                _as_utc(trig.window_end)
                if trig.window_end
                else planned_at + timedelta(minutes=10)
            )
            if now > end:
                trig.status = "missed_human_confirm"
                _append_trigger_note(trig, "错过所选时间窗口，禁止自动补发，需人工确认")
                release_trigger_reservations(session, trig)
                _alert_window_missed(session, trig, sched)
                session.commit()
                results.append({"trigger_id": trig.id, "status": trig.status})
                continue
            if planned_at > now:
                continue

            # Machine-wide slot (single source of truth with create_run).
            active_run = get_active_run(session)
            if active_run:
                adjustment = dict(trig.conflict_adjustment_json or {})
                adjustment.update(
                    {
                        "reason": "machine_slot",
                        "blocked_by_run": active_run.run_id,
                        "active_status": active_run.status,
                        "observed_at": now.isoformat(),
                    }
                )
                trig.conflict_adjustment_json = adjustment
                trig.updated_at = now
                session.commit()
                results.append(
                    {
                        "trigger_id": trig.id,
                        "status": "pending_machine_slot",
                        "run_id": active_run.run_id,
                        "active_status": active_run.status,
                    }
                )
                return results

            # Atomic claim: only one tick (background or manual) may move
            # pending → preparing. Conditional UPDATE prevents double materialize.
            claimed = claim_trigger_pending(session, trig.id, now=now)
            if not claimed:
                results.append(
                    {
                        "trigger_id": trig.id,
                        "status": "claim_lost",
                        "note": "another tick already claimed this trigger",
                    }
                )
                continue
            # Refresh after CAS so subsequent writes see preparing status.
            session.refresh(trig)
            try:
                mat = materialize_from_schedule(session, sched, trigger=trig)
            except ValueError as exc:
                trig.status = "skipped"
                _append_trigger_note(trig, str(exc))
                release_trigger_reservations(session, trig)
                session.commit()
                results.append(
                    {"trigger_id": trig.id, "status": "skipped", "error": str(exc)}
                )
                continue
            except Exception as exc:  # noqa: BLE001
                # Never leave a claimed preparing trigger orphan after unexpected errors.
                log.exception(
                    "materialize_from_schedule failed trigger_id=%s", trig.id
                )
                trig.status = "pending"
                trig.claimed_at = None
                _append_trigger_note(
                    trig, f"materialize error reopened: {type(exc).__name__}: {exc}"[:300]
                )
                release_trigger_reservations(session, trig)
                session.commit()
                results.append(
                    {
                        "trigger_id": trig.id,
                        "status": "materialize_error_reopened",
                        "error": str(exc)[:200],
                    }
                )
                continue
            if mat.get("pending_generation"):
                from engine.ops.automation_loop import create_generation_occurrence

                job_id = int((mat.get("evidence") or {}).get("job_id") or 0)
                from engine.catalog.db import Job

                job = session.get(Job, job_id)
                if not job:
                    trig.status = "skipped"
                    _append_trigger_note(trig, "生成任务创建失败")
                    session.commit()
                    continue
                occurrence = create_generation_occurrence(
                    session,
                    schedule=sched,
                    trigger=trig,
                    job=job,
                    frozen_config=dict(sched.source_config_json or {}),
                )
                # create_generation_occurrence already sets trigger.status=preparing
                trig.updated_at = _now_utc()
                session.commit()
                results.append(
                    {
                        "trigger_id": trig.id,
                        "status": "preparing",
                        "automation_occurrence_id": occurrence.id,
                        "job_id": job.id,
                    }
                )
                return results
            rows = mat.get("queue_items") or []
            if not rows:
                trig.status = "skipped"
                _append_trigger_note(trig, "无可用队列项")
                release_trigger_reservations(session, trig)
                session.commit()
                continue
            run = create_run(
                session,
                customer_id=customer_id,
                items=[
                    {
                        "queue_id": row.id,
                        "platform": row.platform,
                        "chrome_profile": sched.chrome_profile,
                        "title": row.title,
                        "body": row.body,
                        "video_path": row.video_path,
                        "pack_dir": row.pack_dir,
                        "gate_snapshot": mat.get("evidence") or {},
                    }
                    for row in rows
                ],
                source="schedule",
                schedule_id=sched.id,
                trigger_id=trig.id,
                accept_risk=True,
            )
            trig.status = "materialized"
            trig.run_id = run.run_id
            trig.updated_at = _now_utc()
            session.commit()
            started = start_run(run.run_id)
            if not started.get("ok"):
                run.status = "cancelled"
                run.error = str(started.get("error") or "worker_busy")
                run.finished_at = _now_utc()
                trig.status = "pending"
                trig.run_id = None
                trig.claimed_at = None
                _append_trigger_note(
                    trig, str(started.get("error") or "worker_busy，等待再次领取")
                )
                session.commit()
            results.append(
                {"trigger_id": trig.id, "status": trig.status, "run_id": run.run_id}
            )
            return results
    return results


def claim_trigger_for_manual_tick(session: Session, trigger_id: int) -> bool:
    """Public alias used by API/manual tick paths."""
    return claim_trigger_pending(session, trigger_id)
