from __future__ import annotations

import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from engine.config.paths import check_paths
from engine.config.settings import AppSettings, load_settings


def disk_report(
    settings: AppSettings | None = None, *, force_write_probe: bool = False
) -> dict[str, Any]:
    from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
    from engine.catalog.db import get_session

    settings = settings or load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        scoped = settings_with_customer_paths(settings, customer)
    finally:
        session.close()
    health = check_paths(scoped, force_write_probe=force_write_probe)

    def _usage(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {
                "path": str(path),
                "exists": False,
                "free_gb": 0.0,
                "total_gb": 0.0,
                "used_pct": 100.0,
                "writable": False,
            }
        u = shutil.disk_usage(path)
        free = u.free / (1024**3)
        total = u.total / (1024**3)
        used_pct = round((1 - u.free / u.total) * 100, 1) if u.total else 100.0
        return {
            "path": str(path),
            "exists": True,
            "free_gb": round(free, 2),
            "total_gb": round(total, 2),
            "used_pct": used_pct,
            "below_watermark": free < settings.min_free_disk_gb,
            "writable": None,
        }

    def _attach_writable(info: dict[str, Any], role: str) -> dict[str, Any]:
        for check in health.write_checks or []:
            if str(check.get("role") or "") != role:
                continue
            info["writable"] = bool(check.get("ok"))
            if not check.get("ok") and check.get("detail"):
                info["writable_detail"] = str(check.get("detail"))
            break
        return info

    cache = _attach_writable(_usage(scoped.paths.cache_root), "cache")
    output = _attach_writable(_usage(scoped.paths.output_root), "output")
    render = _attach_writable(_usage(scoped.paths.render_root), "render")
    data = _usage(scoped.paths.data_root)
    warnings = list(health.warnings)
    errors = list(health.errors)
    for label, info in (("缓存盘", cache), ("输出盘", output), ("渲染盘", render)):
        if info.get("below_watermark"):
            warnings.append(
                f"{label}剩余 {info['free_gb']}GB，低于水位线 {settings.min_free_disk_gb}GB"
            )
    return {
        "ok": health.ok
        and not cache.get("below_watermark")
        and not output.get("below_watermark")
        and not render.get("below_watermark"),
        "min_free_disk_gb": settings.min_free_disk_gb,
        "path_health": health.__dict__,
        "volumes": {"cache": cache, "output": output, "render": render, "data": data},
        "warnings": warnings,
        "errors": errors,
    }


def assert_production_ready(settings: AppSettings | None = None) -> None:
    """Raise ValueError if jobs must not start."""
    from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
    from engine.catalog.db import get_session

    settings = settings or load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        scoped = settings_with_customer_paths(settings, customer)
    finally:
        session.close()
    report = disk_report(scoped, force_write_probe=True)
    if report["errors"]:
        raise ValueError("; ".join(report["errors"]))
    # Hard block when external required and libraries missing (already in errors).
    # Soft: below watermark still blocks new jobs to avoid filling disk mid-render.
    lows = []
    for name, vol in report["volumes"].items():
        if vol.get("below_watermark"):
            lows.append(f"{name} 剩余 {vol.get('free_gb')}GB < {settings.min_free_disk_gb}GB")
    if lows:
        raise ValueError("磁盘水位不足，禁止开跑: " + "; ".join(lows))
    # Fail closed when brand lock requires clone TTS but runtime cannot deliver it.
    try:
        from engine.pack.voice_clone import (
            active_customer_requires_clone,
            assert_clone_runtime_ready,
        )

        if active_customer_requires_clone(scoped):
            assert_clone_runtime_ready()
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        # Path/customer probe failures already covered above; ignore soft probe noise.
        pass


def clean_cache(
    settings: AppSettings | None = None,
    *,
    older_than_hours: float = 24.0,
    older_than_days: float | None = None,
    targets: list[str] | None = None,
    cache_root: Path | str | None = None,
    render_root: Path | str | None = None,
) -> dict[str, Any]:
    """Remove stale files under allowlisted work-cache roots only.

    targets: subset of temp | render | frames | proxies. Never library.
    Path guard: DISK_CLEANUP_LOCK allowlist (assert before unlink).
    """
    from engine.ops.disk_cleanup import (
        CACHE_TARGETS_ALLOWED,
        allowed_cleanup_roots,
        path_is_under_allowed_root,
    )

    if cache_root is not None and render_root is not None:
        # Injected roots (tests / facade): only temp/frames/proxies/render are swept.
        output_root = Path(cache_root)
    else:
        from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
        from engine.catalog.db import get_session

        settings = settings or load_settings()
        session = get_session()
        try:
            customer = require_active_customer(session, settings)
            scoped = settings_with_customer_paths(settings, customer)
            cache_root = cache_root or scoped.paths.cache_root
            render_root = render_root or scoped.paths.render_root
            output_root = customer.output_root or scoped.paths.output_root
        finally:
            session.close()

    roots = allowed_cleanup_roots(
        cache_root=cache_root,
        render_root=render_root,
        output_root=output_root,
    )
    request = [
        str(t).strip().lower()
        for t in (targets if targets is not None else ["temp", "render"])
    ]
    if not request:
        request = ["temp", "render"]
    for t in request:
        if t not in CACHE_TARGETS_ALLOWED:
            raise ValueError(f"clean_cache 禁止目标 {t}（仅 temp/render/frames/proxies）")
        if t == "library":
            raise ValueError("clean_cache 永不删除 cache/library")

    # frames/proxies age by days; temp/render by hours.
    hour_cutoff = time.time() - max(1.0, float(older_than_hours)) * 3600
    day_cutoff = time.time() - max(1.0, float(older_than_days if older_than_days is not None else 14.0)) * 86400

    removed_files = 0
    skipped_unsafe = 0
    freed = 0
    scanned = 0
    target_paths: list[str] = []

    def _sweep(name: str, root: Path, cutoff: float) -> None:
        nonlocal removed_files, freed, scanned, skipped_unsafe
        if not root.exists():
            return
        target_paths.append(str(root))
        for path in root.rglob("*"):
            if path.is_symlink():
                skipped_unsafe += 1
                continue
            if not path.is_file():
                continue
            scanned += 1
            if not path_is_under_allowed_root(path, [root]):
                skipped_unsafe += 1
                continue
            try:
                st = path.stat()
                if st.st_mtime > cutoff:
                    continue
                size = st.st_size
                path.unlink(missing_ok=True)
                removed_files += 1
                freed += size
            except OSError:
                continue
        # prune empty dirs under the swept root
        try:
            for d in sorted(root.rglob("*"), reverse=True):
                if d.is_dir() and not d.is_symlink():
                    try:
                        d.rmdir()
                    except OSError:
                        pass
        except OSError:
            pass

    for name in request:
        root = roots.get(name)
        if root is None:
            continue
        cutoff = day_cutoff if name in ("frames", "proxies") else hour_cutoff
        _sweep(name, root, cutoff)

    return {
        "scanned": scanned,
        "removed_files": removed_files,
        "skipped_unsafe": skipped_unsafe,
        "freed_mb": round(freed / (1024 * 1024), 2),
        "freed_bytes": freed,
        "older_than_hours": older_than_hours,
        "older_than_days": older_than_days,
        "target_names": request,
        "targets": target_paths,
    }


def scheduler_state_path(settings: AppSettings) -> Path:
    return settings.paths.data_root / "scheduler_state.json"


def load_scheduler_state(settings: AppSettings) -> dict[str, Any]:
    path = scheduler_state_path(settings)
    if not path.exists():
        return {"last_auto_day": None, "last_job_id": None}
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"last_auto_day": None, "last_job_id": None}


def save_scheduler_state(settings: AppSettings, state: dict[str, Any]) -> None:
    import json

    path = scheduler_state_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def maybe_run_daily_job(settings: AppSettings | None = None) -> dict[str, Any] | None:
    """If auto daily enabled and local hour matches and not yet run today, create calendar job."""
    from engine.catalog.calendar import today_plan
    from engine.catalog.customer_scope import require_active_customer
    from engine.catalog.db import get_session
    from engine.jobs.queue import CreateJobRequest, create_job

    settings = settings or load_settings()
    if not getattr(settings, "auto_daily_enabled", False):
        return None

    now = datetime.now().astimezone()
    if now.hour != int(getattr(settings, "auto_daily_hour", 9)):
        return None

    today = date.today().isoformat()
    state = load_scheduler_state(settings)
    if state.get("last_auto_day") == today:
        return None

    try:
        assert_production_ready(settings)
    except ValueError as e:
        return {"skipped": True, "reason": str(e)}

    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        from engine.ops.keyword_stats import keyword_stats

        kw = keyword_stats(
            session,
            customer_id=customer.id,
            keyword_pack_path=getattr(customer, "keyword_pack_path", None),
        )
        if kw.get("empty"):
            return {"skipped": True, "reason": "词库为空，已挡日更（请先导入词池）"}
        row = today_plan(session, customer.id, today)
        if not row:
            return {"skipped": True, "reason": "今日无日历计划"}
        job = create_job(
            session,
            CreateJobRequest(
                mode="count",
                target_count=row.quota,
                template_name=row.template_name,
                theme=row.theme,
                category=row.category,
                customer_name=row.customer_name,
            ),
            customer_id=customer.id,
        )
        state = {"last_auto_day": today, "last_job_id": job.id, "triggered_at": now.isoformat()}
        save_scheduler_state(settings, state)
        return {"created": True, "job_id": job.id, "day": today, "theme": row.theme, "quota": row.quota}
    finally:
        session.close()
