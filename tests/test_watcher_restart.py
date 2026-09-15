"""IngestWatcher must not spawn dual Observers after stop join timeout."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engine.ingest.watcher import IngestWatcher


def test_stop_join_timeout_keeps_ref_and_start_refuses_dual():
    settings = SimpleNamespace(paths=SimpleNamespace(all_library_roots=lambda: []))
    watcher = IngestWatcher(settings)  # type: ignore[arg-type]

    sticky = MagicMock()
    sticky.is_alive.return_value = True

    watcher._observer = sticky
    watcher.stop()
    # Join timed out conceptually: sticky still alive → ref must remain.
    assert watcher._observer is sticky
    assert watcher.is_alive() is True

    with patch.object(watcher, "_active_context", return_value=(settings, 1)):
        # Must not replace sticky with a second Observer while first still lives.
        watcher.start()
    assert watcher._observer is sticky
    sticky.stop.assert_called()


def test_start_idempotent_when_alive():
    settings = SimpleNamespace(paths=SimpleNamespace(all_library_roots=lambda: []))
    watcher = IngestWatcher(settings)  # type: ignore[arg-type]
    alive = MagicMock()
    alive.is_alive.return_value = True
    watcher._observer = alive
    with patch.object(watcher, "_active_context") as ctx:
        watcher.start()
        ctx.assert_not_called()
    assert watcher._observer is alive


def test_stop_clears_ref_when_observer_exits():
    settings = SimpleNamespace(paths=SimpleNamespace(all_library_roots=lambda: []))
    watcher = IngestWatcher(settings)  # type: ignore[arg-type]
    dead = MagicMock()
    dead.is_alive.return_value = False
    watcher._observer = dead
    watcher.stop()
    dead.stop.assert_called_once()
    dead.join.assert_called_once()
    assert watcher._observer is None
    assert watcher.is_alive() is False
