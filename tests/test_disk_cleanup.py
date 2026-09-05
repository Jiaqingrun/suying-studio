"""Unit tests: DISK_CLEANUP_LOCK allowlist + facade tiers."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from engine.catalog.db import Base, Customer, Job, RenderOutput
from engine.ops.disk_cleanup import (
    FORBIDDEN_TIERS,
    path_is_under_allowed_root,
    report,
    run,
)
from engine.ops.maintenance import clean_cache


def _session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 't.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_path_allowlist_blocks_escape_and_symlink(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    temp = cache / "temp"
    temp.mkdir(parents=True)
    good = temp / "a.bin"
    good.write_bytes(b"ok")
    outside = tmp_path / "library_video.mp4"
    outside.write_bytes(b"secret")
    roots = [temp.resolve()]
    assert path_is_under_allowed_root(good, roots) is True
    assert path_is_under_allowed_root(outside, roots) is False

    link = temp / "escape_link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not available")
    assert path_is_under_allowed_root(link, roots) is False


def test_clean_cache_only_allowlisted_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    render = tmp_path / "render"
    out = tmp_path / "out"
    lib = tmp_path / "library"
    for p in (cache / "temp", cache / "frames", cache / "proxies", cache / "library", render, lib):
        p.mkdir(parents=True)

    old_temp = cache / "temp" / "old.bin"
    old_temp.write_bytes(b"temp")
    os.utime(old_temp, (time.time() - 100_000, time.time() - 100_000))

    lib_file = cache / "library" / "keep.bin"
    lib_file.write_bytes(b"library")
    os.utime(lib_file, (time.time() - 100_000, time.time() - 100_000))

    ready = out / "ready" / "2026-01-01"
    ready.mkdir(parents=True)
    ready_mp4 = ready / "out.mp4"
    ready_mp4.write_bytes(b"ready")
    os.utime(ready_mp4, (time.time() - 100_000, time.time() - 100_000))

    # frames old
    fr = cache / "frames" / "f.jpg"
    fr.write_bytes(b"frame")
    os.utime(fr, (time.time() - 20 * 86400, time.time() - 20 * 86400))

    result = clean_cache(
        settings=None,
        older_than_hours=1,
        targets=["temp", "render"],
        cache_root=cache,
        render_root=render,
    )
    assert result["removed_files"] >= 1
    assert not old_temp.exists()
    assert lib_file.exists(), "cache/library must never be swept by work_cache"

    rebuild = clean_cache(
        settings=None,
        older_than_days=14,
        targets=["frames", "proxies"],
        cache_root=cache,
        render_root=render,
    )
    assert rebuild["removed_files"] >= 1
    assert not fr.exists()
    assert lib_file.exists()
    assert ready_mp4.exists()

    with pytest.raises(ValueError, match="禁止目标"):
        clean_cache(
            targets=["library"],
            cache_root=cache,
            render_root=render,
        )


def test_run_forbidden_tiers_and_failed_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path)
    cache = tmp_path / "cache"
    render = tmp_path / "render"
    out = tmp_path / "out"
    for p in (cache / "temp", render, out / "failed" / "2026-01-01", out / "ready"):
        p.mkdir(parents=True)

    customer = Customer(name="c1", output_root=str(out))
    session.add(customer)
    session.commit()
    job = Job(
        customer_id=customer.id,
        status="failed",
        theme="t",
        category="c",
        target_count=1,
        template_name="default",
    )
    session.add(job)
    session.commit()

    fail_mp4 = out / "failed" / "2026-01-01" / "f.mp4"
    fail_mp4.write_bytes(b"failed-video")
    os.utime(fail_mp4, (time.time() - 200 * 3600, time.time() - 200 * 3600))
    out_row = RenderOutput(
        job_id=job.id,
        output_path=str(fail_mp4),
        state="failed",
        seed=1,
        qc_json={},
    )
    session.add(out_row)
    session.commit()

    ready_file = out / "ready" / "keep.mp4"
    ready_file.write_bytes(b"ready-keep")
    db_file = tmp_path / "data" / "montage.db"
    db_file.parent.mkdir(parents=True)
    db_file.write_bytes(b"db")

    with pytest.raises(ValueError, match="禁止清理档位"):
        run(
            session,
            customer_id=customer.id,
            cache_root=cache,
            render_root=render,
            output_root=out,
            tiers=["library"],
            confirm=True,
        )
    assert "library" in FORBIDDEN_TIERS
    assert "ready" in FORBIDDEN_TIERS

    with pytest.raises(ValueError, match="确认"):
        run(
            session,
            customer_id=customer.id,
            cache_root=cache,
            render_root=render,
            output_root=out,
            tiers=["failed"],
            confirm=False,
        )

    result = run(
        session,
        customer_id=customer.id,
        cache_root=cache,
        render_root=render,
        output_root=out,
        tiers=["failed"],
        confirm=True,
        actor="test",
    )
    assert result["ok"] is True
    assert not fail_mp4.exists()
    assert ready_file.exists()
    assert db_file.exists()


def test_report_structure(tmp_path: Path) -> None:
    session = _session(tmp_path)
    cache = tmp_path / "cache"
    render = tmp_path / "render"
    out = tmp_path / "out"
    (cache / "temp").mkdir(parents=True)
    (render).mkdir(parents=True)
    customer = Customer(name="c-report", output_root=str(out))
    session.add(customer)
    session.commit()
    rep = report(
        session,
        customer_id=customer.id,
        cache_root=cache,
        render_root=render,
        output_root=out,
    )
    assert rep["ok"] is True
    assert "temp" in rep["categories"]
    assert "frames" in rep["categories"]
    assert "policy" in rep
    assert "published_eligible" in rep
