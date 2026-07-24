from __future__ import annotations

import logging
import threading

from engine.catalog.customer_scope import require_active_customer
from engine.catalog.db import get_session
from engine.config.settings import load_settings
from engine.ops.maintenance import maybe_run_daily_job

log = logging.getLogger("montage.scheduler")


class DailyScheduler:
    """Periodic jobs: daily calendar production + ingest reconcile."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ticks = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ticks = 0
        self._thread = threading.Thread(target=self._loop, daemon=True, name="montage-daily-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        # First reconcile soon after boot (catch missed indexes)
        self._stop.wait(5.0)
        while not self._stop.is_set():
            settings = load_settings()
            try:
                maybe_run_daily_job(settings)
            except Exception:
                log.exception("daily job tick failed")

            # Pause reconcile while a full library scan is writing/normalizing
            try:
                from engine.ops.scan_state import scan_state

                if scan_state.get("running"):
                    self._ticks += 1
                    self._stop.wait(30.0)
                    continue
            except Exception:
                pass

            # Vectorization gate: never auto-index unless customer enabled it
            if not settings.vectorization_enabled:
                self._ticks += 1
                self._stop.wait(30.0)
                continue

            # Every ~30s: process a batch of assets missing cliplets/embeddings
            try:
                from engine.ingest.pipeline import reconcile_pending_assets

                session = get_session()
                try:
                    customer = require_active_customer(session, settings)
                    result = reconcile_pending_assets(
                        session,
                        settings,
                        customer_id=customer.id,
                        limit=5,
                        use_vision=True,
                        mode="incremental",
                    )
                    if result.get("processed"):
                        log.info("reconcile tick: %s", result)
                finally:
                    session.close()
            except Exception:
                log.exception("reconcile tick failed")

            self._ticks += 1
            self._stop.wait(30.0)


scheduler = DailyScheduler()
