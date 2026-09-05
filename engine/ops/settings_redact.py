"""Sanitize settings payloads returned to the App."""

from __future__ import annotations

from typing import Any


def redact_settings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop secrets from settings API responses (legacy cursor key + future secrets)."""
    out = dict(payload)
    raw = out.pop("cursor_api_key", None)
    value = raw.strip() if isinstance(raw, str) else ""
    out["cursor_api_key_configured"] = bool(value)
    if not value:
        out["cursor_api_key_hint"] = ""
    elif len(value) <= 8:
        out["cursor_api_key_hint"] = "••••"
    else:
        out["cursor_api_key_hint"] = f"…{value[-4:]}"
    return out
