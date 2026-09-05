"""72h retention: purge failed / invalid / expired deliverable leftovers."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Job, RenderOutput
from engine.catalog.output_purge import purge_output_media
from engine.ops.maintenance import clean_cache

log = logging.getLogger("montage.failed_cleanup")

POLICY_PATH = Path.home() / "Suying" / "data" / "failed-resource-cleanup-policy.json"
DEFAULT_POLICY = {
    "enabled": True,
    "retention_hours": 72,
    "run_interval_hours": 1,
    # Rebuildable cache (frames/proxies): off by default — DISK_CLEANUP_LOCK tier D.
    "frames_proxies_enabled": False,
    "frames_proxies_days": 14,
}
ORPHAN_STATES = ("failed",)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_policy() -> dict[str, Any]:
    try:
        raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    policy = {**DEFAULT_POLICY, **(raw if isinstance(raw, dict) else {})}
    policy["enabled"] = bool(policy.get("enabled", True))
    policy["retention_hours"] = max(1, min(24 * 90, int(policy.get("retention_hours", 72))))
    policy["run_interval_hours"] = max(1, min(24, int(policy.get("run_interval_hours", 1))))
    policy["frames_proxies_enabled"] = bool(policy.get("frames_proxies_enabled", False))
    policy["frames_proxies_days"] = max(1, min(365, int(policy.get("frames_proxies_days", 14))))
    return policy


def save_policy(patch: dict[str, Any]) -> dict[str, Any]:
    policy = {**load_policy(), **patch}
    policy["enabled"] = bool(policy.get("enabled", True))
    policy["retention_hours"] = max(1, min(24 * 90, int(policy.get("retention_hours", 72))))
    policy["run_interval_hours"] = max(1, min(24, int(policy.get("run_interval_hours", 1))))
    policy["frames_proxies_enabled"] = bool(policy.get("frames_proxies_enabled", False))
    policy["frames_proxies_days"] = max(1, min(365, int(policy.get("frames_proxies_days", 14))))
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = POLICY_PATH.with_name(f".{POLICY_PATH.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, POLICY_PATH)
    return policy


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def purge_stale_failed_outputs(
    session: Session,
    *,
    customer_id: int,
    older_than_hours: float = 72.0,
    actor: str = "scheduler",
) -> dict[str, Any]:
    """Delete on-disk media for failed outputs older than retention."""
    cutoff = _now() - timedelta(hours=max(1.0, float(older_than_hours)))
    rows = list(
        session.scalars(
            select(RenderOutput)
            .join(Job, RenderOutput.job_id == Job.id)
            .where(
                Job.customer_id == customer_id,
                RenderOutput.state.in_(ORPHAN_STATES),
            )
            .order_by(RenderOutput.id.asc())
        ).all()
    )
    purged: list[dict[str, Any]] = []
    for output in rows:
        created = _aware(output.created_at)
        if created is not None and created > cutoff:
            continue
        qc = output.qc_json if isinstance(output.qc_json, dict) else {}
        if qc.get("media_purged_at"):
            # Already purged: still re-try when artifacts reappear.
            pass
        result = purge_output_media(
            session,
            output,
            reason="failed_retention",
            actor=actor,
        )
        if result.get("removed") or not result.get("already_purged"):
            purged.append(result)
    if purged:
        session.commit()
    return {
        "ok": True,
        "count": len(purged),
        "freed_bytes": sum(int(item.get("freed_bytes") or 0) for item in purged),
        "items": purged[:100],
    }


def _sweep_orphans_under(
    root: Path,
    *,
    cutoff_ts: float,
    live_paths: set[str],
) -> dict[str, Any]:
    """Remove files/dirs under failed/ older than cutoff and not in live ready paths."""
    if not root.is_dir():
        return {"removed": 0, "freed_bytes": 0, "scanned": 0}
    removed = 0
    freed = 0
    scanned = 0
    for path in sorted(root.rglob("*"), reverse=True):
        scanned += 1
        try:
            resolved = str(path.resolve())
        except OSError:
            continue
        if resolved in live_paths:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime > cutoff_ts:
            continue
        if path.is_symlink():
            continue
        if path.is_file():
            try:
                size = st.st_size
                path.unlink(missing_ok=True)
                removed += 1
                freed += size
            except OSError:
                continue
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return {"removed": removed, "freed_bytes": freed, "scanned": scanned}


def sweep_failed_tree(
    session: Session,
    *,
    customer_id: int,
    output_root: str | Path,
    older_than_hours: float = 72.0,
) -> dict[str, Any]:
    """Orphan files under output_root/failed older than retention."""
    root = Path(output_root).expanduser().resolve() / "failed"
    cutoff_ts = time.time() - max(1.0, float(older_than_hours)) * 3600
    live: set[str] = set()
    for path in session.scalars(
        select(RenderOutput.output_path)
        .join(Job, RenderOutput.job_id == Job.id)
        .where(
            Job.customer_id == customer_id,
            RenderOutput.state.in_(("ready", "review")),
            RenderOutput.output_path.is_not(None),
        )
    ).all():
        try:
            live.add(str(Path(str(path)).expanduser().resolve()))
        except OSError:
            continue
    return _sweep_orphans_under(root, cutoff_ts=cutoff_ts, live_paths=live)


def maybe_run_failed_resource_cleanup(
    session: Session,
    *,
    customer_id: int,
    output_root: str | Path,
    settings: Any | None = None,
) -> dict[str, Any] | None:
    """Scheduler entry: at most once per run_interval_hours while enabled."""
    policy = load_policy()
    if not policy["enabled"]:
        return None
    now = _now()
    last_raw = str(policy.get("last_run_at") or "")
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            interval = timedelta(hours=int(policy["run_interval_hours"]))
            if now - last < interval:
                return None
        except ValueError:
            pass

    hours = float(policy["retention_hours"])
    failed = purge_stale_failed_outputs(
        session,
        customer_id=customer_id,
        older_than_hours=hours,
        actor="scheduler",
    )
    orphans = sweep_failed_tree(
        session,
        customer_id=customer_id,
        output_root=output_root,
        older_than_hours=hours,
    )
    cache: dict[str, Any] = {}
    try:
        cache = (
            clean_cache(
                settings,
                older_than_hours=hours,
                targets=["temp", "render"],
            )
            if settings is not None
            else {}
        )
    except Exception:
        log.exception("failed resource cache sweep failed")
        cache = {"error": "cache_sweep_failed"}

    rebuild: dict[str, Any] = {}
    if policy.get("frames_proxies_enabled") and settings is not None:
        try:
            rebuild = clean_cache(
                settings,
                older_than_days=float(policy.get("frames_proxies_days") or 14),
                targets=["frames", "proxies"],
            )
        except Exception:
            log.exception("failed resource frames/proxies sweep failed")
            rebuild = {"error": "rebuild_cache_sweep_failed"}

    save_policy({"last_run_at": now.isoformat()})
    result = {
        "ok": True,
        "retention_hours": hours,
        "failed_outputs": failed,
        "failed_tree": orphans,
        "cache": cache,
        "rebuild_cache": rebuild,
    }
    log.info(
        "failed resource cleanup: outputs=%s orphans=%s cache_removed=%s rebuild_removed=%s",
        failed.get("count"),
        orphans.get("removed"),
        cache.get("removed_files"),
        rebuild.get("removed_files"),
    )
    return result
