from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from engine.catalog.db import (
    Base,
    Customer,
    Job,
    PublicationGroup,
    PublicationTarget,
    RenderOutput,
    ReviewItem,
    get_session,
    init_db,
    reset_engine,
)
from engine.config.settings import AppSettings, PathConfig, save_settings
from engine.ops.reporting import build_report_snapshot


def _output(
    session: Session,
    customer: Customer,
    *,
    state: str,
    gate_ok: bool,
    created_at: datetime,
) -> RenderOutput:
    job = Job(
        customer_id=customer.id,
        status="completed",
        template_name="default",
        created_at=created_at,
    )
    session.add(job)
    session.flush()
    output = RenderOutput(
        job_id=job.id,
        output_path=f"/tmp/report-{customer.id}-{job.id}.mp4",
        state=state,
        seed=job.id,
        qc_json={"ready_gate": {"ok": gate_ok}},
        created_at=created_at,
    )
    session.add(output)
    session.flush()
    return output


def test_report_facts_deduplicate_current_review_and_publication_group(tmp_path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        customer = Customer(name="统计客户", output_root=str(tmp_path), profile_json={})
        other = Customer(name="其他客户", output_root=str(tmp_path / "other"), profile_json={})
        session.add_all([customer, other])
        session.flush()
        first = _output(
            session,
            customer,
            state="ready",
            gate_ok=True,
            created_at=datetime(2026, 7, 29, 15, 59, tzinfo=timezone.utc),
        )
        second = _output(
            session,
            customer,
            state="ready",
            gate_ok=True,
            created_at=datetime(2026, 7, 29, 16, 1, tzinfo=timezone.utc),
        )
        failed = _output(
            session,
            customer,
            state="failed",
            gate_ok=False,
            created_at=datetime(2026, 7, 29, 16, 2, tzinfo=timezone.utc),
        )
        _output(
            session,
            other,
            state="failed",
            gate_ok=False,
            created_at=datetime(2026, 7, 29, 16, 3, tzinfo=timezone.utc),
        )
        session.add_all(
            [
                ReviewItem(
                    render_output_id=first.id,
                    status="approved",
                    is_current=False,
                    decision_source="manual",
                ),
                ReviewItem(
                    render_output_id=first.id,
                    status="uncertain",
                    is_current=True,
                    decision_source="manual",
                ),
                ReviewItem(
                    render_output_id=second.id,
                    status="approved",
                    is_current=True,
                    decision_source="worker",
                ),
                ReviewItem(
                    render_output_id=failed.id,
                    status="rejected",
                    is_current=True,
                    decision_source="manual",
                ),
            ]
        )
        group = PublicationGroup(
            customer_id=customer.id,
            output_id=first.id,
            group_key="report-group",
            status="isolated",
        )
        session.add(group)
        session.flush()
        published_at = datetime(2026, 7, 29, 16, 5, tzinfo=timezone.utc)
        session.add_all(
            [
                PublicationTarget(
                    group_id=group.id,
                    customer_id=customer.id,
                    output_id=first.id,
                    platform="douyin",
                    account_key="a",
                    status="published",
                    published_at=published_at,
                ),
                PublicationTarget(
                    group_id=group.id,
                    customer_id=customer.id,
                    output_id=first.id,
                    platform="xhs",
                    account_key="b",
                    status="published",
                    published_at=published_at,
                ),
            ]
        )
        session.commit()

        now = datetime(2026, 7, 29, 16, 30, tzinfo=timezone.utc)
        before = build_report_snapshot(
            session, customer_id=customer.id, output_root=tmp_path, now=now
        )
        assert before["timezone"] == "Asia/Shanghai"
        assert before["business_date"] == "2026-07-30"
        assert before["production_passed_today"] == 1
        assert before["production_passed"] == 2
        assert before["failed"] == 1
        assert before["quality_pass_rate"] == 0.6667
        assert before["uncertain_open"] == 1
        assert before["auto_approved"] == 1
        assert before["manual_decided"] == 1
        assert before["published"] == 1
        assert before["published_today"] == 1
        assert before["ready_available"] == 1
        assert before["reconciliation"]["filesystem"]["ready_files"] is None

        first.state = "retired_published"
        group.status = "retired_published"
        session.commit()
        after = build_report_snapshot(
            session, customer_id=customer.id, output_root=tmp_path, now=now
        )
        assert after["quality_pass_rate"] == before["quality_pass_rate"]
        assert after["production_passed"] == before["production_passed"]
        assert after["retired"] == 1
        assert after["published"] == 1


def test_filesystem_drift_is_reconciliation_only(tmp_path) -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        customer = Customer(name="漂移客户", output_root=str(tmp_path), profile_json={})
        session.add(customer)
        session.flush()
        output = _output(
            session,
            customer,
            state="ready",
            gate_ok=True,
            created_at=datetime(2026, 7, 29, 16, 1, tzinfo=timezone.utc),
        )
        ready_dir = tmp_path / "ready"
        ready_dir.mkdir()
        (ready_dir / "one.mp4").write_bytes(b"1")
        (ready_dir / "two.mp4").write_bytes(b"2")
        output.output_path = str(ready_dir / "one.mp4")
        session.commit()
        report = build_report_snapshot(
            session,
            customer_id=customer.id,
            output_root=tmp_path,
            now=datetime(2026, 7, 29, 17, 0, tzinfo=timezone.utc),
        )
        assert report["ready_available"] == 1
        assert report["reconciliation"]["filesystem"]["ready_files"] == 2
        assert report["reconciliation"]["filesystem"]["ready_db_paths_existing"] == 1
        assert report["reconciliation"]["differences"]["ready_available_minus_files"] == 0
        assert report["source"] == "database"


def test_all_report_gets_have_no_output_state_side_effects(tmp_path, monkeypatch) -> None:
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
        active_customer="只读报表客户",
        onboarded=True,
    )
    save_settings(settings)
    reset_engine()
    init_db(settings)
    with get_session() as session:
        customer = session.query(Customer).filter_by(name="只读报表客户").one()
        output = _output(
            session,
            customer,
            state="ready",
            gate_ok=True,
            created_at=datetime.now(timezone.utc),
        )
        output_id = output.id
        session.commit()

    from engine.api.app import app

    client = TestClient(app)
    for path in ("/reports/summary", "/reports/quality", "/reports/ops"):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.json()["source"] == "database"

    with get_session() as session:
        assert session.get(RenderOutput, output_id).state == "ready"
    reset_engine()
