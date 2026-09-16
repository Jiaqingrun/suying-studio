"""Job pause / stale-reap ↔ ResourceGate release (PL-01 / PL-15).

Design:
- Cooperative cancel first (in-process Event); holders check the flag and exit.
- Bounded timeout then force-releases *gate tokens only* — never SIGKILL ffmpeg.
- Ollama heavy tokens are kind:uuid (not job:id); cancel_event + lease sweep cover them.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("suying.jobs.gate_release")

# After pause: wait this long for cooperative exit, then drop job:* gate tokens.
PAUSE_GATE_RELEASE_AFTER_SEC = 8.0

_lock = threading.RLock()
_cancel_events: dict[int, threading.Event] = {}
_release_timers: dict[int, threading.Timer] = {}


def job_gate_tokens(job_id: int) -> tuple[str, str]:
    jid = int(job_id)
    return (f"job:{jid}", f"job:{jid}:tts")


def request_job_cancel(job_id: int) -> threading.Event:
    """Mark job for cooperative abort; returns the Event holders should observe."""
    jid = int(job_id)
    with _lock:
        ev = _cancel_events.get(jid)
        if ev is None:
            ev = threading.Event()
            _cancel_events[jid] = ev
        ev.set()
        return ev


def clear_job_cancel(job_id: int) -> None:
    jid = int(job_id)
    with _lock:
        ev = _cancel_events.pop(jid, None)
        if ev is not None:
            ev.clear()
        _cancel_scheduled_unlocked(jid)


def is_job_cancel_requested(job_id: int) -> bool:
    jid = int(job_id)
    with _lock:
        ev = _cancel_events.get(jid)
        return bool(ev is not None and ev.is_set())


def cancel_event_for(job_id: int) -> threading.Event | None:
    """Return existing cancel Event without creating one (worker may OR it)."""
    jid = int(job_id)
    with _lock:
        return _cancel_events.get(jid)


def release_job_gate_tokens(
    job_id: int,
    *,
    reason: str = "gate_release",
    gate: Any | None = None,
) -> dict[str, Any]:
    """Drop ResourceGate holders for this job's known tokens. Idempotent.

    Does **not** terminate ffmpeg / Ollama processes — only frees slot accounting
    so a new job can acquire. Legitimate long ffmpeg should usually exit via
    cooperative cancel before this runs; timeout path unblocks capacity.
    """
    if gate is None:
        from engine.runtime.resource_gate import gate as resource_gate

        gate = resource_gate

    primary, tts = job_gate_tokens(job_id)
    before = _held_slots_for_tokens(gate, (primary, tts))
    try:
        gate.release_all(primary)
        gate.release_all(tts)
    except Exception as exc:  # noqa: BLE001
        log.warning("release_job_gate_tokens failed job=%s: %s", job_id, exc)
    after = _held_slots_for_tokens(gate, (primary, tts))
    released_slots = sorted(set(before) - set(after))
    result = {
        "job_id": int(job_id),
        "reason": reason,
        "tokens": [primary, tts],
        "released_slots": released_slots,
        "still_held_slots": after,
        "at_monotonic": time.monotonic(),
    }
    if released_slots:
        log.info(
            "job gate tokens released job=%s reason=%s slots=%s",
            job_id,
            reason,
            released_slots,
        )
    return result


def schedule_pause_gate_release(
    job_id: int,
    *,
    after_sec: float | None = None,
    gate: Any | None = None,
) -> float:
    """Arm a one-shot timer to force-release job gate tokens after ``after_sec``."""
    jid = int(job_id)
    delay = float(PAUSE_GATE_RELEASE_AFTER_SEC if after_sec is None else after_sec)
    delay = max(0.0, delay)
    if gate is None:
        from engine.runtime.resource_gate import gate as resource_gate

        gate = resource_gate
    target_gate = gate

    def _fire() -> None:
        with _lock:
            _release_timers.pop(jid, None)
        release_job_gate_tokens(jid, reason="pause_timeout", gate=target_gate)

    with _lock:
        _cancel_scheduled_unlocked(jid)
        if delay <= 0.0:
            # Run outside the lock to avoid nesting with gate locks.
            pass
        else:
            timer = threading.Timer(delay, _fire)
            timer.daemon = True
            _release_timers[jid] = timer
            timer.start()
            return delay

    release_job_gate_tokens(jid, reason="pause_immediate", gate=target_gate)
    return 0.0


def cancel_scheduled_gate_release(job_id: int) -> None:
    with _lock:
        _cancel_scheduled_unlocked(int(job_id))


def _cancel_scheduled_unlocked(job_id: int) -> None:
    timer = _release_timers.pop(int(job_id), None)
    if timer is not None:
        try:
            timer.cancel()
        except Exception:  # noqa: BLE001
            pass


def _held_slots_for_tokens(gate: Any, tokens: tuple[str, ...]) -> list[str]:
    held: list[str] = []
    try:
        pools = getattr(gate, "_pools", {}) or {}
        for name, pool in pools.items():
            holders = getattr(pool, "holders", {}) or {}
            if any(tok in holders for tok in tokens):
                held.append(str(name))
    except Exception:  # noqa: BLE001
        pass
    return held


def _reset_for_tests() -> None:
    """Clear cancel registry and timers (unit tests only)."""
    with _lock:
        for jid in list(_release_timers.keys()):
            _cancel_scheduled_unlocked(jid)
        _cancel_events.clear()
        _release_timers.clear()
