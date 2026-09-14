"""Tests for /readiness business-usable gate."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from engine.api.readiness import build_readiness_snapshot


def test_readiness_development_build_authorized():
    with patch("engine.security.license.is_packaged_runtime", return_value=False):
        snap = build_readiness_snapshot()
    assert snap["development_build"] is True
    assert snap["license_authorized"] is True
    assert snap["runtime_source_valid"] is True


def test_readiness_packaged_missing_trusted_keys():
    with patch("engine.security.license.is_packaged_runtime", return_value=True):
        with patch(
            "engine.api.readiness._trusted_keys_path",
            return_value=__import__("pathlib").Path("/nonexistent/trusted_release_keys.json"),
        ):
            snap = build_readiness_snapshot()
    assert snap["runtime_source_valid"] is False
    assert snap["ready"] is False
    assert "缺失许可证公钥" in snap["runtime_source_reason"]


def test_readiness_route_registered_in_app_source():
    app_py = Path(__file__).resolve().parents[1] / "engine" / "api" / "app.py"
    text = app_py.read_text(encoding="utf-8")
    assert "/readiness" in text
    assert "engine_readiness" in text


def test_readiness_never_calls_live_ollama_probe():
    """Desktop kickstart budgets ~800ms on /readiness; live Ollama must not run."""

    def boom(*_a, **_k):
        raise AssertionError("live Ollama HTTP must not run on /readiness")

    with (
        patch("engine.security.license.is_packaged_runtime", return_value=False),
        patch(
            "engine.catalog.ollama_status.check_ollama",
            side_effect=boom,
        ),
        patch(
            "engine.catalog.ollama_runtime._shallow_tags_probe",
            side_effect=boom,
        ),
        patch(
            "engine.catalog.ollama_runtime.ollama_health_snapshot",
            side_effect=boom,
        ),
        patch(
            "engine.catalog.ollama_status.ollama_status_for_health",
            return_value={
                "reachable": True,
                "models": ["qwen3.5:9b"],
                "cached": True,
            },
        ),
        patch(
            "engine.catalog.ollama_runtime.ollama_control_plane_snapshot",
            return_value={"circuit": {}, "chat_probe_ok": True, "live_http": False},
        ),
    ):
        snap = build_readiness_snapshot()
    assert snap["process_alive"] is True
    assert snap.get("chat_probe_ok") is True
    assert "narration_model_missing" in snap
