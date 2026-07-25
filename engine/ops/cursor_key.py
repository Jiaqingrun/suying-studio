"""Cursor API key helpers — store in settings, never echo full key to clients."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def apply_cursor_api_key_env(key: str | None) -> None:
    """Expose key to CURSOR_API_KEY for SDK / subprocesses in this process."""
    value = (key or "").strip()
    if value:
        os.environ["CURSOR_API_KEY"] = value
    elif "CURSOR_API_KEY" in os.environ:
        # Keep process env if settings cleared but operator exported manually.
        pass


def cursor_key_hint(key: str | None) -> str:
    value = (key or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"…{value[-4:]}"


def public_cursor_key_fields(key: str | None) -> dict[str, Any]:
    value = (key or "").strip()
    return {
        "cursor_api_key_configured": bool(value),
        "cursor_api_key_hint": cursor_key_hint(value),
    }


def redact_settings_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip raw key from API responses; add configured + hint fields."""
    out = dict(payload)
    raw = out.pop("cursor_api_key", None)
    out.update(public_cursor_key_fields(raw if isinstance(raw, str) else None))
    return out


def verify_cursor_api_key(key: str) -> dict[str, Any]:
    """Call Cursor Cloud Agents /v1/me to confirm the key works."""
    token = (key or "").strip()
    if not token:
        return {"ok": False, "error": "API Key 为空"}

    req = urllib.request.Request(
        "https://api.cursor.com/v1/me",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "suying-montage-studio/0.4",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data: Any
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {"raw": body[:200]}
            return {
                "ok": True,
                "status": resp.status,
                "me": data if isinstance(data, dict) else {"value": data},
            }
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        return {
            "ok": False,
            "status": e.code,
            "error": f"HTTP {e.code}",
            "detail": detail,
        }
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"网络错误: {e.reason}"}
    except TimeoutError:
        return {"ok": False, "error": "请求超时"}
