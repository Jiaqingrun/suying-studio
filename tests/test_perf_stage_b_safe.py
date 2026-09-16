"""S5 / S6 / S8 unit probes for control-plane idle lean."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch


class TestUvicornAccessLogDefault(unittest.TestCase):
    def test_default_quiet(self) -> None:
        from engine.main import uvicorn_access_log_enabled

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SUYING_UVICORN_ACCESS_LOG", None)
            self.assertFalse(uvicorn_access_log_enabled())

    def test_opt_in(self) -> None:
        from engine.main import uvicorn_access_log_enabled

        with patch.dict(os.environ, {"SUYING_UVICORN_ACCESS_LOG": "1"}):
            self.assertTrue(uvicorn_access_log_enabled())


class TestVectorizationIdleWait(unittest.TestCase):
    def _run_one_wait(self, *, enabled: bool, should_claim: bool = True) -> float:
        from engine.catalog.vectorization_runtime import VectorizationExecutor

        ex = VectorizationExecutor()
        wake = MagicMock()

        def _wait(_timeout: float) -> bool:
            ex._stop.set()
            return True

        wake.wait.side_effect = _wait
        ex._wake = wake
        ex._stop.clear()
        settings = MagicMock()
        settings.vectorization_enabled = enabled
        with patch("engine.config.settings.load_settings", return_value=settings):
            with patch(
                "engine.runtime.pause_coordinator.coordinator.should_claim_jobs",
                return_value=should_claim,
            ):
                ex._generation = 1
                with patch.object(ex, "_tick"):
                    ex._loop(gen=1)
        wake.wait.assert_called()
        return float(wake.wait.call_args[0][0])

    def test_disabled_uses_long_wait(self) -> None:
        self.assertGreaterEqual(self._run_one_wait(enabled=False), 5.0)

    def test_enabled_keeps_short_wait(self) -> None:
        self.assertLessEqual(self._run_one_wait(enabled=True), 0.5)

    def test_paused_claim_uses_long_wait(self) -> None:
        self.assertGreaterEqual(
            self._run_one_wait(enabled=True, should_claim=False), 5.0
        )


class TestPublishPriorityCopy(unittest.TestCase):
    def test_message_mentions_yield(self) -> None:
        from engine.reach.chrome_runtime import (
            PublishPriorityError,
            acquire_operation,
            clear_publish_active,
            mark_publish_active,
        )

        owner = "publish_run:s8-copy"
        clear_publish_active()
        mark_publish_active(owner)
        try:
            with self.assertRaises(PublishPriorityError) as ctx:
                acquire_operation("message_scan:s8")
            self.assertIn("因发布让路", str(ctx.exception))
            self.assertEqual(ctx.exception.code, "publish_priority")
        finally:
            clear_publish_active(owner)


if __name__ == "__main__":
    unittest.main()
