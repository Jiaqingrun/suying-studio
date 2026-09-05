"""Process-wide boot phase: listen early, finish services in background."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.RLock()
# uninitialized until lifespan mark_starting(); avoid permanent "starting" under TestClient without CM.
_phase = "uninitialized"  # uninitialized | starting | ready | failed | blocked
_error: str | None = None
_started_at = time.time()
_ready_at: float | None = None
_shutdown = False


def reset_for_tests() -> None:
    global _phase, _error, _started_at, _ready_at, _shutdown
    with _lock:
        _phase = "uninitialized"
        _error = None
        _started_at = time.time()
        _ready_at = None
        _shutdown = False


def mark_starting() -> None:
    global _phase, _error, _started_at, _ready_at
    with _lock:
        _phase = "starting"
        _error = None
        _started_at = time.time()
        _ready_at = None


def mark_ready() -> None:
    global _phase, _error, _ready_at
    with _lock:
        _phase = "ready"
        _error = None
        _ready_at = time.time()


def mark_blocked(reason: str = "") -> None:
    """Control-plane only (e.g. workspace missing). HTTP control plane is up."""
    global _phase, _error, _ready_at
    with _lock:
        _phase = "blocked"
        _error = reason or None
        _ready_at = time.time()


def mark_failed(reason: str) -> None:
    global _phase, _error, _ready_at
    with _lock:
        _phase = "failed"
        _error = reason
        _ready_at = time.time()


def request_shutdown() -> None:
    global _shutdown
    with _lock:
        _shutdown = True


def is_shutting_down() -> bool:
    with _lock:
        return _shutdown


def is_services_ready() -> bool:
    with _lock:
        return _phase in ("ready", "blocked")


def phase() -> str:
    with _lock:
        return _phase


def snapshot() -> dict[str, Any]:
    with _lock:
        elapsed = time.time() - _started_at
        return {
            "boot_phase": _phase,
            "boot_error": _error,
            "boot_elapsed_sec": round(elapsed, 3),
            "services_boot_complete": _phase in ("ready", "blocked", "failed"),
            "started_at_epoch": _started_at,
            "ready_at_epoch": _ready_at,
        }
