#!/usr/bin/env python3
"""Assert PAPER_SLIP daily caps: cliplet/phrase ≤2 then third rejected."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from engine.catalog.db import Base, DailyUsage, get_engine, init_db, reset_engine
    from engine.catalog.paper_slip import (
        MAX_DAILY_USES,
        KIND_CLIPLET,
        KIND_PHRASE,
        assert_paper_slip_integrity,
        bump_daily,
        cliplet_ids_at_daily_cap,
        commit_paper_slip_for_ready,
        get_daily_count,
        local_day,
        normalize_phrase_key,
        phrase_is_blocked,
        phrases_at_daily_cap,
    )
    from engine.pack.video_lock import DEFAULT_LOCK, load_video_lock
    from sqlalchemy.orm import Session

    assert_paper_slip_integrity()
    assert MAX_DAILY_USES == 2
    assert int((DEFAULT_LOCK.get("paper_slip") or {}).get("max_daily_uses") or 0) == 2

    lock = load_video_lock("北京始峰伟业")
    assert lock["paper_slip"]["max_daily_uses"] == 2
    soft = load_video_lock(
        "北京始峰伟业",
        profile={"video_lock": {"paper_slip": {"max_daily_uses": 99}}},
    )
    assert soft["paper_slip"]["max_daily_uses"] == 2

    # Isolated temp DB
    with tempfile.TemporaryDirectory(prefix="paper-slip-") as td:
        db = Path(td) / "t.db"
        import engine.catalog.db as dbmod
        import engine.config.settings as settings_mod

        # Point db_path via env-less monkey: reset + create_engine on temp
        reset_engine()
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        eng = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng)
        SessionLocal = sessionmaker(bind=eng, autoflush=False, autocommit=False)
        session = SessionLocal()
        try:
            day = local_day()
            cid = 1
            # cliplet 999: bump to 2 → at cap
            assert bump_daily(session, KIND_CLIPLET, "999", customer_id=cid, day=day) == 1
            assert bump_daily(session, KIND_CLIPLET, "999", customer_id=cid, day=day) == 2
            session.commit()
            blocked = cliplet_ids_at_daily_cap(session, customer_id=cid, day=day)
            assert 999 in blocked, blocked
            assert get_daily_count(session, KIND_CLIPLET, "999", customer_id=cid, day=day) == 2

            try:
                bump_daily(session, KIND_CLIPLET, "999", customer_id=cid, day=day)
                raise AssertionError("third cliplet use must be rejected")
            except ValueError as e:
                assert "配额已满" in str(e)

            # phrase
            title = "本地仓配\n现货常备"
            commit_paper_slip_for_ready(session, title=title, customer_id=cid, day=day)
            commit_paper_slip_for_ready(session, title=title, customer_id=cid, day=day)
            session.commit()
            ph = phrases_at_daily_cap(session, customer_id=cid, day=day)
            key = normalize_phrase_key(title)
            assert key in ph, (key, ph)
            assert phrase_is_blocked(title, ph)
            assert get_daily_count(session, KIND_PHRASE, key, customer_id=cid, day=day) == 2
            try:
                commit_paper_slip_for_ready(session, title=title, customer_id=cid, day=day)
                raise AssertionError("third phrase use must be rejected")
            except ValueError as e:
                assert "配额已满" in str(e)

            # Database layer must also reject bypass attempts above the hard cap.
            from sqlalchemy import text
            from sqlalchemy.exc import IntegrityError

            try:
                session.execute(
                    text(
                        "INSERT INTO daily_usage(customer_id, kind, key, day, count, updated_at) "
                        "VALUES (:cid, 'cliplet', 'db-bypass', :day, 3, CURRENT_TIMESTAMP)"
                    ),
                    {"cid": cid, "day": day},
                )
                session.flush()
                raise AssertionError("database accepted count > 2")
            except IntegrityError:
                session.rollback()

            # different day not blocked
            other = "2099-01-01"
            assert 999 not in cliplet_ids_at_daily_cap(session, customer_id=cid, day=other)
        finally:
            session.close()
            reset_engine()

    doc = ROOT / "docs" / "PAPER_SLIP_LOCK.md"
    assert doc.is_file()
    assert "MAX_DAILY_USES" in doc.read_text(encoding="utf-8") or "每天最多" in doc.read_text(
        encoding="utf-8"
    )

    print("SMOKE_PAPER_SLIP OK", MAX_DAILY_USES, "day=", local_day())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
