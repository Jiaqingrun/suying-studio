"""Pause vectorization while produce / review / publish hold workload slots.

GCustomerUX: vectorization yields automatically and resumes when idle.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger("montage.workload_yield")

_lock = threading.RLock()
_holders: set[str] = set()
_paused_by_us = False


def _snapshot() -> dict[str, Any]:
    return {
        "holders": sorted(_holders),
        "yielding": bool(_holders),
        "paused_by_workload": _paused_by_us,
    }


def acquire_workload(token: str) -> dict[str, Any]:
    """Call when produce/review/publish starts. Pauses vectorization if needed."""
    global _paused_by_us
    with _lock:
        _holders.add(str(token))
        first = len(_holders) == 1
    if first:
        try:
            from engine.catalog.vectorization_runtime import executor

            snap = executor.pause(owner="workload", timeout=1.5)
            with _lock:
                _paused_by_us = True
            log.info("vectorization yielded to workload token=%s status=%s", token, snap.get("status"))
        except Exception:
            log.exception("vectorization yield pause failed")
    return _snapshot()


def release_workload(token: str) -> dict[str, Any]:
    """Call when produce/review/publish ends. Resumes vectorization when no holders."""
    global _paused_by_us
    with _lock:
        _holders.discard(str(token))
        empty = not _holders
        should_resume = empty and _paused_by_us
        if empty:
            _paused_by_us = False
    if should_resume:
        try:
            from engine.catalog.vectorization_runtime import executor

            snap = executor.resume(owner="workload")
            log.info("vectorization resumed after workload status=%s", snap.get("status"))
        except Exception:
            log.exception("vectorization yield resume failed")
    return _snapshot()


def status() -> dict[str, Any]:
    with _lock:
        return _snapshot()
