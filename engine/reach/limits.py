"""Daily quota + failure circuit for Reach queue (G5). Human-in-the-loop only."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.catalog.db import ReachQueueItem
from engine.reach.queue import PLATFORMS, set_status

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


def normalize_platform_quotas(
    platform_quotas: Mapping[str, Any] | None,
    *,
    known: tuple[str, ...] = PLATFORMS,
) -> dict[str, int]:
    """Keep only known platforms; clamp to >= 0."""
    out: dict[str, int] = {}
    raw = platform_quotas or {}
    for plat in known:
        if plat not in raw:
            continue
        try:
            out[plat] = max(0, int(raw[plat]))
        except (TypeError, ValueError):
            out[plat] = 0
    return out


def validate_quota_config(
    daily_quota: int,
    platform_quotas: Mapping[str, Any] | None,
) -> dict[str, int]:
    """Validate total + platform caps. Raises ValueError on invalid config."""
    total = max(0, int(daily_quota))
    plats = normalize_platform_quotas(platform_quotas)
    allocated = sum(plats.values())
    if allocated > total:
        raise ValueError(
            f"平台配额合计 {allocated} 超过日总额 {total}；请调低各平台或提高总额"
        )
    return plats


def platform_cap(
    daily_quota: int,
    platform_quotas: Mapping[str, int] | None,
    platform: str | None,
) -> int | None:
    """Effective cap for a platform.

    - No platform_quotas configured → only total applies (return None for platform-specific).
    - Configured map present → missing platform means 0 (must be allocated to use).
    """
    if not platform:
        return None
    plats = normalize_platform_quotas(platform_quotas)
    if not plats:
        return None
    return int(plats.get(platform, 0))


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
    platform_quotas: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    total_quota = max(0, int(daily_quota))
    plats = normalize_platform_quotas(platform_quotas)
    used_total = count_today_attempts(session, customer_id=customer_id)
    fails = count_recent_failures(session, customer_id=customer_id, platform=platform)
    threshold = max(1, int(fail_threshold))

    used_plat = (
        count_today_attempts(session, customer_id=customer_id, platform=platform)
        if platform
        else used_total
    )
    plat_quota = platform_cap(total_quota, plats, platform)

    blocked_total = used_total >= total_quota
    blocked_plat = False
    if plat_quota is not None and platform:
        blocked_plat = used_plat >= plat_quota
    blocked_quota = blocked_total or blocked_plat
    blocked_circuit = fails >= threshold

    platform_rows: list[dict[str, Any]] = []
    for pid in PLATFORMS:
        u = count_today_attempts(session, customer_id=customer_id, platform=pid)
        q = platform_cap(total_quota, plats, pid)
        platform_rows.append(
            {
                "id": pid,
                "quota": q,
                "configured": q is not None,
                "used_today": u,
                "remaining": None if q is None else max(0, q - u),
                "blocked_quota": False if q is None else u >= q,
            }
        )

    return {
        "day": _today(),
        "platform": platform,
        "daily_quota": total_quota,
        "used_today": used_total if not platform else used_plat,
        "used_total": used_total,
        "remaining": max(0, total_quota - used_total),
        "platform_quotas": plats,
        "platform_quota": plat_quota,
        "platforms": platform_rows,
        "allocated": sum(plats.values()) if plats else None,
        "unallocated": (total_quota - sum(plats.values())) if plats else None,
        "consecutive_failures": fails,
        "fail_threshold": threshold,
        "blocked_quota": blocked_quota,
        "blocked_total": blocked_total,
        "blocked_platform": blocked_plat,
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
    platform_quotas: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Raise ValueError if quota or circuit blocks new enqueue."""
    st = quota_status(
        session,
        customer_id=customer_id,
        daily_quota=daily_quota,
        fail_threshold=fail_threshold,
        platform=platform,
        platform_quotas=platform_quotas,
    )
    if st["blocked_circuit"]:
        raise ValueError(
            f"触达熔断：连续失败 {st['consecutive_failures']}≥{st['fail_threshold']}，请人工处理后再入队"
        )
    if st.get("blocked_total"):
        raise ValueError(f"触达日配额已满：今日 {st['used_total']}/{st['daily_quota']}")
    if st.get("blocked_platform") and platform:
        pq = st.get("platform_quota")
        used = count_today_attempts(session, customer_id=customer_id, platform=platform)
        raise ValueError(f"平台「{platform}」日配额已满：今日 {used}/{pq}")
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
