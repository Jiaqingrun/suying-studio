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
        self._publish_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ticks = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ticks = 0
        self._thread = threading.Thread(target=self._loop, daemon=True, name="montage-daily-scheduler")
        self._thread.start()
        self._publish_thread = threading.Thread(
            target=self._publish_loop,
            daemon=True,
            name="montage-publish-clock",
        )
        self._publish_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        if self._publish_thread:
            self._publish_thread.join(timeout=3)

    def is_alive(self) -> bool:
        return bool(
            self._thread
            and self._thread.is_alive()
            and self._publish_thread
            and self._publish_thread.is_alive()
        )

    def _loop(self) -> None:
        # First reconcile soon after boot (catch missed indexes)
        self._stop.wait(5.0)
        while not self._stop.is_set():
            from engine.runtime.pause_coordinator import coordinator as pause_coordinator

            if not pause_coordinator.should_claim_jobs():
                self._ticks += 1
                self._stop.wait(2.0)
                continue

            settings = load_settings()
            try:
                maybe_run_daily_job(settings)
            except Exception:
                log.exception("daily job tick failed")
            try:
                from engine.ops.backup_service import maybe_run_scheduled_backup

                backup_tick = maybe_run_scheduled_backup()
                if backup_tick:
                    log.info("scheduled backup started: %s", backup_tick)
            except Exception:
                log.exception("scheduled backup tick failed")
            try:
                from engine.ops.published_cleanup import maybe_run_scheduled_cleanup

                session = get_session()
                try:
                    customer = require_active_customer(session, settings)
                    cleanup_tick = maybe_run_scheduled_cleanup(
                        session,
                        customer_id=customer.id,
                        output_root=customer.output_root or settings.paths.output_root,
                    )
                    if cleanup_tick:
                        log.info("published cleanup tick: %s", cleanup_tick)
                finally:
                    session.close()
            except Exception:
                log.exception("published cleanup tick failed")

            try:
                from engine.ops.failed_resource_cleanup import (
                    maybe_run_failed_resource_cleanup,
                )

                session = get_session()
                try:
                    customer = require_active_customer(session, settings)
                    failed_tick = maybe_run_failed_resource_cleanup(
                        session,
                        customer_id=customer.id,
                        output_root=customer.output_root or settings.paths.output_root,
                        settings=settings,
                    )
                    if failed_tick:
                        log.info("failed resource cleanup tick: %s", failed_tick)
                finally:
                    session.close()
            except Exception:
                log.exception("failed resource cleanup tick failed")

            # Pause only vector reconcile while a full library scan is writing.
            # Publish/automation/message ticks below must remain independent.
            scan_running = False
            try:
                from engine.ops.scan_state import scan_state

                scan_running = bool(scan_state.get("running"))
            except Exception:
                pass

            # Every ~30s: schedule edges / auto-stop, then re-queue gaps if allowed.
            if not scan_running:
                try:
                    from engine.catalog.vectorization_runtime import (
                        executor,
                        maybe_auto_incremental_tick,
                    )
                    from engine.catalog.vectorization_schedule import (
                        maybe_vectorization_policy_tick,
                    )

                    policy = maybe_vectorization_policy_tick()
                    if policy.get("edge", {}).get("actions") or policy.get(
                        "auto_stop", {}
                    ).get("stopped"):
                        log.info("vector policy tick: %s", policy)
                    if policy.get("allow_work"):
                        tick = maybe_auto_incremental_tick()
                        if tick.get("started_run") or tick.get("enqueued"):
                            log.info("vector auto incremental tick: %s", tick)
                        else:
                            executor.wake()
                except Exception:
                    log.exception("vector executor auto tick failed")

            # Every ~5 min: walk library for files missed by FSEvents (e.g. atomic .partial rename).
            if self._ticks > 0 and self._ticks % 10 == 0 and not scan_running:
                try:
                    from engine.runtime import services as runtime_services

                    w = runtime_services.get("watcher")
                    if w is not None and hasattr(w, "scan_existing"):
                        n = w.scan_existing(limit=30, defer_index=True)
                        if n:
                            log.info("periodic library scan ingested_ready=%s", n)
                except Exception:
                    log.exception("periodic library scan failed")

            # GSemanticOps: one idempotent incremental check per UTC day.
            # This reads only persisted coarse/strict rows and never invokes VLM.
            try:
                from engine.catalog.semantic_ops import run_health_check

                session = get_session()
                try:
                    customer = require_active_customer(session, settings)
                    health_run = run_health_check(
                        session,
                        customer_id=customer.id,
                        mode="daily",
                        requested_by="scheduler",
                    )
                    if health_run.checked_count:
                        log.info("semantic health tick: run=%s checked=%s", health_run.id, health_run.checked_count)
                finally:
                    session.close()
            except Exception:
                log.exception("semantic health tick failed")

            # Persistent production → pack → publish checkpoints.
            try:
                from engine.ops.automation_loop import tick_automation

                session = get_session()
                try:
                    customer = require_active_customer(session, settings)
                    auto_tick = tick_automation(session, customer_id=customer.id)
                    if auto_tick:
                        log.info("automation tick: %s", auto_tick)
                finally:
                    session.close()
            except Exception:
                log.exception("automation tick failed")

            try:
                from engine.ops.human_alerts import dispatch_due_alerts

                session = get_session()
                try:
                    customer = require_active_customer(session, settings)
                    alert_tick = dispatch_due_alerts(
                        session, customer_id=customer.id
                    )
                    if alert_tick:
                        log.info("human alert tick: %s", alert_tick)
                finally:
                    session.close()
            except Exception:
                log.exception("human alert tick failed")

            # Z-Space → 外置片库单向拉取：必须在 App/引擎进程内写 /Volumes（LaunchAgent 会 EPERM）
            try:
                from engine.ops.one_way_media import maybe_run_scheduled

                pull_tick = maybe_run_scheduled()
                if pull_tick:
                    log.info("one-way media pull tick: %s", pull_tick)
            except Exception:
                log.exception("one-way media pull tick failed")

            self._ticks += 1
            self._stop.wait(30.0)

    def _publish_loop(self) -> None:
        """Second-level durable publish clock; the 30s loop remains reconciliation-only."""
        self._stop.wait(1.0)
        while not self._stop.is_set():
            try:
                from engine.runtime.pause_coordinator import coordinator as pause_coordinator

                if pause_coordinator.should_claim_jobs():
                    from engine.reach.publish_schedule import tick_schedules

                    settings = load_settings()
                    session = get_session()
                    try:
                        customer = require_active_customer(session, settings)
                        pub_tick = tick_schedules(session, customer_id=customer.id)
                        if pub_tick:
                            log.info("publish clock tick: %s", pub_tick)
                    finally:
                        session.close()
            except Exception:
                log.exception("publish clock tick failed")
            self._stop.wait(1.0)


scheduler = DailyScheduler()
