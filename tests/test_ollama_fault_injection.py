"""Local fault-injection for cancelable Ollama gateway (no live daemon required)."""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from engine.catalog import ollama_runtime as rt
from engine.runtime.resource_gate import ResourceGate


class FaultInjection(unittest.TestCase):
    def setUp(self) -> None:
        with rt._circuit_lock:
            rt._circuit.state = rt.CircuitState.CLOSED
            rt._circuit.consecutive_failures = 0
            rt._circuit.open_until = 0.0
            rt._circuit.half_open_probe_inflight = False

    def test_wall_clock_kills_hanging_subprocess(self) -> None:
        class HangProc:
            def __init__(self):
                self._killed = False

            def poll(self):
                return None if not self._killed else -9

            def kill(self):
                self._killed = True

            def wait(self, timeout=None):
                return -9

            def communicate(self, timeout=None):
                return ("", "killed")

        with patch("engine.catalog.ollama_runtime.subprocess.Popen", return_value=HangProc()):
            started = time.monotonic()
            out = rt.run_cancelable_post(
                path="/api/chat",
                payload={"model": "x"},
                timeout_sec=1.0,
            )
        self.assertLess(time.monotonic() - started, 4.0)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error_kind"], "timeout")

    def test_circuit_blocks_storm(self) -> None:
        for i in range(rt._FAILURE_THRESHOLD):
            rt.record_failure(f"runner-{i}", kind=rt.OllamaErrorKind.RUNNER_CRASH)
        hits = []
        for _ in range(5):
            out = rt.heavy_request(
                kind="narration",
                path="/api/chat",
                payload={"model": "qwen3.5:9b"},
                timeout_sec=2.0,
                model="qwen3.5:9b",
            )
            hits.append(out.get("error_kind"))
        self.assertTrue(all(h == "circuit_open" for h in hits))

    def test_heavy_slot_single_holder(self) -> None:
        gate = ResourceGate()
        gate.configure(ollama_heavy_slots=1)
        held = []

        def _fake_acquire(slot, token):
            if slot != "ollama_heavy":
                return True
            if held:
                return False
            held.append(token)
            return True

        def _fake_release(slot, token):
            if held and held[0] == token:
                held.clear()

        with patch("engine.runtime.resource_gate.gate", gate):
            with patch.object(gate, "try_acquire", side_effect=_fake_acquire):
                with patch.object(gate, "release", side_effect=_fake_release):
                    with patch(
                        "engine.catalog.ollama_runtime.run_cancelable_post",
                        return_value={"ok": True, "status_code": 200, "body": {}},
                    ):
                        a = rt.heavy_request(
                            kind="narration",
                            path="/api/chat",
                            payload={},
                            timeout_sec=5.0,
                            model="m",
                            acquire_wait_sec=0.5,
                        )
                        self.assertTrue(a["ok"])


if __name__ == "__main__":
    unittest.main()
