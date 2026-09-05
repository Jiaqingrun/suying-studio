"""Customer-safe local backup snapshots for Suying critical data."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.catalog.customer_scope import require_active_customer
from engine.catalog.db import Customer, db_path, get_session
from engine.config.settings import load_settings

SCHEMA = "suying.backup.v1"
ROOT_MARKER = ".suying-backup-root"
DEFAULT_ROOT = Path.home() / "Suying" / "backups"
POLICY_PATH = Path.home() / "Suying" / "data" / "backup-policy.json"
STATUS_PATH = Path.home() / "Suying" / "runtime" / "backup-status.json"
_lock = threading.Lock()

DEFAULT_POLICY: dict[str, Any] = {
    "enabled": False,
    "weekday": 6,
    "hour": 3,
    "retention_count": 4,
    "include_chrome": True,
    "root": str(DEFAULT_ROOT),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, mode)
    os.replace(temp, path)
    os.chmod(path, mode)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_policy() -> dict[str, Any]:
    try:
        raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    policy = {**DEFAULT_POLICY, **(raw if isinstance(raw, dict) else {})}
    policy["enabled"] = bool(policy["enabled"])
    policy["weekday"] = max(0, min(6, int(policy["weekday"])))
    policy["hour"] = max(0, min(23, int(policy["hour"])))
    policy["retention_count"] = max(3, min(52, int(policy["retention_count"])))
    policy["include_chrome"] = bool(policy["include_chrome"])
    policy["root"] = str(Path(str(policy["root"])).expanduser().resolve())
    return policy


def save_policy(patch: dict[str, Any]) -> dict[str, Any]:
    policy = {**load_policy(), **patch}
    policy["weekday"] = max(0, min(6, int(policy.get("weekday", 6))))
    policy["hour"] = max(0, min(23, int(policy.get("hour", 3))))
    policy["retention_count"] = max(3, min(52, int(policy.get("retention_count", 4))))
    policy["enabled"] = bool(policy.get("enabled"))
    policy["include_chrome"] = bool(policy.get("include_chrome", True))
    policy["root"] = str(Path(str(policy.get("root") or DEFAULT_ROOT)).expanduser().resolve())
    _atomic_json(POLICY_PATH, policy)
    return policy


def _approved_root(policy: dict[str, Any] | None = None) -> Path:
    root = Path(str((policy or load_policy()).get("root") or DEFAULT_ROOT)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    marker = root / ROOT_MARKER
    if not marker.exists():
        marker.write_text(SCHEMA + "\n", encoding="utf-8")
        os.chmod(marker, 0o600)
    if marker.is_symlink() or marker.read_text(encoding="utf-8").strip() != SCHEMA:
        raise RuntimeError("备份目录标记无效")
    return root


def _write_status(payload: dict[str, Any]) -> None:
    _atomic_json(STATUS_PATH, {"schema": SCHEMA, **payload})


def status() -> dict[str, Any]:
    try:
        saved = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {"schema": SCHEMA, "status": "idle"}
    return {"policy": load_policy(), **saved}


def _sqlite_snapshot(source: Path, destination: Path) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    target_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(target_conn)
        check = target_conn.execute("PRAGMA integrity_check").fetchone()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError(f"数据库备份完整性检查失败：{check}")
        vector_count = int(
            target_conn.execute(
                "SELECT count(*) FROM cliplets WHERE embedding_json IS NOT NULL"
            ).fetchone()[0]
        )
    finally:
        target_conn.close()
        source_conn.close()
    return {
        "kind": "database",
        "path": destination.name,
        "size": destination.stat().st_size,
        "sha256": _sha256(destination),
        "integrity": "ok",
        "vector_count": vector_count,
    }


def _copy_file(source: Path, destination: Path, kind: str) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "kind": kind,
        "path": destination.name,
        "size": destination.stat().st_size,
        "sha256": _sha256(destination),
        "status": "completed",
    }


def _chrome_snapshot(customer: Customer, destination: Path) -> dict[str, Any]:
    from engine.reach.browser import chrome_profiles_root

    source = chrome_profiles_root() / f"customer-{customer.id}"
    if not source.is_dir():
        return {"kind": "chrome", "status": "skipped_missing", "size": 0}
    locks = [
        path
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket")
        for path in source.rglob(name)
    ]
    if locks:
        return {
            "kind": "chrome",
            "status": "skipped_busy",
            "message": "受管浏览器正在使用，登录资料本次未备份",
            "size": 0,
        }
    shutil.copytree(source, destination, symlinks=False)
    files = [path for path in destination.rglob("*") if path.is_file()]
    return {
        "kind": "chrome",
        "status": "completed",
        "path": destination.name,
        "size": sum(path.stat().st_size for path in files),
        "files": len(files),
    }


def _run_backup(job_id: str, trigger: str) -> None:
    with _lock:
        policy = load_policy()
        root = _approved_root(policy)
        stamp = _now().strftime("%Y%m%dT%H%M%SZ")
        temp = root / f".incomplete-{job_id}"
        destination = root / f"{stamp}-{job_id[:8]}"
        _write_status(
            {
                "job_id": job_id,
                "status": "running",
                "trigger": trigger,
                "started_at": _now().isoformat(),
            }
        )
        session = get_session()
        customer: Customer | None = None
        try:
            settings = load_settings()
            customer = require_active_customer(session, settings)
            temp.mkdir(parents=True, exist_ok=False)
            os.chmod(temp, 0o700)
            artifacts: list[dict[str, Any]] = [
                _sqlite_snapshot(db_path(settings), temp / "montage.db")
            ]
            keyword = Path(customer.keyword_pack_path or "").expanduser()
            if keyword.is_file():
                artifacts.append(
                    _copy_file(keyword, temp / "keyword-pack.json", "keyword_pack")
                )
            else:
                artifacts.append(
                    {"kind": "keyword_pack", "status": "skipped_missing", "size": 0}
                )
            if policy["include_chrome"]:
                artifacts.append(_chrome_snapshot(customer, temp / "chrome-profiles"))
            manifest = {
                "schema": SCHEMA,
                "backup_id": job_id,
                "trigger": trigger,
                "customer_id": customer.id,
                "customer_name": customer.name,
                "created_at": _now().isoformat(),
                "artifacts": artifacts,
                "source_paths": {
                    "database": str(db_path(settings)),
                    "keyword_pack": str(keyword),
                },
            }
            _atomic_json(temp / "manifest.json", manifest)
            os.replace(temp, destination)
            partial = any(
                str(item.get("status") or "completed").startswith("skipped")
                for item in artifacts
            )
            _write_status(
                {
                    "job_id": job_id,
                    "status": "partial" if partial else "completed",
                    "trigger": trigger,
                    "finished_at": _now().isoformat(),
                    "path": str(destination),
                    "artifacts": artifacts,
                }
            )
            _audit_backup(
                customer.id,
                "backup_completed",
                "重要数据备份完成" if not partial else "重要数据备份完成，部分项目已跳过",
                details={"backup_id": job_id, "path": str(destination), "artifacts": artifacts},
            )
            prune_backups(policy["retention_count"], dry_run=False)
        except Exception as exc:
            shutil.rmtree(temp, ignore_errors=True)
            _write_status(
                {
                    "job_id": job_id,
                    "status": "failed",
                    "trigger": trigger,
                    "finished_at": _now().isoformat(),
                    "error": str(exc),
                }
            )
            _audit_backup(
                customer.id if customer else None,
                "backup_failed",
                "重要数据备份失败",
                level="error",
                details={"backup_id": job_id, "error": str(exc)},
            )
        finally:
            session.close()


def start_backup(trigger: str = "manual") -> dict[str, Any]:
    if _lock.locked():
        return {"ok": False, "status": "busy", **status()}
    job_id = uuid.uuid4().hex
    threading.Thread(
        target=_run_backup,
        args=(job_id, trigger),
        daemon=True,
        name="suying-backup",
    ).start()
    return {"ok": True, "job_id": job_id, "status": "queued"}


def run_backup_now(trigger: str = "scheduled") -> dict[str, Any]:
    if _lock.locked():
        return {"ok": False, "status": "busy"}
    job_id = uuid.uuid4().hex
    _run_backup(job_id, trigger)
    return {"ok": True, "job_id": job_id, **status()}


def maybe_run_scheduled_backup() -> dict[str, Any] | None:
    policy = load_policy()
    if not policy["enabled"] or _lock.locked():
        return None
    local = datetime.now().astimezone()
    if local.weekday() != int(policy["weekday"]) or local.hour != int(policy["hour"]):
        return None
    week_key = f"{local.isocalendar().year}-W{local.isocalendar().week:02d}"
    if str(policy.get("last_scheduled_week") or "") == week_key:
        return None
    policy["last_scheduled_week"] = week_key
    _atomic_json(POLICY_PATH, policy)
    return start_backup("scheduled")


def list_backups() -> list[dict[str, Any]]:
    root = _approved_root()
    rows: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob("*/manifest.json"), reverse=True):
        if ".trash" in manifest_path.parts or manifest_path.is_symlink():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("schema") != SCHEMA:
            continue
        rows.append(
            {
                **manifest,
                "path": str(manifest_path.parent),
                "size": sum(
                    path.stat().st_size
                    for path in manifest_path.parent.rglob("*")
                    if path.is_file()
                ),
            }
        )
    return rows


def prune_backups(retention_count: int | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    keep = max(3, int(retention_count or load_policy()["retention_count"]))
    root = _approved_root()
    rows = list_backups()
    candidates = rows[keep:]
    result = {
        "ok": True,
        "dry_run": dry_run,
        "keep": keep,
        "candidates": [
            {"backup_id": row["backup_id"], "path": row["path"], "size": row["size"]}
            for row in candidates
        ],
        "moved": [],
    }
    if dry_run:
        return result
    trash = root / ".trash"
    trash.mkdir(exist_ok=True)
    os.chmod(trash, 0o700)
    for row in candidates:
        source = Path(str(row["path"])).resolve()
        if source.is_symlink() or not source.is_relative_to(root):
            continue
        if not (source / "manifest.json").is_file():
            continue
        target = trash / f"{source.name}-{uuid.uuid4().hex[:8]}"
        os.replace(source, target)
        result["moved"].append(str(target))
    return result


def _audit_backup(
    customer_id: int | None,
    event: str,
    message: str,
    *,
    level: str = "info",
    details: dict[str, Any] | None = None,
) -> None:
    try:
        from engine.ops.audit_log import write_log

        session = get_session()
        try:
            write_log(
                session,
                customer_id=customer_id,
                scope="customer" if customer_id else "machine",
                category="backup",
                event=event,
                message=message,
                level=level,
                source_type="backup",
                source_id=str((details or {}).get("backup_id") or ""),
                details=details,
                commit=True,
            )
        finally:
            session.close()
    except Exception:
        pass

