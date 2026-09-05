"""GSystemPause unit tests — isolated runtime dir, no real ~/Suying pollution."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.catalog.db import init_db, reset_engine
from engine.config.settings import AppSettings, PathConfig, save_settings
from engine.runtime.pause_coordinator import (
    REASON_MANUAL,
    REASON_POWER_OFF,
    REASON_SCREEN_SLEEP,
    REASON_SYSTEM_SLEEP,
    STATE_ACTIVE,
    STATE_PAUSED,
    STATE_PAUSED_BLOCKED,
    PauseState,
    SystemEventControl,
    coordinator,
    default_system_event_control,
)
from engine.runtime.quiesce import capture_owned_snapshot


@pytest.fixture()
def tmp_runtime(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    lib = tmp_path / "lib"
    out = tmp_path / "out"
    boot = tmp_path / "boot"
    runtime = tmp_path / "runtime"
    for p in (data, lib, out, boot, runtime):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    monkeypatch.setenv("MONTAGE_DATA_ROOT", str(boot))
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(runtime))
    # Reset singleton state for isolated test
    coordinator._state = PauseState()  # noqa: SLF001
    coordinator._token = None  # noqa: SLF001
    settings = AppSettings(
        paths=PathConfig(
            data_root=data,
            library_root=lib,
            library_roots=[str(lib)],
            output_root=out,
            cache_root=data / "cache",
            render_root=data / "render",
        ),
        active_customer="系统暂停客户",
        onboarded=True,
        system_event_control=default_system_event_control(),
    )
    save_settings(settings)
    reset_engine()
    init_db(settings)
    return settings, runtime


def test_policy_defaults():
    p = default_system_event_control()
    assert p.pause_on_system_sleep is True
    assert p.pause_on_screen_sleep is False
    assert p.auto_resume_on_wake is True
    assert p.auto_resume_on_session_active is False


def test_duplicate_event_idempotent(tmp_runtime):
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(wake_settle_seconds=0)
    r1 = coordinator.handle_event(event_id="e1", kind="will_sleep", policy=policy, owned_snapshot={})
    assert r1["applied"] is True
    assert REASON_SYSTEM_SLEEP in r1["pause_reasons"]
    r2 = coordinator.handle_event(event_id="e1", kind="will_sleep", policy=policy)
    assert r2["duplicate"] is True
    assert r2["applied"] is False


def test_manual_pause_survives_wake(tmp_runtime):
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(wake_settle_seconds=0, resume_requires_path_health=False, resume_requires_disk_health=False)
    coordinator.pause_manual(reason=REASON_MANUAL, owned_snapshot={})
    paused = coordinator.handle_event(
        event_id="s1", kind="will_sleep", policy=policy, owned_snapshot={}
    )
    coordinator.mark_paused(generation=paused["generation"])
    coordinator.handle_event(
        event_id="w1",
        kind="did_wake",
        policy=policy,
        generation_hint=paused["generation"],
    )
    checked = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
    assert checked["restore_ready"] is True
    state = coordinator.complete_resume()
    assert REASON_MANUAL in state["pause_reasons"]
    assert state["state"] == STATE_PAUSED


def test_wake_clears_system_sleep(tmp_runtime):
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(wake_settle_seconds=0, resume_requires_path_health=False, resume_requires_disk_health=False)
    paused = coordinator.handle_event(
        event_id="s2",
        kind="will_sleep",
        policy=policy,
        owned_snapshot={"scheduler": True},
    )
    coordinator.mark_paused(generation=paused["generation"])
    assert not coordinator.should_claim_jobs()
    coordinator.handle_event(
        event_id="w2",
        kind="did_wake",
        policy=policy,
        generation_hint=paused["generation"],
    )
    checked = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
    assert checked["restore_ready"] is True
    state = coordinator.complete_resume()
    assert state["state"] == STATE_ACTIVE
    assert state["pause_reasons"] == []


def test_wake_during_quiesce_waits_for_safe_boundary(tmp_runtime):
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(
        wake_settle_seconds=0,
        resume_requires_path_health=False,
        resume_requires_disk_health=False,
    )
    paused = coordinator.handle_event(
        event_id="sleep-race",
        kind="will_sleep",
        policy=policy,
        owned_snapshot={"worker": True},
    )
    generation = int(paused["generation"])
    assert paused["state"] == "PAUSING"
    wake = coordinator.handle_event(
        event_id="wake-race",
        kind="did_wake",
        policy=policy,
        generation_hint=generation,
    )
    assert wake["state"] == "PAUSING"
    assert wake["pending_resume"]["trigger"] == "did_wake"
    boundary = coordinator.mark_paused(generation=generation)
    assert boundary["state"] == "RESUMING"
    checked = coordinator.apply_resume_after_checks(
        path_ok=True, disk_ok=True, policy=policy
    )
    assert checked["restore_ready"] is True
    assert coordinator.complete_resume()["state"] == STATE_ACTIVE


def test_quiesce_fail_with_pending_wake_promotes_to_resuming(tmp_runtime):
    """message_sync etc. timeout must not strand pending_resume in PAUSED_BLOCKED."""
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(
        wake_settle_seconds=0,
        resume_requires_path_health=False,
        resume_requires_disk_health=False,
    )
    paused = coordinator.handle_event(
        event_id="sleep-quiesce-fail",
        kind="will_sleep",
        policy=policy,
        owned_snapshot={"message_sync": True},
    )
    generation = int(paused["generation"])
    wake = coordinator.handle_event(
        event_id="wake-quiesce-fail",
        kind="did_wake",
        policy=policy,
        generation_hint=generation,
    )
    assert wake["state"] == "PAUSING"
    assert wake["pending_resume"]
    blocked = coordinator.mark_blocked(
        ["quiesce_failed"],
        error="quiesce_timeout:message_sync",
        generation=generation,
    )
    assert blocked["state"] == "RESUMING"
    assert blocked["pending_resume"]["trigger"] == "did_wake"
    assert "quiesce_failed" in blocked["resume_blockers"]
    checked = coordinator.apply_resume_after_checks(
        path_ok=True, disk_ok=True, policy=policy
    )
    assert checked.get("restore_ready") is True
    state = coordinator.complete_resume()
    assert state["state"] == STATE_ACTIVE
    assert state["accepts_new_work"] is True
    assert state["resume_blockers"] == []


def test_path_block_keeps_paused(tmp_runtime):
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(wake_settle_seconds=0, resume_requires_path_health=True, resume_requires_disk_health=False)
    paused = coordinator.handle_event(
        event_id="s3", kind="will_sleep", policy=policy, owned_snapshot={}
    )
    coordinator.mark_paused(generation=paused["generation"])
    coordinator.handle_event(
        event_id="w3",
        kind="did_wake",
        policy=policy,
        generation_hint=paused["generation"],
    )
    state = coordinator.apply_resume_after_checks(path_ok=False, disk_ok=True, policy=policy)
    assert state["state"] == "PAUSED_BLOCKED"
    assert "path_health" in state["resume_blockers"]


def test_stale_quiesce_block_ignored_after_wake(tmp_runtime):
    """Late quiesce_timeout must not re-block ACTIVE after wake cleared holds."""
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(
        wake_settle_seconds=0,
        resume_requires_path_health=False,
        resume_requires_disk_health=False,
    )
    paused = coordinator.handle_event(
        event_id="s-stale", kind="will_sleep", policy=policy, owned_snapshot={}
    )
    gen = int(paused["generation"])
    coordinator.mark_paused(generation=gen)
    coordinator.handle_event(
        event_id="w-stale",
        kind="did_wake",
        policy=policy,
        generation_hint=gen,
    )
    checked = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
    assert checked.get("restore_ready") is True
    state = coordinator.complete_resume()
    assert state["state"] == STATE_ACTIVE
    # Stale block from the sleep generation must be ignored
    blocked = coordinator.mark_blocked(
        ["quiesce_failed"],
        error="quiesce_timeout:worker_output",
        generation=gen,
    )
    assert blocked["state"] == STATE_ACTIVE
    assert blocked["accepts_new_work"] is True
    assert coordinator.should_claim_jobs() is True


def test_orphan_paused_blocked_self_heals(tmp_runtime):
    """Empty-reason PAUSED_BLOCKED (historical stuck state) must return to ACTIVE."""
    from engine.runtime.pause_coordinator import STATE_PAUSED_BLOCKED

    _settings, _runtime = tmp_runtime
    coordinator._state.state = STATE_PAUSED_BLOCKED  # noqa: SLF001
    coordinator._state.pause_reasons = []  # noqa: SLF001
    coordinator._state.pause_holds = {}  # noqa: SLF001
    coordinator._state.resume_blockers = ["quiesce_failed"]  # noqa: SLF001
    coordinator._state.last_error = "quiesce_timeout:watcher,worker_output"  # noqa: SLF001
    assert coordinator.should_claim_jobs() is True
    snap = coordinator.snapshot()
    assert snap["state"] == STATE_ACTIVE
    assert snap["accepts_new_work"] is True
    assert snap["resume_blockers"] == []


def test_api_pause_gate_423(tmp_runtime):
    _settings, _runtime = tmp_runtime
    from engine.api.app import app

    client = TestClient(app)
    coordinator.pause_manual(owned_snapshot={})
    # Force paused state for gate
    coordinator.mark_paused()
    r = client.post("/jobs", json={"mode": "count", "target_count": 1, "template_name": "default-vertical"})
    assert r.status_code == 423


def test_manual_resume_requires_explicit_reasons(tmp_runtime):
    _settings, _runtime = tmp_runtime
    from engine.api.app import app

    client = TestClient(app)
    coordinator.pause_manual(owned_snapshot={})
    coordinator.mark_paused()
    response = client.post("/system/resume", json={})
    assert response.status_code == 400
    assert REASON_MANUAL in coordinator.snapshot()["pause_reasons"]


def test_manual_resume_clears_system_power_off(tmp_runtime):
    """Operator resume must clear system-owned power_off (owner mismatch was a freeze bug)."""
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(
        wake_settle_seconds=0,
        resume_requires_path_health=False,
        resume_requires_disk_health=False,
    )
    paused = coordinator.handle_event(
        event_id="power-1",
        kind="will_power_off",
        policy=policy,
        owned_snapshot={"worker": True, "scheduler": True},
    )
    coordinator.mark_paused(generation=paused["generation"])
    assert REASON_POWER_OFF in coordinator.snapshot()["pause_reasons"]
    prepared = coordinator.prepare_manual_resume(reasons=[REASON_POWER_OFF])
    assert prepared["state"] == "RESUMING"
    checked = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
    assert checked.get("restore_ready") is True
    state = coordinator.complete_resume()
    assert state["state"] == STATE_ACTIVE
    assert state["pause_reasons"] == []
    assert coordinator.should_claim_jobs() is True


def test_did_wake_clears_power_off(tmp_runtime):
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(
        wake_settle_seconds=0,
        resume_requires_path_health=False,
        resume_requires_disk_health=False,
    )
    paused = coordinator.handle_event(
        event_id="power-2",
        kind="will_power_off",
        policy=policy,
        owned_snapshot={},
    )
    gen = int(paused["generation"])
    coordinator.mark_paused(generation=gen)
    coordinator.handle_event(
        event_id="wake-power",
        kind="did_wake",
        policy=policy,
        generation_hint=gen,
    )
    checked = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
    assert checked.get("restore_ready") is True
    state = coordinator.complete_resume()
    assert state["state"] == STATE_ACTIVE
    assert REASON_POWER_OFF not in state["pause_reasons"]


def test_boot_load_clears_stale_power_off(tmp_runtime):
    """Persisted power_off after reboot must self-heal on coordinator load."""
    _settings, runtime = tmp_runtime
    state_path = runtime / "pause_state.json"
    state_path.write_text(
        __import__("json").dumps(
            {
                "state": STATE_PAUSED_BLOCKED,
                "generation": 10,
                "pause_reasons": [REASON_POWER_OFF],
                "pause_holds": {REASON_POWER_OFF: {"owner": "system", "generation": 10}},
                "owned_units": {"worker": True},
                "pending_resume": None,
                "side_effect_interruptions": {},
                "resume_blockers": ["restart_during_transition"],
                "last_event_id": "will_power_off-x",
                "last_event_kind": "will_power_off",
                "last_event_at": "1",
                "paused_at": "2026-07-31T00:00:00+00:00",
                "wake_received_at": None,
                "last_error": None,
                "seen_event_ids": [],
            }
        ),
        encoding="utf-8",
    )
    coordinator._token = None  # noqa: SLF001
    coordinator._state = PauseState()  # noqa: SLF001
    coordinator._load()  # noqa: SLF001
    snap = coordinator.snapshot()
    assert snap["state"] == STATE_ACTIVE
    assert snap["pause_reasons"] == []
    assert snap["accepts_new_work"] is True
    assert coordinator.should_claim_jobs() is True


def test_boot_load_clears_stale_sleep_holds(tmp_runtime):
    """Persisted system_sleep/screen_sleep after process restart must self-heal."""
    _settings, runtime = tmp_runtime
    state_path = runtime / "pause_state.json"
    state_path.write_text(
        __import__("json").dumps(
            {
                "state": STATE_PAUSED,
                "generation": 7,
                "pause_reasons": [REASON_SYSTEM_SLEEP, REASON_SCREEN_SLEEP],
                "pause_holds": {
                    REASON_SYSTEM_SLEEP: {"owner": "system", "generation": 7},
                    REASON_SCREEN_SLEEP: {"owner": "system", "generation": 7},
                },
                "owned_units": {"worker": True},
                "pending_resume": None,
                "side_effect_interruptions": {},
                "resume_blockers": [],
                "last_event_id": "will_sleep-x",
                "last_event_kind": "will_sleep",
                "last_event_at": "1",
                "paused_at": "2026-08-04T00:00:00+00:00",
                "wake_received_at": "2026-08-05T00:00:00+00:00",
                "last_error": None,
                "seen_event_ids": [],
            }
        ),
        encoding="utf-8",
    )
    coordinator._token = None  # noqa: SLF001
    coordinator._state = PauseState()  # noqa: SLF001
    coordinator._load()  # noqa: SLF001
    snap = coordinator.snapshot()
    assert snap["state"] == STATE_ACTIVE
    assert snap["pause_reasons"] == []
    assert coordinator.should_claim_jobs() is True


def test_boot_load_keeps_manual_pause(tmp_runtime):
    _settings, runtime = tmp_runtime
    state_path = runtime / "pause_state.json"
    state_path.write_text(
        __import__("json").dumps(
            {
                "state": STATE_PAUSED,
                "generation": 3,
                "pause_reasons": [REASON_MANUAL],
                "pause_holds": {REASON_MANUAL: {"owner": "manual", "generation": 3}},
                "owned_units": {},
                "pending_resume": None,
                "side_effect_interruptions": {},
                "resume_blockers": [],
                "last_event_id": None,
                "last_event_kind": "manual_pause",
                "last_event_at": "1",
                "paused_at": "2026-08-05T00:00:00+00:00",
                "wake_received_at": None,
                "last_error": None,
                "seen_event_ids": [],
            }
        ),
        encoding="utf-8",
    )
    coordinator._token = None  # noqa: SLF001
    coordinator._state = PauseState()  # noqa: SLF001
    coordinator._load()  # noqa: SLF001
    snap = coordinator.snapshot()
    assert snap["state"] == STATE_PAUSED
    assert REASON_MANUAL in snap["pause_reasons"]
    assert coordinator.should_claim_jobs() is False


def test_did_wake_also_clears_screen_sleep(tmp_runtime):
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(
        pause_on_screen_sleep=True,
        wake_settle_seconds=0,
        resume_requires_path_health=False,
        resume_requires_disk_health=False,
    )
    p1 = coordinator.handle_event(
        event_id="sleep-sys",
        kind="will_sleep",
        policy=policy,
        owned_snapshot={},
    )
    gen = int(p1["generation"])
    coordinator.handle_event(
        event_id="sleep-screen",
        kind="screens_sleep",
        policy=policy,
        owned_snapshot={},
    )
    coordinator.mark_paused(generation=gen)
    assert set(coordinator.snapshot()["pause_reasons"]) >= {
        REASON_SYSTEM_SLEEP,
        REASON_SCREEN_SLEEP,
    }
    coordinator.handle_event(
        event_id="wake-both",
        kind="did_wake",
        policy=policy,
        generation_hint=gen,
    )
    checked = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
    assert checked.get("restore_ready") is True
    state = coordinator.complete_resume()
    assert state["state"] == STATE_ACTIVE
    assert state["pause_reasons"] == []


def test_did_wake_tolerates_stale_generation_hint(tmp_runtime):
    """Tauri may send an outdated generation; should still clear current system holds."""
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(
        wake_settle_seconds=0,
        resume_requires_path_health=False,
        resume_requires_disk_health=False,
    )
    paused = coordinator.handle_event(
        event_id="sleep-stale-gen",
        kind="will_sleep",
        policy=policy,
        owned_snapshot={},
    )
    gen = int(paused["generation"])
    coordinator.mark_paused(generation=gen)
    # Wrong / outdated hint (simulates engine restart mid-outbox).
    applied = coordinator.handle_event(
        event_id="wake-stale-gen",
        kind="did_wake",
        policy=policy,
        generation_hint=gen - 1 if gen > 0 else 999999,
    )
    assert applied.get("applied") is True
    checked = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
    assert checked.get("restore_ready") is True
    assert coordinator.complete_resume()["state"] == STATE_ACTIVE


def test_session_active_clears_residual_system_sleep(tmp_runtime):
    _settings, _runtime = tmp_runtime
    policy = SystemEventControl(
        auto_resume_on_session_active=True,
        auto_resume_on_wake=False,
        wake_settle_seconds=0,
        resume_requires_path_health=False,
        resume_requires_disk_health=False,
    )
    paused = coordinator.handle_event(
        event_id="sleep-session",
        kind="will_sleep",
        policy=policy,
        owned_snapshot={},
    )
    gen = int(paused["generation"])
    coordinator.mark_paused(generation=gen)
    coordinator.handle_event(
        event_id="unlock-session",
        kind="session_active",
        policy=policy,
        generation_hint=gen,
    )
    checked = coordinator.apply_resume_after_checks(path_ok=True, disk_ok=True, policy=policy)
    assert checked.get("restore_ready") is True
    assert coordinator.complete_resume()["state"] == STATE_ACTIVE


def test_system_event_control_roundtrip(tmp_runtime):
    _settings, _runtime = tmp_runtime
    from engine.api.app import app

    client = TestClient(app)
    r = client.put(
        "/system/event-control",
        json={"pause_on_screen_sleep": True, "wake_settle_seconds": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pause_on_screen_sleep"] is True
    assert body["wake_settle_seconds"] == 3


def test_system_event_api_requires_persisted_token(tmp_runtime):
    _settings, runtime = tmp_runtime
    token = "a" * 64
    (runtime / "system_token.txt").write_text(token, encoding="utf-8")
    from engine.api.app import app

    client = TestClient(app)
    payload = {"event_id": "token-check", "kind": "unknown"}
    denied = client.post("/system/events", json=payload)
    assert denied.status_code == 401
    accepted = client.post(
        "/system/events",
        json=payload,
        headers={"X-Suying-System-Token": token},
    )
    assert accepted.status_code == 200
    assert accepted.json()["applied"] is False
    assert accepted.json().get("acknowledged") is True


def test_system_event_duplicate_is_acknowledged(tmp_runtime):
    """Desktop must dequeue on duplicate; engine must ACK without blocking on audit DB."""
    _settings, runtime = tmp_runtime
    token = "b" * 64
    (runtime / "system_token.txt").write_text(token, encoding="utf-8")
    from engine.api.app import app

    client = TestClient(app)
    headers = {"X-Suying-System-Token": token}
    first = client.post(
        "/system/events",
        json={"event_id": "wake-dup-1", "kind": "did_wake"},
        headers=headers,
    )
    assert first.status_code == 200
    body1 = first.json()
    assert body1.get("acknowledged") is True
    assert coordinator.has_seen_event("wake-dup-1") is True

    second = client.post(
        "/system/events",
        json={"event_id": "wake-dup-1", "kind": "did_wake"},
        headers=headers,
    )
    assert second.status_code == 200
    body2 = second.json()
    assert body2.get("duplicate") is True
    assert body2.get("applied") is False
    assert body2.get("acknowledged") is True


def test_capture_owned_snapshot_runs(tmp_runtime):
    _settings, _runtime = tmp_runtime
    snap = capture_owned_snapshot()
    assert isinstance(snap, dict)
    assert "worker" in snap
