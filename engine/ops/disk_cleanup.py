"""Unified disk cleanup facade with hard allowlist (DISK_CLEANUP_LOCK)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

log = logging.getLogger("montage.disk_cleanup")

ALLOWED_TIERS = frozenset({"failed", "work_cache", "rebuild_cache", "published"})
FORBIDDEN_TIERS = frozenset(
    {
        "library",
        "ready",
        "review",
        "data",
        "library_root",
        "cache_library",
        "db",
        "chrome",
        "carrier",
        "backup",
    }
)
WORK_CACHE_TARGETS = ("temp", "render")
REBUILD_CACHE_TARGETS = ("frames", "proxies")
CACHE_TARGETS_ALLOWED = frozenset({*WORK_CACHE_TARGETS, *REBUILD_CACHE_TARGETS})


def _resolve(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def allowed_cleanup_roots(
    *,
    cache_root: Path | str,
    render_root: Path | str,
    output_root: Path | str,
) -> dict[str, Path]:
    """Paths that cleanup may descend into (never cache/library)."""
    cache = _resolve(cache_root)
    render = _resolve(render_root)
    out = _resolve(output_root)
    return {
        "temp": cache / "temp",
        "frames": cache / "frames",
        "proxies": cache / "proxies",
        "render": render,
        "failed": out / "failed",
        "published_trash": out / "retired" / ".trash" / "published-videos",
    }


def path_is_under_allowed_root(
    path: Path | str,
    allowed_roots: Iterable[Path],
) -> bool:
    """True if path resolves under one allowlisted root and is not a symlink leaf."""
    try:
        p = Path(path)
        if p.is_symlink():
            return False
        resolved = p.resolve(strict=False)
        # Reject if any parent component is a symlink outside after resolve fails pattern.
        for root in allowed_roots:
            root_r = _resolve(root)
            try:
                resolved.relative_to(root_r)
            except ValueError:
                continue
            # Ensure we did not escape via symlink: reconstruct under root must match.
            return True
        return False
    except OSError:
        return False


def assert_path_deletable(path: Path | str, allowed_roots: Iterable[Path]) -> bool:
    """Public alias used by clean_cache sweeps."""
    return path_is_under_allowed_root(path, allowed_roots)


def _dir_usage(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {
            "path": str(root),
            "exists": False,
            "bytes": 0,
            "files": 0,
        }
    total = 0
    files = 0
    try:
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                total += path.stat().st_size
                files += 1
            except OSError:
                continue
    except OSError:
        pass
    return {
        "path": str(root),
        "exists": True,
        "bytes": total,
        "files": files,
    }


def _stale_usage(root: Path, *, older_than_seconds: float) -> dict[str, Any]:
    """Count files older than cutoff (for report estimate)."""
    base = _dir_usage(root)
    if not base.get("exists"):
        base["stale_bytes"] = 0
        base["stale_files"] = 0
        return base
    cutoff = time.time() - max(0.0, older_than_seconds)
    stale_b = 0
    stale_n = 0
    try:
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                st = path.stat()
                if st.st_mtime > cutoff:
                    continue
                stale_b += st.st_size
                stale_n += 1
            except OSError:
                continue
    except OSError:
        pass
    base["stale_bytes"] = stale_b
    base["stale_files"] = stale_n
    return base


def load_merged_policy() -> dict[str, Any]:
    from engine.ops.failed_resource_cleanup import load_policy as load_failed
    from engine.ops.published_cleanup import load_policy as load_published

    failed = load_failed()
    published = load_published()
    return {
        "failed": {
            "enabled": bool(failed.get("enabled", True)),
            "retention_hours": int(failed.get("retention_hours", 72)),
            "run_interval_hours": int(failed.get("run_interval_hours", 1)),
            "frames_proxies_enabled": bool(failed.get("frames_proxies_enabled", False)),
            "frames_proxies_days": int(failed.get("frames_proxies_days", 14)),
            "last_run_at": failed.get("last_run_at"),
        },
        "published": {
            "enabled": bool(published.get("enabled", False)),
            "retention_days": int(published.get("retention_days", 30)),
            "trash_grace_days": int(published.get("trash_grace_days", 7)),
            "last_run_day": published.get("last_run_day"),
        },
        "work_cache": {
            # Aligned with failed retention for auto sweeps; manual default 24h.
            "temp_render_hours": int(failed.get("retention_hours", 72)),
            "frames_proxies_enabled": bool(failed.get("frames_proxies_enabled", False)),
            "frames_proxies_days": int(failed.get("frames_proxies_days", 14)),
        },
        "forbidden_note": (
            "待审成片(ready/review)、片库、cache/library、数据库永不被本 facade 删除"
        ),
    }


def save_merged_policy(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply nested patch keys: failed / published / work_cache."""
    from engine.ops.failed_resource_cleanup import save_policy as save_failed
    from engine.ops.published_cleanup import save_policy as save_published

    failed_patch: dict[str, Any] = {}
    published_patch: dict[str, Any] = {}

    if isinstance(patch.get("failed"), dict):
        raw = patch["failed"]
        for key in (
            "enabled",
            "retention_hours",
            "run_interval_hours",
            "frames_proxies_enabled",
            "frames_proxies_days",
        ):
            if key in raw:
                failed_patch[key] = raw[key]
    if isinstance(patch.get("work_cache"), dict):
        raw = patch["work_cache"]
        if "temp_render_hours" in raw:
            failed_patch["retention_hours"] = raw["temp_render_hours"]
        if "frames_proxies_enabled" in raw:
            failed_patch["frames_proxies_enabled"] = raw["frames_proxies_enabled"]
        if "frames_proxies_days" in raw:
            failed_patch["frames_proxies_days"] = raw["frames_proxies_days"]
    if isinstance(patch.get("published"), dict):
        raw = patch["published"]
        for key in ("enabled", "retention_days", "trash_grace_days"):
            if key in raw:
                published_patch[key] = raw[key]

    if failed_patch:
        save_failed(failed_patch)
    if published_patch:
        save_published(published_patch)
    return load_merged_policy()


def report(
    session: Session,
    *,
    customer_id: int,
    cache_root: Path | str,
    render_root: Path | str,
    output_root: Path | str,
) -> dict[str, Any]:
    policy = load_merged_policy()
    roots = allowed_cleanup_roots(
        cache_root=cache_root,
        render_root=render_root,
        output_root=output_root,
    )
    hours = float(policy["failed"]["retention_hours"])
    hours_sec = hours * 3600
    days_fp = float(policy["work_cache"]["frames_proxies_days"])
    days_sec = days_fp * 86400

    categories = {
        "temp": _stale_usage(roots["temp"], older_than_seconds=hours_sec),
        "render": _stale_usage(roots["render"], older_than_seconds=hours_sec),
        "frames": _stale_usage(roots["frames"], older_than_seconds=days_sec),
        "proxies": _stale_usage(roots["proxies"], older_than_seconds=days_sec),
        "failed_tree": _stale_usage(roots["failed"], older_than_seconds=hours_sec),
        "published_trash": _dir_usage(roots["published_trash"]),
    }

    from engine.ops.published_cleanup import preview as published_preview

    published = published_preview(
        session,
        customer_id=customer_id,
        output_root=output_root,
        older_than_days=int(policy["published"]["retention_days"]),
    )

    return {
        "ok": True,
        "policy": policy,
        "categories": categories,
        "published_eligible": {
            "count": published.get("count", 0),
            "bytes": published.get("bytes", 0),
            "items": published.get("items", [])[:50],
        },
        "allowlist_roots": {k: str(v) for k, v in roots.items()},
        "forbidden": sorted(FORBIDDEN_TIERS),
    }


def run(
    session: Session,
    *,
    customer_id: int,
    cache_root: Path | str,
    render_root: Path | str,
    output_root: Path | str,
    settings: Any | None = None,
    tiers: list[str] | None = None,
    confirm: bool = False,
    actor: str = "user",
) -> dict[str, Any]:
    """Execute allowlisted cleanup tiers. Forbidden tier names raise ValueError."""
    if not confirm:
        raise ValueError("清理前必须明确确认 confirm=true")

    raw_tiers = [str(t).strip().lower() for t in (tiers or []) if str(t).strip()]
    if not raw_tiers:
        raise ValueError("请指定至少一档 tiers")

    bad = [t for t in raw_tiers if t in FORBIDDEN_TIERS]
    if bad:
        raise ValueError(f"禁止清理档位（DISK_CLEANUP_LOCK）：{', '.join(bad)}")
    unknown = [t for t in raw_tiers if t not in ALLOWED_TIERS]
    if unknown:
        raise ValueError(f"未知清理档位：{', '.join(unknown)}")

    policy = load_merged_policy()
    results: dict[str, Any] = {"ok": True, "tiers": raw_tiers, "parts": {}}

    for tier in raw_tiers:
        if tier == "failed":
            from engine.ops.failed_resource_cleanup import (
                purge_stale_failed_outputs,
                sweep_failed_tree,
            )

            hours = float(policy["failed"]["retention_hours"])
            failed_out = purge_stale_failed_outputs(
                session,
                customer_id=customer_id,
                older_than_hours=hours,
                actor=actor,
            )
            orphans = sweep_failed_tree(
                session,
                customer_id=customer_id,
                output_root=output_root,
                older_than_hours=hours,
            )
            results["parts"]["failed"] = {
                "failed_outputs": failed_out,
                "failed_tree": orphans,
            }
        elif tier == "work_cache":
            from engine.ops.maintenance import clean_cache

            hours = float(policy["work_cache"]["temp_render_hours"] or 24)
            results["parts"]["work_cache"] = clean_cache(
                settings,
                older_than_hours=max(1.0, hours),
                targets=list(WORK_CACHE_TARGETS),
                cache_root=cache_root,
                render_root=render_root,
            )
        elif tier == "rebuild_cache":
            from engine.ops.maintenance import clean_cache

            days = float(policy["work_cache"]["frames_proxies_days"] or 14)
            results["parts"]["rebuild_cache"] = clean_cache(
                settings,
                older_than_days=max(1.0, days),
                targets=list(REBUILD_CACHE_TARGETS),
                cache_root=cache_root,
                render_root=render_root,
            )
        elif tier == "published":
            from engine.ops.published_cleanup import cleanup, purge_trash

            days = int(policy["published"]["retention_days"])
            cleaned = cleanup(
                session,
                customer_id=customer_id,
                output_root=output_root,
                older_than_days=days,
                actor=actor,
            )
            trash = purge_trash(
                output_root,
                int(policy["published"]["trash_grace_days"]),
            )
            results["parts"]["published"] = {"cleanup": cleaned, "trash": trash}

    try:
        from engine.ops.audit_log import write_log

        write_log(
            session,
            customer_id=customer_id,
            category="cleanup",
            event="disk_cleanup_run",
            message=f"磁盘清理：{','.join(raw_tiers)}",
            stage="completed",
            source_type="disk_cleanup",
            source_id=actor,
            details={
                "tiers": raw_tiers,
                "parts_summary": {
                    k: _summary_bytes(v) for k, v in results.get("parts", {}).items()
                },
            },
            commit=True,
        )
    except Exception:  # noqa: BLE001
        log.exception("disk cleanup audit log failed")

    return results


def _summary_bytes(part: Any) -> dict[str, Any]:
    if not isinstance(part, dict):
        return {}
    out: dict[str, Any] = {}
    if "removed_files" in part:
        out["removed_files"] = part.get("removed_files")
        out["freed_mb"] = part.get("freed_mb")
    if "failed_outputs" in part:
        fo = part.get("failed_outputs") or {}
        out["failed_count"] = fo.get("count")
        out["failed_freed"] = fo.get("freed_bytes")
    if "cleanup" in part:
        c = part.get("cleanup") or {}
        out["published_count"] = c.get("count")
        out["published_bytes"] = c.get("bytes")
    return out
