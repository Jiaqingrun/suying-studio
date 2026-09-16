"""V-01: Ollama half_open recover ops surface (no Ops-Token expansion)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from engine.api.app import app
from engine.catalog import ollama_runtime as rt


def _reset_circuit() -> None:
    with rt._circuit_lock:
        rt._circuit.state = rt.CircuitState.CLOSED
        rt._circuit.consecutive_failures = 0
        rt._circuit.open_until = 0.0
        rt._circuit.half_open_probe_inflight = False
        rt._circuit.last_error = ""


def test_ops_ollama_circuit_read_only() -> None:
    _reset_circuit()
    client = TestClient(app)
    resp = client.get("/ops/ollama/circuit")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["circuit"]["state"] == "closed"
    assert body["circuit"]["allows_request"] is True
    assert body["circuit"]["allows_narration"] is True


def test_ops_ollama_recover_closes_circuit() -> None:
    _reset_circuit()
    for i in range(rt._FAILURE_THRESHOLD):
        rt.record_failure(f"timeout-{i}", kind=rt.OllamaErrorKind.TIMEOUT)
    assert rt.circuit_snapshot()["state"] == "open"

    client = TestClient(app)
    with patch(
        "engine.ops.ollama_service.maybe_recover_ollama_service",
        return_value={"ok": True, "action": "none", "probe": {"ok": True}},
    ) as recover:
        # Simulate recover closing the circuit like production does.
        def _side_effect(*, kind: str = "chat"):
            rt.record_success(model="m")
            return {"ok": True, "action": "none", "probe": {"ok": True}, "kind": kind}

        recover.side_effect = _side_effect
        resp = client.post("/ops/ollama/recover", json={"kind": "chat"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["circuit"]["state"] == "closed"
    assert body["circuit"]["allows_request"] is True
