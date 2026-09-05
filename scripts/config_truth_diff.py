#!/usr/bin/env python3
"""Read-only config truth: customer runtime facts vs seed pack expectations.

Does not write DB / chrome / profile_json. Exit 0 always when runnable;
prints JSON with diffs. Non-zero only on usage / I/O errors.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_video_lock(seed: Path) -> dict[str, Any] | None:
    for candidate in (
        seed / "brand" / "VIDEO_LOCK.json",
        seed / "VIDEO_LOCK.json",
    ):
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"_error": f"unreadable:{candidate}"}
    return None


def _keyword_revision(path: Path | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(raw, dict):
        rev = raw.get("revision") or raw.get("version") or raw.get("pack_revision")
        if rev is not None:
            return str(rev)
        titles = raw.get("titles") or raw.get("keywords")
        if isinstance(titles, list):
            return f"count:{len(titles)}"
    return "present"


def main() -> int:
    ap = argparse.ArgumentParser(description="速影配置真相 diff（只读）")
    ap.add_argument("--customer", required=True, help="客户显示名（与 active_customer 对齐）")
    ap.add_argument(
        "--seed",
        required=True,
        help="期望种子目录，如 configs/customers/北京始峰伟业",
    )
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args()

    seed = Path(args.seed).expanduser()
    if not seed.is_dir():
        print(f"seed 不存在: {seed}", file=sys.stderr)
        return 2

    from engine.catalog.db import get_session, init_db
    from engine.catalog.customer_scope import resolve_customer
    from engine.config.settings import load_settings
    from engine.template.rule_store import get_active_rule

    settings = load_settings()
    init_db(settings)
    session = get_session()
    try:
        customer = resolve_customer(session, args.customer)
        if customer is None:
            print(json.dumps({"ok": False, "error": "customer_not_found"}, ensure_ascii=False))
            return 3

        active_rule = None
        try:
            row = get_active_rule(
                session, customer_id=int(customer.id), content_category="default"
            )
            if row is not None:
                active_rule = {
                    "id": getattr(row, "id", None),
                    "name": getattr(row, "name", None),
                    "revision": getattr(row, "revision", None),
                    "status": getattr(row, "status", None),
                }
        except Exception as exc:  # noqa: BLE001
            active_rule = {"_error": str(exc)}

        live_lock = None
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        if isinstance(profile.get("video_lock"), dict):
            live_lock = profile.get("video_lock")
        seed_lock = _load_video_lock(seed)

        pack_path = Path(customer.keyword_pack_path) if customer.keyword_pack_path else None
        seed_pack = None
        for cand in (seed / "keyword_pack.json", seed / "词池.json", seed / "keywords.json"):
            if cand.is_file():
                seed_pack = cand
                break

        facts = {
            "customer_id": int(customer.id),
            "customer_name": customer.name,
            "active_customer_setting": settings.active_customer,
            "keyword_pack_path": customer.keyword_pack_path,
            "keyword_revision": _keyword_revision(pack_path),
            "seed_keyword_revision": _keyword_revision(seed_pack),
            "active_rule": active_rule,
            "video_lock_live": live_lock,
            "video_lock_seed": seed_lock,
            "max_render_concurrency": getattr(settings, "max_render_concurrency", None),
            "data_root": str(settings.paths.data_root),
            "note": "规则/词池在客户库，不会随 App 一体包自动等于开发机 DB",
        }

        diffs: list[dict[str, Any]] = []
        if settings.active_customer != customer.name and settings.active_customer != args.customer:
            diffs.append(
                {
                    "field": "active_customer",
                    "live": settings.active_customer,
                    "expected": args.customer,
                }
            )
        if facts["keyword_revision"] and facts["seed_keyword_revision"]:
            if facts["keyword_revision"] != facts["seed_keyword_revision"]:
                diffs.append(
                    {
                        "field": "keyword_revision",
                        "live": facts["keyword_revision"],
                        "expected": facts["seed_keyword_revision"],
                    }
                )
        if seed_lock and live_lock:
            for key in ("provider", "tts_provider", "lock_version"):
                if key in seed_lock and seed_lock.get(key) != (live_lock or {}).get(key):
                    diffs.append(
                        {
                            "field": f"video_lock.{key}",
                            "live": (live_lock or {}).get(key),
                            "expected": seed_lock.get(key),
                        }
                    )
        elif seed_lock and not live_lock:
            diffs.append({"field": "video_lock", "live": None, "expected": "present_in_seed"})

        out = {"ok": True, "facts": facts, "diffs": diffs, "drift": bool(diffs)}
        text = json.dumps(out, ensure_ascii=False, indent=2)
        print(text)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
