"""Render-output identity: customer display_no (#001) + optional SY- serial."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.catalog.db import Job, RenderOutput


def format_display_no(n: int | None) -> str:
    """Customer-facing sole UID, e.g. #001."""
    if n is None or int(n) < 1:
        return ""
    value = int(n)
    width = 3 if value < 1000 else len(str(value))
    return f"#{value:0{width}d}"


def allocate_display_no(
    session: Session,
    *,
    customer_id: int | None,
) -> int:
    """Next monotonic display number in the customer domain (first output → 1)."""
    stmt = select(func.max(RenderOutput.display_no)).select_from(RenderOutput)
    if customer_id is not None:
        stmt = stmt.join(Job, RenderOutput.job_id == Job.id).where(
            Job.customer_id == int(customer_id)
        )
    else:
        stmt = stmt.where(RenderOutput.job_id.is_(None))
    current = session.scalar(stmt)
    try:
        return int(current or 0) + 1
    except (TypeError, ValueError):
        return 1


def backfill_display_nos(session: Session, *, customer_id: int | None = None) -> int:
    """Assign display_no by created_at for rows still null. Returns rows updated."""
    stmt = (
        select(RenderOutput)
        .where(RenderOutput.display_no.is_(None))
        .order_by(RenderOutput.created_at.asc(), RenderOutput.id.asc())
    )
    if customer_id is not None:
        stmt = stmt.join(Job, RenderOutput.job_id == Job.id).where(
            Job.customer_id == int(customer_id)
        )
    rows = list(session.scalars(stmt).all())
    if not rows:
        return 0
    # Group by customer so each customer domain starts at 1 independently.
    by_customer: dict[int | None, list[RenderOutput]] = {}
    for row in rows:
        job = session.get(Job, row.job_id) if row.job_id else None
        cid = int(job.customer_id) if job and job.customer_id is not None else None
        by_customer.setdefault(cid, []).append(row)
    updated = 0
    for cid, group in by_customer.items():
        max_stmt = select(func.max(RenderOutput.display_no)).select_from(RenderOutput)
        if cid is not None:
            max_stmt = max_stmt.join(Job, RenderOutput.job_id == Job.id).where(
                Job.customer_id == cid
            )
        else:
            max_stmt = max_stmt.where(RenderOutput.job_id.is_(None))
        start = int(session.scalar(max_stmt) or 0)
        for i, row in enumerate(group, start=start + 1):
            row.display_no = i
            updated += 1
    if updated:
        session.commit()
    return updated

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SAFE_RE = re.compile(r"[^a-z0-9]+")


def sanitize_customer_key(raw: str) -> str:
    """ASCII-safe slug for serial prefix; Chinese-only names fall back to hashed key."""
    text = str(raw or "").strip().lower()
    slug = _SAFE_RE.sub("-", text).strip("-")
    if slug and re.search(r"[a-z0-9]", slug):
        return slug[:64]
    digest = hashlib.sha1(str(raw or "customer").encode("utf-8")).hexdigest()[:10]
    return f"c{digest}"


def resolve_customer_key(
    *,
    customer_name: str | None = None,
    pack_data: dict[str, Any] | None = None,
    profile_json: dict[str, Any] | None = None,
) -> str:
    """Prefer keyword-pack / profile customer_key, else sanitize display name."""
    meta = pack_data.get("meta") if isinstance(pack_data, dict) else None
    if isinstance(meta, dict):
        key = str(meta.get("customer_key") or "").strip()
        if key:
            return sanitize_customer_key(key)
    if isinstance(profile_json, dict):
        key = str(profile_json.get("customer_key") or "").strip()
        if key:
            return sanitize_customer_key(key)
        brand = profile_json.get("brand") if isinstance(profile_json.get("brand"), dict) else {}
        key = str(brand.get("customer_key") or "").strip()
        if key:
            return sanitize_customer_key(key)
    return sanitize_customer_key(customer_name or "customer")


def shanghai_day_token(now: datetime | None = None) -> str:
    dt = now or datetime.now(_SHANGHAI)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_SHANGHAI)
    else:
        dt = dt.astimezone(_SHANGHAI)
    return dt.strftime("%Y%m%d")


def allocate_render_serial(
    session: Session,
    *,
    customer_id: int | None,
    customer_key: str,
    now: datetime | None = None,
) -> str:
    """Allocate next SY-{key}-{yyyyMMdd}-{seq6} in the customer day domain."""
    key = sanitize_customer_key(customer_key)
    day = shanghai_day_token(now)
    prefix = f"SY-{key}-{day}-"
    max_seq = 0
    stmt = select(RenderOutput.serial).where(
        RenderOutput.serial.is_not(None),
        RenderOutput.serial.like(f"{prefix}%"),
    )
    if customer_id is not None:
        stmt = stmt.join(Job, RenderOutput.job_id == Job.id).where(Job.customer_id == customer_id)
    for value in session.scalars(stmt).all():
        text = str(value or "")
        if not text.startswith(prefix):
            continue
        tail = text[len(prefix) :]
        try:
            max_seq = max(max_seq, int(tail))
        except ValueError:
            continue
    return f"{prefix}{max_seq + 1:06d}"
