"""JobWorker start/stop must revive after stop join timeout (no permanent dead worker)."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from engine.jobs.worker import JobWorker


def test_start_while_winding_down_revives_with_new_generation():
    """stop() join timeout left _stop set + alive thread; old start() returned early → dead."""
    worker = JobWorker()
    loops = {"n": 0}
    hold = threading.Event()
    first_entered = threading.Event()
    second_entered = threading.Event()

    def fake_loop(self, gen: int) -> None:
        loops["n"] += 1
        if loops["n"] == 1:
            first_entered.set()
            hold.wait(2.0)
        else:
            second_entered.set()
        while not self._stop.is_set() and gen == self._generation:
            self._stop.wait(0.05)

    with patch.object(JobWorker, "_loop", fake_loop):
        worker.start()
        assert first_entered.wait(1.0)
        first_gen = worker._generation
        # Mimic quiesce/ops stop while loop is held past join timeout.
        worker._stop.set()
        worker._generation += 1
        if worker._thread:
            worker._thread.join(timeout=0.2)
        assert worker._thread and worker._thread.is_alive()
        # Resume must drain orphan and spawn a new generation.
        worker.start()
        assert worker._generation > first_gen
        hold.set()
        assert second_entered.wait(2.0)
        assert loops["n"] == 2
        worker.stop()


def test_healthy_start_is_idempotent():
    worker = JobWorker()
    entered = threading.Event()

    def idle_loop(self, gen: int) -> None:
        entered.set()
        while not self._stop.is_set() and gen == self._generation:
            self._stop.wait(0.05)

    with patch.object(JobWorker, "_loop", idle_loop):
        worker.start()
        assert entered.wait(1.0)
        gen1 = worker._generation
        t1 = worker._thread
        worker.start()
        assert worker._generation == gen1
        assert worker._thread is t1
        worker.stop()
