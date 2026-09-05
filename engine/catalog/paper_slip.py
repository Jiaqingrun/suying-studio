"""纸片规则（PAPER SLIP）— 滚动避重（Rolling Diversity）。

权威：docs/PAPER_SLIP_LOCK.md
仅 ready 成片计入近窗；禁止日/周满额硬拒与配额熔断。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from engine.catalog.db import (
    DailyUsage,
    KeywordUsage,
    PaperSlipReservation,
    WeeklyUsage,
)

# HARD — 滚动避近窗（不可改回日/周满额）
ROLLING_CLIPLET_WINDOW = 20
ROLLING_PHRASE_WINDOW = 15
ROLLING_CLIPLET_STEPS = (20, 10, 0)
ROLLING_PHRASE_STEPS = (15, 8, 0)
# Legacy aliases kept so old imports don't crash; MUST NOT be used as hard caps.
MAX_DAILY_USES = ROLLING_CLIPLET_WINDOW  # deprecated semantic
MAX_WEEKLY_USES = ROLLING_CLIPLET_WINDOW  # deprecated semantic
RESERVATION_TTL_SECONDS = 6 * 60 * 60
KIND_CLIPLET = "cliplet"
KIND_PHRASE = "phrase"
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def local_day(now: datetime | None = None) -> str:
    """本地日历日 YYYY-MM-DD（Asia/Shanghai）。"""
    dt = now or datetime.now(_LOCAL_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LOCAL_TZ)
    else:
        dt = dt.astimezone(_LOCAL_TZ)
    return dt.strftime("%Y-%m-%d")


def local_week(now: datetime | None = None, *, day: str | None = None) -> str:
    """上海自然周周键（周一日期）— 仅用于账本分区，不作满额硬拒。"""
    if day:
        local_date = datetime.strptime(day, "%Y-%m-%d").date()
    else:
        dt = now or datetime.now(_LOCAL_TZ)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_LOCAL_TZ)
        local_date = dt.astimezone(_LOCAL_TZ).date()
    return (local_date - timedelta(days=local_date.weekday())).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_phrase_key(text: str) -> str:
    from engine.pack.text_sanitize import strip_all_punctuation

    raw = strip_all_punctuation(str(text or ""), keep_newlines=True)
    lines = [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]
    return "|".join(lines)


def phrase_keys_from_text(text: str) -> list[str]:
    keys: list[str] = []
    full = normalize_phrase_key(text)
    if full:
        keys.append(full)
    for ln in str(text or "").replace("\r", "").split("\n"):
        k = normalize_phrase_key(ln)
        if k and k not in keys:
            keys.append(k)
    return keys


def _cust(customer_id: int | None) -> int:
    return int(customer_id or 0)


def get_daily_count(
    session: Session,
    kind: str,
    key: str,
    *,
    customer_id: int | None = None,
    day: str | None = None,
) -> int:
    if not key:
        return 0
    d = day or local_day()
    row = session.scalar(
        select(DailyUsage).where(
            DailyUsage.customer_id == _cust(customer_id),
            DailyUsage.kind == kind,
            DailyUsage.key == key,
            DailyUsage.day == d,
        )
    )
    return int(row.count) if row else 0


def get_weekly_count(
    session: Session,
    kind: str,
    key: str,
    *,
    customer_id: int | None = None,
    week: str | None = None,
) -> int:
    if not key:
        return 0
    w = week or local_week()
    row = session.scalar(
        select(WeeklyUsage).where(
            WeeklyUsage.customer_id == _cust(customer_id),
            WeeklyUsage.kind == kind,
            WeeklyUsage.key == key,
            WeeklyUsage.week == w,
        )
    )
    stored = int(row.count) if row is not None else 0
    monday = datetime.strptime(w, "%Y-%m-%d").date()
    sunday = (monday + timedelta(days=6)).isoformat()
    value = session.scalar(
        select(func.coalesce(func.sum(DailyUsage.count), 0)).where(
            DailyUsage.customer_id == _cust(customer_id),
            DailyUsage.kind == kind,
            DailyUsage.key == key,
            DailyUsage.day >= w,
            DailyUsage.day <= sunday,
        )
    )
    return max(stored, int(value or 0))


def reap_expired_reservations(session: Session, *, now: datetime | None = None) -> int:
    current = now or _utcnow()
    result = session.execute(
        update(PaperSlipReservation)
        .where(
            PaperSlipReservation.status == "reserved",
            PaperSlipReservation.expires_at <= current,
        )
        .values(status="expired", updated_at=current, released_at=current)
    )
    return int(result.rowcount or 0)


def recently_used_cliplet_ids(
    session: Session,
    *,
    customer_id: int | None = None,
    limit: int = ROLLING_CLIPLET_WINDOW,
) -> set[int]:
    """最近 ready 切片用量中的 cliplet id（近窗避重；仅 committed / 日账本）。"""
    lim = max(0, int(limit))
    if lim <= 0:
        return set()
    cid = _cust(customer_id)
    out: set[int] = set()

    stmt = (
        select(PaperSlipReservation.key)
        .where(
            PaperSlipReservation.kind == KIND_CLIPLET,
            PaperSlipReservation.status == "committed",
            PaperSlipReservation.customer_id == cid,
        )
        .order_by(PaperSlipReservation.id.desc())
        .limit(lim * 4)
    )
    for raw in session.scalars(stmt).all():
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
        if len(out) >= lim:
            return out

    # Fallback: ready daily ledger (installs / tests without reservation rows)
    dstmt = (
        select(DailyUsage.key)
        .where(
            DailyUsage.kind == KIND_CLIPLET,
            DailyUsage.count > 0,
            DailyUsage.customer_id == cid,
        )
        .order_by(DailyUsage.updated_at.desc(), DailyUsage.id.desc())
        .limit(lim * 3)
    )
    for raw in session.scalars(dstmt).all():
        try:
            out.add(int(raw))
        except (TypeError, ValueError):
            continue
        if len(out) >= lim:
            break
    return out


def recently_used_phrase_keys(
    session: Session,
    *,
    customer_id: int | None = None,
    limit: int = ROLLING_PHRASE_WINDOW,
) -> set[str]:
    """最近 ready 提交的 phrase keys + 近期标题词（近窗避重）。"""
    lim = max(0, int(limit))
    if lim <= 0:
        return set()
    cid = _cust(customer_id)
    out: set[str] = set()
    stmt = (
        select(PaperSlipReservation.key)
        .where(
            PaperSlipReservation.kind == KIND_PHRASE,
            PaperSlipReservation.status == "committed",
            PaperSlipReservation.customer_id == cid,
        )
        .order_by(PaperSlipReservation.id.desc())
        .limit(lim * 4)
    )
    for key in session.scalars(stmt).all():
        k = str(key or "").strip()
        if k:
            out.add(k)
        if len(out) >= lim:
            return out
    # Fallback / supplement: keyword_usage titles
    kstmt = (
        select(KeywordUsage.keyword)
        .where(KeywordUsage.customer_id == cid)
        .order_by(KeywordUsage.id.desc())
        .limit(lim * 2)
    )
    for kw in session.scalars(kstmt).all():
        for key in phrase_keys_from_text(str(kw or "")):
            out.add(key)
            if len(out) >= lim:
                return out
    # Fallback: ready daily ledger for phrases
    dstmt = (
        select(DailyUsage.key)
        .where(
            DailyUsage.kind == KIND_PHRASE,
            DailyUsage.count > 0,
            DailyUsage.customer_id == cid,
        )
        .order_by(DailyUsage.updated_at.desc(), DailyUsage.id.desc())
        .limit(lim * 3)
    )
    for key in session.scalars(dstmt).all():
        k = str(key or "").strip()
        if k:
            out.add(k)
        if len(out) >= lim:
            break
    return out


def rolling_exclude_steps(*, kind: str) -> tuple[int, ...]:
    if kind == KIND_CLIPLET:
        return ROLLING_CLIPLET_STEPS
    return ROLLING_PHRASE_STEPS


def keys_at_daily_cap(
    session: Session,
    kind: str,
    *,
    customer_id: int | None = None,
    day: str | None = None,
    max_uses: int | None = None,
    exclude_reservation_key: str | None = None,
    limit: int | None = None,
) -> set[str]:
    """兼容旧名：返回近窗已用 key（不再表示日/周满额）。"""
    del day, max_uses, exclude_reservation_key  # unused under rolling diversity
    if kind == KIND_CLIPLET:
        ids = recently_used_cliplet_ids(
            session,
            customer_id=customer_id,
            limit=limit if limit is not None else ROLLING_CLIPLET_WINDOW,
        )
        return {str(i) for i in ids}
    return recently_used_phrase_keys(
        session,
        customer_id=customer_id,
        limit=limit if limit is not None else ROLLING_PHRASE_WINDOW,
    )


def cliplet_ids_at_daily_cap(
    session: Session,
    *,
    customer_id: int | None = None,
    day: str | None = None,
    exclude_reservation_key: str | None = None,
    limit: int | None = None,
) -> set[int]:
    del day, exclude_reservation_key
    return recently_used_cliplet_ids(
        session,
        customer_id=customer_id,
        limit=limit if limit is not None else ROLLING_CLIPLET_WINDOW,
    )


def phrases_at_daily_cap(
    session: Session,
    *,
    customer_id: int | None = None,
    day: str | None = None,
    exclude_reservation_key: str | None = None,
    limit: int | None = None,
) -> set[str]:
    del day, exclude_reservation_key
    return recently_used_phrase_keys(
        session,
        customer_id=customer_id,
        limit=limit if limit is not None else ROLLING_PHRASE_WINDOW,
    )


def phrase_is_blocked(text: str, blocked: set[str]) -> bool:
    if not blocked:
        return False
    for k in phrase_keys_from_text(text):
        if k in blocked:
            return True
    return False


def paper_slip_block_reasons(
    session: Session,
    *,
    cliplet_ids: list[int] | None = None,
    phrases: list[str] | None = None,
    title: str | None = None,
    customer_id: int | None = None,
    day: str | None = None,
    exclude_reservation_key: str | None = None,
) -> list[str]:
    """满额硬拒已废止：最终核验不再因近窗产生 block_reasons。"""
    del session, cliplet_ids, phrases, title, customer_id, day, exclude_reservation_key
    return []


def quota_windows(
    session: Session,
    kind: str,
    key: str,
    *,
    customer_id: int | None = None,
    day: str | None = None,
    week: str | None = None,
    exclude_reservation_key: str | None = None,
) -> dict[str, Any]:
    """兼容旧调用：永远不报告 blocked_windows。"""
    del exclude_reservation_key
    d = day or local_day()
    w = week or local_week(day=d)
    cid = _cust(customer_id)
    return {
        "day": d,
        "week": w,
        "day_count": get_daily_count(session, kind, key, customer_id=cid, day=d),
        "week_count": get_weekly_count(session, kind, key, customer_id=cid, week=w),
        "day_reserved": 0,
        "week_reserved": 0,
        "blocked_windows": [],
    }


def bump_daily(
    session: Session,
    kind: str,
    key: str,
    *,
    customer_id: int | None = None,
    day: str | None = None,
    n: int = 1,
) -> int:
    """Ready 账本 +n；无次数上限。"""
    if not key or n <= 0:
        return get_daily_count(session, kind, key, customer_id=customer_id, day=day)
    d = day or local_day()
    cid = _cust(customer_id)
    now = datetime.now(_LOCAL_TZ).astimezone().replace(tzinfo=None)
    stmt = (
        update(DailyUsage)
        .where(
            DailyUsage.customer_id == cid,
            DailyUsage.kind == kind,
            DailyUsage.key == key,
            DailyUsage.day == d,
        )
        .values(count=DailyUsage.count + int(n), updated_at=now)
    )
    if session.execute(stmt).rowcount:
        return get_daily_count(session, kind, key, customer_id=cid, day=d)
    session.add(
        DailyUsage(customer_id=cid, kind=kind, key=key, day=d, count=int(n), updated_at=now)
    )
    session.flush()
    return int(n)


def _bump_weekly(
    session: Session,
    kind: str,
    key: str,
    *,
    customer_id: int,
    week: str,
    n: int = 1,
) -> int:
    now = _utcnow()
    stmt = (
        update(WeeklyUsage)
        .where(
            WeeklyUsage.customer_id == customer_id,
            WeeklyUsage.kind == kind,
            WeeklyUsage.key == key,
            WeeklyUsage.week == week,
        )
        .values(count=WeeklyUsage.count + n, updated_at=now)
    )
    if session.execute(stmt).rowcount:
        return get_weekly_count(session, kind, key, customer_id=customer_id, week=week)
    session.add(
        WeeklyUsage(
            customer_id=customer_id,
            kind=kind,
            key=key,
            week=week,
            count=n,
            updated_at=now,
        )
    )
    session.flush()
    return get_weekly_count(session, kind, key, customer_id=customer_id, week=week)


def _usage_items(
    *,
    cliplet_ids: list[int] | None = None,
    phrases: list[str] | None = None,
    title: str | None = None,
) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for cliplet_id in cliplet_ids or []:
        if cliplet_id:
            items.append((KIND_CLIPLET, str(int(cliplet_id))))
    for phrase in [*(phrases or []), *([title] if title else [])]:
        items.extend((KIND_PHRASE, key) for key in phrase_keys_from_text(phrase))
    return list(dict.fromkeys(items))


def reserve_paper_slip(
    session: Session,
    *,
    reservation_key: str,
    job_id: int,
    cliplet_ids: list[int] | None = None,
    phrases: list[str] | None = None,
    title: str | None = None,
    customer_id: int | None = None,
    day: str | None = None,
    now: datetime | None = None,
    ttl_seconds: int = RESERVATION_TTL_SECONDS,
) -> dict[str, Any]:
    """并发租约（不再因日/周满额拒绝）。"""
    current = _utcnow() if now is None else (
        now.replace(tzinfo=None) if now.tzinfo else now
    )
    d = day or local_day(now)
    w = local_week(day=d)
    cid = _cust(customer_id)
    reap_expired_reservations(session, now=current)
    items = _usage_items(cliplet_ids=cliplet_ids, phrases=phrases, title=title)
    # Drop terminal rows first so a prior skip/release cannot trip "内容不一致"
    # when the same reservation_key is reused with a newly planned clip set.
    session.execute(
        delete(PaperSlipReservation).where(
            PaperSlipReservation.reservation_key == reservation_key,
            PaperSlipReservation.status.in_(("released", "expired")),
        )
    )
    session.flush()
    rows = list(
        session.scalars(
            select(PaperSlipReservation).where(
                PaperSlipReservation.reservation_key == reservation_key
            )
        ).all()
    )
    existing_items = {(row.kind, row.key) for row in rows}
    if rows and existing_items != set(items):
        raise ValueError("纸片规则：同一 reservation_key 的预留内容不一致")
    if rows and all(row.status == "committed" for row in rows):
        return {"reservation_key": reservation_key, "status": "committed", "day": d, "week": w}
    if rows and all(row.status == "reserved" and row.expires_at > current for row in rows):
        return {"reservation_key": reservation_key, "status": "reserved", "day": d, "week": w}
    if rows:
        session.execute(
            delete(PaperSlipReservation).where(
                PaperSlipReservation.reservation_key == reservation_key,
                PaperSlipReservation.status.in_(("released", "expired")),
            )
        )
        session.flush()
    expires = current + timedelta(seconds=max(60, int(ttl_seconds)))
    for kind, key in items:
        session.add(
            PaperSlipReservation(
                reservation_key=reservation_key,
                job_id=int(job_id),
                customer_id=cid,
                kind=kind,
                key=key,
                day=d,
                week=w,
                status="reserved",
                expires_at=expires,
                created_at=current,
                updated_at=current,
            )
        )
        session.flush()
    return {
        "reservation_key": reservation_key,
        "status": "reserved",
        "day": d,
        "week": w,
        "expires_at": expires.isoformat(),
        "items": [{"kind": kind, "key": key} for kind, key in items],
    }


def assert_reservation_active(
    session: Session,
    reservation_key: str,
    *,
    customer_id: int | None = None,
    stage: str,
) -> dict[str, Any]:
    reap_expired_reservations(session)
    rows = list(
        session.scalars(
            select(PaperSlipReservation).where(
                PaperSlipReservation.reservation_key == reservation_key,
                PaperSlipReservation.customer_id == _cust(customer_id),
            )
        ).all()
    )
    if not rows:
        raise ValueError(f"纸片规则：{stage} 核验失败，预留缺失")
    if any(row.status != "reserved" or row.expires_at <= _utcnow() for row in rows):
        raise ValueError(
            f"纸片规则：{stage} 核验失败，预留已释放或过期"
            f"（day={rows[0].day} week={rows[0].week}）"
        )
    return {"stage": stage, "day": rows[0].day, "week": rows[0].week, "items": len(rows)}


def release_paper_slip(
    session: Session,
    reservation_key: str,
    *,
    customer_id: int | None = None,
) -> int:
    now = _utcnow()
    result = session.execute(
        update(PaperSlipReservation)
        .where(
            PaperSlipReservation.reservation_key == reservation_key,
            PaperSlipReservation.customer_id == _cust(customer_id),
            PaperSlipReservation.status == "reserved",
        )
        .values(status="released", released_at=now, updated_at=now)
    )
    return int(result.rowcount or 0)


def release_job_reservations(session: Session, job_id: int) -> int:
    now = _utcnow()
    result = session.execute(
        update(PaperSlipReservation)
        .where(
            PaperSlipReservation.job_id == int(job_id),
            PaperSlipReservation.status == "reserved",
        )
        .values(status="released", released_at=now, updated_at=now)
    )
    return int(result.rowcount or 0)


def release_committed_paper_slip_for_job(
    session: Session,
    job_id: int,
    *,
    customer_id: int | None = None,
) -> int:
    """On reject: free committed paper-slip keys for this job (rolling window)."""
    now = _utcnow()
    stmt = (
        update(PaperSlipReservation)
        .where(
            PaperSlipReservation.job_id == int(job_id),
            PaperSlipReservation.status == "committed",
        )
        .values(status="released", released_at=now, updated_at=now)
    )
    if customer_id is not None:
        stmt = stmt.where(PaperSlipReservation.customer_id == _cust(customer_id))
    result = session.execute(stmt)
    # Also drop any still-reserved rows for the same job.
    release_job_reservations(session, int(job_id))
    return int(result.rowcount or 0)


def release_reservations_for_inactive_jobs(session: Session) -> int:
    from engine.catalog.db import Job

    now = _utcnow()
    inactive_ids = list(
        session.scalars(select(Job.id).where(Job.status.notin_(("queued", "running")))).all()
    )
    if not inactive_ids:
        return 0
    result = session.execute(
        update(PaperSlipReservation)
        .where(
            PaperSlipReservation.status == "reserved",
            PaperSlipReservation.job_id.in_(inactive_ids),
        )
        .values(status="released", released_at=now, updated_at=now)
    )
    return int(result.rowcount or 0)


def commit_paper_slip_for_ready(
    session: Session,
    *,
    reservation_key: str | None = None,
    job_id: int | None = None,
    cliplet_ids: list[int] | None = None,
    phrases: list[str] | None = None,
    title: str | None = None,
    customer_id: int | None = None,
    day: str | None = None,
) -> dict[str, Any]:
    """Ready 记账（无限额）。"""
    d = day or local_day()
    w = local_week(day=d)
    cid = _cust(customer_id)
    if reservation_key:
        rows = list(
            session.scalars(
                select(PaperSlipReservation).where(
                    PaperSlipReservation.reservation_key == reservation_key,
                    PaperSlipReservation.customer_id == cid,
                )
            ).all()
        )
        if rows and all(row.status == "committed" for row in rows):
            return {
                "reservation_key": reservation_key,
                "status": "committed",
                "idempotent": True,
                "day": rows[0].day,
                "week": rows[0].week,
            }
        assert_reservation_active(
            session, reservation_key, customer_id=cid, stage="READY_GATE前"
        )
        d, w = rows[0].day, rows[0].week
        items = [(row.kind, row.key) for row in rows]
        now = _utcnow()
        for row in rows:
            row.status = "committed"
            row.committed_at = now
            row.updated_at = now
        session.flush()
    else:
        items = _usage_items(cliplet_ids=cliplet_ids, phrases=phrases, title=title)
    bumped_c: list[str] = []
    bumped_p: list[str] = []
    for kind, key in items:
        bump_daily(session, kind, key, customer_id=cid, day=d)
        _bump_weekly(session, kind, key, customer_id=cid, week=w)
        (bumped_c if kind == KIND_CLIPLET else bumped_p).append(key)
    return {
        "reservation_key": reservation_key,
        "job_id": job_id,
        "status": "committed",
        "day": d,
        "week": w,
        "rolling_cliplet_window": ROLLING_CLIPLET_WINDOW,
        "rolling_phrase_window": ROLLING_PHRASE_WINDOW,
        "cliplets": bumped_c,
        "phrases": bumped_p,
    }


def assert_paper_slip_integrity() -> None:
    assert ROLLING_CLIPLET_WINDOW == 20
    assert ROLLING_PHRASE_WINDOW == 15
    assert ROLLING_CLIPLET_STEPS == (20, 10, 0)
    assert ROLLING_PHRASE_STEPS == (15, 8, 0)
    assert KIND_CLIPLET == "cliplet"
    assert KIND_PHRASE == "phrase"
