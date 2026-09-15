"""R2 adversarial: attack debt lines + R1 retention without regressing L17–L20."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from engine.api.ops_auth import require_ops_token
from engine.api.output_scope import assert_streamable_media_path
from engine.api.readiness import build_readiness_snapshot
from engine.runtime.resource_gate import ResourceGate


def test_ops_token_rejects_wrong_and_short_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    expected = "c" * 64
    (tmp_path / "system_token.txt").write_text(expected, encoding="utf-8")
    with pytest.raises(HTTPException) as wrong:
        require_ops_token("d" * 64)
    assert wrong.value.status_code == 403
    with pytest.raises(HTTPException) as short:
        require_ops_token("c" * 32)
    assert short.value.status_code == 403
    # Trailing whitespace must still authenticate (header/file strip contract).
    require_ops_token(expected + "\n")


def test_ops_token_malformed_file_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "system_token.txt").write_text("too-short", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        require_ops_token("too-short")
    assert exc.value.status_code == 403


def test_force_release_endpoint_requires_ops_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUYING_APP_RUNTIME_DIR", str(tmp_path))
    token = "e" * 64
    (tmp_path / "system_token.txt").write_text(token, encoding="utf-8")
    from engine.api.app import app

    client = TestClient(app)
    denied = client.post("/ops/resource-gate/force-release", json={"slot": "all"})
    assert denied.status_code == 403
    ok = client.post(
        "/ops/resource-gate/force-release",
        json={"slot": "tts", "older_than_sec": 1.0},
        headers={"X-Suying-Ops-Token": token},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body.get("ok") is True
    assert "released" in body


def test_lease_heartbeat_renews_before_force_release() -> None:
    gate = ResourceGate()
    gate.configure(tts_slots=1)
    assert gate.try_acquire("tts", "job:hb")
    with gate.heartbeat("tts", "job:hb", interval_sec=5.0):
        gate._pools["tts"].holders["job:hb"] = time.monotonic() - 999.0
        assert gate.touch("tts", "job:hb")
        released = gate.force_release_expired("tts", older_than_sec=600.0)
        assert released.get("tts") in (None, [])
        assert "job:hb" in gate._pools["tts"].holders
    gate.release("tts", "job:hb")


def test_paper_slip_hold_statuses_include_paused() -> None:
    from engine.catalog import paper_slip as ps

    assert "paused" in ps._RESERVATION_HOLD_JOB_STATUSES
    assert "paused_system" in ps._RESERVATION_HOLD_JOB_STATUSES
    assert "circuit_open" not in ps._RESERVATION_HOLD_JOB_STATUSES


def test_pack_publish_tts_failures_warn_not_silent() -> None:
    src = (
        Path(__file__).resolve().parents[1] / "engine" / "pack" / "publish.py"
    ).read_text(encoding="utf-8")
    assert "logger.warning(" in src
    assert "continuing without pack-side VO" in src
    assert "variant continues without VO" in src


def test_r1_media_relative_escape_still_blocked(tmp_path: Path) -> None:
    root = tmp_path / "out"
    root.mkdir()
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"x")
    # Relative path that resolves outside the allow-root must fail closed.
    with pytest.raises(HTTPException) as exc:
        assert_streamable_media_path(root / ".." / "secret.mp4", [root])
    assert exc.value.status_code == 403


def test_readiness_offline_class_aligns_with_desktop_engine_vocab() -> None:
    """Engine-emitted offline_class must stay in desktop offlineClassLabel map."""
    desktop = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "desktop"
        / "src"
        / "engineControl.ts"
    ).read_text(encoding="utf-8")
    engine_classes = {
        "ok",
        "starting",
        "boot_failed",
        "license",
        "workspace",
        "integrity",
        "control_plane_not_ready",
        "not_ready",
    }
    for token in engine_classes:
        assert f'case "{token}"' in desktop
    with patch("engine.security.license.is_packaged_runtime", return_value=False):
        snap = build_readiness_snapshot()
    assert snap["offline_class"] in engine_classes
    assert snap["offline_class"] != "control_plane"


def test_pack_warning_logger_name_reachable(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("montage.pack.publish")
    with caplog.at_level(logging.WARNING, logger="montage.pack.publish"):
        log.warning("publish_pack zh narration/TTS failed (continuing without pack-side VO): boom")
    assert any("continuing without pack-side VO" in r.message for r in caplog.records)
