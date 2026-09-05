from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from engine.catalog.db import (
    AutomationOccurrence,
    AutomationPlan,
    Base,
    Customer,
    ReachPublishReservation,
    ReachPublishRun,
    ReachPublishRunItem,
    ReachPublishSchedule,
    ReachPublishTrigger,
)
from engine.ops.automation_loop import _create_replacement, advance_occurrence
from engine.ops.human_alerts import acknowledge_alert, create_alert, dispatch_due_alerts
from engine.reach.publish_schedule import (
    ensure_window_triggers,
    release_trigger_reservations,
    trigger_to_dict,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _customer(session: Session) -> Customer:
    row = Customer(name="自动闭环测试", profile_json={})
    session.add(row)
    session.commit()
    return row


def test_each_window_occurrence_has_independent_stable_seed() -> None:
    session = _session()
    try:
        customer = _customer(session)
        schedule = ReachPublishSchedule(
            customer_id=customer.id,
            name="上午三次",
            timezone="Asia/Shanghai",
            chrome_profile="抖音-01",
            platform="douyin",
            windows_json=[
                {
                    "start": "09:00",
                    "end": "12:00",
                    "weekdays": [0, 1, 2, 3, 4, 5, 6],
                    "count": 3,
                    "min_gap_minutes": 20,
                }
            ],
        )
        session.add(schedule)
        session.commit()
        local_day = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
        first = ensure_window_triggers(session, schedule, local_dates=[local_day])
        second = ensure_window_triggers(session, schedule, local_dates=[local_day])
        assert len(first) == 3
        assert len({row.random_seed for row in first}) == 3
        assert len({row.planned_at for row in first}) == 3
        assert [(row.id, row.random_seed, row.planned_at) for row in first] == [
            (row.id, row.random_seed, row.planned_at) for row in second
        ]
        reserved = session.query(ReachPublishReservation).filter_by(status="reserved").count()
        assert reserved == 0
        assert all(row.status == "pending" for row in first)
    finally:
        session.close()


def test_concurrent_materialize_absorbs_occurrence_key_race() -> None:
    """Race: two sessions insert the same occurrence_key — loser must not 500."""
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_a = Session(engine)
    session_b = Session(engine)
    try:
        customer = _customer(session_a)
        schedule = ReachPublishSchedule(
            customer_id=customer.id,
            name="竞态",
            timezone="Asia/Shanghai",
            chrome_profile="抖音-01",
            platform="douyin",
            windows_json=[
                {
                    "start": "09:00:00",
                    "end": "11:00:00",
                    "weekdays": [0, 1, 2, 3, 4, 5, 6],
                    "count": 1,
                }
            ],
        )
        session_a.add(schedule)
        session_a.commit()
        schedule_id = schedule.id
        schedule_b = session_b.get(ReachPublishSchedule, schedule_id)
        assert schedule_b is not None
        local_day = datetime(2026, 8, 1, tzinfo=ZoneInfo("Asia/Shanghai"))

        # Simulate the loser path: insert with same key, then ensure_window must
        # treat IntegrityError as already-materialized.
        key = f"schedule:{schedule_id}:{local_day.date().isoformat()}:window:0:ordinal:0"
        winner = ReachPublishTrigger(
            schedule_id=schedule_id,
            customer_id=customer.id,
            planned_at=datetime(2026, 8, 1, 2, 0, tzinfo=None),
            status="pending",
            occurrence_key=key,
            local_date=local_day.date().isoformat(),
            random_seed="deadbeef",
            random_algorithm="window_seconds_v2",
            ordinal=0,
        )
        session_a.add(winner)
        session_a.commit()

        rows = ensure_window_triggers(
            session_b, schedule_b, local_dates=[local_day]
        )
        assert len(rows) >= 1
        assert any(row.occurrence_key == key for row in rows)
        # Second session must not leave a broken transaction / raise IntegrityError.
        assert session_b.query(ReachPublishTrigger).filter_by(occurrence_key=key).count() == 1
    except IntegrityError:
        raise AssertionError("IntegrityError must be absorbed for concurrent materialize")
    finally:
        session_a.close()
        session_b.close()


def test_schedule_revision_generates_new_random_occurrences() -> None:
    session = _session()
    try:
        customer = _customer(session)
        schedule = ReachPublishSchedule(
            customer_id=customer.id,
            name="可编辑计划",
            timezone="Asia/Shanghai",
            chrome_profile="抖音-01",
            platform="douyin",
            content_source="generate_then_publish",
            source_config_json={"schedule_revision": 1},
            windows_json=[
                {
                    "start": "09:00:01",
                    "end": "10:00:59",
                    "weekdays": [0, 1, 2, 3, 4, 5, 6],
                    "count": 1,
                }
            ],
        )
        session.add(schedule)
        session.commit()
        local_day = datetime(2026, 8, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        first = ensure_window_triggers(session, schedule, local_dates=[local_day])
        schedule.source_config_json = {"schedule_revision": 2}
        session.commit()
        second = ensure_window_triggers(session, schedule, local_dates=[local_day])

        assert len(first) == 1
        assert len(second) == 1
        assert first[0].occurrence_key != second[0].occurrence_key
        assert first[0].random_seed != second[0].random_seed
    finally:
        session.close()


def test_cross_midnight_window_persists_bounds() -> None:
    session = _session()
    try:
        customer = _customer(session)
        schedule = ReachPublishSchedule(
            customer_id=customer.id,
            name="跨午夜",
            timezone="Asia/Shanghai",
            chrome_profile="抖音-01",
            platform="douyin",
            windows_json=[{"start": "23:30", "end": "00:30", "count": 1}],
        )
        session.add(schedule)
        session.commit()
        rows = ensure_window_triggers(
            session,
            schedule,
            local_dates=[datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))],
        )
        assert len(rows) == 1
        assert rows[0].window_end - rows[0].window_start == timedelta(hours=1)
        assert rows[0].window_start <= rows[0].planned_at <= rows[0].window_end
    finally:
        session.close()


def test_trigger_serialization_marks_naive_sqlite_datetime_as_utc() -> None:
    trigger = ReachPublishTrigger(
        id=1,
        schedule_id=1,
        customer_id=1,
        planned_at=datetime(2026, 8, 1, 1, 2, 3),
        status="pending",
    )
    assert trigger_to_dict(trigger)["planned_at"] == "2026-08-01T01:02:03Z"


def test_release_trigger_reservations_is_idempotent() -> None:
    session = _session()
    try:
        customer = _customer(session)
        schedule = ReachPublishSchedule(
            customer_id=customer.id,
            name="释放预留",
            timezone="Asia/Shanghai",
            chrome_profile="抖音-01",
            platform="douyin",
        )
        session.add(schedule)
        session.flush()
        trigger = ReachPublishTrigger(
            schedule_id=schedule.id,
            customer_id=customer.id,
            planned_at=datetime(2026, 8, 1, 1, 2, 3),
            status="pending",
        )
        session.add(trigger)
        session.flush()
        reservation = ReachPublishReservation(
            customer_id=customer.id,
            schedule_id=schedule.id,
            trigger_id=trigger.id,
            ordinal=0,
            platform="douyin",
            chrome_profile="抖音-01",
            status="reserved",
        )
        session.add(reservation)
        session.commit()

        assert release_trigger_reservations(session, trigger) == 1
        session.commit()
        assert reservation.status == "released"
        assert release_trigger_reservations(session, trigger) == 0
    finally:
        session.close()


def test_completed_run_with_skipped_item_is_not_marked_published() -> None:
    session = _session()
    try:
        customer = _customer(session)
        occurrence = AutomationOccurrence(
            occurrence_key="not-all-published",
            customer_id=customer.id,
            local_date="2026-08-01",
            status="publishing",
            publish_run_id="run-partial",
        )
        run = ReachPublishRun(
            customer_id=customer.id,
            run_id="run-partial",
            status="completed",
            source="automation",
            requested_total=1,
        )
        session.add_all([occurrence, run])
        session.flush()
        session.add(
            ReachPublishRunItem(
                run_id=run.id,
                customer_id=customer.id,
                ordinal=0,
                platform="douyin",
                chrome_profile="抖音-01",
                phase="skipped",
                outcome="skipped",
            )
        )
        session.commit()

        result = advance_occurrence(session, occurrence)
        assert result["status"] == "publish_scheduled"
        assert occurrence.status != "published"
    finally:
        session.close()


def test_quality_replacement_is_bounded_and_bypasses_window() -> None:
    session = _session()
    try:
        customer = _customer(session)
        plan = AutomationPlan(
            customer_id=customer.id,
            name="质量补偿",
            max_replacement_attempts=1,
        )
        session.add(plan)
        session.flush()
        original = AutomationOccurrence(
            occurrence_key="quality:1",
            customer_id=customer.id,
            plan_id=plan.id,
            local_date="2026-07-28",
            status="failed",
            replacement_attempt=0,
            step_json={"frozen_config": {"template_name": "default-vertical"}},
        )
        session.add(original)
        session.commit()
        replacement = _create_replacement(session, original)
        assert replacement is not None
        assert replacement.bypass_window_reason == "quality_recovery"
        assert replacement.replacement_of_id == original.id
        assert _create_replacement(session, replacement) is None
        assert replacement.status == "blocked_quality"
    finally:
        session.close()


def test_human_alert_three_channels_are_idempotent_and_ack_stops() -> None:
    session = _session()
    try:
        customer = _customer(session)
        alert = create_alert(
            session,
            alert_key="login:1",
            customer_id=customer.id,
            kind="login_required",
            source_type="publish_run",
            source_id="run-1",
            summary="抖音 · 账号一 · 需要登录；验证码 123456 不应外发",
            deep_link="suying://publish?run=run-1",
        )
        assert "123456" not in alert.safe_summary
        with (
            patch(
                "engine.ops.human_alerts._send_macos",
                return_value={"ok": True},
            ),
            patch(
                "engine.ops.human_alerts._send_ntfy",
                return_value={"sent": True, "status": "sent"},
            ),
        ):
            first = dispatch_due_alerts(session, customer_id=customer.id)
            assert len(first) == 1
            assert set(alert.channels_json["step_0"]) >= {"app", "macos", "ntfy"}
            assert dispatch_due_alerts(session, customer_id=customer.id) == []
        acknowledged = acknowledge_alert(
            session, alert.id, customer_id=customer.id
        )
        assert acknowledged is not None
        assert acknowledged.next_escalation_at is None
        assert acknowledged.status == "acknowledged"
    finally:
        session.close()
