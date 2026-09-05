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
