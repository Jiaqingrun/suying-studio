"""VIDEO_LOCK writes must never land inside a signed App runtime tree."""

from __future__ import annotations

from pathlib import Path

from engine.pack.video_lock import (
    is_inside_packaged_app_runtime,
    lock_paths_for_customer,
    writable_lock_paths_for_customer,
)


def test_detects_packaged_app_runtime_path(tmp_path: Path) -> None:
    bundled = (
        tmp_path
        / "Applications"
        / "速影 Studio.app"
        / "Contents"
        / "Resources"
        / "runtime"
        / "studio"
        / "configs"
        / "customers"
        / "demo"
        / "brand"
        / "VIDEO_LOCK.json"
    )
    bundled.parent.mkdir(parents=True)
    bundled.write_text("{}\n", encoding="utf-8")
    assert is_inside_packaged_app_runtime(bundled)
    outside = tmp_path / "Suying" / "customer" / "05-品牌" / "VIDEO_LOCK.json"
    outside.parent.mkdir(parents=True)
    outside.write_text("{}\n", encoding="utf-8")
    assert not is_inside_packaged_app_runtime(outside)


def test_writable_paths_skip_bundle_even_when_repo_root_is_bundled(
    tmp_path: Path, monkeypatch
) -> None:
    studio = (
        tmp_path
        / "速影 Studio.app"
        / "Contents"
        / "Resources"
        / "runtime"
        / "studio"
    )
    studio.mkdir(parents=True)
    out_root = tmp_path / "customer" / "02-成片"
    out_root.mkdir(parents=True)
    brand = tmp_path / "customer" / "05-品牌" / "VIDEO_LOCK.json"

    import engine.pack.video_lock as video_lock

    monkeypatch.setattr(video_lock, "_REPO_ROOT", studio)
    all_paths = lock_paths_for_customer("演示客户", output_root=out_root)
    assert any(is_inside_packaged_app_runtime(p) for p in all_paths)

    writable = writable_lock_paths_for_customer("演示客户", output_root=out_root)
    assert writable
    assert all(not is_inside_packaged_app_runtime(p) for p in writable)
    assert brand in writable or brand.as_posix() in {p.as_posix() for p in writable}
