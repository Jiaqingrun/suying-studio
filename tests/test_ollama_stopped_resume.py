"""Ollama STAT=T resume + non-blocking /health ollama cache."""

from __future__ import annotations

import signal
from unittest.mock import patch

from engine.catalog import ollama_status
from engine.ops import ollama_service


def test_is_stopped_state():
    assert ollama_service.is_stopped_state("T")
    assert ollama_service.is_stopped_state("Tt")
    assert not ollama_service.is_stopped_state("S")
    assert not ollama_service.is_stopped_state("")


def test_resume_stopped_sends_sigcont(tmp_path, monkeypatch):
    state_file = tmp_path / "service_state.json"
    monkeypatch.setattr(ollama_service, "STATE_FILE", state_file)
    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))

    with (
        patch(
            "engine.ops.ollama_service.list_ollama_related_pids",
            return_value=[4242, 4243],
        ),
        patch(
            "engine.ops.ollama_service.process_state_code",
            side_effect=lambda pid: "T" if pid == 4242 else "S",
        ),
        patch(
            "engine.ops.ollama_service.process_command",
            return_value="/opt/homebrew/bin/ollama serve",
        ),
        patch("engine.ops.ollama_service.os.kill", side_effect=fake_kill),
    ):
        out = ollama_service.resume_stopped_ollama_processes(force=True)
    assert out["action"] == "sigcont"
    assert out["resumed_pids"] == [4242]
    assert sent == [(4242, signal.SIGCONT)]


def test_resume_respects_cooldown(tmp_path, monkeypatch):
    state_file = tmp_path / "service_state.json"
    monkeypatch.setattr(ollama_service, "STATE_FILE", state_file)
    ollama_service.merge_state(last_cont_at=10_000_000.0)
    with patch("engine.ops.ollama_service.time.time", return_value=10_000_005.0):
        out = ollama_service.resume_stopped_ollama_processes(force=False)
    assert out["action"] == "cooldown"
    assert out["resumed_pids"] == []


def test_resume_respects_scan_cooldown_without_prior_cont(monkeypatch):
    """Healthy hosts never write last_cont_at — scan floor still must apply."""
    monkeypatch.setattr(ollama_service, "_LAST_CONT_SCAN_AT", 10_000_000.0)
    listed = {"n": 0}

    def boom_list():
        listed["n"] += 1
        return [1]

    with (
        patch("engine.ops.ollama_service.time.time", return_value=10_000_003.0),
        patch("engine.ops.ollama_service.list_ollama_related_pids", side_effect=boom_list),
        patch("engine.ops.ollama_service.read_state", return_value={}),
    ):
        out = ollama_service.resume_stopped_ollama_processes(force=False)
    assert out["action"] == "scan_cooldown"
    assert listed["n"] == 0
    assert out["resumed_pids"] == []


def test_resume_scan_runs_after_scan_cooldown(monkeypatch):
    monkeypatch.setattr(ollama_service, "_LAST_CONT_SCAN_AT", 10_000_000.0)
    with (
        patch("engine.ops.ollama_service.time.time", return_value=10_000_010.0),
        patch(
            "engine.ops.ollama_service.list_ollama_related_pids",
            return_value=[7],
        ),
        patch("engine.ops.ollama_service.process_state_code", return_value="S"),
        patch("engine.ops.ollama_service.read_state", return_value={}),
    ):
        out = ollama_service.resume_stopped_ollama_processes(force=False)
    assert out["action"] == "none"
    assert out["checked_pids"] == [{"pid": 7, "state": "S"}]
    assert ollama_service._LAST_CONT_SCAN_AT == 10_000_010.0


def test_ollama_status_for_health_never_blocks_on_live_http(monkeypatch):
    """Hot path must return immediately and only schedule background refresh."""
    monkeypatch.setattr(ollama_status, "_CACHE", None)
    monkeypatch.setattr(ollama_status, "_CACHE_AT", 0.0)

    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("live probe must not run on health hot path")

    with (
        patch.object(ollama_status, "check_ollama", side_effect=boom),
        patch.object(ollama_status, "schedule_ollama_status_refresh") as sched,
    ):
        out = ollama_status.ollama_status_for_health()
    assert out.get("cache_pending") is True
    assert out.get("reachable") is False
    assert called["n"] == 0
    sched.assert_called_once()


def test_ollama_status_for_health_returns_cache(monkeypatch):
    monkeypatch.setattr(
        ollama_status,
        "_CACHE",
        {"reachable": True, "ready": True, "message": "ok", "models": ["x"]},
    )
    monkeypatch.setattr(ollama_status, "_CACHE_AT", 1_000_000.0)
    with (
        patch("engine.catalog.ollama_status.time.monotonic", return_value=1_000_005.0),
        patch.object(ollama_status, "schedule_ollama_status_refresh") as sched,
    ):
        out = ollama_status.ollama_status_for_health(max_age_sec=20)
    assert out["reachable"] is True
    assert out["cached"] is True
    assert out["cache_age_sec"] == 5.0
    sched.assert_not_called()


def test_readiness_never_calls_live_ollama_http():
    """App kickstart uses /readiness with 800ms budget — no live Ollama I/O."""
    from engine.api.readiness import build_readiness_snapshot

    def boom(*_a, **_k):
        raise AssertionError("live Ollama HTTP must not run on /readiness")

    with (
        patch("engine.security.license.is_packaged_runtime", return_value=False),
        patch(
            "engine.catalog.ollama_runtime._shallow_tags_probe",
            side_effect=boom,
        ),
        patch(
            "engine.catalog.ollama_status.check_ollama",
            side_effect=boom,
        ),
        patch(
            "engine.catalog.ollama_status.ollama_status_for_health",
            return_value={
                "reachable": True,
                "models": ["nomic-embed-text:latest"],
                "ready": True,
            },
        ),
        patch(
            "engine.catalog.ollama_runtime.ollama_control_plane_snapshot",
            return_value={
                "circuit": {"state": "closed"},
                "chat_probe_ok": True,
                "live_http": False,
            },
        ),
    ):
        snap = build_readiness_snapshot()
    assert "ready" in snap
    assert snap.get("chat_probe_ok") is True


def test_schedule_refresh_queues_force_cont(monkeypatch):
    monkeypatch.setattr(ollama_status, "_REFRESHING", True)
    monkeypatch.setattr(ollama_status, "_PENDING_FORCE_CONT", False)
    ollama_status.schedule_ollama_status_refresh(force_cont=True)
    assert ollama_status._PENDING_FORCE_CONT is True


def test_scheduler_source_runs_ollama_cont_before_pause_gate():
    """Sleep/wake pause must not skip STAT=T recovery (source contract)."""
    from pathlib import Path

    text = Path(ollama_service.__file__).resolve().parents[1] / "ops" / "scheduler.py"
    src = text.read_text(encoding="utf-8")
    cont_at = src.find("resume_stopped_ollama_processes")
    pause_at = src.find("should_claim_jobs()")
    assert cont_at != -1 and pause_at != -1
    assert cont_at < pause_at, "Ollama SIGCONT must run before pause continue"


def test_ollama_status_for_health_schedules_refresh_when_stale(monkeypatch):
    monkeypatch.setattr(
        ollama_status,
        "_CACHE",
        {"reachable": True, "ready": True, "message": "ok", "models": ["x"]},
    )
    monkeypatch.setattr(ollama_status, "_CACHE_AT", 1_000_000.0)
    with (
        patch("engine.catalog.ollama_status.time.monotonic", return_value=1_000_050.0),
        patch.object(ollama_status, "schedule_ollama_status_refresh") as sched,
    ):
        out = ollama_status.ollama_status_for_health(max_age_sec=20)
    assert out["stale"] is True
    assert out["reachable"] is True
    sched.assert_called_once()


def test_ensure_listener_force_cont_when_still_stopped(tmp_path, monkeypatch):
    state_file = tmp_path / "service_state.json"
    monkeypatch.setattr(ollama_service, "STATE_FILE", state_file)
    cont_calls: list[bool] = []

    def fake_resume(*, force: bool = False):
        cont_calls.append(force)
        return {"ok": True, "action": "sigcont" if force else "cooldown", "resumed_pids": [99] if force else []}

    with (
        patch.object(ollama_service, "resume_stopped_ollama_processes", side_effect=fake_resume),
        patch.object(ollama_service, "listener_pid", return_value=99),
        patch.object(ollama_service, "process_state_code", return_value="T"),
        patch("httpx.Client") as client_cls,
    ):
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value.status_code = 200
        out = ollama_service.ensure_ollama_listener_responsive(force_cont=False)
    assert cont_calls == [False, True]
    assert out["ok"] is True
    assert out["listener_pid"] == 99
