#!/usr/bin/env python3
"""Cancel stalled montage jobs (paused/circuit_open, zero produced)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from engine.catalog.db import Job, get_session, log_event


def main() -> int:
    ap = argparse.ArgumentParser(description="Cancel zero-output stalled jobs")
    ap.add_argument("--apply", action="store_true", help="Write cancelled status (default dry-run)")
    ap.add_argument("--job-id", type=int, action="append", default=[], help="Only these job ids")
    ap.add_argument("--customer-id", type=int, default=0, help="Optional customer filter")
    args = ap.parse_args()

    session = get_session()
    try:
        stmt = select(Job).where(
            Job.status.in_(("paused", "circuit_open")),
            Job.produced_count == 0,
        )
        if args.job_id:
            stmt = stmt.where(Job.id.in_(args.job_id))
        if args.customer_id:
            stmt = stmt.where(Job.customer_id == int(args.customer_id))
        rows = list(session.scalars(stmt.order_by(Job.id)).all())
        preview = [
            {
                "id": j.id,
                "status": j.status,
                "customer_id": j.customer_id,
                "theme": j.theme,
                "fails": j.consecutive_failures,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in rows
        ]
        print(json.dumps({"count": len(preview), "jobs": preview}, ensure_ascii=False, indent=2))
        if not args.apply:
            print("\nDry-run only. Re-run with --apply to cancel.", file=sys.stderr)
            return 0 if preview else 0
        now = datetime.now(timezone.utc).isoformat()
        for job in rows:
            prev = str(job.status)
            job.status = "cancelled"
            snap = dict(job.config_snapshot_json or {})
            snap["_pipeline_phase"] = "cancelled_cleanup"
            snap["cancelled_at"] = now
            job.config_snapshot_json = snap
            log_event(
                session,
                job.id,
                "info",
                "运维清理：零产出僵死任务已取消",
                {"previous_status": prev, "produced_count": 0},
            )
        session.commit()
        print(f"\nCancelled {len(rows)} job(s).", file=sys.stderr)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
