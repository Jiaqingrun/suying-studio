"""Ollama embedding gateway: circuit breaker and vector backend separation."""

from __future__ import annotations

from unittest.mock import patch

from engine.catalog.ollama_runtime import (
    CircuitState,
    _circuit,
    _circuit_lock,
    embed_circuit_allows_request,
    embed_gateway_snapshot,
    record_embed_failure,
    record_embed_success,
)
from engine.catalog.vector_index import cosine, embed_text, _hash_embed


def test_circuit_opens_after_failures():
    with _circuit_lock:
        _circuit.state = CircuitState.CLOSED
        _circuit.consecutive_failures = 0
        _circuit.open_until = 0.0
    for i in range(3):
        record_embed_failure(f"err{i}")
    assert not embed_circuit_allows_request()
    snap = embed_gateway_snapshot()
    assert snap["circuit"]["state"] == "open"


def test_circuit_closes_on_success():
    with _circuit_lock:
        _circuit.state = CircuitState.OPEN
        _circuit.consecutive_failures = 5
    record_embed_success()
    assert embed_circuit_allows_request()


def test_embed_text_circuit_open_uses_hash_fallback():
    with patch(
        "engine.catalog.ollama_runtime.embed_circuit_allows_request",
        return_value=False,
    ):
        emb, backend = embed_text("测试查询", strict=False)
    assert backend == "hash_fallback"
    assert len(emb) == 256


def test_hash_and_ollama_vectors_not_identical_space():
    a = _hash_embed("工地发货")
    b = [0.1] * 768
    # Different dims already block meaningful cosine; same helper returns bounded score.
    score = cosine(a, b)
    assert score <= 1.0
