#!/usr/bin/env python3
"""Explicit config import from seed → customer DB (never silent).

Forbidden without flags:
  - customers.profile_json whole-table overwrite
  - chrome-profiles / reach account bindings

Allowed with --apply:
  - keyword_pack_path reload from seed file copy into customer pack path
  - VIDEO_LOCK merge into profile_json.video_lock only (opt-in --video-lock)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="速影配置显式导入（禁止静默覆盖）")
    ap.add_argument("--customer", required=True)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--apply", action="store_true", help="实际写入；默认 dry-run")
    ap.add_argument("--keyword-pack", action="store_true", help="导入词池文件")
    ap.add_argument(
        "--video-lock",
        action="store_true",
        help="仅合并 profile_json.video_lock（不整表替换 profile）",
    )
    args = ap.parse_args()

    seed = Path(args.seed).expanduser()
    if not seed.is_dir():
        print(f"seed 不存在: {seed}", file=sys.stderr)
        return 2
    if not args.keyword_pack and not args.video_lock:
        print("请指定 --keyword-pack 和/或 --video-lock", file=sys.stderr)
        return 2

    from engine.catalog.db import get_session, init_db
    from engine.catalog.customer_scope import resolve_customer
    from engine.config.settings import load_settings

    settings = load_settings()
    init_db(settings)
    session = get_session()
    actions: list[dict] = []
    try:
        customer = resolve_customer(session, args.customer)
        if customer is None:
            print(json.dumps({"ok": False, "error": "customer_not_found"}, ensure_ascii=False))
            return 3

        if args.keyword_pack:
            src = None
            for cand in (seed / "keyword_pack.json", seed / "词池.json", seed / "keywords.json"):
                if cand.is_file():
                    src = cand
                    break
            if src is None:
                actions.append({"op": "keyword_pack", "error": "seed_missing"})
            else:
                dest = Path(customer.keyword_pack_path) if customer.keyword_pack_path else None
                if dest is None:
                    dest = Path(settings.paths.data_root) / "keyword_packs" / f"{customer.id}.json"
                actions.append(
                    {
                        "op": "keyword_pack",
                        "src": str(src),
                        "dest": str(dest),
                        "apply": bool(args.apply),
                    }
                )
                if args.apply:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    customer.keyword_pack_path = str(dest)

        if args.video_lock:
            lock_path = seed / "brand" / "VIDEO_LOCK.json"
            if not lock_path.is_file():
                lock_path = seed / "VIDEO_LOCK.json"
            if not lock_path.is_file():
                actions.append({"op": "video_lock", "error": "seed_missing"})
            else:
                lock = json.loads(lock_path.read_text(encoding="utf-8"))
                profile = dict(customer.profile_json) if isinstance(customer.profile_json, dict) else {}
                actions.append(
                    {
                        "op": "video_lock",
                        "src": str(lock_path),
                        "apply": bool(args.apply),
                        "note": "only profile_json.video_lock; chrome bindings untouched",
                    }
                )
                if args.apply:
                    profile["video_lock"] = lock
                    customer.profile_json = profile

        if args.apply:
            session.commit()
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": not args.apply,
                    "actions": actions,
                    "forbidden": [
                        "whole profile_json replace",
                        "chrome-profiles",
                        "reach account bindings",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
