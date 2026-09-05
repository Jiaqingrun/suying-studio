"""Ollama narration: keep_alive / think:false / infra classify / idle-loop guards."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from engine.catalog.ollama_runtime import OLLAMA_NARRATION_KEEP_ALIVE
from engine.pack.ollama_narration import (
    _chat_payload,
    is_ollama_infra_error,
    resolve_narration_model,
    rewrite_narration_with_ollama,
)


def _chat_ok(content: str) -> dict:
    return {
        "ok": True,
        "status_code": 200,
        "body": {"message": {"content": content}},
        "latency_ms": 5.0,
        "kind": "narration",
        "model": "qwen3.5:9b",
        "infra": False,
    }


def _chat_fail(error: str, *, error_kind: str = "timeout", infra: bool = True) -> dict:
    return {
        "ok": False,
        "error": error,
        "error_kind": error_kind,
        "infra": infra,
        "kind": "narration",
        "model": "qwen3.5:9b",
    }


class OllamaNarrationIdleGuards(unittest.TestCase):
    def test_infra_error_classify(self) -> None:
        self.assertTrue(is_ollama_infra_error("timed out"))
        self.assertTrue(is_ollama_infra_error("ReadTimeout"))
        self.assertTrue(is_ollama_infra_error("ollama HTTP 500"))
        self.assertFalse(is_ollama_infra_error("模型文案过短"))
        self.assertFalse(is_ollama_infra_error("无法解析模型输出"))

    def test_chat_payload_pins_think_keepalive_predict(self) -> None:
        payload = _chat_payload(
            model="qwen3.5:9b",
            prompt="hi",
            tone="plain",
            variation_seed=7,
        )
        self.assertFalse(payload["think"])
        self.assertEqual(payload["keep_alive"], OLLAMA_NARRATION_KEEP_ALIVE)
        self.assertEqual(payload["options"]["num_predict"], 640)
        self.assertEqual(payload["options"]["seed"], 7)
        self.assertNotEqual(payload["keep_alive"], 0)

    def test_resolve_default_prefers_qwen35(self) -> None:
        s = MagicMock()
        s.ollama_narration_model = ""
        s.ollama_vision_model = ""
        with patch(
            "engine.catalog.ollama_status.active_models_from_settings",
            side_effect=RuntimeError("offline"),
        ):
            self.assertEqual(resolve_narration_model(s), "qwen3.5:9b")

    def test_rewrite_retries_then_ok(self) -> None:
        bad = _chat_ok("")
        ok = _chat_ok(
            '{"script":"装车工位码齐箱。车厢门拉开放货。工人封绳再看一眼。","emoji_cues":[]}'
        )

        with patch("engine.pack.ollama_narration.circuit_allows_request", return_value=True):
            with patch(
                "engine.pack.ollama_narration.chat_completion",
                side_effect=[bad, ok],
            ) as chat_mock:
                with patch("engine.pack.ollama_narration.time.sleep"):
                    result = rewrite_narration_with_ollama(
                        base_script="底稿参考句。",
                        visual_hints=["装车工位码齐箱", "车厢门拉开"],
                        theme="发货",
                        brand="测试",
                        target_duration_sec=12.0,
                        model="qwen3.5:9b",
                        timeout=5.0,
                    )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["attempts"], 2)
        self.assertIn("装车", result["script"])
        self.assertEqual(chat_mock.call_count, 2)
        kwargs = chat_mock.call_args.kwargs
        self.assertFalse(kwargs["think"])
        self.assertEqual(kwargs["keep_alive"], OLLAMA_NARRATION_KEEP_ALIVE)

    def test_rewrite_timeout_marks_infra(self) -> None:
        with patch("engine.pack.ollama_narration.circuit_allows_request", return_value=True):
            with patch(
                "engine.pack.ollama_narration.chat_completion",
                return_value=_chat_fail("ReadTimeout: timed out", error_kind="timeout"),
            ):
                with patch("engine.pack.ollama_narration.time.sleep"):
                    result = rewrite_narration_with_ollama(
                        base_script="底稿参考句。",
                        visual_hints=["装车"],
                        theme="发货",
                        brand="测试",
                        target_duration_sec=12.0,
                        model="qwen3.5:9b",
                        timeout=5.0,
                    )
        self.assertFalse(result["ok"])
        self.assertTrue(result["infra"])
        self.assertEqual(result["attempts"], 2)

    def test_rewrite_cancel_event_stops_between_attempts(self) -> None:
        cancel_event = threading.Event()

        def _fail_and_cancel(*_a, **_k):
            cancel_event.set()
            return _chat_fail("ollama HTTP 500", error_kind="runner_crash")

        with patch("engine.pack.ollama_narration.circuit_allows_request", return_value=True):
            with patch(
                "engine.pack.ollama_narration.chat_completion",
                side_effect=_fail_and_cancel,
            ) as chat_mock:
                with patch("engine.pack.ollama_narration.time.sleep"):
                    result = rewrite_narration_with_ollama(
                        base_script="底稿参考句。",
                        visual_hints=["装车"],
                        theme="发货",
                        brand="测试",
                        target_duration_sec=12.0,
                        model="qwen3.5:9b",
                        timeout=5.0,
                        cancel_event=cancel_event,
                    )
        self.assertFalse(result["ok"])
        self.assertTrue(result["infra"])
        # Second attempt must observe the cancel flag instead of retrying.
        self.assertEqual(chat_mock.call_count, 1)

    def test_rewrite_wall_clock_releases_ollama_heavy(self) -> None:
        from engine.runtime.resource_gate import ResourceGate

        gate = ResourceGate()
        gate.configure(ollama_heavy_slots=1)

        with patch("engine.pack.ollama_narration.circuit_allows_request", return_value=True):
            with patch("engine.pack.ollama_narration._NARRATION_WALL_SEC", 1.5):
                with patch(
                    "engine.pack.ollama_narration.chat_completion",
                    side_effect=lambda **kw: {
                        "ok": False,
                        "error": "wall-clock timeout (1s)",
                        "error_kind": "timeout",
                        "infra": True,
                        "kind": "narration",
                        "model": kw.get("model"),
                    },
                ):
                    result = rewrite_narration_with_ollama(
                        base_script="底稿参考句。",
                        visual_hints=["装车"],
                        theme="发货",
                        brand="测试",
                        target_duration_sec=12.0,
                        model="qwen3.5:9b",
                        timeout=60.0,
                    )
        self.assertFalse(result["ok"])
        self.assertTrue(result["infra"])
        self.assertIn("wall-clock", result["error"])
        self.assertEqual(gate.snapshot()["pools"]["ollama_heavy"]["used"], 0)
        self.assertTrue(gate.try_acquire("ollama_heavy", "narration:next"))
        gate.release("ollama_heavy", "narration:next")


class InfraPauseThresholdTests(unittest.TestCase):
    def test_skip_pauses_after_threshold(self) -> None:
        from unittest.mock import MagicMock, patch

        from engine.jobs.worker import skip_current_item

        job = MagicMock()
        job.id = 1
        job.customer_id = 1
        job.produced_count = 0
        job.target_count = 10
        job.status = "running"
        job.config_snapshot_json = {"consecutive_ollama_infra": 2}

        session = MagicMock()
        settings = MagicMock()
        settings.ollama_infra_pause_threshold = 3

        with patch("engine.catalog.paper_slip.release_paper_slip"):
            with patch("engine.runtime.resource_gate.gate") as gate:
                gate.release = MagicMock()
                gate.release_all = MagicMock()
                with patch("engine.config.settings.load_settings", return_value=settings):
                    with patch("engine.jobs.worker.log_event"):
                        skip_current_item(
                            session,
                            job,
                            reason="ollama_infra",
                            stage="narration",
                            error="circuit_open",
                            infra=True,
                        )
        self.assertEqual(job.status, "paused")
        snap = job.config_snapshot_json
        self.assertEqual(snap.get("consecutive_ollama_infra"), 3)
        self.assertEqual(snap.get("_pipeline_phase"), "paused_ollama_infra")


class RawVisionSpokenGuardTests(unittest.TestCase):
    def test_rejects_identical_long_caption(self) -> None:
        from engine.pack.narration_script import assert_spoken_not_raw_vision

        caption = "仓库货架上整齐码放着各种规格的五金配件与紧固件样品展示区全景"
        with self.assertRaises(ValueError):
            assert_spoken_not_raw_vision(caption, [caption])

    def test_allows_industry_line(self) -> None:
        from engine.pack.narration_script import assert_spoken_not_raw_vision

        assert_spoken_not_raw_vision(
            "配送装车按清单清点，出库节奏清楚。",
            ["仓库货架上整齐码放着各种规格的五金配件与紧固件样品展示区全景"],
        )


if __name__ == "__main__":
    unittest.main()
