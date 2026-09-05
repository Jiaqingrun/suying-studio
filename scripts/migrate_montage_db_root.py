#!/usr/bin/env python3
"""Move the authoritative montage.db to a local APFS data root safely.

Dry-run is the default. Pass --execute only after the App/engine has stopped.
The source database is retained as the rollback copy.
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


KEY_TABLES = ("customers", "jobs", "assets", "cliplets", "render_outputs", "reach_queue")


def _bootstrap_settings() -> Path:
    return Path.home() / "Suying" / "data" / "settings.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"设置文件不存在: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"设置文件不可读: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"设置文件顶层必须是对象: {path}")
    return raw


def _configured_source_root(settings_path: Path) -> Path:
    raw = _read_json(settings_path)
    configured = str((raw.get("paths") or {}).get("data_root") or "").strip()
    if not configured:
        return settings_path.parent.expanduser().resolve()
    return Path(configured).expanduser().resolve()


def _open_pids(path: Path) -> list[int]:
    proc = subprocess.run(
        ["lsof", "-t", "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"无法检查数据库占用: {(proc.stderr or proc.stdout).strip()}")
    out: list[int] = []
    for line in proc.stdout.splitlines():
        try:
            out.append(int(line.strip()))
        except ValueError:
            continue
    return sorted(set(out))


def _integrity_and_counts(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"数据库不存在: {path}")
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.lower() != "ok":
            raise RuntimeError(f"数据库完整性检查失败: {path}: {integrity}")
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        counts = {
            table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in KEY_TABLES
            if table in tables
        }
        return {
            "integrity": integrity,
            "counts": counts,
            "size": path.stat().st_size,
        }
    finally:
        conn.close()


def _sqlite_backup(source: Path, target: Path) -> None:
    tmp = target.with_name(f".{target.name}.migration.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True, timeout=60)
    target_conn = sqlite3.connect(tmp, timeout=60)
    try:
        source_conn.backup(target_conn)
        target_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target_conn.commit()
    finally:
        target_conn.close()
        source_conn.close()
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.migration.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def migrate(
    *,
    settings_path: Path,
    target_root: Path,
    execute: bool,
) -> dict[str, Any]:
    settings_path = settings_path.expanduser().resolve()
    source_root = _configured_source_root(settings_path)
    target_root = target_root.expanduser().resolve()
    source_db = source_root / "montage.db"
    target_db = target_root / "montage.db"

    if source_root == target_root:
        raw = _read_json(settings_path)
        stale_volume = bool(str(raw.get("workspace_volume_uuid") or "").strip())
        backups: list[str] = []
        if execute and stale_volume:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            settings_backup = settings_path.with_name(
                f"{settings_path.name}.pre-db-migration-{stamp}.bak"
            )
            shutil.copy2(settings_path, settings_backup)
            backups.append(str(settings_backup))
            raw["workspace_volume_uuid"] = ""
            _atomic_write_json(settings_path, raw)
        return {
            "ok": True,
            "changed": bool(execute and stale_volume),
            "source": str(source_db),
            "target": str(target_db),
            "reason": (
                "data_root 已是目标目录；已清理旧外置卷绑定"
                if execute and stale_volume
                else "data_root 已是目标目录"
            ),
            "backups": backups,
        }
    source_info = _integrity_and_counts(source_db)
    open_pids = _open_pids(source_db)
    result: dict[str, Any] = {
        "ok": not open_pids,
        "dry_run": not execute,
        "source": str(source_db),
        "target": str(target_db),
        "source_info": source_info,
        "open_pids": open_pids,
        "settings": str(settings_path),
    }
    if open_pids:
        raise RuntimeError(f"数据库仍被进程占用，先退出速影 App/引擎: {open_pids}")
    if not execute:
        return result

    target_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backups: list[str] = []
    for existing in (target_db, target_db.with_name("montage.db-wal"), target_db.with_name("montage.db-shm")):
        if not existing.exists():
            continue
        backup = existing.with_name(f"{existing.name}.pre-migration-{stamp}.bak")
        shutil.copy2(existing, backup)
        backups.append(str(backup))

    _sqlite_backup(source_db, target_db)
    target_info = _integrity_and_counts(target_db)
    if target_info["counts"] != source_info["counts"]:
        raise RuntimeError(
            f"迁移后关键表计数不一致: source={source_info['counts']} target={target_info['counts']}"
        )

    settings_backup = settings_path.with_name(f"{settings_path.name}.pre-db-migration-{stamp}.bak")
    shutil.copy2(settings_path, settings_backup)
    backups.append(str(settings_backup))
    raw = _read_json(settings_path)
    paths = raw.setdefault("paths", {})
    if not isinstance(paths, dict):
        raise RuntimeError("settings.paths 必须是对象")
    paths["data_root"] = str(target_root)
    raw["workspace_volume_uuid"] = ""
    _atomic_write_json(settings_path, raw)

    result.update(
        {
            "ok": True,
            "changed": True,
            "dry_run": False,
            "target_info": target_info,
            "backups": backups,
            "source_retained": True,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移速影 SQLite 权威库到本机 APFS 数据目录")
    parser.add_argument("--settings", type=Path, default=_bootstrap_settings())
    parser.add_argument("--target-root", type=Path, default=Path.home() / "Suying" / "data")
    parser.add_argument("--execute", action="store_true", help="执行迁移；默认仅预检")
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                migrate(
                    settings_path=args.settings,
                    target_root=args.target_root,
                    execute=args.execute,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
