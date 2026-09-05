"""Unit tests for stale job reap + ResourceGate isolation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.catalog.db import Base, Customer, Job, log_event
from engine.jobs.stale import reap_stale_running_jobs
from engine.ops.install_profile import render_concurrency_for_tier
from engine.runtime.resource_gate import ResourceGate


def test_render_concurrency_by_tier() -> None:
    assert render_concurrency_for_tier("lite") == 1
    assert render_concurrency_for_tier("standard") == 1
    assert render_concurrency_for_tier("pro") == 1
    assert render_concurrency_for_tier("max") == 2


def test_resource_gate_publish_and_render_do_not_block_each_other() -> None:
    gate = ResourceGate()
    gate.configure(render_slots=1, tts_slots=1, publish_slots=1, produce_slots=1)
    assert gate.try_acquire("render", "job:1")
    assert gate.try_acquire("publish", "pub:a")
    assert not gate.try_acquire("render", "job:2")
    assert not gate.try_acquire("publish", "pub:b")
    assert gate.try_acquire("tts", "job:1")
    gate.release("tts", "job:1")
    gate.release("render", "job:1")
    assert gate.try_acquire("render", "job:2")
    gate.release_all("job:2")
    gate.release_all("pub:a")


def test_resource_gate_same_token_reentrant() -> None:
    gate = ResourceGate()
    gate.configure(render_slots=1, tts_slots=1)
    assert gate.try_acquire("render", "job:1")
    assert gate.try_acquire("render", "job:1")  # re-enter same token
    assert not gate.try_acquire("render", "job:2")
    gate.release("render", "job:1")
    assert gate.try_acquire("render", "job:2")
    gate.release_all("job:2")


def test_resource_gate_host_pressure_refuses_heavy_slots() -> None:
    gate = ResourceGate()
    gate.configure(ollama_heavy_slots=1, tts_slots=1, render_slots=1)

    class _TinyHost:
        ram_gb = 4.0

    with patch("engine.catalog.host_profile.probe_host", return_value=_TinyHost()):
        assert not gate.try_acquire("ollama_heavy", "narration:x")
        assert not gate.try_acquire("tts", "tts:x")
        assert not gate.try_acquire("render", "job:x")
        # publish is not memory-gated in resource_gate
        assert gate.try_acquire("publish", "pub:x")
        refused = gate.snapshot()["last_refuse"]
        assert refused.get("ollama_heavy", {}).get("reason") == "host_pressure"
        gate.release_all("pub:x")


def test_resource_gate_lease_expires_ollama_heavy() -> None:
    gate = ResourceGate()
    gate.configure(ollama_heavy_slots=1, tts_slots=1, render_slots=1)
    assert gate.try_acquire("ollama_heavy", "narration:stuck")
    assert not gate.try_acquire("ollama_heavy", "narration:other")
    # Pretend holder is ancient
    pool = gate._pools["ollama_heavy"]
    pool.holders["narration:stuck"] = time.monotonic() - 200.0
    released = gate.force_release_expired("ollama_heavy", max_age_sec=120.0)
    assert released.get("ollama_heavy") == ["narration:stuck"]
    assert gate.try_acquire("ollama_heavy", "narration:other")
    snap = gate.snapshot()
    assert snap["pools"]["ollama_heavy"]["holders_detail"]
    assert snap.get("last_lease_reap", {}).get("released", {}).get("ollama_heavy") == [
        "narration:stuck"
    ]
    gate.release("ollama_heavy", "narration:other")


def test_resource_gate_sweep_default_leases() -> None:
    gate = ResourceGate()
    gate.configure(ollama_heavy_slots=1, tts_slots=1)
    assert gate.try_acquire("ollama_heavy", "narration:old")
    gate._pools["ollama_heavy"].holders["narration:old"] = time.monotonic() - 999.0
    released = gate.sweep_default_leases()
    assert "ollama_heavy" in released
    assert gate.try_acquire("ollama_heavy", "narration:fresh")
    gate.release_all("narration:fresh")


def test_resource_gate_unknown_slot_refused() -> None:
    gate = ResourceGate()
    assert not gate.try_acquire("not_a_slot", "t1")
    assert gate.snapshot()["last_refuse"]["not_a_slot"]["reason"] == "unknown_slot"


def test_reap_stale_running_jobs(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'stale.db'}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        customer = Customer(name="perf-test")
        session.add(customer)
        session.flush()
        now = datetime.now(timezone.utc)
        stale = Job(
            customer_id=customer.id,
            status="running",
            theme="t",
            template_name="default-vertical",
            mode="count",
            target_count=1,
            produced_count=0,
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=1),
            config_snapshot_json={},
        )
        fresh = Job(
            customer_id=customer.id,
            status="running",
            theme="t2",
            template_name="default-vertical",
            mode="count",
            target_count=1,
            produced_count=0,
            created_at=now,
            updated_at=now,
            config_snapshot_json={},
        )
        held = Job(
            customer_id=customer.id,
            status="running",
            theme="t3",
            template_name="default-vertical",
            mode="count",
            target_count=1,
            produced_count=0,
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=1),
            config_snapshot_json={},
        )
        session.add_all([stale, fresh, held])
        session.commit()
        # Fresh activity event so fresh stays running even if updated_at were old.
        log_event(session, fresh.id, "info", "heartbeat")
        session.commit()

        reaped = reap_stale_running_jobs(
            session,
            held_job_id=held.id,
            stale_after_sec=600,
        )
        session.refresh(stale)
        session.refresh(fresh)
        session.refresh(held)
        assert stale.status == "queued"
        assert fresh.status == "running"
        assert held.status == "running"
        assert any(item["id"] == stale.id for item in reaped)
        assert all(item["id"] != held.id for item in reaped)
        assert all(item["id"] != fresh.id for item in reaped)
    finally:
        session.close()
