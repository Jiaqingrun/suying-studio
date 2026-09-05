"""Global service handles registered by FastAPI lifespan (avoids circular imports)."""

from __future__ import annotations

from typing import Any

_handles: dict[str, Any] = {}


def register(name: str, obj: Any) -> None:
    _handles[name] = obj


def get(name: str) -> Any:
    return _handles.get(name)


def clear() -> None:
    _handles.clear()
