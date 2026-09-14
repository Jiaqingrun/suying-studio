"""auto_upload poll must not leave a sticky in-progress phase on timeout."""

from __future__ import annotations

from unittest.mock import patch

from engine.reach import auto_upload


def test_poll_batch_job_marks_failed_on_timeout():
    auto_upload._job = {
        "job_id": "run-1",
        "phase": "uploading",
        "message": "",
        "error": None,
        "need_human": False,
        "_cancel": False,
    }

    def forever_running(_run_id: str):
        return {"status": "running", "phase": "uploading", "current_item": {}}

    # Local ``import time`` inside _poll_batch_job → patch the stdlib module.
    with (
        patch("engine.reach.publish_runner.get_status", side_effect=forever_running),
        patch.object(auto_upload, "_cancelled", return_value=False),
        patch("time.time", side_effect=[1000.0, 1000.5, 1065.0]),
        patch("time.sleep", return_value=None),
    ):
        # timeout_sec=60 → deadline 1060; third time.time() exceeds it.
        auto_upload._poll_batch_job(run_id="run-1", timeout_sec=60)

    assert auto_upload._job is not None
    assert auto_upload._job["phase"] == "failed"
    assert auto_upload._job.get("error") == "poll_timeout"
    auto_upload._job = None
