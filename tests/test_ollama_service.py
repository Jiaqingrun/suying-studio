"""Ollama service ownership and install gate."""

from __future__ import annotations

from unittest.mock import patch

from engine.ops.ollama_service import classify_listener, install_gate_block_reason


def test_classify_external_ollama():
    assert classify_listener(99, "/Applications/Ollama.app/Contents/Resources/ollama serve") == "external"


def test_classify_suying_managed():
    cmd = "/Users/x/Suying/runtime/tools/bin/ollama serve"
    assert classify_listener(1, cmd) == "suying_managed"


def test_install_gate_blocks_external():
    with patch(
        "engine.ops.ollama_service.ownership_snapshot",
        return_value={"ownership": "external", "listener_pid": 42},
    ):
        reason = install_gate_block_reason()
    assert reason is not None
    assert "外部" in reason
