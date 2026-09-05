"""Reach consecutive-failure circuit breaker.

Daily production/publish quotas were removed by explicit product decision.
This module intentionally keeps only failure safety; it never counts daily work.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import ReachQueueItem
from engine.reach.queue import set_status

DEFAULT_FAIL_THRESHOLD = 3


def count_recent_failures(
    session: Session,
    *,
    customer_id: int,
    platform: str | None = None,
    limit_lookback: int = 20,
) -> int:
    stmt = select(ReachQueueItem).where(ReachQueueItem.customer_id == customer_id)
    if platform:
        stmt = stmt.where(ReachQueueItem.platform == platform)
    rows = list(
        session.scalars(
            stmt.order_by(ReachQueueItem.id.desc()).limit(max(1, limit_lookback))
        ).all()
    )
    failures = 0
    for row in rows:
        if row.status == "failed":
            failures += 1
            continue
        if row.status == "cancelled":
            continue
        break
    return failures


def circuit_status(
    session: Session,
    *,
    customer_id: int,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    platform: str | None = None,
) -> dict[str, int | bool | str | None]:
    threshold = max(1, int(fail_threshold))
    failures = count_recent_failures(
        session,
        customer_id=customer_id,
        platform=platform,
    )
    return {
        "platform": platform,
        "consecutive_failures": failures,
        "fail_threshold": threshold,
        "blocked_circuit": failures >= threshold,
    }


def assert_circuit_closed(
    session: Session,
    *,
    customer_id: int,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    platform: str | None = None,
) -> dict[str, int | bool | str | None]:
    status = circuit_status(
        session,
        customer_id=customer_id,
        fail_threshold=fail_threshold,
        platform=platform,
    )
    if status["blocked_circuit"]:
        raise ValueError(
            "发布已暂停：连续失败 "
            f"{status['consecutive_failures']} 次，请处理账号或页面问题后再继续"
        )
    return status


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
    status = circuit_status(
        session,
        customer_id=customer_id,
        fail_threshold=fail_threshold,
        platform=platform,
    )
    if not status["blocked_circuit"]:
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
