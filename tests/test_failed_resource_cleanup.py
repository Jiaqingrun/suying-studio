"""Unit tests: rejected media purge + 72h failed-resource cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from engine.catalog.db import Base, Customer, Job, RenderOutput
from engine.catalog.output_purge import purge_output_media
from engine.ops.failed_resource_cleanup import (
    purge_stale_failed_outputs,
    sweep_failed_tree,
)


def _session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 't.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_purge_output_media_removes_artifacts(tmp_path: Path) -> None:
    session = _session(tmp_path)
    customer = Customer(name="c1", output_root=str(tmp_path / "out"))
    session.add(customer)
    session.commit()
    job = Job(
        customer_id=customer.id,
        status="completed",
        theme="t",
        category="c",
        target_count=1,
        template_name="default",
    )
    session.add(job)
    session.commit()
    day = tmp_path / "out" / "ready" / "2026-08-01"
    day.mkdir(parents=True)
    mp4 = day / "montage_1_1.mp4"
    side = day / "montage_1_1.json"
    pack = day / "montage_1_1.publish_pack"
    pack.mkdir()
    (pack / "caption.txt").write_text("x", encoding="utf-8")
    mp4.write_bytes(b"video")
    side.write_text("{}", encoding="utf-8")
    out = RenderOutput(
        job_id=job.id,
        output_path=str(mp4),
        state="ready",
        seed=1,
        sidecar_path=str(side),
        pack_dir=str(pack),
        qc_json={},
    )
    session.add(out)
    session.commit()

    result = purge_output_media(session, out, reason="rejected", actor="test")
    session.commit()
    assert result["ok"] is True
    assert result["removed"] >= 2
    assert not mp4.exists()
    assert not side.exists()
    assert not pack.exists()
    assert out.state == "failed"
    assert (out.qc_json or {}).get("media_purged_at")


def test_stale_failed_outputs_and_orphan_tree(tmp_path: Path) -> None:
    session = _session(tmp_path)
    out_root = tmp_path / "out"
    customer = Customer(name="c2", output_root=str(out_root))
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
    old_dir = out_root / "failed" / "2020-01-01"
    old_dir.mkdir(parents=True)
    old_mp4 = old_dir / "old.mp4"
    old_mp4.write_bytes(b"old")
    # make mtime old
    import os

    old_ts = (datetime.now(timezone.utc) - timedelta(hours=80)).timestamp()
    os.utime(old_mp4, (old_ts, old_ts))

    out = RenderOutput(
        job_id=job.id,
        output_path=str(old_mp4),
        state="failed",
        seed=2,
        qc_json={},
        created_at=datetime.now(timezone.utc) - timedelta(hours=80),
    )
    session.add(out)
    session.commit()

    purged = purge_stale_failed_outputs(
        session, customer_id=customer.id, older_than_hours=72, actor="test"
    )
    assert purged["count"] == 1
    assert not old_mp4.exists()

    orphan_dir = out_root / "failed" / "2020-01-02"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    orphan = orphan_dir / "orphan.mp4"
    orphan.write_bytes(b"z")
    os.utime(orphan, (old_ts, old_ts))
    sweep = sweep_failed_tree(
        session,
        customer_id=customer.id,
        output_root=out_root,
        older_than_hours=72,
    )
    assert sweep["removed"] >= 1
    assert not orphan.exists()
