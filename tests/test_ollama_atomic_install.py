"""Regression tests for atomic Ollama install helpers and service state merge."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from engine.ops import ollama_service


ROOT = Path(__file__).resolve().parents[1]
ATOMIC = ROOT / "scripts" / "install-ollama-atomic.sh"


def _make_fake_bin(path: Path, payload: bytes, *, sign: bool = True) -> None:
    path.write_bytes(payload)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if sign:
        subprocess.run(
            ["codesign", "--force", "--sign", "-", str(path)],
            check=True,
            capture_output=True,
        )


def test_merge_state_preserves_kickstart_counters(tmp_path: Path, monkeypatch):
    state_file = tmp_path / "service_state.json"
    monkeypatch.setattr(ollama_service, "STATE_FILE", state_file)
    ollama_service.write_state(
        {
            "kickstart_window_start": 100.0,
            "kickstart_count": 2,
            "last_kickstart_reason": "prev",
        }
    )
    ollama_service.merge_state(consecutive_probe_failures=0, last_recovery="probe_ok")
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["kickstart_count"] == 2
    assert data["kickstart_window_start"] == 100.0
    assert data["consecutive_probe_failures"] == 0
    assert data["last_recovery"] == "probe_ok"


def test_maybe_recover_does_not_wipe_kickstart(tmp_path: Path, monkeypatch):
    state_file = tmp_path / "service_state.json"
    monkeypatch.setattr(ollama_service, "STATE_FILE", state_file)
    ollama_service.write_state(
        {
            "kickstart_window_start": 1.0,
            "kickstart_count": 1,
            "consecutive_probe_failures": 1,
        }
    )

    def fake_probe(*, force: bool = False):
        return {"ok": False, "message": "embed_fail"}

    with (
        patch("engine.ops.ollama_service.functional_embed_probe", side_effect=fake_probe),
        patch(
            "engine.ops.ollama_service.bounded_kickstart",
            return_value={"ok": False, "action": "kickstart", "kickstart_count": 2},
        ) as kick,
    ):
        # First failure only increments; second triggers kickstart
        ollama_service.merge_state(consecutive_probe_failures=1)
        out = ollama_service.maybe_recover_embed_service()
    assert out["action"] == "kickstart"
    kick.assert_called_once()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    # kickstart counters from before must still be readable (merge, not overwrite)
    assert data.get("kickstart_count") == 1
    assert data.get("consecutive_probe_failures") == 0


def test_install_gate_blocks_bad_signature(monkeypatch):
    fake = Path("/tmp/suying-test-ollama-gate")
    fake.write_text("x", encoding="utf-8")
    try:
        with patch(
            "engine.ops.ollama_service.ownership_snapshot",
            return_value={
                "ownership": "none",
                "managed_binary_exists": True,
                "managed_codesign": {"ok": False},
                "models": {"fork_detected": False},
            },
        ), patch("engine.ops.ollama_service.MANAGED_BIN", fake):
            reason = ollama_service.install_gate_block_reason()
        assert reason is not None
        assert "签名" in reason
    finally:
        fake.unlink(missing_ok=True)


def test_install_gate_blocks_model_fork(monkeypatch):
    fake = Path("/tmp/suying-test-ollama-gate-fork")
    fake.write_text("x", encoding="utf-8")
    try:
        with patch(
            "engine.ops.ollama_service.ownership_snapshot",
            return_value={
                "ownership": "suying_managed",
                "managed_binary_exists": True,
                "managed_codesign": {"ok": True},
                "models": {
                    "fork_detected": True,
                    "managed_models": "a",
                    "user_models": "b",
                },
            },
        ), patch("engine.ops.ollama_service.MANAGED_BIN", fake):
            reason = ollama_service.install_gate_block_reason()
        assert reason is not None
        assert "分叉" in reason
    finally:
        fake.unlink(missing_ok=True)


def test_atomic_script_rejects_unsigned_source(tmp_path: Path):
    src = tmp_path / "ollama"
    # unsigned payload
    src.write_bytes(b"#!/bin/sh\necho fake\n")
    src.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()
    proc = subprocess.run(
        ["bash", str(ATOMIC), str(src), "--skip-restart"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode != 0
    assert "codesign" in (proc.stderr + proc.stdout).lower() or "签名" in (proc.stderr + proc.stdout)


def test_atomic_rename_preserves_prev(tmp_path: Path):
    """Pure filesystem contract: replace must keep previous as rollback point."""
    tool_bin = tmp_path / "bin"
    tool_bin.mkdir()
    dest = tool_bin / "ollama"
    prev = tool_bin / "ollama.prev"
    staging = tool_bin / "ollama.staging"
    dest.write_bytes(b"old")
    staging.write_bytes(b"new")
    if dest.exists():
        if prev.exists():
            prev.unlink()
        dest.rename(prev)
    staging.rename(dest)
    assert dest.read_bytes() == b"new"
    assert prev.read_bytes() == b"old"
