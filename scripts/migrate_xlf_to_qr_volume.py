#!/usr/bin/env python3
"""Cut over customer Mac from /Volumes/xlf → /Volumes/QR after disk install.

Rewrites bootstrap settings + APFS montage.db absolute paths, binds the new
volume UUID, points media sync at the mounted QR volume, and restarts App.
Keeps paths.data_root on local APFS (~/Suying/data).
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

OLD_PREFIX = "/Volumes/xlf"
NEW_PREFIX = "/Volumes/QR"
VOLUME_UUID = "EF259876-00CC-4DB0-928C-15CC218A007D"
WORK = Path(NEW_PREFIX) / "速影工作区"
SYNC = Path(NEW_PREFIX) / "极空间团队文件同步"
BOOT_DATA = Path.home() / "Suying" / "data"
BOOT_SETTINGS = BOOT_DATA / "settings.json"
BOOT_DB = BOOT_DATA / "montage.db"
WORK_SETTINGS = WORK / "db" / "settings.json"
SYNC_CFG = Path.home() / ".qr" / "suying-sync.json"
QR_VOLUME_LINK = Path.home() / "QR-Volume"
APP_CANDIDATES = [
    Path("/Applications/速影 Studio.app"),
    Path.home() / "Applications" / "速影 Studio.app",
    Path("/Applications/速影.app"),
    Path.home() / "Applications" / "速影.app",
]


def rewrite_text(value: str) -> str:
    return value.replace(OLD_PREFIX, NEW_PREFIX)


def rewrite_json_file(path: Path, *, also: dict | None = None) -> None:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    text = json.dumps(data, ensure_ascii=False)
    text = rewrite_text(text)
    data = json.loads(text)
    if also:
        for key, value in also.items():
            if key == "paths" and isinstance(value, dict):
                paths = data.setdefault("paths", {})
                paths.update(value)
            else:
                data[key] = value
    # Never move authoritative DB onto the external volume.
    paths = data.setdefault("paths", {})
    paths["data_root"] = str(BOOT_DATA)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rewrote json: {path}")


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
        "version": 3,
        "local_root": str(SYNC),
        "work_root": str(WORK),
        "volume_uuid": VOLUME_UUID,
        "volume_relpath": "极空间团队文件同步",
        "sync_mode": "media",
        "media_sync_enabled": True,
        "carrier_relpath": "手机相册备份/速影载体",
        "carrier_remote_root": "",
        "carrier_mirror": str(Path.home() / "Suying" / "carrier"),
        "media_sources": [
            {
                "customer_key": "北京始峰伟业",
                "display_name": "北京始峰伟业",
                "remote_root": "手机相册备份/徐玲飞",
                "local_target": "速影客户/北京始峰伟业/01-片库/徐玲飞",
                "pull_only": True,
            }
        ],
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
        "pull_remote_roots": ["手机相册备份/徐玲飞"],
        "update_remote_root": "/nvme11/my/data/速影/更新包",
    }
    SYNC_CFG.parent.mkdir(parents=True, exist_ok=True)
    SYNC_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SYNC_CFG}")


def ensure_qr_volume_link() -> None:
    target = Path(NEW_PREFIX)
    if QR_VOLUME_LINK.is_symlink():
        current = Path(os.path.realpath(QR_VOLUME_LINK))
        if current == target.resolve():
            print(f"QR-Volume link ok: {QR_VOLUME_LINK} -> {target}")
            return
        QR_VOLUME_LINK.unlink()
    elif QR_VOLUME_LINK.exists():
        # Do not destroy a real directory; only link when missing.
        print(f"skip QR-Volume link: {QR_VOLUME_LINK} already exists and is not a symlink")
        return
    QR_VOLUME_LINK.symlink_to(target)
    print(f"linked {QR_VOLUME_LINK} -> {target}")


def find_app() -> Path:
    for p in APP_CANDIDATES:
        if p.exists():
            return p.resolve()
    raise SystemExit("速影.app not found")


def stop_engine_listeners() -> None:
    try:
        out = subprocess.check_output(
            ["lsof", "-t", "-iTCP:8766", "-sTCP:LISTEN"], text=True
        ).strip()
    except subprocess.CalledProcessError:
        return
    for pid in out.splitlines():
        subprocess.run(["kill", pid], check=False)
    time.sleep(1)


def quit_app() -> None:
    for name in ("速影 Studio", "速影"):
        subprocess.run(["osascript", "-e", f'quit app "{name}"'], check=False)
    time.sleep(2)
    stop_engine_listeners()


def open_app(app: Path) -> None:
    subprocess.run(["open", "-n", "-a", str(app)], check=False)
    print(f"opened {app}")


def reload_sync_agent() -> None:
    plist = Path.home() / "Library/LaunchAgents/com.qr.zspace-team-sync.plist"
    if not plist.exists():
        print("sync LaunchAgent missing; skip reload")
        return
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
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"{domain}/com.qr.zspace-team-sync"],
        check=False,
    )
    print(f"reloaded {plist}")


def wait_health(timeout: int = 90) -> dict:
    import urllib.request

    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8766/health", timeout=3) as resp:
                data = json.loads(resp.read().decode())
                if data.get("status") == "ok":
                    return data
                last = json.dumps(data, ensure_ascii=False)[:400]
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(1)
    raise SystemExit(f"health timeout: {last}")


def post_reconnect() -> dict:
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:8766/workspace/reconnect",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    if not WORK.is_dir() or not SYNC.is_dir():
        raise SystemExit(f"missing work/sync under {NEW_PREFIX}")
    if not BOOT_SETTINGS.exists() or not BOOT_DB.exists():
        raise SystemExit(f"missing bootstrap db/settings under {BOOT_DATA}")

    info = subprocess.run(
        ["diskutil", "info", "-plist", VOLUME_UUID],
        capture_output=True,
        check=False,
    )
    if info.returncode != 0:
        raise SystemExit(f"volume uuid not mounted: {VOLUME_UUID}")
    volume = __import__("plistlib").loads(info.stdout)
    mount = str(volume.get("MountPoint") or "").strip()
    if mount != NEW_PREFIX:
        raise SystemExit(f"volume mounted at {mount!r}, expected {NEW_PREFIX!r}")

    quit_app()

    also = {
        "workspace_volume_uuid": VOLUME_UUID,
        "active_customer": "北京始峰伟业",
        "paths": {
            "library_root": f"{NEW_PREFIX}/极空间团队文件同步/速影客户/北京始峰伟业/01-片库",
            "output_root": f"{NEW_PREFIX}/极空间团队文件同步/速影客户/北京始峰伟业/02-成片",
            "cache_root": f"{NEW_PREFIX}/速影工作区/cache",
            "render_root": f"{NEW_PREFIX}/速影工作区/render",
            "music_root": f"{NEW_PREFIX}/极空间团队文件同步/速影客户/北京始峰伟业/04-音乐",
            "data_root": str(BOOT_DATA),
            "external_required": True,
        },
    }
    # Backup then rewrite bootstrap settings/db (authority).
    stamp = time.strftime("%Y%m%dT%H%M%S")
    bak = BOOT_DATA / f"cutover-bak-{stamp}"
    bak.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BOOT_SETTINGS, bak / "settings.json")
    shutil.copy2(BOOT_DB, bak / "montage.db")
    print(f"backup -> {bak}")

    rewrite_json_file(BOOT_SETTINGS, also=also)
    rewrite_db(BOOT_DB)

    if WORK_SETTINGS.exists():
        # Keep external mirror settings consistent, but force data_root to APFS.
        rewrite_json_file(WORK_SETTINGS, also=also)

    for kp in SYNC.rglob("keyword-pack.json"):
        try:
            raw = kp.read_text(encoding="utf-8")
            new = rewrite_text(raw)
            if new != raw:
                kp.write_text(new, encoding="utf-8")
                print(f"rewrote {kp}")
        except OSError as e:
            print(f"skip {kp}: {e}")

    write_sync_config()
    ensure_qr_volume_link()
    reload_sync_agent()

    app = find_app()
    open_app(app)
    health = wait_health()
    print(
        "health ok:",
        health.get("active_customer"),
        health.get("paths", {}).get("data_root"),
        health.get("paths", {}).get("library_root"),
    )
    ph = health.get("path_health") or {}
    print("path_health:", json.dumps(ph, ensure_ascii=False)[:500])

    try:
        recon = post_reconnect()
        print("reconnect:", json.dumps(recon, ensure_ascii=False)[:500])
    except Exception as e:  # noqa: BLE001
        print(f"reconnect warn: {e}")

    # Remaining old-prefix check
    con = sqlite3.connect(str(BOOT_DB))
    left = 0
    for table, col in [
        ("customers", "library_root"),
        ("assets", "source_path"),
        ("render_outputs", "output_path"),
    ]:
        try:
            left += con.execute(
                f"SELECT count(*) FROM {table} WHERE {col} LIKE ?",
                (f"{OLD_PREFIX}%",),
            ).fetchone()[0]
        except sqlite3.Error:
            pass
    con.close()
    print(f"remaining {OLD_PREFIX} refs: {left}")

    sync_py = Path.home() / "QR" / "tools" / "zspace-team-sync.py"
    if sync_py.exists():
        dry = subprocess.run(
            [sys.executable, str(sync_py), "--dry-run", "--pull-only"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        print("sync dry-run rc:", dry.returncode)
        if dry.stdout:
            print(dry.stdout[-1500:])
        if dry.stderr:
            print(dry.stderr[-800:])
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
