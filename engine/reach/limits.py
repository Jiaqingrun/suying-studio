"""Daily quota + failure circuit for Reach queue (G5). Human-in-the-loop only."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.catalog.db import ReachQueueItem
from engine.reach.queue import set_status

DEFAULT_DAILY_QUOTA = 5
DEFAULT_FAIL_THRESHOLD = 3


def _today() -> str:
    return date.today().isoformat()


def _day_start_utc() -> datetime:
    """Local calendar day start approximated as UTC midnight for smoke simplicity.

    Production can later align to Asia/Shanghai; quota is per customer_id + local day string
    stored on item logs / counted by created_at date in UTC for now.
    """
    d = date.today()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def count_today_attempts(
    session: Session,
    *,
    customer_id: int,
    platform: str | None = None,
) -> int:
    """Count items created today that are not cancelled (attempts toward quota)."""
    start = _day_start_utc()
    stmt = select(func.count()).select_from(ReachQueueItem).where(
        ReachQueueItem.customer_id == customer_id,
        ReachQueueItem.created_at >= start,
        ReachQueueItem.status != "cancelled",
    )
    if platform:
        stmt = stmt.where(ReachQueueItem.platform == platform)
    return int(session.scalar(stmt) or 0)


def count_recent_failures(
    session: Session,
    *,
    customer_id: int,
    platform: str | None = None,
    limit_lookback: int = 20,
) -> int:
    """Consecutive failures from newest items (same idea as quality circuit)."""
    stmt = select(ReachQueueItem).where(ReachQueueItem.customer_id == customer_id)
    if platform:
        stmt = stmt.where(ReachQueueItem.platform == platform)
    stmt = stmt.order_by(ReachQueueItem.id.desc()).limit(limit_lookback)
    rows = list(session.scalars(stmt).all())
    n = 0
    for r in rows:
        if r.status == "failed":
            n += 1
            continue
        if r.status in ("cancelled",):
            continue
        break
    return n


def quota_status(
    session: Session,
    *,
    customer_id: int,
    daily_quota: int = DEFAULT_DAILY_QUOTA,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    platform: str | None = None,
) -> dict[str, Any]:
    used = count_today_attempts(session, customer_id=customer_id, platform=platform)
    fails = count_recent_failures(session, customer_id=customer_id, platform=platform)
    quota = max(0, int(daily_quota))
    threshold = max(1, int(fail_threshold))
    blocked_quota = used >= quota
    blocked_circuit = fails >= threshold
    return {
        "day": _today(),
        "platform": platform,
        "daily_quota": quota,
        "used_today": used,
        "remaining": max(0, quota - used),
        "consecutive_failures": fails,
        "fail_threshold": threshold,
        "blocked_quota": blocked_quota,
        "blocked_circuit": blocked_circuit,
        "blocked": blocked_quota or blocked_circuit,
        "human_in_loop": True,
        "auto_publish": False,
    }


def assert_can_enqueue(
    session: Session,
    *,
    customer_id: int,
    daily_quota: int = DEFAULT_DAILY_QUOTA,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    platform: str | None = None,
) -> dict[str, Any]:
    """Raise ValueError if quota or circuit blocks new enqueue."""
    st = quota_status(
        session,
        customer_id=customer_id,
        daily_quota=daily_quota,
        fail_threshold=fail_threshold,
        platform=platform,
    )
    if st["blocked_circuit"]:
        raise ValueError(
            f"触达熔断：连续失败 {st['consecutive_failures']}≥{st['fail_threshold']}，请人工处理后再入队"
        )
    if st["blocked_quota"]:
        raise ValueError(f"触达日配额已满：今日 {st['used_today']}/{st['daily_quota']}")
    return st


def record_failure(
    session: Session,
    item: ReachQueueItem,
    *,
    error: str,
    note: str = "",
) -> ReachQueueItem:
    return set_status(session, item, "failed", note=note or item.note, error=error)


def apply_circuit_block(
    session: Session,
    *,
    customer_id: int,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    platform: str | None = None,
) -> list[ReachQueueItem]:
    """If circuit open, mark queued/awaiting_human items as blocked."""
    st = quota_status(
        session,
        customer_id=customer_id,
        fail_threshold=fail_threshold,
        platform=platform,
    )
    if not st["blocked_circuit"]:
        return []
    stmt = select(ReachQueueItem).where(
        ReachQueueItem.customer_id == customer_id,
        ReachQueueItem.status.in_(("queued", "awaiting_human")),
    )
    if platform:
        stmt = stmt.where(ReachQueueItem.platform == platform)
    blocked: list[ReachQueueItem] = []
    for item in session.scalars(stmt).all():
        blocked.append(
            set_status(
                session,
                item,
                "blocked",
                error=f"circuit_open failures>={fail_threshold}",
            )
        )
    return blocked
