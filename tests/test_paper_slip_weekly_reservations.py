"""Paper-slip rolling diversity: no day/week hard caps; reservation lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from engine.catalog.db import (
    Base,
    DailyUsage,
    install_paper_slip_guards,
)
from engine.config.settings import AppSettings, PathConfig
from engine.catalog.paper_slip import (
    KIND_CLIPLET,
    commit_paper_slip_for_ready,
    get_daily_count,
    get_weekly_count,
    local_week,
    reap_expired_reservations,
    recently_used_cliplet_ids,
    release_paper_slip,
    reserve_paper_slip,
)


@pytest.fixture()
def sessions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'paper-slip.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        install_paper_slip_guards(conn)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    engine.dispose()


def _reserve(
    session: Session,
    token: str,
    *,
    customer_id: int = 1,
    day: str,
    cliplet_id: int = 9,
):
    return reserve_paper_slip(
        session,
        reservation_key=token,
        job_id=int(token.split("-")[-1]),
        cliplet_ids=[cliplet_id],
        title="品牌短句\n标题整句",
        customer_id=customer_id,
        day=day,
    )


def test_same_week_allows_third_ready_and_monday_reset(sessions) -> None:
    session = sessions()
    try:
        _reserve(session, "job-1", day="2026-07-27")
        commit_paper_slip_for_ready(
            session, reservation_key="job-1", job_id=1, customer_id=1
        )
        session.commit()
        _reserve(session, "job-2", day="2026-07-28")
        commit_paper_slip_for_ready(
            session, reservation_key="job-2", job_id=2, customer_id=1
        )
        session.commit()
        # Third same-week ready must succeed (hard weekly cap removed).
        _reserve(session, "job-3", day="2026-07-29")
        commit_paper_slip_for_ready(
            session, reservation_key="job-3", job_id=3, customer_id=1
        )
        session.commit()
        assert get_weekly_count(
            session, KIND_CLIPLET, "9", customer_id=1, week="2026-07-27"
        ) == 3
        assert 9 in recently_used_cliplet_ids(session, customer_id=1, limit=5)

        # The next Monday is a new Shanghai natural week (ledger partition only).
        _reserve(session, "job-4", day="2026-08-03")
        session.commit()
    finally:
        session.close()


def test_concurrent_reservations_release_expiry_and_customer_isolation(sessions) -> None:
    first, second = sessions(), sessions()
    try:
        # No hard cap: three concurrent reserves of the same key are allowed.
        _reserve(first, "job-11", day="2026-07-30")
        first.commit()
        _reserve(second, "job-12", day="2026-07-30")
        second.commit()
        _reserve(first, "job-13", day="2026-07-30")
        first.commit()

        assert release_paper_slip(second, "job-12", customer_id=1) > 0
        second.commit()

        # Same key belongs to an independent customer domain.
        _reserve(second, "job-14", customer_id=2, day="2026-07-30")
        second.commit()

        first.execute(
            text(
                "UPDATE paper_slip_reservations SET expires_at=:past "
                "WHERE reservation_key='job-13'"
            ),
            {"past": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)},
        )
        assert reap_expired_reservations(first) >= 1
        first.commit()
        _reserve(second, "job-15", day="2026-07-30")
        second.commit()
    finally:
        first.close()
        second.close()


def test_ready_commit_is_idempotent_and_db_allows_over_two(sessions) -> None:
    session = sessions()
    try:
        _reserve(session, "job-21", day="2026-07-30")
        first = commit_paper_slip_for_ready(
            session, reservation_key="job-21", job_id=21, customer_id=1
        )
        second = commit_paper_slip_for_ready(
            session, reservation_key="job-21", job_id=21, customer_id=1
        )
        session.commit()
        assert first["status"] == second["status"] == "committed"
        assert second["idempotent"] is True
        assert get_daily_count(
            session, KIND_CLIPLET, "9", customer_id=1, day="2026-07-30"
        ) == 1

        # Direct SQL may create additional same-week uses (no weekly hard cap).
        session.execute(
            text(
                "INSERT INTO daily_usage(customer_id,kind,key,day,count,updated_at) "
                "VALUES (1,'cliplet','9','2026-08-01',1,CURRENT_TIMESTAMP)"
            )
        )
        session.commit()
        assert local_week(day="2026-07-31") == "2026-07-27"
        assert get_weekly_count(
            session, KIND_CLIPLET, "9", customer_id=1, week="2026-07-27"
        ) >= 2

        _reserve(session, "job-22", customer_id=2, day="2026-08-03")
        release_paper_slip(session, "job-22", customer_id=2)
        session.commit()
        with pytest.raises(IntegrityError, match="cannot be reactivated"):
            session.execute(
                text(
                    "UPDATE paper_slip_reservations SET status='reserved' "
                    "WHERE reservation_key='job-22'"
                )
            )
            session.flush()
        session.rollback()
    finally:
        session.close()


def test_incremental_migration_preserves_legacy_daily_usage(tmp_path) -> None:
    from engine.catalog import db as dbmod
    from engine.catalog.db import WeeklyUsage

    data_root = tmp_path / "legacy-data"
    data_root.mkdir()
    legacy = create_engine(f"sqlite:///{data_root / 'montage.db'}")
    with legacy.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE daily_usage ("
                "id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL DEFAULT 0, "
                "kind VARCHAR(32), key VARCHAR(512), day VARCHAR(10), "
                "count INTEGER NOT NULL DEFAULT 0, updated_at DATETIME, "
                "UNIQUE(customer_id,kind,key,day))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO daily_usage(customer_id,kind,key,day,count,updated_at) VALUES "
                "(7,'cliplet','77','2026-07-27',1,CURRENT_TIMESTAMP),"
                "(7,'cliplet','77','2026-07-28',1,CURRENT_TIMESTAMP)"
            )
        )
    legacy.dispose()

    settings = AppSettings(paths=PathConfig(data_root=data_root))
    dbmod.reset_engine()
    try:
        dbmod.init_db(settings)
        session = dbmod.get_session()
        try:
            old_rows = list(
                session.scalars(
                    select(DailyUsage)
                    .where(DailyUsage.customer_id == 7, DailyUsage.key == "77")
                    .order_by(DailyUsage.day)
                ).all()
            )
            assert [(row.day, row.count) for row in old_rows] == [
                ("2026-07-27", 1),
                ("2026-07-28", 1),
            ]
            weekly = session.scalar(
                select(WeeklyUsage).where(
                    WeeklyUsage.customer_id == 7,
                    WeeklyUsage.key == "77",
                    WeeklyUsage.week == "2026-07-27",
                )
            )
            assert weekly is not None and weekly.count == 2
        finally:
            session.close()
    finally:
        dbmod.reset_engine()


def test_rolling_near_window_prefers_exclude_then_allows_reuse(sessions) -> None:
    session = sessions()
    try:
        for i in range(1, 4):
            _reserve(session, f"job-{i}", day="2026-07-30", cliplet_id=100 + i)
            commit_paper_slip_for_ready(
                session, reservation_key=f"job-{i}", job_id=i, customer_id=1
            )
        session.commit()
        near = recently_used_cliplet_ids(session, customer_id=1, limit=2)
        assert len(near) == 2
        assert 103 in near and 102 in near
        # Relaxed window of 0 means no exclude.
        assert recently_used_cliplet_ids(session, customer_id=1, limit=0) == set()
    finally:
        session.close()


def test_rereserve_after_release_allows_different_items(sessions) -> None:
    """Wall-skip releases rows; next plan on same key may pick different clips."""
    session = sessions()
    try:
        reserve_paper_slip(
            session,
            reservation_key="job:413:seed:1",
            job_id=413,
            cliplet_ids=[11, 12],
            title="甲标题",
            customer_id=1,
            day="2026-08-15",
        )
        session.commit()
        release_paper_slip(session, "job:413:seed:1", customer_id=1)
        session.commit()
        # Must not raise「预留内容不一致」against released rows.
        out = reserve_paper_slip(
            session,
            reservation_key="job:413:seed:1",
            job_id=413,
            cliplet_ids=[21, 22, 23],
            title="乙标题",
            customer_id=1,
            day="2026-08-15",
        )
        session.commit()
        assert out["status"] == "reserved"
        assert {i["key"] for i in out["items"] if i["kind"] == KIND_CLIPLET} == {
            "21",
            "22",
            "23",
        }
    finally:
        session.close()
