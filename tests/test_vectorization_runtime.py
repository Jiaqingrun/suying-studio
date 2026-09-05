from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from engine.catalog import db as dbmod
from engine.catalog.db import (
    Asset,
    Cliplet,
    Customer,
    VectorizationQueue,
    VectorizationRun,
)
from engine.catalog.vector_index import EmbeddingCancelled
from engine.catalog.vectorization_runtime import (
    VectorizationExecutor,
    create_run,
    enqueue_asset,
    enqueue_customer_gaps,
    recover_after_restart,
    status_snapshot,
)
from engine.config.settings import AppSettings


@pytest.fixture()
def vector_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = AppSettings()
    settings.paths.data_root = tmp_path / "data"
    settings.paths.library_root = tmp_path / "library"
    settings.paths.output_root = tmp_path / "output"
    settings.paths.cache_root = tmp_path / "cache"
    settings.paths.render_root = tmp_path / "render"
    settings.vectorization_enabled = True
    for path in (
        settings.paths.data_root,
        settings.paths.library_root,
        settings.paths.output_root,
        settings.paths.cache_root,
        settings.paths.render_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("engine.config.settings.load_settings", lambda: settings)
    monkeypatch.setattr(
        "engine.runtime.pause_coordinator.coordinator.should_claim_jobs", lambda: True
    )
    dbmod.reset_engine()
    dbmod.init_db(settings)
    yield settings
    dbmod.reset_engine()


def _customer(name: str = "客户") -> Customer:
    session = dbmod.get_session()
    try:
        row = Customer(name=name, active=True, profile_json={})
        session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row
    finally:
        session.close()


def _asset(customer_id: int, ordinal: int, *, created_at: datetime | None = None) -> Asset:
    session = dbmod.get_session()
    try:
        row = Asset(
            customer_id=customer_id,
            uuid=str(uuid.uuid4()),
            source_path=f"/source/{ordinal}.mp4",
            storage_path=f"/cache/{ordinal}.mp4",
            status="ready",
            duration_sec=3,
            width=1080,
            height=1920,
            orientation="portrait",
            created_at=created_at or datetime.now(timezone.utc),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
        return row
    finally:
        session.close()


def _cliplets(asset: Asset, count: int = 2) -> None:
    session = dbmod.get_session()
    try:
        for idx in range(count):
            session.add(
                Cliplet(
                    asset_id=asset.id,
                    asset_uuid=asset.uuid,
                    start_sec=float(idx),
                    end_sec=float(idx + 1),
                    duration_sec=1,
                    description=f"片段 {idx}",
                    category="default",
                    score=10,
                    status="usable",
                    semantic_schema_version="coarse.v1",
                )
            )
        session.commit()
    finally:
        session.close()


def _wait_status(expected: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = dbmod.get_session()
        try:
            state = status_snapshot(session)
        finally:
            session.close()
        if state["status"] == expected:
            return state
        time.sleep(0.02)
    raise AssertionError(f"未进入 {expected}: {state}")


def test_oldest_first_count_freeze_and_idempotent_enqueue(vector_db) -> None:
    customer = _customer()
    base = datetime.now(timezone.utc) - timedelta(days=1)
    assets = [_asset(customer.id, i, created_at=base + timedelta(seconds=i)) for i in range(4)]
    session = dbmod.get_session()
    try:
        assert status_snapshot(session, customer_id=customer.id)["pending_assets"] == 4
        assert enqueue_customer_gaps(session, customer.id) == 4
        assert enqueue_customer_gaps(session, customer.id) == 0
        run = create_run(session, customer_id=customer.id, mode="count", count=2)
        frozen = list(
            session.scalars(
                select(VectorizationQueue)
                .where(VectorizationQueue.run_id == run.run_id)
                .order_by(VectorizationQueue.enqueued_at, VectorizationQueue.asset_id)
            )
        )
        assert [row.asset_id for row in frozen] == [assets[0].id, assets[1].id]
        assert run.frozen_assets == 2
    finally:
        session.close()


def test_all_cutoff_excludes_new_asset_and_new_material_only_enqueues(vector_db) -> None:
    customer = _customer()
    older = _asset(customer.id, 1)
    session = dbmod.get_session()
    try:
        enqueue_asset(session, older)
        run = create_run(session, customer_id=customer.id, mode="all")
    finally:
        session.close()
    newer = _asset(customer.id, 2, created_at=datetime.now(timezone.utc) + timedelta(seconds=1))
    session = dbmod.get_session()
    try:
        queued = enqueue_asset(session, newer)
        assert queued is not None
        assert queued.run_id is None
        assert queued.status == "QUEUED"
        assert session.scalar(
            select(VectorizationQueue.run_id).where(VectorizationQueue.asset_id == older.id)
        ) == run.run_id
    finally:
        session.close()


def test_machine_single_slot_and_customer_isolation(vector_db) -> None:
    customer_a = _customer("A")
    customer_b = _customer("B")
    asset_a = _asset(customer_a.id, 1)
    asset_b = _asset(customer_b.id, 2)
    session = dbmod.get_session()
    try:
        enqueue_asset(session, asset_a)
        enqueue_asset(session, asset_b)
        run = create_run(session, customer_id=customer_a.id, mode="all")
        with pytest.raises(RuntimeError, match="machine_slot_busy"):
            create_run(session, customer_id=customer_b.id, mode="all")
        rows = list(
            session.scalars(
                select(VectorizationQueue).where(VectorizationQueue.run_id == run.run_id)
            )
        )
        assert {row.customer_id for row in rows} == {customer_a.id}
    finally:
        session.close()


def test_pause_closes_request_within_two_seconds_keeps_commit_and_resumes(
    vector_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    customer = _customer()
    asset = _asset(customer.id, 1)
    _cliplets(asset, 3)
    session = dbmod.get_session()
    try:
        enqueue_asset(session, asset)
        create_run(session, customer_id=customer.id, mode="all")
    finally:
        session.close()

    request_started = threading.Event()

    class BlockingClient:
        def __init__(self) -> None:
            self.closed = threading.Event()

        def close(self) -> None:
            self.closed.set()

    def blocking_index(session, asset, *, cancel_event, client_callback):
        rows = list(
            session.scalars(
                select(Cliplet).where(Cliplet.asset_id == asset.id).order_by(Cliplet.id)
            )
        )
        rows[0].embedding_json = [1.0]
        session.commit()
        client = BlockingClient()
        client_callback(client)
        request_started.set()
        client.closed.wait(5)
        raise EmbeddingCancelled("closed")

    monkeypatch.setattr(
        "engine.catalog.vector_index.index_asset_cliplets", blocking_index
    )
    executor = VectorizationExecutor()
    executor.start()
    executor.wake()
    assert request_started.wait(1)
    started = time.monotonic()
    paused = executor.pause(timeout=2)
    assert time.monotonic() - started < 2
    assert paused["status"] == "PAUSED"
    session = dbmod.get_session()
    try:
        rows = list(
            session.scalars(
                select(Cliplet).where(Cliplet.asset_id == asset.id).order_by(Cliplet.id)
            )
        )
        assert rows[0].embedding_json == [1.0]
        assert rows[1].embedding_json is None
        queue = session.scalar(
            select(VectorizationQueue).where(VectorizationQueue.asset_id == asset.id)
        )
        assert queue.status == "QUEUED"
        assert queue.claim_token is None
    finally:
        session.close()

    def finish_missing(session, asset, *, cancel_event, client_callback):
        done = 0
        for row in session.scalars(
            select(Cliplet).where(Cliplet.asset_id == asset.id).order_by(Cliplet.id)
        ):
            if row.embedding_json is None:
                row.embedding_json = [float(row.id)]
                session.commit()
                done += 1
        return {"completed_cliplets": done}

    monkeypatch.setattr(
        "engine.catalog.vector_index.index_asset_cliplets", finish_missing
    )
    executor.resume()
    completed = _wait_status("COMPLETED")
    assert completed["completed_assets"] == 1
    assert completed["completed_cliplets"] == 3
    executor.stop()


def test_restart_and_system_resume_never_clear_manual_pause(vector_db) -> None:
    customer = _customer()
    asset = _asset(customer.id, 1)
    session = dbmod.get_session()
    try:
        enqueue_asset(session, asset)
        run = create_run(session, customer_id=customer.id, mode="all")
        run.status = "PAUSED"
        run.pause_owner = "manual"
        session.commit()
    finally:
        session.close()
    recover_after_restart()
    executor = VectorizationExecutor()
    state = executor.resume(owner="system")
    assert state["status"] == "PAUSED"
    assert state["pause_owner"] == "manual"
    session = dbmod.get_session()
    try:
        run = session.scalar(select(VectorizationRun))
        run.pause_owner = "system"
        session.commit()
    finally:
        session.close()
    state = executor.resume(owner="system")
    assert state["status"] == "IDLE"
    assert state["pause_owner"] is None


def test_model_error_is_real_failed_state(vector_db, monkeypatch: pytest.MonkeyPatch) -> None:
    customer = _customer()
    asset = _asset(customer.id, 1)
    _cliplets(asset, 1)
    session = dbmod.get_session()
    try:
        enqueue_asset(session, asset)
        create_run(session, customer_id=customer.id, mode="all")
    finally:
        session.close()

    def fail(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("engine.catalog.vector_index.index_asset_cliplets", fail)
    executor = VectorizationExecutor()
    executor.start()
    executor.wake()
    failed = _wait_status("FAILED")
    assert "model unavailable" in failed["error"]
    executor.stop()


def test_vectorization_api_contract_and_compatibility_routes_are_registered(vector_db) -> None:
    from engine.api.app import app

    methods_by_path = {
        route.path: set(route.methods or set())
        for route in app.routes
        if hasattr(route, "methods")
    }
    assert "GET" in methods_by_path["/vectorization/status"]
    for path in (
        "/vectorization/runs",
        "/vectorization/pause",
        "/vectorization/resume",
        "/vectorization/disable",
        "/vectorization/enable",
        "/vectorization/enable-switch",
        "/vectorization/schedule",
        "/assets/reconcile",
    ):
        assert "POST" in methods_by_path[path]


def test_in_local_window_same_day_and_overnight() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from engine.catalog.vectorization_schedule import in_local_window

    tz = ZoneInfo("Asia/Shanghai")
    # 09:00–17:00 same day
    assert in_local_window(datetime(2026, 8, 7, 9, 0, tzinfo=tz), "09:00", "17:00")
    assert in_local_window(datetime(2026, 8, 7, 16, 59, tzinfo=tz), "09:00", "17:00")
    assert not in_local_window(datetime(2026, 8, 7, 17, 0, tzinfo=tz), "09:00", "17:00")
    assert not in_local_window(datetime(2026, 8, 7, 8, 59, tzinfo=tz), "09:00", "17:00")
    # overnight 22:00–07:00
    assert in_local_window(datetime(2026, 8, 7, 22, 0, tzinfo=tz), "22:00", "07:00")
    assert in_local_window(datetime(2026, 8, 7, 3, 0, tzinfo=tz), "22:00", "07:00")
    assert not in_local_window(datetime(2026, 8, 7, 12, 0, tzinfo=tz), "22:00", "07:00")
    assert not in_local_window(datetime(2026, 8, 7, 7, 0, tzinfo=tz), "22:00", "07:00")
    # equal → empty
    assert not in_local_window(datetime(2026, 8, 7, 10, 0, tzinfo=tz), "10:00", "10:00")


def test_schedule_edges_enable_once_and_disable_on_leave(
    vector_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from engine.catalog.vectorization_schedule import apply_schedule_edges
    from engine.config.settings import load_settings

    tz = ZoneInfo("Asia/Shanghai")
    settings = vector_db
    settings.vectorization_enabled = False
    settings.vectorization_schedule_enabled = True
    settings.vectorization_schedule_start = "22:00"
    settings.vectorization_schedule_end = "07:00"
    settings.vectorization_schedule_last_in_window = False

    calls: list[str] = []

    def fake_enable(s=None):
        settings.vectorization_enabled = True
        calls.append("enable")
        return settings

    def fake_disable(s=None):
        settings.vectorization_enabled = False
        calls.append("disable")
        return settings

    monkeypatch.setattr(
        "engine.config.settings.enable_vectorization", fake_enable
    )
    monkeypatch.setattr(
        "engine.config.settings.disable_vectorization", fake_disable
    )
    monkeypatch.setattr(
        "engine.config.settings.load_settings", lambda: settings
    )
    monkeypatch.setattr(
        "engine.config.settings.save_settings", lambda s: None
    )

    # enter window
    r1 = apply_schedule_edges(now=datetime(2026, 8, 7, 22, 5, tzinfo=tz))
    assert "enable_on_enter" in r1["actions"]
    assert calls.count("enable") == 1
    assert settings.vectorization_schedule_last_in_window is True

    # still inside — no second enable
    r2 = apply_schedule_edges(now=datetime(2026, 8, 7, 23, 0, tzinfo=tz))
    assert "enable_on_enter" not in r2["actions"]
    assert calls.count("enable") == 1

    # leave window
    r3 = apply_schedule_edges(now=datetime(2026, 8, 7, 8, 0, tzinfo=tz))
    assert "disable_on_leave" in r3["actions"]
    assert calls.count("disable") == 1
    assert settings.vectorization_schedule_last_in_window is False


def test_auto_stop_when_done_disables_without_pending(
    vector_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engine.catalog.vectorization_schedule import maybe_auto_stop_when_done

    settings = vector_db
    settings.vectorization_enabled = True
    settings.vectorization_auto_stop_when_done = True
    # require active customer for gap check — create empty customer
    customer = _customer()
    monkeypatch.setattr(
        "engine.catalog.customer_scope.require_active_customer",
        lambda session, s: customer,
    )
    disabled: list[bool] = []

    def fake_disable(s=None):
        settings.vectorization_enabled = False
        disabled.append(True)
        return settings

    monkeypatch.setattr(
        "engine.config.settings.disable_vectorization", fake_disable
    )
    monkeypatch.setattr(
        "engine.config.settings.load_settings", lambda: settings
    )

    out = maybe_auto_stop_when_done()
    assert out.get("stopped") is True
    assert disabled == [True]
    assert settings.vectorization_enabled is False

    # second call while disabled: skip
    out2 = maybe_auto_stop_when_done()
    assert out2.get("skipped") == "disabled"


def test_auto_stop_skips_when_pending_exists(
    vector_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engine.catalog.vectorization_schedule import maybe_auto_stop_when_done

    settings = vector_db
    settings.vectorization_enabled = True
    settings.vectorization_auto_stop_when_done = True
    customer = _customer()
    asset = _asset(customer.id, 1)
    _cliplets(asset, 1)  # usable without embedding → pending
    session = dbmod.get_session()
    try:
        enqueue_asset(session, asset)
    finally:
        session.close()
    monkeypatch.setattr(
        "engine.catalog.customer_scope.require_active_customer",
        lambda session, s: customer,
    )
    monkeypatch.setattr(
        "engine.config.settings.load_settings", lambda: settings
    )
    out = maybe_auto_stop_when_done()
    assert out.get("skipped") == "pending"
    assert settings.vectorization_enabled is True
