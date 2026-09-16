"""Publish efficiency pass: upload stall recovery + produce idle (no browser).

Cover opt-in default-off is owned by main (`publish_confirm_upload_cover`);
this pass locks stall/recovery + idle waits on top of that.
"""

from __future__ import annotations

import inspect
import unittest
from unittest.mock import MagicMock, patch

from engine.reach.cdp_publish import (
    UPLOAD_PROGRESS_STALL_SEC,
    _should_upload_covers,
    _skipped_cover_result,
    upload_video_with_retries,
    wait_upload_ready,
)
from engine.reach.publish_assets import publish_confirm_upload_cover_enabled


class PublishEfficiencyPassTests(unittest.TestCase):
    def test_cover_upload_default_off(self) -> None:
        with patch("engine.config.settings.load_settings") as load:
            load.return_value = MagicMock(publish_confirm_upload_cover=False)
            self.assertFalse(publish_confirm_upload_cover_enabled())
            load.return_value = MagicMock(publish_confirm_upload_cover=True)
            self.assertTrue(publish_confirm_upload_cover_enabled())

    def test_skip_cover_saves_soft_timeout_path(self) -> None:
        assets = {
            "cover_optional": True,
            "covers": [],
            "cover_upload_confirmed": False,
        }
        for plat in ("douyin", "channels", "kuaishou", "xhs"):
            self.assertFalse(_should_upload_covers(assets, plat), plat)
            r = _skipped_cover_result(reason="cover_upload_not_confirmed")
            self.assertTrue(r["skipped"])

    def test_upload_stall_triggers_retry(self) -> None:
        sess = MagicMock()
        sess.evaluate.return_value = {"failed": False}
        with patch(
            "engine.reach.cdp_publish.upload_video_via_cdp",
            return_value={"ok": True, "phase": "injected"},
        ), patch(
            "engine.reach.cdp_publish.wait_upload_ready",
            side_effect=[
                {"ok": False, "state": "upload_stalled", "hint": "upload_progress_stalled"},
                {"ok": True, "state": "form"},
            ],
        ), patch(
            "engine.reach.cdp_publish._click_reupload",
            return_value="clicked:重新上传",
        ), patch(
            "engine.reach.vision_reach.snapshot_page",
            return_value={},
        ), patch("engine.reach.cdp_publish.time.sleep"):
            out = upload_video_with_retries(sess, "/tmp/v.mp4", max_attempts=2, platform="douyin")
        self.assertTrue(out.get("ok"), msg=repr(out))
        self.assertGreaterEqual(len(out["attempts"]), 2)
        self.assertTrue(any(a.get("stall_recovery") for a in out["attempts"]))

    def test_stall_budget_constant(self) -> None:
        self.assertGreaterEqual(UPLOAD_PROGRESS_STALL_SEC, 8.0)
        self.assertLessEqual(UPLOAD_PROGRESS_STALL_SEC, 20.0)

    def test_wait_upload_ready_has_stall_logic(self) -> None:
        src = inspect.getsource(wait_upload_ready)
        self.assertIn("upload_stalled", src)
        self.assertIn("UPLOAD_PROGRESS_STALL_SEC", src)
        self.assertIn("progress_pct", src)

    def test_worker_idle_waits_not_subsecond_busy(self) -> None:
        from engine.jobs import worker as worker_mod

        src = inspect.getsource(worker_mod.JobWorker._loop)
        self.assertIn("self._stop.wait(5.0)", src)
        self.assertIn("self._stop.wait(2.0)", src)
        self.assertNotIn("self._stop.wait(0.5)", src)

    def test_align_preflight_still_wired(self) -> None:
        from engine.jobs import worker as worker_mod

        src = inspect.getsource(worker_mod)
        self.assertIn("preflight_align_gate", src)
        self.assertIn("align_preflight", src)


if __name__ == "__main__":
    unittest.main()
