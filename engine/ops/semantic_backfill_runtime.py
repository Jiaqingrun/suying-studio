"""Lab/runtime full-library strict semantic.v1 backfill (9B primary only).

Fleet default remains off. App Ops toggle writes local.env via
``set_semantic_full_backfill_lab``; this module runs bounded batches while
the lab flag is on. Never enables 27B cascade.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from engine.config.settings import (
    allow_semantic_full_backfill_env,
    load_settings,
)

log = logging.getLogger("montage.semantic_backfill")

# Shared with /index/captions so App loop and manual POST never double-claim.
CAPTIONS_BACKFILL_LOCK = threading.Lock()

_BATCH_LIMIT = 1
_IDLE_SLEEP_SEC = 8.0
_BUSY_SLEEP_SEC = 15.0
_ERROR_SLEEP_SEC = 20.0


class SemanticBackfillRuntime:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last: dict[str, Any] = {}

    def status(self) -> dict[str, Any]:
        alive = bool(self._thread and self._thread.is_alive())
        return {
            "lab_enabled": allow_semantic_full_backfill_env(),
            "worker_running": alive,
            "last": dict(self._last),
        }

    def ensure_started(self) -> None:
        """Start daemon loop when lab flag is on (idempotent)."""
        if not allow_semantic_full_backfill_env():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="suying-semantic-backfill",
                daemon=True,
            )
            self._thread.start()
            log.info("semantic full-backfill worker started")

    def stop(self, *, join_timeout: float = 2.0) -> None:
        """Request stop; current batch may finish under CAPTIONS_BACKFILL_LOCK."""
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=join_timeout)
        with self._lock:
            if self._thread is t:
                self._thread = None
        log.info("semantic full-backfill worker stop requested")

    def apply_lab_enabled(self, enabled: bool) -> dict[str, Any]:
        if enabled:
            self.ensure_started()
        else:
            self.stop()
        return self.status()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not allow_semantic_full_backfill_env():
                self._last = {"event": "lab_off", "ts": time.time()}
                if self._stop.wait(_IDLE_SLEEP_SEC):
                    break
                continue
            try:
                from engine.runtime.pause_coordinator import coordinator as pause_coordinator

                if not pause_coordinator.is_active_for_new_work():
                    self._last = {"event": "paused", "ts": time.time()}
                    if self._stop.wait(_BUSY_SLEEP_SEC):
                        break
                    continue
            except Exception:
                log.exception("semantic backfill pause check failed")

            if not CAPTIONS_BACKFILL_LOCK.acquire(blocking=False):
                self._last = {"event": "busy", "ts": time.time()}
                if self._stop.wait(_BUSY_SLEEP_SEC):
                    break
                continue
            try:
                tick = self._run_one_batch()
                self._last = {"event": "batch", "ts": time.time(), **tick}
                remaining = int(tick.get("remaining") or 0)
                sleep_for = _IDLE_SLEEP_SEC if remaining <= 0 else 1.0
            except Exception as exc:
                log.exception("semantic full-backfill batch failed")
                self._last = {
                    "event": "error",
                    "ts": time.time(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                sleep_for = _ERROR_SLEEP_SEC
            finally:
                CAPTIONS_BACKFILL_LOCK.release()
            if self._stop.wait(sleep_for):
                break

    def _run_one_batch(self) -> dict[str, Any]:
        from engine.api.scope import active_scope
        from engine.catalog.db import get_session
        from engine.ingest.cliplet import (
            recaption_existing_cliplets,
            semantic_backfill_progress,
        )

        settings = load_settings()
        session = get_session()
        try:
            _, customer, _ = active_scope(session, settings)
            result = recaption_existing_cliplets(
                session,
                customer_id=customer.id,
                limit=_BATCH_LIMIT,
                use_vision=True,
            )
            progress = semantic_backfill_progress(session, customer_id=customer.id)
            return {
                "customer_id": customer.id,
                "processed": result.get("processed"),
                "passed": result.get("passed"),
                "rejected": result.get("rejected"),
                "remaining": progress.get("remaining"),
                "eligible": progress.get("eligible"),
            }
        finally:
            session.close()


semantic_backfill_runtime = SemanticBackfillRuntime()
