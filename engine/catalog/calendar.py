from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import CalendarEntry, Customer
from engine.template.engine import template_for_theme


class CalendarEntryIn(BaseModel):
    day: str  # YYYY-MM-DD
    theme: str = "default"
    category: str = "default"
    customer_name: str = ""
    template_name: str = "default-vertical"
    quota: int = Field(default=5, ge=1, le=100)
    note: str = ""
    active: bool = True


def _parse_day(day: str) -> str:
    date.fromisoformat(day)  # validate
    return day


def entry_to_dict(e: CalendarEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "customer_id": e.customer_id,
        "day": e.day,
        "theme": e.theme,
        "category": e.category,
        "customer_name": e.customer_name,
        "template_name": e.template_name,
        "quota": e.quota,
        "note": e.note,
        "active": e.active,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


def list_entries(
    session: Session,
    customer_id: int,
    from_day: str | None = None,
    to_day: str | None = None,
) -> list[CalendarEntry]:
    q = (
        select(CalendarEntry)
        .where(CalendarEntry.customer_id == customer_id)
        .order_by(CalendarEntry.day.asc())
    )
    if from_day:
        q = q.where(CalendarEntry.day >= from_day)
    if to_day:
        q = q.where(CalendarEntry.day <= to_day)
    return list(session.scalars(q).all())


def get_entry(session: Session, customer_id: int, day: str) -> CalendarEntry | None:
    day = _parse_day(day)
    return session.scalar(
        select(CalendarEntry).where(CalendarEntry.customer_id == customer_id, CalendarEntry.day == day)
    )


def upsert_entry(session: Session, customer_id: int, body: CalendarEntryIn) -> CalendarEntry:
    day = _parse_day(body.day)
    existing = get_entry(session, customer_id, day)
    now = datetime.now(timezone.utc)
    cust = session.get(Customer, customer_id)
    customer_name = cust.name if cust else body.customer_name
    if existing:
        existing.theme = body.theme
        existing.category = body.category
        existing.customer_name = customer_name
        existing.template_name = body.template_name
        existing.quota = body.quota
        existing.note = body.note
        existing.active = body.active
        existing.updated_at = now
        session.commit()
        session.refresh(existing)
        return existing
    row = CalendarEntry(
        customer_id=customer_id,
        day=day,
        theme=body.theme,
        category=body.category,
        customer_name=customer_name,
        template_name=body.template_name,
        quota=body.quota,
        note=body.note,
        active=body.active,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_entry(session: Session, customer_id: int, day: str) -> bool:
    row = get_entry(session, customer_id, day)
    if not row:
        return False
    session.delete(row)
    session.commit()
    return True


def today_plan(session: Session, customer_id: int, day: str | None = None) -> CalendarEntry | None:
    d = day or date.today().isoformat()
    row = get_entry(session, customer_id, d)
    if row and not row.active:
        return None
    return row


def seed_week_if_empty(session: Session, customer_id: int, customer_name: str = "") -> int:
    """Seed a demo week of calendar entries if none exist for this customer."""
    from engine.catalog.industry_pack import load_industry_pack, pack_id_for_customer

    existing = session.scalar(
        select(CalendarEntry.id).where(CalendarEntry.customer_id == customer_id).limit(1)
    )
    if existing:
        return 0
    cust = session.get(Customer, customer_id)
    pack_id = pack_id_for_customer(customer_name or (cust.name if cust else ""), cust.profile_json if cust else None)
    configured = [
        str(x).strip()
        for x in (load_industry_pack(pack_id).get("content_themes") or ["default"])
        if str(x).strip()
    ] or ["default"]
    themes = [
        (configured[i % len(configured)], f"行业包日历 · 第 {i + 1} 天")
        for i in range(7)
    ]
    created = 0
    for i, (theme, note) in enumerate(themes):
        day = (date.today() + timedelta(days=i)).isoformat()
        upsert_entry(
            session,
            customer_id,
            CalendarEntryIn(
                day=day,
                theme=theme,
                category="default",
                customer_name=customer_name or (cust.name if cust else ""),
                template_name=template_for_theme(theme, pack_id=pack_id),
                quota=5,
                note=note,
            ),
        )
        created += 1
    return created
