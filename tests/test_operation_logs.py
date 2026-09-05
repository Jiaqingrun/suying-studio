"""Operation log API merges JobEvent production timeline."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from engine.api.system_routes import (
    _include_legacy_production_logs,
    _legacy_production_job_events,
)
from engine.catalog.db import Base, Customer, Job, OperationLog, log_event


def test_include_legacy_for_all_and_production_categories():
    assert _include_legacy_production_logs(category=None, level=None, search=None, page=1)
    assert _include_legacy_production_logs(category="all", level=None, search=None, page=1)
    assert _include_legacy_production_logs(category="production", level=None, search=None, page=1)
    assert not _include_legacy_production_logs(category="publish", level=None, search=None, page=1)
    assert not _include_legacy_production_logs(category="all", level=None, search="foo", page=1)


def test_legacy_production_job_events_filters_noise_and_level() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        customer = Customer(name="日志客户", output_root="/tmp/logs", profile_json={})
        session.add(customer)
        session.flush()
        job = Job(
            customer_id=customer.id,
            status="running",
            template_name="test",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(job)
        session.flush()
        log_event(session, job.id, "info", "审片自动通过")
        log_event(session, job.id, "info", "开始渲染成片")
        log_event(session, job.id, "error", "渲染失败", {"step": "render"})
        session.commit()

        # Newly emitted JobEvents are mirrored into the structured append-only
        # log and therefore intentionally excluded from the legacy fallback.
        assert _legacy_production_job_events(session, customer_id=customer.id) == []
        rows = session.query(OperationLog).order_by(OperationLog.id).all()
        messages = {row.message for row in rows}
        assert "审片自动通过" not in messages
        assert "开始渲染成片" in messages
        assert "渲染失败" in messages
        assert [row.message for row in rows if row.level == "error"] == ["渲染失败"]
