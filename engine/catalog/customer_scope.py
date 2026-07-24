from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Customer
from engine.config.settings import AppSettings, PathConfig

# Product default: empty → require explicit active_customer or first active row.
# Never pin a real paying customer name here (see docs/DEV_LOCK.md G2).
DEFAULT_CUSTOMER = ""
DEMO_CUSTOMER = "演示客户"


def customer_to_dict(c: Customer) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "library_root": c.library_root,
        "library_roots": c.library_roots or [],
        "output_root": c.output_root,
        "keyword_pack_path": c.keyword_pack_path,
        "active": c.active,
        "profile": c.profile_json or {},
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def list_customers(session: Session) -> list[Customer]:
    return list(session.scalars(select(Customer).where(Customer.active.is_(True)).order_by(Customer.id)).all())


def resolve_customer(session: Session, name_or_id: str | int | None) -> Customer | None:
    if name_or_id is None:
        return None
    if isinstance(name_or_id, int):
        return session.get(Customer, name_or_id)
    return session.scalar(select(Customer).where(Customer.name == name_or_id))


def get_or_create_customer(
    session: Session,
    name: str,
    *,
    library_root: str | None = None,
    library_roots: list[str] | None = None,
    output_root: str | None = None,
    profile_json: dict[str, Any] | None = None,
) -> Customer:
    row = session.scalar(select(Customer).where(Customer.name == name))
    if row:
        if library_root and not row.library_root:
            row.library_root = library_root
        if library_roots is not None and not row.library_roots:
            row.library_roots = library_roots
        if output_root and not row.output_root:
            row.output_root = output_root
        if profile_json:
            row.profile_json = profile_json
        session.commit()
        session.refresh(row)
        return row
    row = Customer(
        name=name,
        library_root=library_root,
        library_roots=library_roots or [],
        output_root=output_root,
        profile_json=profile_json or {},
        active=True,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def require_active_customer(session: Session, settings: AppSettings) -> Customer:
    name = (settings.active_customer or "").strip()
    if name:
        row = session.scalar(select(Customer).where(Customer.name == name))
        if row:
            return row
        return get_or_create_customer(
            session,
            name,
            library_root=str(settings.paths.library_root),
            library_roots=[str(p) for p in settings.paths.library_roots],
            output_root=str(settings.paths.output_root),
        )
    # No active name: prefer any existing active customer (stable), else demo fixture
    existing = list_customers(session)
    if existing:
        return existing[0]
    return get_or_create_customer(
        session,
        DEMO_CUSTOMER,
        library_root=str(settings.paths.library_root),
        library_roots=[str(p) for p in settings.paths.library_roots],
        output_root=str(settings.paths.output_root),
        profile_json={"industry_pack": "_blank"},
    )


def effective_paths(settings: AppSettings, customer: Customer) -> PathConfig:
    paths = settings.paths.model_copy(deep=True)
    if customer.library_root:
        paths.library_root = Path(customer.library_root)
    if customer.library_roots:
        paths.library_roots = [Path(p) for p in customer.library_roots]
    if customer.output_root:
        paths.output_root = Path(customer.output_root)
    return paths


def settings_with_customer_paths(settings: AppSettings, customer: Customer) -> AppSettings:
    s = settings.model_copy(deep=True)
    s.paths = effective_paths(settings, customer)
    return s
