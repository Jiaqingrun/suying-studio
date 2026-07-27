from __future__ import annotations

import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from engine.config.paths import check_paths
from engine.config.settings import AppSettings, load_settings


def disk_report(settings: AppSettings | None = None) -> dict[str, Any]:
    from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
    from engine.catalog.db import get_session

    settings = settings or load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        scoped = settings_with_customer_paths(settings, customer)
    finally:
        session.close()
    health = check_paths(scoped)

    def _usage(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"path": str(path), "exists": False, "free_gb": 0.0, "total_gb": 0.0, "used_pct": 100.0}
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
        }

    cache = _usage(scoped.paths.cache_root)
    output = _usage(scoped.paths.output_root)
    data = _usage(scoped.paths.data_root)
    warnings = list(health.warnings)
    errors = list(health.errors)
    for label, info in (("缓存盘", cache), ("输出盘", output)):
        if info.get("below_watermark"):
            warnings.append(
                f"{label}剩余 {info['free_gb']}GB，低于水位线 {settings.min_free_disk_gb}GB"
            )
    return {
        "ok": health.ok and not cache.get("below_watermark") and not output.get("below_watermark"),
        "min_free_disk_gb": settings.min_free_disk_gb,
        "path_health": health.__dict__,
        "volumes": {"cache": cache, "output": output, "data": data},
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
    report = disk_report(scoped)
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


def clean_cache(settings: AppSettings | None = None, *, older_than_hours: float = 24.0) -> dict[str, Any]:
    from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
    from engine.catalog.db import get_session

    settings = settings or load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        scoped = settings_with_customer_paths(settings, customer)
    finally:
        session.close()
    temp = scoped.paths.cache_root / "temp"
    rendering = scoped.paths.render_root
    cutoff = time.time() - older_than_hours * 3600
    removed_files = 0
    freed = 0
    scanned = 0

    def _sweep(root: Path) -> None:
        nonlocal removed_files, freed, scanned
        if not root.exists():
            return
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            scanned += 1
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
        # remove empty dirs under temp
        if root == temp:
            for d in sorted(root.rglob("*"), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass

    _sweep(temp)
    _sweep(rendering)
    return {
        "scanned": scanned,
        "removed_files": removed_files,
        "freed_mb": round(freed / (1024 * 1024), 2),
        "older_than_hours": older_than_hours,
        "targets": [str(temp), str(rendering)],
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
