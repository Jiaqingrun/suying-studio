"""Backward-compatible re-export of entitlement evaluation."""

from __future__ import annotations

from engine.security.entitlement import TRIAL_DAYS, entitlement

__all__ = ["TRIAL_DAYS", "entitlement"]
