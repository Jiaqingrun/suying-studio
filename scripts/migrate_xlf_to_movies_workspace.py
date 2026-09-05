#!/usr/bin/env python3
"""Cut over 速影 customer Mac: external /Volumes/QR → ~/Movies/速影工作区 (local APFS).

Moves 速影媒体与工作区 cache/render；权威 DB 仍在 ~/Suying/data。
默认 dry-run；--execute 执行 rsync + 改路径 + 应用 settings。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HOME = Path.home()
DST_WORK = HOME / "Movies" / "速影工作区"
SRC_CUSTOMERS = Path("/Volumes/QR/极空间团队文件同步/速影客户")
SRC_WORK = Path("/Volumes/QR/速影工作区")
BOOT_DATA = HOME / "Suying" / "data"
BOOT_SETTINGS = BOOT_DATA / "settings.json"
BOOT_DB = BOOT_DATA / "montage.db"
SYNC_CFG = HOME / ".qr" / "suying-sync.json"
LOG = HOME / "Suying" / "logs" / "migrate-to-movies-workspace.log"

# Paths rewrites (longest first)
REWRITES: list[tuple[str, str]] = [
    (
        "/Volumes/QR/极空间团队文件同步/速影客户",
        str(DST_WORK / "速影客户"),
    ),
    (
        "/Volumes/QR/速影工作区",
        str(DST_WORK),
    ),
]

NEW_LIBRARY = DST_WORK / "速影客户" / "北京始峰伟业" / "01-片库"
NEW_OUTPUT = DST_WORK / "速影客户" / "北京始峰伟业" / "02-成片"
NEW_MUSIC = DST_WORK / "速影客户" / "北京始峰伟业" / "04-音乐"
NEW_CACHE = DST_WORK / "cache"
NEW_RENDER = DST_WORK / "render"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, check=check, text=True, capture_output=False)


def rewrite_text(value: str) -> str:
    out = value
    for old, new in REWRITES:
        out = out.replace(old, new)
    return out


def rsync_tree(src: Path, dst: Path, *, dry_run: bool) -> None:
    if not src.exists():
        log(f"skip missing src: {src}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Use portable flags: macOS ships older openrsync/rsync without --info=progress2.
    cmd = [
        "rsync",
        "-a",
        "--human-readable",
        "--progress",
        "--exclude",
        ".DS_Store",
        "--exclude",
        ".DocumentRevisions-V100",
        "--exclude",
        ".TemporaryItems",
        "--exclude",
        ".Trashes",
        "--exclude",
        ".fseventsd",
        "--exclude",
        ".Spotlight-V100",
        f"{src}/",
        f"{dst}/",
    ]
    if dry_run:
        cmd.insert(1, "-n")
        log(f"[dry-run] rsync {src} -> {dst}")
    else:
        log(f"rsync {src} -> {dst}")
    run(cmd)


def rewrite_db(path: Path, *, dry_run: bool) -> None:
    if not path.is_file():
        log(f"db missing: {path}")
        return
    tables = [
        ("customers", "library_root"),
        ("customers", "output_root"),
        ("customers", "keyword_pack_path"),
        ("assets", "source_path"),
        ("assets", "storage_path"),
        ("assets", "proxy_path"),
        ("render_outputs", "output_path"),
        ("render_outputs", "sidecar_path"),
        ("cliplets", "path"),
        ("cliplets", "proxy_path"),
        ("jobs", "output_path"),
    ]
    if dry_run:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        cur = con.cursor()
        for table, col in tables:
            try:
                n = 0
                for old, _new in REWRITES:
                    n += int(
                        cur.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE ?",
                            (f"{old}%",),
                        ).fetchone()[0]
                    )
                log(f"[dry-run] db {table}.{col} matches≈{n}")
            except sqlite3.Error as e:
                log(f"[dry-run] db skip {table}.{col}: {e}")
        con.close()
        return

    con = sqlite3.connect(str(path))
    cur = con.cursor()
    for table, col in tables:
        try:
            total = 0
            for old, new in REWRITES:
                total += cur.execute(
                    f"UPDATE {table} SET {col}=replace({col}, ?, ?) "
                    f"WHERE {col} LIKE ?",
                    (old, new, f"{old}%"),
                ).rowcount
            log(f"db {table}.{col}: {total}")
        except sqlite3.Error as e:
            log(f"db skip {table}.{col}: {e}")
    con.commit()
    con.close()


def apply_settings(*, dry_run: bool) -> None:
    if not BOOT_SETTINGS.is_file():
        raise SystemExit(f"missing settings: {BOOT_SETTINGS}")
    raw: dict[str, Any] = json.loads(BOOT_SETTINGS.read_text(encoding="utf-8"))
    # Text rewrite first for any buried absolute paths
    raw = json.loads(rewrite_text(json.dumps(raw, ensure_ascii=False)))
    paths = raw.setdefault("paths", {})
    paths["library_root"] = str(NEW_LIBRARY)
    paths["output_root"] = str(NEW_OUTPUT)
    paths["music_root"] = str(NEW_MUSIC)
    paths["cache_root"] = str(NEW_CACHE)
    paths["render_root"] = str(NEW_RENDER)
    paths["data_root"] = str(BOOT_DATA)
    paths["external_required"] = False
    raw["workspace_volume_uuid"] = ""
    # keep existing workspace_id identity or clear volume bind
    if dry_run:
        log("[dry-run] would write settings paths:")
        for k in (
            "library_root",
            "output_root",
            "music_root",
            "cache_root",
            "render_root",
            "data_root",
            "external_required",
        ):
            log(f"  {k}={paths.get(k)}")
        log(f"  workspace_volume_uuid={raw.get('workspace_volume_uuid')!r}")
        return

    BOOT_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = BOOT_SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, BOOT_SETTINGS)
    log(f"wrote {BOOT_SETTINGS}")

    # Marker for local workspace (no external volume)
    marker = {
        "workspace_id": str(raw.get("workspace_id") or ""),
        "volume_uuid": None,
        "data_root": str(BOOT_DATA),
        "media_root": str(DST_WORK),
        "cutover": "movies-workspace",
        "cutover_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    mpath = BOOT_DATA / ".suying-workspace.json"
    mpath.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"wrote {mpath}")


def apply_sync_config(*, dry_run: bool) -> None:
    """Keep carrier-only default; media destinations under Movies if enabled later."""
    cfg: dict[str, Any] = {}
    if SYNC_CFG.is_file():
        try:
            cfg = json.loads(SYNC_CFG.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
    cfg["work_root"] = str(DST_WORK)
    cfg["local_root"] = str(HOME / "Suying" / "sync")
    # No longer bind to external QR volume for media mirror
    cfg["volume_uuid"] = ""
    cfg["volume_relpath"] = ""
    # Preserve zspace account; keep sync_mode carrier_only unless already media
    cfg.setdefault("sync_mode", "carrier_only")
    cfg.setdefault("media_sync_enabled", False)
    cfg.setdefault("carrier_mirror", str(HOME / "Suying" / "carrier"))
    # Fix media_sources local_target absolute if present
    for ms in cfg.get("media_sources") or []:
        if not isinstance(ms, dict):
            continue
        # relative local_target stays under former team mirror; make absolute under DST
        lt = str(ms.get("local_target") or "").strip()
        if lt and not lt.startswith("/"):
            ms["local_target"] = str(DST_WORK / lt)
        elif lt.startswith("/Volumes/QR/"):
            ms["local_target"] = rewrite_text(lt)
    text = rewrite_text(json.dumps(cfg, ensure_ascii=False))
    cfg = json.loads(text)
    if dry_run:
        log(f"[dry-run] suying-sync work_root={cfg.get('work_root')} volume_uuid={cfg.get('volume_uuid')!r}")
        return
    SYNC_CFG.parent.mkdir(parents=True, exist_ok=True)
    SYNC_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"wrote {SYNC_CFG}")


def verify() -> None:
    checks = [
        NEW_LIBRARY,
        NEW_OUTPUT,
        NEW_MUSIC,
        NEW_CACHE,
        NEW_RENDER,
        BOOT_SETTINGS,
        BOOT_DB,
    ]
    for p in checks:
        log(f"verify {'OK' if p.exists() else 'MISSING'}: {p}")
    try:
        raw = json.loads(BOOT_SETTINGS.read_text(encoding="utf-8"))
        paths = raw.get("paths") or {}
        log(f"settings.library_root={paths.get('library_root')}")
        log(f"settings.output_root={paths.get('output_root')}")
        log(f"settings.data_root={paths.get('data_root')}")
        log(f"external_required={paths.get('external_required')}")
        log(f"workspace_volume_uuid={raw.get('workspace_volume_uuid')!r}")
    except Exception as e:
        log(f"settings verify error: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="实际执行（默认 dry-run）")
    ap.add_argument("--skip-rsync", action="store_true", help="只改配置/DB（数据已在目标）")
    ap.add_argument("--skip-cache", action="store_true", help="不迁移 68G cache（可再生）")
    args = ap.parse_args()
    dry = not args.execute
    log(f"=== migrate to {DST_WORK} dry_run={dry} ===")

    if not SRC_CUSTOMERS.exists():
        raise SystemExit(f"source customers missing: {SRC_CUSTOMERS}")

    free = shutil.disk_usage(HOME).free
    log(f"free on home volume: {free / (1024**3):.1f} GiB")
    if not dry and free < 180 * (1024**3):
        log("WARNING: free space under 180 GiB; migration may fail")

    if not args.skip_rsync:
        rsync_tree(SRC_CUSTOMERS, DST_WORK / "速影客户", dry_run=dry)
        if not args.skip_cache and (SRC_WORK / "cache").exists():
            rsync_tree(SRC_WORK / "cache", NEW_CACHE, dry_run=dry)
        if (SRC_WORK / "render").exists():
            rsync_tree(SRC_WORK / "render", NEW_RENDER, dry_run=dry)

    # Ensure leaf dirs exist even if skip-cache
    if not dry:
        for d in (NEW_LIBRARY, NEW_OUTPUT, NEW_MUSIC, NEW_CACHE, NEW_RENDER):
            d.mkdir(parents=True, exist_ok=True)

    apply_settings(dry_run=dry)
    apply_sync_config(dry_run=dry)
    rewrite_db(BOOT_DB, dry_run=dry)

    if not dry:
        verify()
        log("DONE. 请在客户机图形界面完全退出并重新打开「速影 Studio」以加载新路径。")
        log("权威库仍在 ~/Suying/data；媒体在 ~/Movies/速影工作区。")
    else:
        log("dry-run complete; re-run with --execute")
    return 0


if __name__ == "__main__":
    sys.exit(main())
