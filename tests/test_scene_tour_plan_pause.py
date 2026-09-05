"""跟镜精品 · 规划门禁暂停（不熔断）与恢复收尾。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from engine.catalog.db import Base, Job, RenderOutput
from engine.jobs.queue import resume_job


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_resume_zombie_success_marks_completed() -> None:
    session = _session()
    job = Job(
        status="paused",
        mode="count",
        target_count=1,
        produced_count=1,
        template_name="fast-ship",
        theme="scene_tour",
        category="scene_tour",
        config_snapshot_json={"_pipeline_phase": "rendering"},
        consecutive_failures=0,
    )
    session.add(job)
    session.flush()
    out = RenderOutput(
        job_id=job.id,
        output_path="/tmp/x.mp4",
        state="ready",
        pack_status="ready",
        seed=1,
    )
    session.add(out)
    session.commit()

    resumed = resume_job(session, job.id)
    assert resumed is not None
    assert resumed.status == "completed"
    assert (resumed.config_snapshot_json or {}).get("_pipeline_phase") == "completed"


def test_resume_blocked_plan_clears_retry_counters() -> None:
    session = _session()
    job = Job(
        status="paused",
        mode="count",
        target_count=1,
        produced_count=0,
        template_name="fast-ship",
        theme="scene_tour",
        category="scene_tour",
        config_snapshot_json={
            "_pipeline_phase": "blocked_plan",
            "scene_tour_plan_retries": 3,
            "block_reasons": ["旁白缺少画面要点，请重试写词。"],
        },
        consecutive_failures=0,
    )
    session.add(job)
    session.commit()

    resumed = resume_job(session, job.id)
    assert resumed is not None
    assert resumed.status == "queued"
    snap = resumed.config_snapshot_json or {}
    assert "scene_tour_plan_retries" not in snap
    assert snap.get("_pipeline_phase") == "producing"
