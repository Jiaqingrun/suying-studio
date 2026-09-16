"""Version single-source contract (DEEP OPT.P0)."""

from __future__ import annotations

import re
from pathlib import Path

from engine.version import ENGINE_VERSION


def test_engine_version_format() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", ENGINE_VERSION)


def test_engine_dunder_matches() -> None:
    import engine

    assert engine.__version__ == ENGINE_VERSION


def test_package_json_matches_engine_version() -> None:
    import json

    root = Path(__file__).resolve().parents[1]
    pkg = json.loads((root / "apps/desktop/package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == ENGINE_VERSION



def test_four_version_sources_aligned() -> None:
    """VERSION_SOURCE: package.json · tauri.conf · Cargo.toml · ENGINE_VERSION."""
    import json
    import re

    root = Path(__file__).resolve().parents[1]
    pkg = json.loads((root / "apps/desktop/package.json").read_text(encoding="utf-8"))
    tauri = json.loads(
        (root / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )
    cargo = (root / "apps/desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    # First [package] version= only (not dependency crates).
    m = re.search(r"(?m)^\[package\]\s*\n(?:.*\n)*?^version\s*=\s*\"([^\"]+)\"", cargo)
    assert m, "Cargo.toml [package].version missing"
    cargo_ver = m.group(1)
    assert pkg["version"] == ENGINE_VERSION
    assert tauri["version"] == ENGINE_VERSION
    assert cargo_ver == ENGINE_VERSION


def test_no_stale_supervisor_literal() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "engine/api/maintenance_routes.py").read_text(encoding="utf-8")
    assert "0.6.39" not in text
    assert "ENGINE_VERSION" in text


def test_readiness_includes_engine_version() -> None:
    from engine.api.readiness import build_readiness_snapshot

    snap = build_readiness_snapshot()
    assert snap.get("engine_version") == ENGINE_VERSION
