#!/usr/bin/env python3
"""PL-03/04 data hygiene: uncertain missing archive + ready-pool dead-row heal.

Default is dry-run. Apply with ``--apply`` after reviewing counts.

Examples::

    python3 scripts/pl_p0_data_hygiene.py
    python3 scripts/pl_p0_data_hygiene.py --apply --limit 500
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _rates(session) -> dict:
    from pathlib import Path as P

    from sqlalchemy import select

    from engine.catalog.db import Job, RenderOutput, ReviewItem

    uncertain = list(
        session.scalars(
            select(RenderOutput)
            .join(
                ReviewItem,
                (ReviewItem.render_output_id == RenderOutput.id)
                & (ReviewItem.is_current.is_(True)),
            )
            .where(ReviewItem.status == "uncertain")
        ).all()
    )
    unc_ok = sum(1 for o in uncertain if o.output_path and P(o.output_path).is_file())
    ready = list(
        session.scalars(select(RenderOutput).where(RenderOutput.state == "ready")).all()
    )
    ready_ok = sum(1 for o in ready if o.output_path and P(o.output_path).is_file())
    ready_pub = sum(
        1
        for o in ready
        if o.pack_status == "ready"
        and o.output_path
        and P(o.output_path).is_file()
    )
    ready_pending_miss = sum(
        1
        for o in ready
        if str(o.pack_status or "") == "pending"
        and not (o.output_path and P(o.output_path).is_file())
    )
    return {
        "uncertain_current": len(uncertain),
        "uncertain_media_ok": unc_ok,
        "uncertain_reviewable_rate": round(100 * unc_ok / max(1, len(uncertain)), 2),
        "ready_total": len(ready),
        "ready_media_ok": ready_ok,
        "ready_media_ok_rate": round(100 * ready_ok / max(1, len(ready)), 2),
        "ready_publishable": ready_pub,
        "ready_publishable_rate": round(100 * ready_pub / max(1, len(ready)), 2),
        "ready_pending_missing": ready_pending_miss,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="速影 PL-03/04 数据卫生")
    parser.add_argument("--apply", action="store_true", help="执行写入（默认 dry-run）")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--skip-uncertain",
        action="store_true",
        help="跳过 PL-03 uncertain 缺文件归档",
    )
    parser.add_argument(
        "--skip-ready",
        action="store_true",
        help="跳过 PL-04 ready 池卫生",
    )
    parser.add_argument(
        "--pack-status",
        default="all",
        help="ready 池 pack_status 过滤：all|pending|ready|failed",
    )
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = parser.parse_args()

    from engine.api.scope import active_scope
    from engine.catalog.db import get_session, init_db
    from engine.catalog.review_auto import (
        batch_archive_missing_uncertain,
        batch_hygiene_ready_pool,
    )
    from engine.config.settings import load_settings

    init_db()
    settings = load_settings()
    session = get_session()
    dry_run = not args.apply
    try:
        _, customer, _ = active_scope(session, settings)
        before = _rates(session)
        report: dict = {
            "dry_run": dry_run,
            "customer_id": customer.id,
            "customer": customer.name,
            "before": before,
        }
        if not args.skip_uncertain:
            report["pl03_uncertain_missing"] = batch_archive_missing_uncertain(
                session,
                customer,
                limit=args.limit,
                dry_run=dry_run,
            )
        if not args.skip_ready:
            pack = str(args.pack_status or "pending").strip().lower()
            pack_filter = None if pack in {"all", "*"} else pack
            report["pl04_ready_pool"] = batch_hygiene_ready_pool(
                session,
                customer,
                limit=args.limit,
                dry_run=dry_run,
                heal_pending=True,
                archive_missing=True,
                pack_status=pack_filter,
            )
        # Re-read rates after apply (or same as before on dry-run).
        after = _rates(session) if not dry_run else before
        report["after"] = after
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            mode = "APPLY" if args.apply else "DRY-RUN"
            print(f"==> PL-03/04 data hygiene [{mode}] customer={customer.name}")
            print("before:", json.dumps(before, ensure_ascii=False))
            if "pl03_uncertain_missing" in report:
                p = report["pl03_uncertain_missing"]
                print(
                    "PL-03:",
                    f"would={p.get('would_archive_missing')} "
                    f"archived={p.get('archived_missing')} "
                    f"offline={p.get('volume_offline')} "
                    f"present_skip={p.get('present_skipped')}",
                )
            if "pl04_ready_pool" in report:
                p = report["pl04_ready_pool"]
                print(
                    "PL-04:",
                    f"would_archive={p.get('would_archive_missing')} "
                    f"archived={p.get('archived_missing')} "
                    f"would_heal={p.get('would_heal_pack')} "
                    f"healed={p.get('healed_pack')} "
                    f"offline={p.get('volume_offline')}",
                )
            print("after:", json.dumps(after, ensure_ascii=False))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
