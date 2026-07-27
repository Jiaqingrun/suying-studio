#!/usr/bin/env python3
"""
Migrate “北京始峰伟业” media sources to only “手机相册备份/徐玲飞”.

What it does (apply mode):
1) Backup ~/.qr/suying-sync.json
2) Rewrite config:
   - v3 media_sources: keep only remote_root=手机相册备份/徐玲飞
   - legacy customers[].aliases: keep only the xulingfei alias (optional, for clarity)
   - customers[].push_local: clear (avoid cross-customer pollution through pushes)
3) Move local retired source folders under:
   ~/.../速影客户/北京始峰伟业/01-片库/*  (except 徐玲飞)
   into a timestamped quarantine folder so watcher won't re-ingest them.
4) Archive DB assets for retired folders:
   set Asset.status="archived" for ready assets whose Asset.source_path under retired folders.

Safety:
- Never delete files; only rename/move to quarantine.
- Best-effort on archiving: failure won't block config rewrite.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def now_ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def load_config(cfg_path: Path) -> dict:
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def save_config(cfg_path: Path, cfg: dict) -> None:
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually rewrite config + move dirs + archive DB")
    ap.add_argument("--customer", default="北京始峰伟业")
    ap.add_argument("--remote-base", default="手机相册备份")
    ap.add_argument("--remote-person", default="徐玲飞")
    ap.add_argument("--config-path", default=str(Path.home() / ".qr" / "suying-sync.json"))
    ap.add_argument("--archive-dir-name", default="__retired__")
    args = ap.parse_args()

    cfg_path = Path(args.config_path)
    if not cfg_path.exists():
        raise SystemExit(f"missing config: {cfg_path}")

    cfg = load_config(cfg_path)
    local_root = Path(cfg.get("local_root") or "").expanduser()
    if not local_root.exists():
        print(f"[warn] local_root not exists in config: {local_root}", file=sys.stderr)

    customer_name = str(args.customer).strip()
    remote_base = str(args.remote_base).strip().removeprefix("/public/").strip("/")
    remote_person = str(args.remote_person).strip()

    remote_root_rel = f"{remote_base}/{remote_person}"
    local_target_rel = f"速影客户/{customer_name}/01-片库/{remote_person}"

    if not args.apply:
        print("[dry-run] would migrate:")
        print(f"  config: {cfg_path}")
        print(f"  keep media_sources: {remote_root_rel} -> {local_target_rel}")
        return 0

    ts = now_ts()
    if cfg_path.exists():
        bak = cfg_path.with_suffix(cfg_path.suffix + f".bak.{ts}")
        shutil.copy2(cfg_path, bak)
        print(f"backed up config: {bak}")

    # Stop zspace sync service (best-effort).
    try:
        uid = os.getuid()
        plist = Path.home() / "Library" / "LaunchAgents" / "com.qr.zspace-team-sync.plist"
        if plist.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], check=False, capture_output=True, text=True)
    except Exception:
        pass

    # Rewrite v3 media_sources.
    cfg["version"] = max(int(cfg.get("version") or 0), 3)
    cfg["media_sources"] = [
        {
            "customer_key": customer_name,
            "display_name": customer_name,
            "remote_root": remote_root_rel,
            "local_target": local_target_rel,
            "pull_only": True,
        }
    ]

    # Clarify legacy mappings (optional but keeps scripts deterministic).
    customers = cfg.get("customers") or []
    for c in customers:
        if str(c.get("name") or "").strip() != customer_name:
            continue
        c["aliases"] = [
            {
                "remote": remote_root_rel,
                "local": local_target_rel,
            }
        ]
        c["push_local"] = []
    cfg["customers"] = customers

    save_config(cfg_path, cfg)
    print("rewrote config media_sources + legacy aliases + cleared push_local")

    # Move retired folders.
    local_lib_root = local_root / "速影客户" / customer_name / "01-片库"
    retired_root = local_lib_root / args.archive_dir_name / ts

    moved = 0
    retired_src_dirs: list[Path] = []
    if local_lib_root.exists() and local_lib_root.is_dir():
        for child in local_lib_root.iterdir():
            if not child.is_dir():
                continue
            if child.name == remote_person:
                continue
            src = child
            dst = retired_root / child.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            print(f"moving retired source dir: {src} -> {dst}")
            shutil.move(str(src), str(dst))
            moved += 1
            # Asset.source_path stores the old absolute path; archive using the original src prefix.
            retired_src_dirs.append(src)
    else:
        print(f"[warn] local library root missing: {local_lib_root}", file=sys.stderr)

    # Archive DB assets for retired dirs.
    archived = 0
    if retired_src_dirs:
        try:
            from engine.catalog.db import Asset, Customer, get_session
            from engine.config.settings import load_settings
            from sqlalchemy import select

            # load_settings ensures engine points at correct DB; migration uses authority DB from current settings.
            _ = load_settings()
            session = get_session()
            try:
                cust = session.scalar(select(Customer).where(Customer.name == customer_name))
                if cust:
                    for rd in retired_src_dirs:
                        prefix = str(rd)
                        stmt = (
                            select(Asset)
                            .where(Asset.customer_id == cust.id)
                            .where(Asset.status == "ready")
                            .where(Asset.source_path.like(f"{prefix}%"))
                        )
                        for a in session.scalars(stmt).all():
                            a.status = "archived"
                            archived += 1
                    session.commit()
            finally:
                session.close()
        except Exception as e:
            print(f"[warn] DB archiving failed: {type(e).__name__}: {e}", file=sys.stderr)

    # Restart sync service (best-effort).
    try:
        uid = os.getuid()
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/com.qr.zspace-team-sync"], check=False, capture_output=True, text=True)
    except Exception:
        pass

    print(f"migration done. moved_dirs={moved}, archived_assets={archived}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

