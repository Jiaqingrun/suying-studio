"""Machine-level Ollama circuit + cancelable subprocess gateway."""

from __future__ import annotations

import json
import threading
import time
import unittest
from unittest.mock import patch

from engine.catalog import ollama_runtime as rt


class OllamaRuntimeGateway(unittest.TestCase):
    def setUp(self) -> None:
        with rt._circuit_lock:
            rt._circuit.state = rt.CircuitState.CLOSED
            rt._circuit.consecutive_failures = 0
            rt._circuit.open_until = 0.0
            rt._circuit.half_open_probe_inflight = False
            rt._circuit.last_error = ""

    def test_circuit_opens_after_threshold(self) -> None:
        for i in range(rt._FAILURE_THRESHOLD):
            rt.record_failure(f"timeout-{i}", kind=rt.OllamaErrorKind.TIMEOUT)
        snap = rt.circuit_snapshot()
        self.assertEqual(snap["state"], "open")
        self.assertFalse(rt.circuit_allows_request(for_probe=False))

    def test_half_open_allows_only_one_probe(self) -> None:
        for i in range(rt._FAILURE_THRESHOLD):
            rt.record_failure(f"timeout-{i}", kind=rt.OllamaErrorKind.TIMEOUT)
        with rt._circuit_lock:
            rt._circuit.open_until = rt._now() - 1.0
        self.assertTrue(rt.circuit_allows_request(for_probe=True))
        self.assertFalse(rt.circuit_allows_request(for_probe=True))
        self.assertFalse(rt.circuit_allows_request(for_probe=False))

    def test_heavy_request_blocked_when_circuit_open(self) -> None:
        for i in range(rt._FAILURE_THRESHOLD):
            rt.record_failure(f"timeout-{i}", kind=rt.OllamaErrorKind.TIMEOUT)
        out = rt.heavy_request(
            kind="narration",
            path="/api/chat",
            payload={"model": "x"},
            timeout_sec=5.0,
            model="x",
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_kind"], "circuit_open")

    def test_run_cancelable_post_kills_on_cancel(self) -> None:
        cancel = threading.Event()

        def _slow(*_a, **_k):
            # Simulate a hanging worker by patching Popen
            class Fake:
                def poll(self):
                    return None

                def kill(self):
                    cancel.set()

                def wait(self, timeout=None):
                    return 0

                def communicate(self, timeout=None):
                    return ("", "")

            return Fake()

        with patch("engine.catalog.ollama_runtime.subprocess.Popen", side_effect=_slow):
            cancel_event = threading.Event()

            def _set_soon():
                time.sleep(0.2)
                cancel_event.set()

            threading.Thread(target=_set_soon, daemon=True).start()
            out = rt.run_cancelable_post(
                path="/api/chat",
                payload={"model": "x"},
                timeout_sec=30.0,
                cancel_event=cancel_event,
            )
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_kind"], "cancelled")

    def test_exact_model_has_model(self) -> None:
        from engine.catalog.ollama_status import _has_model

        names = ["qwen3.5:9b", "nomic-embed-text:latest"]
        self.assertTrue(_has_model(names, "qwen3.5:9b"))
        self.assertFalse(_has_model(names, "qwen3.5:14b"))
        self.assertFalse(_has_model(names, "qwen3.5"))
        self.assertTrue(_has_model(names, "nomic-embed-text"))  # bare → :latest


if __name__ == "__main__":
    unittest.main()
