#!/usr/bin/env python3
"""Assert PAPER_SLIP rolling diversity (no day/week hard caps)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from engine.catalog.db import Base, install_paper_slip_guards, reset_engine
    from engine.catalog.paper_slip import (
        KIND_CLIPLET,
        KIND_PHRASE,
        ROLLING_CLIPLET_STEPS,
        ROLLING_CLIPLET_WINDOW,
        ROLLING_PHRASE_STEPS,
        ROLLING_PHRASE_WINDOW,
        assert_paper_slip_integrity,
        bump_daily,
        cliplet_ids_at_daily_cap,
        commit_paper_slip_for_ready,
        get_daily_count,
        get_weekly_count,
        local_day,
        local_week,
        normalize_phrase_key,
        phrase_is_blocked,
        phrases_at_daily_cap,
        recently_used_cliplet_ids,
        release_paper_slip,
        reserve_paper_slip,
    )
    from engine.pack.video_lock import DEFAULT_LOCK, load_video_lock

    assert_paper_slip_integrity()
    assert ROLLING_CLIPLET_WINDOW == 20
    assert ROLLING_PHRASE_WINDOW == 15
    assert ROLLING_CLIPLET_STEPS == (20, 10, 0)
    assert ROLLING_PHRASE_STEPS == (15, 8, 0)
    assert (DEFAULT_LOCK.get("paper_slip") or {}).get("mode") == "rolling_diversity"
    assert "max_daily_uses" not in (DEFAULT_LOCK.get("paper_slip") or {})

    lock = load_video_lock("北京始峰伟业")
    assert lock["paper_slip"]["mode"] == "rolling_diversity"
    assert lock["paper_slip"]["rolling_cliplet_window"] == 20
    assert "max_daily_uses" not in lock["paper_slip"]
    soft = load_video_lock(
        "北京始峰伟业",
        profile={"video_lock": {"paper_slip": {"max_daily_uses": 99, "mode": "hard_cap"}}},
    )
    assert soft["paper_slip"]["mode"] == "rolling_diversity"
    assert "max_daily_uses" not in soft["paper_slip"]

    with tempfile.TemporaryDirectory(prefix="paper-slip-") as td:
        db = Path(td) / "t.db"
        reset_engine()
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        eng = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        with eng.begin() as conn:
            install_paper_slip_guards(conn)
        SessionLocal = sessionmaker(bind=eng, autoflush=False, autocommit=False)
        session = SessionLocal()
        try:
            day = local_day()
            cid = 1
            # Third ready use must succeed (no hard cap).
            assert bump_daily(session, KIND_CLIPLET, "999", customer_id=cid, day=day) == 1
            assert bump_daily(session, KIND_CLIPLET, "999", customer_id=cid, day=day) == 2
            assert bump_daily(session, KIND_CLIPLET, "999", customer_id=cid, day=day) == 3
            session.commit()
            assert get_daily_count(session, KIND_CLIPLET, "999", customer_id=cid, day=day) == 3
            assert 999 in recently_used_cliplet_ids(session, customer_id=cid, limit=20)
            assert 999 in cliplet_ids_at_daily_cap(session, customer_id=cid)

            title = "本地仓配\n现货常备"
            commit_paper_slip_for_ready(session, title=title, customer_id=cid, day=day)
            commit_paper_slip_for_ready(session, title=title, customer_id=cid, day=day)
            commit_paper_slip_for_ready(session, title=title, customer_id=cid, day=day)
            session.commit()
            key = normalize_phrase_key(title)
            ph = phrases_at_daily_cap(session, customer_id=cid)
            # Phrase near-window comes from committed reservations; item-only commits
            # still bump daily ledger — exclude helpers use reservation/keyword paths.
            assert get_daily_count(session, KIND_PHRASE, key, customer_id=cid, day=day) == 3
            assert phrase_is_blocked(title, {key})

            # Database allows count > 2 (hard cap removed).
            from sqlalchemy import text

            session.execute(
                text(
                    "INSERT INTO daily_usage(customer_id, kind, key, day, count, updated_at) "
                    "VALUES (:cid, 'cliplet', 'db-ok', :day, 5, CURRENT_TIMESTAMP)"
                ),
                {"cid": cid, "day": day},
            )
            session.commit()
            assert get_daily_count(session, KIND_CLIPLET, "db-ok", customer_id=cid, day=day) == 5

            # Negative count still rejected.
            from sqlalchemy.exc import IntegrityError

            try:
                session.execute(
                    text(
                        "INSERT INTO daily_usage(customer_id, kind, key, day, count, updated_at) "
                        "VALUES (:cid, 'cliplet', 'db-neg', :day, -1, CURRENT_TIMESTAMP)"
                    ),
                    {"cid": cid, "day": day},
                )
                session.flush()
                raise AssertionError("database accepted negative count")
            except IntegrityError:
                session.rollback()

            # Same week: third ready use must also succeed.
            reserve_paper_slip(
                session,
                reservation_key="smoke-job-1",
                job_id=1,
                cliplet_ids=[1001],
                customer_id=cid,
                day="2026-07-27",
            )
            commit_paper_slip_for_ready(
                session,
                reservation_key="smoke-job-1",
                job_id=1,
                customer_id=cid,
            )
            reserve_paper_slip(
                session,
                reservation_key="smoke-job-2",
                job_id=2,
                cliplet_ids=[1001],
                customer_id=cid,
                day="2026-07-28",
            )
            commit_paper_slip_for_ready(
                session,
                reservation_key="smoke-job-2",
                job_id=2,
                customer_id=cid,
            )
            reserve_paper_slip(
                session,
                reservation_key="smoke-job-3",
                job_id=3,
                cliplet_ids=[1001],
                customer_id=cid,
                day="2026-07-29",
            )
            commit_paper_slip_for_ready(
                session,
                reservation_key="smoke-job-3",
                job_id=3,
                customer_id=cid,
            )
            assert get_weekly_count(
                session, KIND_CLIPLET, "1001", customer_id=cid, week="2026-07-27"
            ) == 3
            assert 1001 in recently_used_cliplet_ids(session, customer_id=cid, limit=5)

            # Failed output releases its lease; a competing job can reserve.
            reserve_paper_slip(
                session,
                reservation_key="smoke-release-4",
                job_id=4,
                cliplet_ids=[1002],
                customer_id=cid,
                day="2026-08-03",
            )
            assert release_paper_slip(session, "smoke-release-4", customer_id=cid) == 1
            reserve_paper_slip(
                session,
                reservation_key="smoke-release-5",
                job_id=5,
                cliplet_ids=[1002],
                customer_id=cid,
                day="2026-08-03",
            )
            session.commit()
            assert local_week(day="2026-08-02") == "2026-07-27"
            assert local_week(day="2026-08-03") == "2026-08-03"
        finally:
            session.close()
            reset_engine()

    doc = ROOT / "docs" / "PAPER_SLIP_LOCK.md"
    text_doc = doc.read_text(encoding="utf-8")
    assert doc.is_file()
    assert "滚动避重" in text_doc
    assert "ROLLING_CLIPLET_WINDOW" in text_doc or "近窗" in text_doc

    print(
        "SMOKE_PAPER_SLIP OK",
        f"cliplet_window={ROLLING_CLIPLET_WINDOW}",
        f"phrase_window={ROLLING_PHRASE_WINDOW}",
        "today=",
        local_day(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
