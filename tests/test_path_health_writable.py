"""Path health must report read-only production roots as not ok/writable."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from engine.config.paths import (
    check_paths,
    clear_write_probe_cache,
    is_readonly_fs_error,
)
from engine.config.settings import AppSettings, PathConfig


def _settings(tmp: Path) -> AppSettings:
    lib = tmp / "library"
    out = tmp / "output"
    cache = tmp / "cache"
    render = tmp / "render"
    data = tmp / "data"
    for p in (lib, out, cache, render, data):
        p.mkdir(parents=True, exist_ok=True)
    return AppSettings(
        paths=PathConfig(
            library_root=lib,
            output_root=out,
            cache_root=cache,
            render_root=render,
            data_root=data,
            external_required=False,
        ),
        min_free_disk_gb=0.0,
    )


def test_writable_roots_ok(tmp_path: Path) -> None:
    clear_write_probe_cache()
    health = check_paths(_settings(tmp_path), force_write_probe=True)
    assert health.ok is True
    assert health.writable is True
    assert all(c.get("ok") for c in health.write_checks)
    assert not health.errors


def test_readonly_render_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_write_probe_cache()
    settings = _settings(tmp_path)
    real_write = Path.write_text

    def _block(self: Path, data: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if ".suying_wprobe_" in self.name and "render" in str(self.parent):
            raise OSError(errno.EROFS, "Read-only file system", str(self))
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _block)
    health = check_paths(settings, force_write_probe=True)
    assert health.ok is False
    assert health.writable is False
    assert any("渲染" in e and ("只读" in e or "不可写" in e) for e in health.errors)
    assert any(
        c.get("role") == "render" and c.get("ok") is False for c in health.write_checks
    )


def test_write_probe_cache_and_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_write_probe_cache()
    settings = _settings(tmp_path)
    h1 = check_paths(settings, force_write_probe=True)
    assert h1.writable is True

    call_count = {"n": 0}
    real_probe = Path.write_text

    def counting_write(self: Path, data: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if ".suying_wprobe_" in self.name:
            call_count["n"] += 1
        return real_probe(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", counting_write)
    # Cached (should not re-touch)
    check_paths(settings, force_write_probe=False)
    assert call_count["n"] == 0
    # Force refreshes
    check_paths(settings, force_write_probe=True)
    assert call_count["n"] >= 1


def test_is_readonly_fs_error_matches_erofs_and_message() -> None:
    assert is_readonly_fs_error(OSError(errno.EROFS, "Read-only file system"))
    assert is_readonly_fs_error(
        RuntimeError("OSError: [Errno 30] Read-only file system: '/Volumes/QR/render'")
    )
    assert not is_readonly_fs_error(OSError(errno.ENOENT, "No such file"))


def test_chmod_unwritable_directory(tmp_path: Path) -> None:
    """On systems where chmod removes write bits, probe must fail closed."""
    clear_write_probe_cache()
    settings = _settings(tmp_path)
    render = Path(settings.paths.render_root)
    try:
        os.chmod(render, 0o555)
        if os.access(render, os.W_OK):
            pytest.skip("platform ignores chmod write bits (e.g. root/docker)")
        health = check_paths(settings, force_write_probe=True)
        assert health.ok is False
        assert health.writable is False
    finally:
        os.chmod(render, 0o755)
