from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Job, JobEvent, RenderOutput


def export_job_events_csv(session: Session, output_path: Path, *, customer_id: int | None = None) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stmt = select(JobEvent).order_by(JobEvent.id)
    if customer_id is not None:
        stmt = stmt.join(Job, JobEvent.job_id == Job.id).where(Job.customer_id == customer_id)
    events = session.scalars(stmt).all()
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["level", "message", "created_at"])
        for e in events:
            writer.writerow([e.level, e.message, e.created_at.isoformat()])
    return output_path


def export_renders_csv(session: Session, output_path: Path, *, customer_id: int | None = None) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stmt = select(RenderOutput).order_by(RenderOutput.id)
    if customer_id is not None:
        stmt = stmt.join(Job, RenderOutput.job_id == Job.id).where(Job.customer_id == customer_id)
    rows = session.scalars(stmt).all()
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["display_label", "state", "output_path", "created_at"])
        for r in rows:
            writer.writerow(
                [
                    f"#{int(r.display_no):03d}" if r.display_no is not None else "",
                    r.state,
                    r.output_path,
                    r.created_at.isoformat(),
                ]
            )
    return output_path
