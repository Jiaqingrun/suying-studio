"""Recipe copy must skip paper-slip capped phrases; stale reserves must free."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from engine.catalog.db import Base, Job, PaperSlipReservation, install_paper_slip_guards
from engine.catalog.paper_slip import (
    release_reservations_for_inactive_jobs,
)
from engine.content.content_fingerprint import select_recipe_components


def test_select_recipe_skips_blocked_phrases() -> None:
    pack = {
        "recipes": {
            "daily_overview": {
                "id": "recipe.daily",
                "sequence": ["hook", "cta"],
            }
        },
        "copy_components": {
            "library": {
                "hook": [
                    {"id": "h1", "text": "镜头从整体环境切到局部细节"},
                    {"id": "h2", "text": "仓内货架可见整齐码放"},
                ],
                "cta": [
                    {"id": "c1", "text": "需要哪类产品可以进一步了解"},
                    {"id": "c2", "text": "按现场情况进一步沟通"},
                ],
            }
        },
    }
    blocked = {
        "镜头从整体环境切到局部细节",
        "需要哪类产品可以进一步了解",
    }
    out = select_recipe_components(
        {"primary_type": "daily_overview", "visible_facts": ["货架"], "scenes": ["warehouse"]},
        pack,
        seed=7,
        exclude_phrases=blocked,
    )
    assert out["component_texts"] == ["仓内货架可见整齐码放", "按现场情况进一步沟通"]


def test_inactive_jobs_release_stale_reservations(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'slip.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        install_paper_slip_guards(conn)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        dead = Job(
            customer_id=1,
            status="circuit_open",
            template_name="fast-ship",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        alive = Job(
            customer_id=1,
            status="running",
            template_name="fast-ship",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add_all([dead, alive])
        session.flush()
        now = datetime.now(timezone.utc)
        for job, key in ((dead, "dead-phrase"), (alive, "alive-phrase")):
            session.add(
                PaperSlipReservation(
                    reservation_key=f"job:{job.id}:seed:1",
                    job_id=job.id,
                    customer_id=1,
                    kind="phrase",
                    key=key,
                    day="2026-07-31",
                    week="2026-07-27",
                    status="reserved",
                    expires_at=now + timedelta(hours=2),
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
        n = release_reservations_for_inactive_jobs(session)
        session.commit()
        assert n == 1
        rows = {
            r.key: r.status
            for r in session.scalars(select(PaperSlipReservation)).all()
        }
        assert rows["dead-phrase"] == "released"
        assert rows["alive-phrase"] == "reserved"

        # Explicit cleanup helper is also safe to call repeatedly.
        assert release_reservations_for_inactive_jobs(session) == 0
        session.commit()
        again = {
            r.key: r.status
            for r in session.scalars(select(PaperSlipReservation)).all()
        }
        assert again["alive-phrase"] == "reserved"
    finally:
        session.close()
        engine.dispose()
