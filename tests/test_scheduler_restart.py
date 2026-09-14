"""DailyScheduler start/stop must not orphan or duplicate publish-clock threads."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from engine.ops.scheduler import DailyScheduler


def test_start_while_partially_alive_does_not_duplicate_publish_clock():
    """quiesce stop→start can leave publish mid-tick; clearing _stop must not revive it."""
    sched = DailyScheduler()
    loops = {"main": 0, "publish": 0}
    hold_publish = threading.Event()
    publish_entered = threading.Event()
    publish_second_entered = threading.Event()

    def fake_loop(self, gen: int) -> None:  # noqa: ARG001
        loops["main"] += 1
        while not self._stop.is_set() and gen == self._generation:
            self._stop.wait(0.05)

    def fake_publish_loop(self, gen: int) -> None:
        loops["publish"] += 1
        if loops["publish"] == 1:
            publish_entered.set()
            hold_publish.wait(2.0)
        else:
            publish_second_entered.set()
        while not self._stop.is_set() and gen == self._generation:
            self._stop.wait(0.05)

    with (
        patch.object(DailyScheduler, "_loop", fake_loop),
        patch.object(DailyScheduler, "_publish_loop", fake_publish_loop),
    ):
        sched.start()
        assert publish_entered.wait(1.0)
        # Simulate stop returning before publish finishes (join timeout path).
        sched._stop.set()
        if sched._thread:
            sched._thread.join(timeout=1.0)
        first_gen = sched._generation
        # Restart while first publish is still held — must drain then respawn.
        sched.start()
        assert sched._generation > first_gen
        hold_publish.set()
        assert publish_second_entered.wait(1.0)
        time.sleep(0.1)
        # First publish loop should exit via generation mismatch (not a 3rd spawn).
        assert loops["publish"] == 2, loops
        assert loops["main"] == 2, loops
        sched.stop()
        assert not (sched._publish_thread and sched._publish_thread.is_alive())


def test_start_is_idempotent_when_both_alive():
    sched = DailyScheduler()

    def idle_loop(self, gen: int) -> None:  # noqa: ARG001
        while not self._stop.is_set() and gen == self._generation:
            self._stop.wait(0.2)

    with (
        patch.object(DailyScheduler, "_loop", idle_loop),
        patch.object(DailyScheduler, "_publish_loop", idle_loop),
    ):
        sched.start()
        t1 = sched._thread
        p1 = sched._publish_thread
        gen1 = sched._generation
        sched.start()
        assert sched._thread is t1
        assert sched._publish_thread is p1
        assert sched._generation == gen1
        sched.stop()


def test_start_revives_when_stop_bit_stuck_with_alive_threads():
    """quiesce: stop join timeout leaves both alive + _stop set; start must clear."""
    sched = DailyScheduler()
    started = {"n": 0}

    def idle_loop(self, gen: int) -> None:  # noqa: ARG001
        started["n"] += 1
        while not self._stop.is_set() and gen == self._generation:
            self._stop.wait(0.05)

    with (
        patch.object(DailyScheduler, "_loop", idle_loop),
        patch.object(DailyScheduler, "_publish_loop", idle_loop),
    ):
        sched.start()
        assert started["n"] == 2
        # Mimic stop() that set the bit but join returned early.
        sched._stop.set()
        time.sleep(0.05)
        # Threads may still be finishing; force the stuck contract:
        # both refs exist, stop bit set — old start() would no-op forever.
        before = started["n"]
        sched.start()
        time.sleep(0.15)
        assert started["n"] > before
        assert not sched._stop.is_set()
        assert sched.is_alive()
        sched.stop()
