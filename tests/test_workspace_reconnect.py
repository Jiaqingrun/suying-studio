"""GStab.WORKSPACE: fail-closed probe — never mkdir empty external DB."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.config.settings import AppSettings, PathConfig, save_settings
from engine.config.workspace import (
    STATE_LOCAL,
    STATE_MISSING,
    STATE_READY,
    WorkspaceIdentity,
    ensure_identity_on_ready,
    is_external_data_root,
    probe_workspace,
    read_marker,
    write_marker,
)


def test_ephemeral_is_local_and_allows_mkdir(tmp_path: Path):
    root = tmp_path / "data"
    settings = AppSettings(paths=PathConfig(data_root=root))
    probe = probe_workspace(settings)
    assert probe.state == STATE_LOCAL
    assert probe.is_external is False
    assert probe.can_mkdir is True
    assert probe.can_init_db is True


def test_missing_external_does_not_mkdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Simulate external path outside ~/Suying and not ephemeral.
    fake_vol = Path("/Volumes/SuyingFakeMissingWS") / "速影工作区" / "db"
    settings = AppSettings(paths=PathConfig(data_root=fake_vol))
    assert is_external_data_root(fake_vol) is True
    probe = probe_workspace(settings)
    assert probe.state == STATE_MISSING
    assert probe.can_mkdir is False
    assert probe.can_init_db is False
    assert not fake_vol.exists()

    # save_settings must not create the external tree
    boot = tmp_path / "boot"
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    save_settings(settings)
    assert not fake_vol.exists()
    assert (boot / "settings.json").is_file()
    raw = json.loads((boot / "settings.json").read_text(encoding="utf-8"))
    assert raw["paths"]["data_root"] == str(fake_vol)


def test_db_path_refuses_empty_external(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from engine.catalog import db as dbmod

    fake_vol = Path("/Volumes/SuyingFakeMissingWS2") / "速影工作区" / "db"
    settings = AppSettings(paths=PathConfig(data_root=fake_vol))
    monkeypatch.setattr(dbmod, "load_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="拒绝|不存在|不可用"):
        dbmod.db_path(settings)
    assert not fake_vol.exists()
    assert not (fake_vol / "montage.db").exists()


def test_ready_external_with_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Build a fake "external" tree under a non-Suying, non-ephemeral-looking path.
    # Use monkeypatch on is_external_data_root / volume helpers to avoid diskutil.
    root = tmp_path / "ext-workspace" / "db"
    root.mkdir(parents=True)
    (root / "settings.json").write_text("{}", encoding="utf-8")
    # Minimal sqlite file with integrity ok
    import sqlite3

    db = root / "montage.db"
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE render_outputs (id INTEGER PRIMARY KEY)")
        con.execute("INSERT INTO customers (id) VALUES (1)")
        con.commit()
    finally:
        con.close()

    settings = AppSettings(paths=PathConfig(data_root=root), workspace_id="ws-test-1")
    write_marker(root, WorkspaceIdentity(workspace_id="ws-test-1", volume_uuid="VOL-1"))

    import engine.config.workspace as ws

    monkeypatch.setattr(ws, "is_external_data_root", lambda *_a, **_k: True)
    monkeypatch.setattr(
        ws,
        "volume_info_for_path",
        lambda *_a, **_k: {
            "ok": True,
            "volume_uuid": "VOL-1",
            "mount_point": str(tmp_path / "ext-workspace"),
            "error": None,
        },
    )
    monkeypatch.setattr(ws, "volume_mount_point", lambda *_a, **_k: str(tmp_path / "ext-workspace"))
    # settings also needs bound volume for mount checks
    settings.workspace_volume_uuid = "VOL-1"

    probe = probe_workspace(settings)
    assert probe.state == STATE_READY
    assert probe.ok is True
    assert probe.can_init_db is True
    assert probe.db_ok is True


def test_volume_mismatch_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "ext-workspace" / "db"
    root.mkdir(parents=True)
    (root / "settings.json").write_text("{}", encoding="utf-8")
    import sqlite3

    db = root / "montage.db"
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE render_outputs (id INTEGER PRIMARY KEY)")
        con.execute("INSERT INTO jobs (id) VALUES (1)")
        con.commit()
    finally:
        con.close()
    write_marker(root, WorkspaceIdentity(workspace_id="ws-a", volume_uuid="VOL-A"))
    settings = AppSettings(
        paths=PathConfig(data_root=root),
        workspace_id="ws-a",
        workspace_volume_uuid="VOL-A",
    )

    import engine.config.workspace as ws

    monkeypatch.setattr(ws, "is_external_data_root", lambda *_a, **_k: True)
    monkeypatch.setattr(ws, "volume_mount_point", lambda *_a, **_k: None)
    probe = probe_workspace(settings)
    assert probe.state == STATE_MISSING
    assert probe.can_init_db is False


def test_ensure_identity_writes_marker(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir()
    settings = AppSettings(paths=PathConfig(data_root=root))
    identity = ensure_identity_on_ready(settings, volume_uuid="VOL-X")
    assert identity.workspace_id
    assert settings.workspace_id == identity.workspace_id
    assert settings.workspace_volume_uuid == "VOL-X"
    marker = read_marker(root)
    assert marker is not None
    assert marker.workspace_id == identity.workspace_id


def test_ensure_layout_skips_missing_external(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from engine.config.paths import ensure_layout

    fake = Path("/Volumes/SuyingLayoutSkip") / "db"
    settings = AppSettings(
        paths=PathConfig(
            data_root=fake,
            cache_root=fake / "cache",
            render_root=fake / "render",
            output_root=fake / "output",
            music_root=fake / "music",
        )
    )
    ensure_layout(settings)
    assert not fake.exists()


def test_reconcile_local_first_clears_external_required(tmp_path: Path, monkeypatch):
    from engine.config.settings import AppSettings, PathConfig, reconcile_local_first_paths

    boot = tmp_path / "Suying" / "data"
    boot.mkdir(parents=True)
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    settings = AppSettings(
        paths=PathConfig(data_root=boot, external_required=True),
        workspace_volume_uuid="STALE-VOL",
    )
    dirty = reconcile_local_first_paths(settings)
    assert dirty is True
    assert settings.paths.external_required is False
    assert settings.workspace_volume_uuid == ""


def test_reconcile_keeps_external_required_on_volume(tmp_path: Path, monkeypatch):
    from engine.config.settings import AppSettings, PathConfig, reconcile_local_first_paths

    boot = tmp_path / "Suying" / "data"
    boot.mkdir(parents=True)
    monkeypatch.setenv("SUYING_DATA_ROOT", str(boot))
    ext = Path("/Volumes/SomeDisk/suying-db")
    settings = AppSettings(
        paths=PathConfig(data_root=ext, external_required=True),
        workspace_volume_uuid="VOL-1",
    )
    dirty = reconcile_local_first_paths(settings)
    assert dirty is False
    assert settings.paths.external_required is True
    assert settings.workspace_volume_uuid == "VOL-1"
