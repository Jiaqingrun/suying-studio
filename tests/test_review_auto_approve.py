"""Mandatory READY_GATE review decisions and publication gate."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from engine.catalog.db import (
    Base,
    Customer,
    Job,
    RenderOutput,
    ReviewItem,
    _migrate_review_decisions,
    get_session,
    init_db,
    reset_engine,
)
from engine.config.settings import AppSettings, PathConfig, save_settings
from engine.catalog.review_auto import (
    AUTO_APPROVE_NOTE,
    BATCH_MANUAL_APPROVE_NOTE,
    backfill_auto_approve,
    batch_manual_approve,
    ensure_output_decision,
    has_verified_ready_gate,
    latest_review,
    maybe_auto_approve_ready,
    merge_review_prefs,
    record_review_decision,
    review_prefs,
)
from engine.reach.publish_sources import has_current_approved_review


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _customer(session: Session, *, auto: bool = False) -> Customer:
    profile = merge_review_prefs({}, {"auto_approve_on_ready": auto}) if auto else {}
    row = Customer(name="审片自动过审测试", profile_json=profile)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _job(session: Session, customer: Customer) -> Job:
    job = Job(
        customer_id=customer.id,
        mode="count",
        target_count=1,
        status="running",
        theme="default",
        category="default",
        template_name="default-vertical",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _output(
    session: Session,
    job: Job,
    *,
    state: str = "ready",
    gate_ok: bool | None = True,
) -> RenderOutput:
    qc: dict = {"passed": True, "reasons": [], "metrics": {}}
    if gate_ok is not None:
        qc["ready_gate"] = {"ok": gate_ok, "fails": [], "fail_count": 0}
    row = RenderOutput(
        job_id=job.id,
        output_path="/tmp/nonexistent-auto-approve.mp4",
        state=state,
        seed=1,
        sidecar_path="",
        qc_json=qc,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_review_prefs_mandatory_and_cannot_be_disabled() -> None:
    assert review_prefs(None)["auto_approve_on_ready"] is True
    assert review_prefs({})["auto_approve_on_ready"] is True
    merged = merge_review_prefs({"brand": {"display_name": "X"}}, {"auto_approve_on_ready": True})
    assert merged["brand"]["display_name"] == "X"
    assert merged["review"]["auto_approve_on_ready"] is True
    locked = merge_review_prefs({}, {"auto_approve_on_ready": False})
    assert locked["review"]["auto_approve_on_ready"] is True


def test_default_customer_auto_approves() -> None:
    session = _session()
    try:
        customer = _customer(session, auto=False)
        job = _job(session, customer)
        out = _output(session, job)
        result = maybe_auto_approve_ready(session, customer, out, source="test", export_pack=False)
        assert result["ok"] is True
        assert result["decision"] == "approved"
        assert session.query(ReviewItem).count() == 1
    finally:
        session.close()


def test_ready_gate_missing_becomes_uncertain_not_approved() -> None:
    session = _session()
    try:
        customer = _customer(session, auto=True)
        job = _job(session, customer)
        out = _output(session, job, gate_ok=None)
        out.qc_json = {"passed": True}
        session.commit()
        result = ensure_output_decision(
            session, customer, out, source="test", export_pack=False
        )
        assert result["decision"] == "uncertain"
        assert out.state == "review"
        assert latest_review(session, out.id).status == "uncertain"
    finally:
        session.close()


def test_review_state_with_gate_ok_auto_approves() -> None:
    session = _session()
    try:
        customer = _customer(session, auto=True)
        job = _job(session, customer)
        out = _output(session, job, state="review", gate_ok=True)
        result = ensure_output_decision(
            session, customer, out, source="test", export_pack=False
        )
        assert result["decision"] == "approved"
        assert out.state == "ready"
        assert latest_review(session, out.id).status == "approved"
    finally:
        session.close()


def test_review_state_without_gate_enters_uncertain_queue() -> None:
    session = _session()
    try:
        customer = _customer(session, auto=True)
        job = _job(session, customer)
        out = _output(session, job, state="review", gate_ok=False)
        result = ensure_output_decision(
            session, customer, out, source="test", export_pack=False
        )
        assert result["decision"] == "uncertain"
        assert session.query(ReviewItem).filter_by(status="uncertain").count() == 1
    finally:
        session.close()


def test_migration_uncertain_with_gate_ok_upgrades_to_approved() -> None:
    session = _session()
    try:
        customer = _customer(session, auto=False)
        out = _output(session, _job(session, customer), state="review", gate_ok=True)
        record_review_decision(
            session,
            out,
            status="uncertain",
            note="待人工：审片状态或证据冲突",
            source="migration",
            decision_key=f"migration-output:{out.id}",
        )
        session.commit()
        result = ensure_output_decision(
            session, customer, out, source="backfill", export_pack=False
        )
        assert result["ok"] is True
        assert result["decision"] == "approved"
        assert result["created"] is True
        assert out.state == "ready"
        assert latest_review(session, out.id).status == "approved"
    finally:
        session.close()


def test_auto_approve_writes_review_item_idempotent() -> None:
    session = _session()
    try:
        customer = _customer(session, auto=True)
        job = _job(session, customer)
        out = _output(session, job)
        first = maybe_auto_approve_ready(session, customer, out, source="test", export_pack=False)
        session.commit()
        assert first["ok"] is True
        assert first.get("review_id")
        item = session.get(ReviewItem, first["review_id"])
        assert item is not None
        assert item.status == "approved"
        assert item.note == AUTO_APPROVE_NOTE
        assert item.is_current is True
        assert item.decided_at is not None

        second = maybe_auto_approve_ready(session, customer, out, source="test", export_pack=False)
        assert second["ok"] is True
        assert second.get("skipped") == "already_approved"
        assert session.query(ReviewItem).count() == 1
    finally:
        session.close()


def test_backfill_is_mandatory_and_customer_scoped() -> None:
    session = _session()
    try:
        customer = _customer(session, auto=False)
        other = Customer(name="另一个客户", profile_json={})
        session.add(other)
        session.commit()
        _output(session, _job(session, customer))
        other_output = _output(session, _job(session, other))
        result = backfill_auto_approve(session, customer, limit=10)
        assert result["ok"] is True
        assert result["approved"] == 1
        assert session.query(ReviewItem).filter(ReviewItem.status == "approved").count() == 1
        assert latest_review(session, other_output.id) is None
    finally:
        session.close()


def test_gate_ok_false_is_uncertain_and_not_publishable() -> None:
    session = _session()
    try:
        customer = _customer(session, auto=True)
        job = _job(session, customer)
        out = _output(session, job, gate_ok=False)
        result = ensure_output_decision(
            session, customer, out, source="test", export_pack=False
        )
        assert result["decision"] == "uncertain"
        assert not has_current_approved_review(session, out.id)
    finally:
        session.close()


def test_hard_failure_rejected_and_latest_decision_is_unique() -> None:
    session = _session()
    try:
        customer = _customer(session)
        out = _output(session, _job(session, customer))
        ensure_output_decision(session, customer, out, source="test")
        out.state = "failed"
        item, created = record_review_decision(
            session,
            out,
            status="rejected",
            note="[other] 人工打回",
            source="manual",
        )
        session.commit()
        assert created is True
        assert item.status == "rejected"
        assert latest_review(session, out.id).id == item.id
        assert (
            session.query(ReviewItem)
            .filter_by(render_output_id=out.id, is_current=True)
            .count()
            == 1
        )
        duplicate, duplicate_created = record_review_decision(
            session,
            out,
            status="rejected",
            note="[other] 人工打回",
            source="manual",
        )
        assert duplicate.id == item.id
        assert duplicate_created is False
        assert session.query(ReviewItem).filter_by(render_output_id=out.id).count() == 2
    finally:
        session.close()


def test_concurrent_auto_decision_is_idempotent(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'review.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sessions() as seed:
        customer = _customer(seed)
        output = _output(seed, _job(seed, customer))
        customer_id, output_id = customer.id, output.id

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def decide() -> None:
        try:
            with sessions() as session:
                customer = session.get(Customer, customer_id)
                output = session.get(RenderOutput, output_id)
                barrier.wait(timeout=5)
                ensure_output_decision(
                    session, customer, output, source="worker", export_pack=False
                )
                session.commit()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=decide) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    with sessions() as session:
        rows = session.query(ReviewItem).filter_by(render_output_id=output_id).all()
        assert len(rows) == 1
        assert rows[0].status == "approved"
        assert rows[0].is_current is True


def test_legacy_sqlite_review_migration_is_incremental_and_idempotent(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE render_outputs ("
                "id INTEGER PRIMARY KEY, state VARCHAR(32), qc_json JSON)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE review_items ("
                "id INTEGER PRIMARY KEY, render_output_id INTEGER, "
                "status VARCHAR(32), note TEXT, created_at DATETIME, decided_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO render_outputs(id,state,qc_json) VALUES "
                "(1,'ready',:passed),(2,'review',:passed),(3,'failed',:failed)"
            ),
            {
                "passed": '{"ready_gate":{"ok":true}}',
                "failed": '{"ready_gate":{"ok":false}}',
            },
        )
        conn.execute(
            text(
                "INSERT INTO review_items(id,render_output_id,status,note) VALUES "
                "(1,2,'approved','旧决定'),(2,2,'pending','最新待定')"
            )
        )
        _migrate_review_decisions(conn)
        _migrate_review_decisions(conn)

        rows = conn.execute(
            text(
                "SELECT render_output_id,status,is_current FROM review_items "
                "ORDER BY render_output_id,id"
            )
        ).fetchall()
        assert rows == [
            (1, "approved", 1),
            (2, "approved", 0),
            (2, "uncertain", 1),
            (3, "rejected", 1),
        ]


def test_reviews_and_reports_api_use_current_customer_decisions(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = tmp_path / "boot"
    data = tmp_path / "data"
    library = tmp_path / "library"
    output_root = tmp_path / "outputs"
    runtime = tmp_path / "runtime"
    for path in (boot, data, library, output_root, runtime):
        path.mkdir()
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    monkeypatch.setenv("MONTAGE_DATA_ROOT", str(boot))
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(runtime))
    settings = AppSettings(
        paths=PathConfig(
            data_root=data,
            library_root=library,
            library_roots=[library],
            output_root=output_root,
            cache_root=data / "cache",
            render_root=data / "render",
        ),
        active_customer="API 审片客户",
        onboarded=True,
    )
    save_settings(settings)
    reset_engine()
    init_db(settings)
    session = get_session()
    try:
        customer = session.query(Customer).filter_by(name="API 审片客户").one()
        ready = _output(session, _job(session, customer), state="ready", gate_ok=True)
        uncertain = _output(
            session, _job(session, customer), state="review", gate_ok=False
        )
        ensure_output_decision(session, customer, ready, source="test")
        ensure_output_decision(session, customer, uncertain, source="test")
        session.commit()
        uncertain_id = uncertain.id
    finally:
        session.close()

    from engine.api.app import app

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    open_rows = client.get("/reviews?scope=open").json()
    assert [row["render_output_id"] for row in open_rows] == [uncertain_id]
    history = client.get("/reviews?scope=history").json()
    assert {row["status"] for row in history} == {"approved", "uncertain"}
    summary = client.get("/reports/summary").json()
    assert summary["reviews"] == {"approved": 1, "uncertain": 1}
    reset_engine()


def _attach_media_and_sidecar(
    tmp_path,
    output: RenderOutput,
    session: Session,
    *,
    has_voice: bool = True,
    subtitle_burned: bool = True,
    tts_provider: str = "edge",
) -> None:
    import json

    media = tmp_path / f"out_{output.id}.mp4"
    media.write_bytes(b"fake-mp4")
    sidecar = tmp_path / f"out_{output.id}.json"
    meta: dict = {
        "subtitle_burned": subtitle_burned,
        "tts_provider": tts_provider,
    }
    if has_voice:
        meta["narration_path"] = str(tmp_path / f"out_{output.id}.voice.wav")
        (tmp_path / f"out_{output.id}.voice.wav").write_bytes(b"wav")
    sidecar.write_text(
        json.dumps({"title": "t", "meta": meta}, ensure_ascii=False),
        encoding="utf-8",
    )
    output.output_path = str(media)
    output.sidecar_path = str(sidecar)
    session.commit()


def test_batch_manual_approve_skips_without_ready_gate(tmp_path) -> None:
    session = _session()
    try:
        customer = _customer(session)
        job = _job(session, customer)
        out = _output(session, job, state="review", gate_ok=False)
        _attach_media_and_sidecar(tmp_path, out, session)
        record_review_decision(
            session,
            out,
            status="uncertain",
            note="待人工：审片状态或证据冲突",
            source="worker",
        )
        session.commit()
        result = batch_manual_approve(
            session, customer, skip_known_issues=False, limit=10, video_lock={}
        )
        assert result["approved"] == 0
        assert result["skipped_gate"] >= 1
        assert latest_review(session, out.id).status == "uncertain"
    finally:
        session.close()


def test_batch_manual_approve_skips_known_issues_when_enabled(tmp_path) -> None:
    session = _session()
    try:
        customer = _customer(session)
        job = _job(session, customer)
        dirty = _output(session, job, state="review", gate_ok=True)
        clean = _output(session, _job(session, customer), state="review", gate_ok=True)
        _attach_media_and_sidecar(tmp_path, dirty, session, has_voice=False)
        _attach_media_and_sidecar(tmp_path, clean, session, has_voice=True)
        for out in (dirty, clean):
            record_review_decision(
                session,
                out,
                status="uncertain",
                note="待人工：审片状态或证据冲突",
                source="worker",
            )
        session.commit()

        skipped = batch_manual_approve(
            session, customer, skip_known_issues=True, limit=10, video_lock={}
        )
        assert skipped["approved"] == 1
        assert skipped["skipped_known"] >= 1
        assert latest_review(session, clean.id).status == "approved"
        assert latest_review(session, clean.id).note == BATCH_MANUAL_APPROVE_NOTE
        assert latest_review(session, dirty.id).status == "uncertain"

        forced = batch_manual_approve(
            session, customer, skip_known_issues=False, limit=10, video_lock={}
        )
        assert forced["approved"] == 1
        assert latest_review(session, dirty.id).status == "approved"
    finally:
        session.close()


def test_batch_manual_approve_api_endpoint(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boot = tmp_path / "boot"
    data = tmp_path / "data"
    library = tmp_path / "library"
    output_root = tmp_path / "outputs"
    runtime = tmp_path / "runtime"
    for path in (boot, data, library, output_root, runtime):
        path.mkdir()
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    monkeypatch.setenv("MONTAGE_DATA_ROOT", str(boot))
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(runtime))
    settings = AppSettings(
        paths=PathConfig(
            data_root=data,
            library_root=library,
            library_roots=[library],
            output_root=output_root,
            cache_root=data / "cache",
            render_root=data / "render",
        ),
        active_customer="批量审片客户",
        onboarded=True,
    )
    save_settings(settings)
    reset_engine()
    init_db(settings)
    session = get_session()
    try:
        customer = session.query(Customer).filter_by(name="批量审片客户").one()
        out = _output(session, _job(session, customer), state="review", gate_ok=True)
        _attach_media_and_sidecar(tmp_path, out, session)
        record_review_decision(
            session,
            out,
            status="uncertain",
            note="待人工：审片状态或证据冲突",
            source="worker",
        )
        session.commit()
    finally:
        session.close()

    from engine.api.app import app

    client = TestClient(app)
    res = client.post("/review/batch-approve?skip_known_issues=true&limit=10")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["approved"] == 1
    assert body["skip_known_issues"] is True
    reset_engine()


def test_reconcile_approves_when_gate_ok(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.catalog.review_auto import RECONCILE_APPROVE_NOTE, reconcile_ready_gate_output

    session = _session()
    try:
        customer = _customer(session)
        out = _output(session, _job(session, customer), state="review", gate_ok=None)
        out.qc_json = {"passed": True}
        _attach_media_and_sidecar(tmp_path, out, session)
        record_review_decision(
            session,
            out,
            status="uncertain",
            note="待人工：审片状态或证据冲突",
            source="worker",
        )
        session.commit()

        monkeypatch.setattr(
            "engine.qc.ready_gate.evaluate_ready_gate",
            lambda *a, **k: {
                "ok": True,
                "fails": [],
                "fail_count": 0,
                "checks": {},
            },
        )
        # Avoid pack export needing full pipeline.
        monkeypatch.setattr(
            "engine.catalog.review_auto.ensure_publish_pack",
            lambda *a, **k: {"ok": True, "skipped": "test"},
        )
        result = reconcile_ready_gate_output(
            session, customer, out, export_pack=True
        )
        assert result["action"] == "approved"
        assert result["ready_gate_ok"] is True
        assert has_verified_ready_gate(out.qc_json)
        assert out.state == "ready"
        rev = latest_review(session, out.id)
        assert rev is not None
        assert rev.status == "approved"
        assert rev.note in {AUTO_APPROVE_NOTE, RECONCILE_APPROVE_NOTE}
    finally:
        session.close()


def test_reconcile_archives_missing_media() -> None:
    from engine.catalog.review_auto import ARCHIVE_MISSING_NOTE, reconcile_ready_gate_output

    session = _session()
    try:
        customer = _customer(session)
        out = _output(session, _job(session, customer), state="review", gate_ok=None)
        out.qc_json = {"passed": True}
        out.output_path = "/tmp/does-not-exist-suying-gate.mp4"
        session.commit()
        record_review_decision(
            session,
            out,
            status="uncertain",
            note="待人工：审片状态或证据冲突",
            source="worker",
        )
        session.commit()
        result = reconcile_ready_gate_output(
            session, customer, out, archive_missing=True, export_pack=False
        )
        assert result["action"] == "archived_missing"
        assert out.state == "failed"
        rev = latest_review(session, out.id)
        assert rev is not None
        assert rev.status == "rejected"
        assert rev.note == ARCHIVE_MISSING_NOTE
    finally:
        session.close()


def test_reconcile_writes_fails_without_approve(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engine.catalog.review_auto import reconcile_ready_gate_output

    session = _session()
    try:
        customer = _customer(session)
        out = _output(session, _job(session, customer), state="review", gate_ok=None)
        out.qc_json = {"passed": True}
        _attach_media_and_sidecar(tmp_path, out, session)
        record_review_decision(
            session,
            out,
            status="uncertain",
            note="待人工：审片状态或证据冲突",
            source="worker",
        )
        session.commit()
        monkeypatch.setattr(
            "engine.qc.ready_gate.evaluate_ready_gate",
            lambda *a, **k: {
                "ok": False,
                "fails": ["basic: too small"],
                "fail_count": 1,
                "checks": {"basic": ["basic: too small"]},
            },
        )
        result = reconcile_ready_gate_output(
            session, customer, out, archive_gate_fail=False, export_pack=False
        )
        assert result["action"] == "gate_failed"
        assert result["ready_gate_ok"] is False
        assert "basic: too small" in (result.get("ready_gate_fails") or [])
        assert has_verified_ready_gate(out.qc_json) is False
        assert (out.qc_json or {}).get("ready_gate", {}).get("ok") is False
        assert latest_review(session, out.id).status == "uncertain"
    finally:
        session.close()


def test_manual_approve_cannot_bypass_gate_without_file() -> None:
    session = _session()
    try:
        customer = _customer(session)
        out = _output(session, _job(session, customer), state="review", gate_ok=None)
        out.qc_json = {"passed": True}
        session.commit()
        # Library-level guard remains strict when no verified gate.
        from engine.catalog.review_auto import has_verified_ready_gate as h

        assert h(out.qc_json) is False
    finally:
        session.close()


def test_archive_unusable_manual() -> None:
    from engine.catalog.review_auto import ARCHIVE_MANUAL_NOTE, archive_unusable_output

    session = _session()
    try:
        customer = _customer(session)
        out = _output(session, _job(session, customer), state="review", gate_ok=False)
        record_review_decision(
            session,
            out,
            status="uncertain",
            note="待人工：审片状态或证据冲突",
            source="worker",
        )
        session.commit()
        result = archive_unusable_output(session, customer, out, reason="manual")
        assert result["action"] == "archived"
        assert out.state == "failed"
        assert latest_review(session, out.id).note == ARCHIVE_MANUAL_NOTE
    finally:
        session.close()
