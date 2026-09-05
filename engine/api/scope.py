"""Shared active-customer scope helpers for API routers."""

from __future__ import annotations

from fastapi import HTTPException

from engine.catalog.customer_scope import (
    require_active_customer,
    resolve_customer,
    settings_with_customer_paths,
)
from engine.config.settings import AppSettings, load_settings


def active_scope(session, settings: AppSettings | None = None):
    settings = settings or load_settings()
    customer = require_active_customer(session, settings)
    scoped = settings_with_customer_paths(settings, customer)
    return settings, customer, scoped


def resolve_job_customer(session, settings: AppSettings, customer_name: str | None):
    if customer_name:
        row = resolve_customer(session, customer_name)
        if not row:
            raise HTTPException(404, f"客户不存在: {customer_name}")
        return row
    return require_active_customer(session, settings)
