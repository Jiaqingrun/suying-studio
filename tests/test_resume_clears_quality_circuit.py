"""resume_job must clear quality-circuit streak or the next tick instant-retrips."""

from __future__ import annotations

from unittest.mock import MagicMock

from engine.jobs.queue import resume_job


def test_resume_job_clears_consecutive_non_ready():
    job = MagicMock()
    job.id = 42
    job.status = "circuit_open"
    job.consecutive_failures = 5
    job.config_snapshot_json = {"consecutive_non_ready": 5, "theme": "仓配"}
    session = MagicMock()
    session.get.return_value = job

    out = resume_job(session, 42)

    assert out is job
    assert job.status == "queued"
    assert job.consecutive_failures == 0
    assert job.config_snapshot_json["consecutive_non_ready"] == 0
    assert job.config_snapshot_json["theme"] == "仓配"
    session.commit.assert_called()
