"""R1 security: media stream path confinement + ntfy secrets fail-closed mkdir."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from engine.api.output_scope import assert_streamable_media_path
from engine.config.settings import AppSettings, PathConfig


def test_assert_streamable_media_path_allows_under_root(tmp_path: Path) -> None:
    root = tmp_path / "output"
    good = root / "ready" / "a.mp4"
    good.parent.mkdir(parents=True)
    good.write_bytes(b"ok")
    resolved = assert_streamable_media_path(good, [root])
    assert resolved == good.resolve()


def test_assert_streamable_media_path_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("leak", encoding="utf-8")
    with pytest.raises(HTTPException) as exc:
        assert_streamable_media_path(secret, [root])
    assert exc.value.status_code == 403


def test_assert_streamable_media_path_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    link = root / "escape.mp4"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not available")
    with pytest.raises(HTTPException) as exc:
        assert_streamable_media_path(link, [root])
    assert exc.value.status_code == 403


def test_ntfy_secrets_read_does_not_mkdir_missing_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engine.reach import notifications as ntfy

    fake = Path("/Volumes/SuyingNtfyMissingSec") / "db"
    settings = AppSettings(paths=PathConfig(data_root=fake))
    monkeypatch.setattr(ntfy, "load_settings", lambda: settings)
    assert ntfy._read_all() == {}
    assert not fake.exists()


def test_ntfy_secrets_write_refuses_missing_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from engine.reach import notifications as ntfy

    fake = Path("/Volumes/SuyingNtfyMissingSec2") / "db"
    settings = AppSettings(paths=PathConfig(data_root=fake))
    monkeypatch.setattr(ntfy, "load_settings", lambda: settings)
    with pytest.raises(ValueError, match="拒绝"):
        ntfy._write_all({"1": {"enabled": False}})
    assert not fake.exists()
