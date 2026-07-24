from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Job, JobEvent, RenderOutput


def export_job_events_csv(session: Session, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events = session.scalars(select(JobEvent).order_by(JobEvent.id)).all()
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "job_id", "level", "message", "created_at", "payload"])
        for e in events:
            writer.writerow([e.id, e.job_id, e.level, e.message, e.created_at.isoformat(), e.payload_json])
    return output_path


def export_renders_csv(session: Session, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = session.scalars(select(RenderOutput).order_by(RenderOutput.id)).all()
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "job_id", "state", "seed", "output_path", "sidecar_path", "created_at", "qc"])
        for r in rows:
            writer.writerow(
                [r.id, r.job_id, r.state, r.seed, r.output_path, r.sidecar_path, r.created_at.isoformat(), r.qc_json]
            )
    return output_path
