"""纸片规则（PAPER SLIP）— 本地日历日 cliplet / 词句最多使用 2 次。

权威：docs/PAPER_SLIP_LOCK.md
仅 ready 成片成功后 +1；failed/review 不计。不可配置放宽。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from engine.catalog.db import DailyUsage

# HARD — 不可配置放宽
MAX_DAILY_USES = 2
KIND_CLIPLET = "cliplet"
KIND_PHRASE = "phrase"
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def local_day(now: datetime | None = None) -> str:
    """本地日历日 YYYY-MM-DD（Asia/Shanghai）。跨日自动换 key，无需清表。"""
    dt = now or datetime.now(_LOCAL_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LOCAL_TZ)
    else:
        dt = dt.astimezone(_LOCAL_TZ)
    return dt.strftime("%Y-%m-%d")


def normalize_phrase_key(text: str) -> str:
    """规范化短语 key：去空白/标点，行用 | 连接。"""
    from engine.pack.text_sanitize import strip_all_punctuation

    raw = strip_all_punctuation(str(text or ""), keep_newlines=True)
    lines = [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]
    return "|".join(lines)


def phrase_keys_from_text(text: str) -> list[str]:
    """标题/短句 → 全句 key + 各行 key（去重保序）。"""
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


def keys_at_daily_cap(
    session: Session,
    kind: str,
    *,
    customer_id: int | None = None,
    day: str | None = None,
    max_uses: int = MAX_DAILY_USES,
) -> set[str]:
    """今日已达上限的 key 集合（禁止再选）。"""
    d = day or local_day()
    stmt = select(DailyUsage.key).where(
        DailyUsage.customer_id == _cust(customer_id),
        DailyUsage.kind == kind,
        DailyUsage.day == d,
        DailyUsage.count >= int(max_uses),
    )
    return {str(k) for k in session.scalars(stmt).all() if k}


def cliplet_ids_at_daily_cap(
    session: Session,
    *,
    customer_id: int | None = None,
    day: str | None = None,
) -> set[int]:
    keys = keys_at_daily_cap(session, KIND_CLIPLET, customer_id=customer_id, day=day)
    out: set[int] = set()
    for k in keys:
        try:
            out.add(int(k))
        except (TypeError, ValueError):
            continue
    return out


def phrases_at_daily_cap(
    session: Session,
    *,
    customer_id: int | None = None,
    day: str | None = None,
) -> set[str]:
    return keys_at_daily_cap(session, KIND_PHRASE, customer_id=customer_id, day=day)


def phrase_is_blocked(text: str, blocked: set[str]) -> bool:
    if not blocked:
        return False
    for k in phrase_keys_from_text(text):
        if k in blocked:
            return True
    return False


def bump_daily(
    session: Session,
    kind: str,
    key: str,
    *,
    customer_id: int | None = None,
    day: str | None = None,
    n: int = 1,
) -> int:
    """Increment only while under the immutable daily cap; otherwise reject."""
    if not key or n <= 0:
        return get_daily_count(session, kind, key, customer_id=customer_id, day=day)
    d = day or local_day()
    cid = _cust(customer_id)
    now = datetime.now(_LOCAL_TZ).astimezone().replace(tzinfo=None)
    if n > MAX_DAILY_USES:
        raise ValueError(f"纸片规则：单次使用数不得超过 {MAX_DAILY_USES}")
    stmt = (
        update(DailyUsage)
        .where(
            DailyUsage.customer_id == cid,
            DailyUsage.kind == kind,
            DailyUsage.key == key,
            DailyUsage.day == d,
            DailyUsage.count <= MAX_DAILY_USES - int(n),
        )
        .values(count=DailyUsage.count + int(n), updated_at=now)
    )
    if session.execute(stmt).rowcount:
        return get_daily_count(session, kind, key, customer_id=cid, day=d)

    row = session.scalar(
        select(DailyUsage).where(
            DailyUsage.customer_id == cid,
            DailyUsage.kind == kind,
            DailyUsage.key == key,
            DailyUsage.day == d,
        )
    )
    if row is not None:
        raise ValueError(f"纸片规则：{kind} {key!r} 今日配额已满（最多 {MAX_DAILY_USES} 次）")
    row = DailyUsage(customer_id=cid, kind=kind, key=key, day=d, count=int(n), updated_at=now)
    session.add(row)
    session.flush()
    return int(row.count)


def commit_paper_slip_for_ready(
    session: Session,
    *,
    cliplet_ids: list[int] | None = None,
    phrases: list[str] | None = None,
    title: str | None = None,
    customer_id: int | None = None,
    day: str | None = None,
) -> dict[str, Any]:
    """仅在成片 state=ready 后调用：为用到的 cliplet / 词句 +1。"""
    d = day or local_day()
    bumped_c: list[str] = []
    bumped_p: list[str] = []
    for cid in cliplet_ids or []:
        if not cid:
            continue
        k = str(int(cid))
        bump_daily(session, KIND_CLIPLET, k, customer_id=customer_id, day=d)
        bumped_c.append(k)
    keys: list[str] = []
    for p in phrases or []:
        keys.extend(phrase_keys_from_text(p))
    if title:
        keys.extend(phrase_keys_from_text(title))
    seen: set[str] = set()
    for k in keys:
        if not k or k in seen:
            continue
        seen.add(k)
        bump_daily(session, KIND_PHRASE, k, customer_id=customer_id, day=d)
        bumped_p.append(k)
    return {
        "day": d,
        "max_daily_uses": MAX_DAILY_USES,
        "cliplets": bumped_c,
        "phrases": bumped_p,
    }


def assert_paper_slip_integrity() -> None:
    assert MAX_DAILY_USES == 2
    assert KIND_CLIPLET == "cliplet"
    assert KIND_PHRASE == "phrase"
