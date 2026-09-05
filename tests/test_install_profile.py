from __future__ import annotations

import stat
from pathlib import Path

import pytest

from engine.catalog.host_profile import HostProfile
from engine.ops.install_profile import (
    INSTALL_PLAN_SCHEMA,
    build_install_plan,
    load_approved_install_plan,
    mark_install_plan_stage,
    persist_install_plan,
    read_install_state,
)


def _host(*, ram_gb: float, tier: str) -> HostProfile:
    return HostProfile(
        ram_gb=ram_gb,
        arch="arm64",
        chip="Apple Test",
        platform="Darwin",
        tier=tier,
        python_path="/usr/bin/python3",
        python_version="3.12.0",
        python_ok=True,
        ffmpeg_ok=True,
        ffmpeg_path="/opt/homebrew/bin/ffmpeg",
        ollama_cli=True,
        ollama_path="/opt/homebrew/bin/ollama",
    )


def test_lite_plan_keeps_vision_optional_and_never_selects_27b() -> None:
    plan = build_install_plan(host=_host(ram_gb=8, tier="lite"))

    assert plan["schema_version"] == INSTALL_PLAN_SCHEMA
    assert plan["profile"]["id"] == "lite"
    assert plan["profile"]["full_library_vision"] is False
    assert plan["selected_models"] == ["nomic-embed-text"]
    assert all("27b" not in model.lower() for model in plan["selected_models"])
    assert plan["settings_patch"]["semantic_analysis_mode"] == "off"
    assert plan["settings_patch"]["semantic_toggle_enabled"] is False
    assert plan["settings_patch"]["clone_tts_allowed"] is False
    assert plan["settings_patch"]["gate_profile_version"] == "gate_profile.v1"
    assert plan["settings_patch"]["semantic_full_backfill_enabled"] is False
    assert plan["settings_patch"]["max_render_concurrency"] == 1


def test_max_plan_allows_render_concurrency_two() -> None:
    plan = build_install_plan(host=_host(ram_gb=96, tier="max"), include_vision=True)
    assert plan["profile"]["id"] == "max"
    assert plan["settings_patch"]["max_render_concurrency"] == 2


def test_plan_id_is_stable_for_same_machine_and_selection() -> None:
    host = _host(ram_gb=32, tier="pro")

    first = build_install_plan(host=host, include_vision=True, include_narration=True)
    second = build_install_plan(host=host, include_vision=True, include_narration=True)

    assert first["plan_id"] == second["plan_id"]
    assert first["selected_models"] == [
        "nomic-embed-text",
        "qwen3.5:9b",
        "qwen3.5:9b",
    ]


def test_persisted_plan_is_exposed_by_install_state(tmp_path: Path) -> None:
    plan = build_install_plan(host=_host(ram_gb=16, tier="standard"))
    plan["approved_at"] = "2026-07-27T00:00:00+00:00"

    target = persist_install_plan(plan, root=tmp_path)
    mark_install_plan_stage(plan, "configured", root=tmp_path)
    state = read_install_state(root=tmp_path)

    assert target == tmp_path / "install-plan.json"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert state["plan"]["plan_id"] == plan["plan_id"]
    assert state["plan"]["configured_at"]
    assert state["receipt"] is None


def test_only_persisted_approved_plan_id_can_be_consumed(tmp_path: Path) -> None:
    plan = build_install_plan(host=_host(ram_gb=16, tier="standard"))
    persist_install_plan(plan, root=tmp_path)

    with pytest.raises(ValueError, match="尚未批准"):
        load_approved_install_plan(plan["plan_id"], root=tmp_path)

    plan["approved_at"] = "2026-07-27T00:00:00+00:00"
    persist_install_plan(plan, root=tmp_path)
    loaded = load_approved_install_plan(plan["plan_id"], root=tmp_path)
    assert loaded["settings_patch"] == plan["settings_patch"]

    with pytest.raises(ValueError, match="不匹配"):
        load_approved_install_plan("wrong-plan-id", root=tmp_path)


def test_remote_upgrade_preserves_existing_customer_paths() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "remote-install.sh"
    ).read_text(encoding="utf-8")

    assert 'customer = request("PATCH", f"/customers/{customer[\'id\']}", payload)' not in script
    assert "current_pack.is_file()" in script
    assert "不能用它覆盖已经绑定的外置盘或同步目录" in script


def test_tampered_approved_plan_is_rejected(tmp_path: Path) -> None:
    plan = build_install_plan(host=_host(ram_gb=32, tier="pro"))
    plan["approved_at"] = "2026-07-27T00:00:00+00:00"
    target = persist_install_plan(plan, root=tmp_path)
    plan["settings_patch"]["max_render_concurrency"] = 99
    target.write_text(__import__("json").dumps(plan), encoding="utf-8")

    with pytest.raises(ValueError, match="plan_id"):
        load_approved_install_plan(plan["plan_id"], root=tmp_path)
