"""PL-01 / PL-15: pause + stale reap must release ResourceGate job tokens."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.catalog.db import Base, Customer, Job
from engine.jobs.gate_release import (
    PAUSE_GATE_RELEASE_AFTER_SEC,
    _reset_for_tests,
    clear_job_cancel,
    is_job_cancel_requested,
    release_job_gate_tokens,
    request_job_cancel,
    schedule_pause_gate_release,
)
from engine.jobs.queue import pause_job, resume_job
from engine.jobs.stale import reap_stale_running_jobs
from engine.runtime.resource_gate import ResourceGate


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'gate_rel.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _job(session, *, status: str = "running", age_hours: float = 1.0) -> Job:
    customer = session.query(Customer).first()
    if customer is None:
        customer = Customer(name="gate-rel-test")
        session.add(customer)
        session.flush()
    now = datetime.now(timezone.utc)
    job = Job(
        customer_id=customer.id,
        status=status,
        theme="t",
        template_name="default-vertical",
        mode="count",
        target_count=1,
        produced_count=0,
        created_at=now - timedelta(hours=age_hours + 1),
        updated_at=now - timedelta(hours=age_hours),
        config_snapshot_json={},
    )
    session.add(job)
    session.commit()
    return job


def setup_function() -> None:
    _reset_for_tests()


def teardown_function() -> None:
    _reset_for_tests()


def test_pause_job_sets_cooperative_cancel_and_releases_after_timeout(tmp_path) -> None:
    session = _session(tmp_path)
    gate = ResourceGate()
    gate.configure(render_slots=1, tts_slots=1, ollama_heavy_slots=1)
    try:
        job = _job(session, status="running")
        assert gate.try_acquire("render", f"job:{job.id}")
        assert gate.try_acquire("tts", f"job:{job.id}:tts")
        assert not gate.try_acquire("render", "job:other")

        with patch("engine.runtime.resource_gate.gate", gate), patch(
            "engine.jobs.gate_release.PAUSE_GATE_RELEASE_AFTER_SEC", 0.12
        ):
            paused = pause_job(session, job.id)
            assert paused is not None
            assert paused.status == "paused"
            assert is_job_cancel_requested(job.id)
            # Cooperative window: still held briefly
            assert f"job:{job.id}" in gate._pools["render"].holders

            deadline = time.monotonic() + 2.0
            while (
                time.monotonic() < deadline
                and f"job:{job.id}" in gate._pools["render"].holders
            ):
                time.sleep(0.04)

            assert f"job:{job.id}" not in gate._pools["render"].holders
            assert f"job:{job.id}:tts" not in gate._pools["tts"].holders
            assert gate.try_acquire("render", "job:other")
            gate.release_all("job:other")
    finally:
        session.close()
        _reset_for_tests()


def test_pause_timeout_releases_only_job_tokens_not_foreign() -> None:
    gate = ResourceGate()
    gate.configure(render_slots=2, tts_slots=1, publish_slots=1)
    assert gate.try_acquire("render", "job:9")
    assert gate.try_acquire("render", "job:8")
    assert gate.try_acquire("publish", "pub:x")

    info = release_job_gate_tokens(9, reason="test", gate=gate)
    assert "render" in info["released_slots"]
    assert "job:9" not in gate._pools["render"].holders
    assert "job:8" in gate._pools["render"].holders
    assert "pub:x" in gate._pools["publish"].holders
    gate.release_all("job:8")
    gate.release_all("pub:x")


def test_pause_does_not_invoke_process_kill() -> None:
    """Timeout path must free slots without killing ffmpeg/ollama processes."""
    gate = ResourceGate()
    gate.configure(render_slots=1)
    assert gate.try_acquire("render", "job:42")

    with patch("subprocess.Popen") as popen, patch("os.kill") as kill:
        schedule_pause_gate_release(42, after_sec=0.05, gate=gate)
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and "job:42" in gate._pools["render"].holders:
            time.sleep(0.04)
        assert "job:42" not in gate._pools["render"].holders
        popen.assert_not_called()
        kill.assert_not_called()


def test_resume_clears_cancel_and_timer(tmp_path) -> None:
    session = _session(tmp_path)
    try:
        job = _job(session, status="paused", age_hours=0.1)
        request_job_cancel(job.id)
        schedule_pause_gate_release(job.id, after_sec=30.0)
        assert is_job_cancel_requested(job.id)
        resumed = resume_job(session, job.id)
        assert resumed is not None
        assert resumed.status == "queued"
        assert not is_job_cancel_requested(job.id)
    finally:
        session.close()
        _reset_for_tests()


def test_reap_stale_releases_orphaned_gate_tokens(tmp_path) -> None:
    session = _session(tmp_path)
    gate = ResourceGate()
    gate.configure(render_slots=1, tts_slots=1)
    try:
        stale = _job(session, status="running", age_hours=2.0)
        held = _job(session, status="running", age_hours=2.0)
        assert gate.try_acquire("render", f"job:{stale.id}")
        assert gate.try_acquire("tts", f"job:{stale.id}:tts")

        with patch("engine.runtime.resource_gate.gate", gate):
            reaped = reap_stale_running_jobs(
                session,
                held_job_id=held.id,
                stale_after_sec=600,
            )

        session.refresh(stale)
        session.refresh(held)
        assert stale.status == "queued"
        assert held.status == "running"
        assert any(item["id"] == stale.id for item in reaped)
        assert f"job:{stale.id}" not in gate._pools["render"].holders
        assert f"job:{stale.id}:tts" not in gate._pools["tts"].holders
        assert gate.try_acquire("render", f"job:{held.id}")
        gate.release_all(f"job:{held.id}")
    finally:
        session.close()
        _reset_for_tests()


def test_cooperative_cancel_flag_roundtrip() -> None:
    clear_job_cancel(7)
    assert not is_job_cancel_requested(7)
    request_job_cancel(7)
    assert is_job_cancel_requested(7)
    clear_job_cancel(7)
    assert not is_job_cancel_requested(7)


def test_pause_gate_release_default_is_bounded() -> None:
    assert 1.0 <= float(PAUSE_GATE_RELEASE_AFTER_SEC) <= 30.0
