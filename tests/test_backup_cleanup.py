from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from engine.catalog.db import Base, Customer, Job, PublicationGroup, RenderOutput
from engine.ops.backup_service import _sqlite_snapshot
from engine.ops.published_cleanup import cleanup, preview


def test_sqlite_backup_is_consistent_and_counts_vectors(tmp_path: Path) -> None:
    source = tmp_path / "montage.db"
    connection = sqlite3.connect(source)
    connection.execute(
        "CREATE TABLE cliplets (id INTEGER PRIMARY KEY, embedding_json JSON)"
    )
    connection.execute("INSERT INTO cliplets(embedding_json) VALUES ('[1,2]'), (NULL)")
    connection.commit()
    connection.close()

    artifact = _sqlite_snapshot(source, tmp_path / "backup" / "montage.db")

    assert artifact["integrity"] == "ok"
    assert artifact["vector_count"] == 1
    assert len(artifact["sha256"]) == 64


def test_published_cleanup_keeps_business_fact_and_writes_tombstone(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    output_root = tmp_path / "outputs"
    archive = output_root / "retired" / "published" / "2026-07-01" / "output-1"
    archive.mkdir(parents=True)
    video = archive / "output.mp4"
    video.write_bytes(b"published-video")

    with Session(engine) as session:
        customer = Customer(name="清理客户", output_root=str(output_root), profile_json={})
        session.add(customer)
        session.flush()
        job = Job(customer_id=customer.id, status="completed", template_name="default")
        session.add(job)
        session.flush()
        output = RenderOutput(
            job_id=job.id,
            output_path=str(video),
            state="retired_published",
            seed=1,
            display_no=7,
            qc_json={},
        )
        session.add(output)
        session.flush()
        group = PublicationGroup(
            customer_id=customer.id,
            output_id=output.id,
            group_key="published-cleanup-test",
            status="retired_published",
            retired_at=datetime.now(timezone.utc) - timedelta(days=40),
        )
        session.add(group)
        session.commit()

        before = preview(
            session,
            customer_id=customer.id,
            output_root=output_root,
            older_than_days=30,
        )
        assert before["count"] == 1

        result = cleanup(
            session,
            customer_id=customer.id,
            output_root=output_root,
            output_ids=[output.id],
        )
        assert result["count"] == 1
        assert not video.exists()
        session.refresh(group)
        session.refresh(output)
        assert group.status == "retired_published"
        assert output.qc_json["published_video_intentional"] is True
        assert Path(output.qc_json["published_video_trash_path"]).is_file()

