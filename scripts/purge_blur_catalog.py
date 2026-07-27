#!/usr/bin/env python3
"""Purge blurry / out-of-focus cliplets & assets (QUALITY_LOCK).

Rescores → marks rejected_blur → clears embeddings so they never vectorize.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8000)
    ap.add_argument("--no-rescore", action="store_true")
    ap.add_argument("--clips-only", action="store_true", help="do not score whole assets")
    ap.add_argument("--via-api", action="store_true", help="call running engine HTTP API")
    args = ap.parse_args()

    if args.via_api:
        import urllib.request

        url = (
            "http://127.0.0.1:8766/cliplets/quality/purge-blur"
            f"?limit={args.limit}&rescore={str(not args.no_rescore).lower()}"
            f"&purge_assets={str(not args.clips_only).lower()}"
        )
        req = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=3600) as r:
            print(r.read().decode())
        return 0

    from engine.catalog.customer_scope import require_active_customer
    from engine.catalog.db import get_session, init_db
    from engine.config.settings import load_settings
    from engine.ingest.quality import purge_blur_from_catalog

    settings = load_settings()
    init_db(settings)
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        result = purge_blur_from_catalog(
            session,
            customer_id=customer.id,
            limit=args.limit,
            rescore=not args.no_rescore,
            purge_assets=not args.clips_only,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
