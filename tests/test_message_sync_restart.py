"""MessageSync quiesce restart + last_scanned_at cooldown semantics."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engine.reach.message_sync import MessageSync


def test_start_scheduler_does_not_spawn_dual_worker_while_scan_held():
    sync = MessageSync()
    hold = threading.Event()
    scans = {"n": 0}
    workers_started = {"n": 0}

    def fake_worker(self, gen: int) -> None:  # noqa: ARG001
        workers_started["n"] += 1
        if workers_started["n"] == 1:
            scans["n"] += 1
            # Longer than stop(3s)+start join(5s) so resume hits pending path.
            hold.wait(20.0)
        while not self._stop.is_set() and gen == self._generation:
            self._stop.wait(0.05)
        self._set(active=False, phase="stopped")
        pending = False
        with self._lock:
            if self._restart_pending and gen == self._generation:
                pending = True
                self._restart_pending = False
        if pending:
            threading.Thread(
                target=self.start_scheduler,
                daemon=True,
                name="reach-message-restart",
            ).start()

    def fake_sched(self, gen: int) -> None:  # noqa: ARG001
        while not self._stop.is_set() and gen == self._generation:
            self._stop.wait(0.05)

    with (
        patch.object(MessageSync, "_worker_loop", fake_worker),
        patch.object(MessageSync, "_scheduler_loop", fake_sched),
        patch.object(MessageSync, "_enqueue_due_accounts"),
    ):
        sync.start_scheduler()
        assert workers_started["n"] == 1
        # Mimic quiesce: stop join times out while worker held.
        sync._stop.set()
        sync._cancel.set()
        if sync._scheduler and sync._scheduler.is_alive():
            sync._scheduler.join(timeout=0.5)
        # Resume while first worker still held — must not start worker #2 yet.
        # stop_scheduler join(3)+extra join(5) still leave worker alive.
        sync.start_scheduler()
        assert workers_started["n"] == 1
        assert sync._restart_pending is True
        hold.set()
        deadline = time.time() + 5.0
        while time.time() < deadline and workers_started["n"] < 2:
            time.sleep(0.05)
        assert workers_started["n"] == 2
        sync.stop_scheduler()


def test_finish_error_cancelled_does_not_stamp_last_scanned_at():
    sync = MessageSync()
    account = SimpleNamespace(
        id=7,
        customer_id=1,
        last_scanned_at=None,
        updated_at=None,
        display_name="a",
        profile_name="a",
        platform="douyin",
    )
    scan = SimpleNamespace(
        id=1,
        account_id=7,
        status="running",
        error_code=None,
        error=None,
        finished_at=None,
    )
    session = MagicMock()
    session.get.return_value = account

    with patch("engine.ops.audit_log.write_log"):
        sync._finish_error(session, scan, "cancelled", "system", "paused")
    assert account.last_scanned_at is None
    assert scan.status == "cancelled"

    with patch("engine.ops.audit_log.write_log"):
        sync._finish_error(session, scan, "deferred", "chrome_busy", "busy")
    assert account.last_scanned_at is None

    with patch("engine.ops.audit_log.write_log"):
        sync._finish_error(session, scan, "failed", "unexpected", "boom")
    assert account.last_scanned_at is not None


def test_enqueue_due_accounts_skips_needs_human_without_n_plus_one():
    """Time-due accounts with latest scan needs_human must not enqueue; one batch query."""
    sync = MessageSync()
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    old = now - __import__("datetime").timedelta(hours=2)

    accounts = [
        SimpleNamespace(
            id=1,
            enabled=True,
            profile_role="message",
            business_scope="video",
            purpose="message",
            provisioning_status="explicit",
            profile_name="a",
            last_scanned_at=old,
        ),
        SimpleNamespace(
            id=2,
            enabled=True,
            profile_role="message",
            business_scope="video",
            purpose="message",
            provisioning_status="explicit",
            profile_name="b",
            last_scanned_at=old,
        ),
        SimpleNamespace(
            id=3,
            enabled=True,
            profile_role="message",
            business_scope="video",
            purpose="message",
            provisioning_status="explicit",
            profile_name="c",
            last_scanned_at=now,  # still in cooldown
        ),
    ]

    session = MagicMock()
    session.scalars.return_value.all.return_value = accounts
    # Batch latest-status query: account 1 blocked, 2 clear.
    session.execute.return_value.all.return_value = [(1, "needs_human"), (2, "ok")]

    enqueued: list[list[int]] = []

    def capture_enqueue(ids, *, reason="manual", dry_run=False):  # noqa: ARG001
        enqueued.append(list(ids))
        return {"queued": len(ids)}

    with (
        patch("engine.reach.message_sync.get_session", return_value=session),
        patch.object(sync, "enqueue", side_effect=capture_enqueue),
    ):
        sync._enqueue_due_accounts()

    assert enqueued == [[2]]
    # Must not per-account scalar(); one execute for latest statuses.
    assert session.scalar.call_count == 0
    assert session.execute.call_count == 1
