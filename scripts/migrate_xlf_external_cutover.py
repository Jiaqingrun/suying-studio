#!/usr/bin/env python3
"""Cut over remote 速影 to /Volumes/xlf workspace after rsync.

Rewrites settings + SQLite absolute paths from local QR-Volume layout to
/Volumes/xlf, installs media sync scoped to 团队空间/手机相册备份/徐玲飞,
and restarts the embedded engine.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

OLD_PREFIX = "/Users/qr/QR-Volume"
NEW_PREFIX = "/Volumes/xlf"
VOLUME_UUID = "8D6AE214-2A79-311D-8C8B-3375EDA3A796"
WORK = Path(NEW_PREFIX) / "速影工作区"
SYNC = Path(NEW_PREFIX) / "极空间团队文件同步"
DB = WORK / "db" / "montage.db"
SETTINGS = WORK / "db" / "settings.json"
BOOT_SETTINGS = Path.home() / "Suying" / "data" / "settings.json"
SYNC_CFG = Path.home() / ".qr" / "suying-sync.json"
TOOLS = Path.home() / "QR" / "tools"
APP_CANDIDATES = [
    Path.home() / "Applications" / "速影.app",
    Path("/Applications/速影.app"),
]


def rewrite_text(value: str) -> str:
    return value.replace(OLD_PREFIX, NEW_PREFIX)


def rewrite_json_file(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    new = rewrite_text(raw)
    if new != raw:
        path.write_text(new, encoding="utf-8")
        print(f"rewrote json: {path}")
    else:
        print(f"json unchanged: {path}")


def rewrite_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    cur = con.cursor()
    updates = [
        ("customers", "library_root"),
        ("customers", "output_root"),
        ("customers", "keyword_pack_path"),
        ("assets", "source_path"),
        ("assets", "storage_path"),
        ("assets", "proxy_path"),
        ("render_outputs", "output_path"),
        ("render_outputs", "sidecar_path"),
    ]
    for table, col in updates:
        try:
            n = cur.execute(
                f"UPDATE {table} SET {col}=replace({col}, ?, ?) "
                f"WHERE {col} LIKE ?",
                (OLD_PREFIX, NEW_PREFIX, f"{OLD_PREFIX}%"),
            ).rowcount
            print(f"db {table}.{col}: {n}")
        except sqlite3.Error as e:
            print(f"db skip {table}.{col}: {e}")
    con.commit()
    con.close()


def write_sync_config() -> None:
    cfg = {
        "version": 2,
        "local_root": str(SYNC),
        "work_root": str(WORK),
        "volume_uuid": VOLUME_UUID,
        "volume_relpath": "极空间团队文件同步",
        "sync_mode": "media",
        "media_sync_enabled": True,
        "carrier_relpath": "手机相册备份/速影载体",
        "carrier_mirror": str(Path.home() / "Suying" / "carrier"),
        "pull_remote_roots": ["手机相册备份/徐玲飞"],
        "zspace": {
            "username": "18911195331",
            "nas_id": "Z02100B2IJ24X",
            "nas_name": "Z2S-J24X",
        },
        "customers": [
            {
                "name": "北京始峰伟业",
                "layout": "legacy_phone_album",
                "aliases": [
                    {
                        "remote": "手机相册备份/徐玲飞",
                        "local": "速影客户/北京始峰伟业/01-片库/徐玲飞",
                    }
                ],
                "push_local": [],
            }
        ],
    }
    SYNC_CFG.parent.mkdir(parents=True, exist_ok=True)
    SYNC_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SYNC_CFG}")


def find_app() -> Path:
    for p in APP_CANDIDATES:
        if p.exists():
            return p.resolve()
    raise SystemExit("速影.app not found")


def restart_engine(app: Path) -> None:
    py = app / "Contents/Resources/runtime/python/bin/python3"
    studio = app / "Contents/Resources/runtime/studio"
    if not py.exists():
        raise SystemExit(f"missing embedded python: {py}")
    # Stop listeners on 8766
    try:
        out = subprocess.check_output(
            ["lsof", "-t", "-iTCP:8766", "-sTCP:LISTEN"], text=True
        ).strip()
        for pid in out.splitlines():
            subprocess.run(["kill", pid], check=False)
    except subprocess.CalledProcessError:
        pass
    time.sleep(1)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(studio)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    log = Path.home() / "Suying" / "logs" / "engine.cutover.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    # Point bootstrap settings at external work db via rewritten BOOT_SETTINGS
    cmd = [
        str(py),
        "-m",
        "uvicorn",
        "engine.api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8766",
    ]
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== cutover restart {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        proc = subprocess.Popen(
            cmd,
            cwd=str(studio),
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (Path.home() / "Suying" / "engine.pid").write_text(str(proc.pid), encoding="utf-8")
    print(f"engine pid {proc.pid}; log {log}")


def wait_health(timeout: int = 60) -> dict:
    import urllib.request

    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8766/health", timeout=3) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "ok":
                    return data
                last = json.dumps(data, ensure_ascii=False)[:300]
        except Exception as e:
            last = str(e)
        time.sleep(1)
    raise SystemExit(f"health timeout: {last}")


def main() -> int:
    if not WORK.is_dir() or not SYNC.is_dir():
        raise SystemExit(f"missing work/sync under {NEW_PREFIX}")
    if not DB.exists() or not SETTINGS.exists():
        raise SystemExit(f"missing db/settings under {WORK}")

    # Ensure ExFAT volume is the expected one
    info = subprocess.run(
        ["diskutil", "info", "-plist", VOLUME_UUID],
        capture_output=True,
        check=False,
    )
    if info.returncode != 0:
        raise SystemExit(f"volume uuid not mounted: {VOLUME_UUID}")

    rewrite_json_file(SETTINGS)
    BOOT_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SETTINGS, BOOT_SETTINGS)
    print(f"bootstrap settings -> {BOOT_SETTINGS}")

    # Keyword pack may embed absolute paths
    for kp in SYNC.rglob("keyword-pack.json"):
        try:
            rewrite_json_file(kp)
        except OSError as e:
            print(f"skip {kp}: {e}")

    rewrite_db(DB)
    write_sync_config()

    # Refresh sync tools if packaging copies were shipped beside this script
    src_tools = Path(__file__).resolve().parents[1] / "packaging" / "zspace-sync"
    if not src_tools.is_dir():
        # When executed from /tmp on remote, prefer sibling copies then installed tools.
        sibling = Path(__file__).resolve().parent
        if (sibling / "zspace-team-sync.py").exists():
            src_tools = sibling
        else:
            src_tools = TOOLS
    TOOLS.mkdir(parents=True, exist_ok=True)
    for name in ("zspace-team-sync.py", "zspace-team-sync.sh"):
        src = src_tools / name
        if src.exists():
            dst = TOOLS / name
            if src.resolve() == dst.resolve():
                print(f"tools already at {dst}")
                continue
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
            print(f"installed {dst}")

    # Reload LaunchAgent so media sync picks up new config / scripts
    plist = Path.home() / "Library/LaunchAgents/com.qr.zspace-team-sync.plist"
    if plist.exists():
        uid = os.getuid()
        domain = f"gui/{uid}"
        subprocess.run(["launchctl", "bootout", domain, str(plist)], capture_output=True)
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        load = subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist)],
            capture_output=True,
            text=True,
        )
        if load.returncode != 0:
            subprocess.run(["launchctl", "load", "-w", str(plist)], check=False)
        print(f"reloaded {plist}")

    app = find_app()
    restart_engine(app)
    health = wait_health()
    print(
        "health ok:",
        health.get("active_customer"),
        health.get("paths", {}).get("data_root"),
        health.get("paths", {}).get("library_root"),
    )
    ph = health.get("path_health") or {}
    print("path_health:", ph.get("ok"), "lib_mounted", ph.get("library_mounted"))

    # Smoke: sync dry-run should resolve only 徐玲飞 pull root
    sync_py = TOOLS / "zspace-team-sync.py"
    if sync_py.exists():
        dry = subprocess.run(
            [sys.executable, str(sync_py), "--dry-run", "--pull-only"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        print("sync dry-run rc:", dry.returncode)
        if dry.stdout:
            print(dry.stdout[-1200:])
        if dry.stderr:
            print(dry.stderr[-800:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
