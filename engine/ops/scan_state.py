"""Shared scan progress state (avoid circular imports between api and scheduler)."""

from __future__ import annotations

from typing import Any

scan_state: dict[str, Any] = {
    "running": False,
    "ingested": 0,
    "attempted": 0,
    "limit": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
