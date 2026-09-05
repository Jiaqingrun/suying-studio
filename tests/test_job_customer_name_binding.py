"""Automation jobs must bind customer_name so paper-slip filtering matches reserve."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from engine.catalog.db import Base, Customer
from engine.jobs.queue import CreateJobRequest, create_job
from engine.template.rule_store import ensure_default_rule


def test_create_job_fills_customer_name_from_customer_id(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'job-bind.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        customer = Customer(name="北京始峰伟业", active=True)
        session.add(customer)
        session.commit()
        session.refresh(customer)
        ensure_default_rule(session, customer=customer, orientation="portrait")

        # Mimic automation_loop: omit customer_name, only pass customer_id.
        job = create_job(
            session,
            CreateJobRequest(target_count=1, use_active_rule=True),
            customer_id=customer.id,
        )
        assert job.customer_id == customer.id
        snap = job.config_snapshot_json or {}
        assert snap.get("customer_name") == "北京始峰伟业"

        # Explicit null must still resolve from customer_id.
        job2 = create_job(
            session,
            CreateJobRequest(
                target_count=1,
                use_active_rule=True,
                customer_name=None,
            ),
            customer_id=customer.id,
        )
        assert (job2.config_snapshot_json or {}).get("customer_name") == "北京始峰伟业"
    finally:
        session.close()
        engine.dispose()


def test_build_plan_paper_slip_uses_job_customer_id(tmp_path) -> None:
    from engine.catalog.db import DailyUsage, WeeklyUsage, install_paper_slip_guards
    from engine.catalog.paper_slip import cliplet_ids_at_daily_cap, local_day, local_week

    engine = create_engine(
        f"sqlite:///{tmp_path / 'plan-bind.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        install_paper_slip_guards(conn)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        customer = Customer(name="北京始峰伟业", active=True)
        session.add(customer)
        session.commit()
        session.refresh(customer)
        day = local_day()
        week = local_week(day=day)
        session.add(
            WeeklyUsage(
                customer_id=customer.id,
                kind="cliplet",
                key="300",
                week=week,
                count=2,
            )
        )
        session.add(
            DailyUsage(
                customer_id=customer.id,
                kind="cliplet",
                key="300",
                day=day,
                count=1,
            )
        )
        session.commit()

        # Wrong customer (0) must not see the block; bound customer must.
        assert 300 not in cliplet_ids_at_daily_cap(session, customer_id=0)
        assert 300 in cliplet_ids_at_daily_cap(session, customer_id=customer.id)
    finally:
        session.close()
        engine.dispose()
