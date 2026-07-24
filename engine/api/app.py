from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select

from engine.catalog.db import (
    Asset,
    CalendarEntry,
    Cliplet,
    Customer,
    Job,
    JobEvent,
    KeywordUsage,
    RenderOutput,
    ReviewItem,
    Template,
    get_session,
    init_db,
)
from engine.catalog.customer_scope import (
    customer_to_dict,
    get_or_create_customer,
    list_customers,
    require_active_customer,
    resolve_customer,
    settings_with_customer_paths,
)
from engine.catalog.calendar import (
    CalendarEntryIn,
    delete_entry,
    entry_to_dict,
    get_entry,
    list_entries,
    seed_week_if_empty,
    today_plan,
    upsert_entry,
)
from engine.catalog.keyword_pack import import_keyword_pack, parse_keyword_file
from engine.catalog.vector_index import index_pending, search_cliplets
from engine.config.paths import check_paths, ensure_layout
from engine.config.settings import AppSettings, PathConfig, load_settings, save_settings
from engine.export.csv_export import export_job_events_csv, export_renders_csv
from engine.ingest.cliplet import create_cliplets_for_asset, recaption_existing_cliplets
from engine.ingest.watcher import IngestWatcher
from engine.jobs.queue import CreateJobRequest, create_job, pause_job, resume_job
from engine.jobs.worker import worker
from engine.ops.maintenance import assert_production_ready, clean_cache, disk_report, load_scheduler_state, maybe_run_daily_job
from engine.ops.scheduler import scheduler
from engine.template.engine import DEFAULT_TEMPLATE, TemplateDefinition, build_plan, plan_to_dict

from engine.ops.scan_state import scan_state as _scan_state

watcher: IngestWatcher | None = None


def _run_scan_background(limit: int | None) -> None:
    global watcher
    import traceback
    from datetime import datetime, timezone

    _scan_state.update(
        {
            "running": True,
            "ingested": 0,
            "limit": limit,
            "error": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }
    )

    def _on_progress(n: int) -> None:
        _scan_state["ingested"] = n

    try:
        if not watcher:
            raise RuntimeError("Watcher not ready")
        count = watcher.scan_existing(limit=limit, on_progress=_on_progress)
        _scan_state["ingested"] = count
    except Exception as e:
        _scan_state["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}"
    finally:
        _scan_state["running"] = False
        _scan_state["finished_at"] = datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global watcher
    settings = load_settings()
    ensure_layout(settings)
    init_db(settings)
    session = get_session()
    try:
        settings = load_settings()
        customer = require_active_customer(session, settings)
        seed_week_if_empty(session, customer.id, customer.name)
    finally:
        session.close()
    watcher = IngestWatcher(settings)
    watcher.start()
    worker.start()
    scheduler.start()
    yield
    scheduler.stop()
    worker.stop()
    if watcher:
        watcher.stop()


app = FastAPI(title="Montage Studio Engine", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SettingsUpdate(BaseModel):
    library_root: str | None = None
    library_roots: list[str] | None = None
    output_root: str | None = None
    cache_root: str | None = None
    data_root: str | None = None
    render_root: str | None = None
    # Deprecated: ignored; frames always under cache_root/frames
    cliplet_root: str | None = None
    external_required: bool | None = None
    min_free_disk_gb: float | None = None
    auto_daily_enabled: bool | None = None
    auto_daily_hour: int | None = None
    active_customer: str | None = None
    vectorization_enabled: bool | None = None
    onboarded: bool | None = None


class CustomerCreate(BaseModel):
    name: str
    library_root: str
    output_root: str
    library_roots: list[str] = Field(default_factory=list)
    keyword_pack_path: str | None = None


class CustomerUpdate(BaseModel):
    library_root: str | None = None
    library_roots: list[str] | None = None
    output_root: str | None = None
    keyword_pack_path: str | None = None
    active: bool | None = None


class CustomerActivate(BaseModel):
    name: str


class DryRunRequest(BaseModel):
    template_name: str = "default-vertical"
    theme: str = "default"
    category: str = "default"
    customer_name: str | None = None
    seed: int | None = None


def _active_scope(session, settings: AppSettings | None = None):
    settings = settings or load_settings()
    customer = require_active_customer(session, settings)
    scoped = settings_with_customer_paths(settings, customer)
    return settings, customer, scoped


def _resolve_job_customer(session, settings: AppSettings, customer_name: str | None):
    if customer_name:
        row = resolve_customer(session, customer_name)
        if not row:
            raise HTTPException(404, f"客户不存在: {customer_name}")
        return row
    return require_active_customer(session, settings)


@app.get("/health")
def health() -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, scoped = _active_scope(session, settings)
        ensure_layout(scoped)
        health_info = check_paths(scoped)
        # One-time setup is done once paths exist — never keep pushing the welcome wizard
        configured = bool(customer.library_root and customer.output_root)
        if configured and not settings.onboarded:
            settings.onboarded = True
            save_settings(settings)
    finally:
        session.close()
    disks = disk_report(settings)
    return {
        "status": "ok" if health_info.ok else "degraded",
        "product_name": settings.product_name,
        "engine_version": "0.4.0",
        "active_customer": settings.active_customer,
        "active_customer_id": customer.id,
        "paths": scoped.paths.model_dump(mode="json"),
        "frames_root": str(scoped.paths.frames_root()),
        "render_root": str(scoped.paths.render_root),
        "vector_store": {
            "kind": "sqlite",
            "db": str(scoped.paths.data_root / "montage.db"),
            "table": "cliplets",
            "column": "embedding_json",
            "note": "片段与向量在工作区 SQLite；抽帧在 cache/frames；渲染中间文件在 render_root（均不同步）",
        },
        "path_health": health_info.__dict__,
        "disk": disks,
        "auto_daily_enabled": settings.auto_daily_enabled,
        "auto_daily_hour": settings.auto_daily_hour,
        "vectorization_enabled": settings.vectorization_enabled,
        "vectorization_enabled_at": settings.vectorization_enabled_at,
        "vectorization_mode": getattr(settings, "vectorization_mode", "incremental"),
        "onboarded": settings.onboarded,
        "setup_complete": bool(customer.library_root and customer.output_root),
        "worker_running": worker.is_alive(),
        "scheduler_running": scheduler.is_alive(),
        "watcher_running": bool(watcher and watcher.is_alive()),
    }


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    return load_settings().model_dump(mode="json")


@app.put("/settings")
def update_settings(body: SettingsUpdate) -> dict[str, Any]:
    global watcher
    settings = load_settings()
    paths = settings.paths.model_dump()
    if body.library_root is not None:
        paths["library_root"] = body.library_root
    if body.library_roots is not None:
        paths["library_roots"] = body.library_roots
    if body.output_root is not None:
        paths["output_root"] = body.output_root
    if body.cache_root is not None:
        paths["cache_root"] = body.cache_root
    if body.data_root is not None:
        paths["data_root"] = body.data_root
    if body.render_root is not None:
        paths["render_root"] = body.render_root
    # cliplet_root is deprecated — clear so frames always use cache_root/frames
    if "cliplet_root" in paths:
        paths["cliplet_root"] = None
    if body.external_required is not None:
        paths["external_required"] = body.external_required
    old_data = str(settings.paths.data_root)
    settings.paths = PathConfig(**paths)
    data_root_changed = str(settings.paths.data_root) != old_data
    if body.min_free_disk_gb is not None:
        settings.min_free_disk_gb = body.min_free_disk_gb
    if body.auto_daily_enabled is not None:
        settings.auto_daily_enabled = body.auto_daily_enabled
    if body.auto_daily_hour is not None:
        if not 0 <= body.auto_daily_hour <= 23:
            raise HTTPException(400, "auto_daily_hour must be 0-23")
        settings.auto_daily_hour = body.auto_daily_hour
    if body.active_customer is not None:
        settings.active_customer = body.active_customer
    if body.onboarded is not None:
        settings.onboarded = body.onboarded
    if body.vectorization_enabled is not None:
        if body.vectorization_enabled and settings.require_manual_vectorization:
            # Allow enable only via explicit API call (this PUT counts as manual)
            settings.vectorization_enabled = True
            settings.vectorization_mode = "incremental"
            if not settings.vectorization_enabled_at:
                from datetime import datetime, timezone

                settings.vectorization_enabled_at = datetime.now(timezone.utc).isoformat()
        elif not body.vectorization_enabled:
            settings.vectorization_enabled = False
    save_settings(settings)
    ensure_layout(settings)
    if data_root_changed:
        from engine.catalog.db import reset_engine

        reset_engine()
        init_db(settings)
    if watcher:
        watcher.reload()
    return settings.model_dump(mode="json")


@app.get("/customers")
def get_customers() -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        rows = list_customers(session)
        return [customer_to_dict(c) for c in rows]
    finally:
        session.close()


@app.post("/customers")
def post_customer(body: CustomerCreate) -> dict[str, Any]:
    session = get_session()
    try:
        row = get_or_create_customer(
            session,
            body.name,
            library_root=body.library_root,
            library_roots=body.library_roots,
            output_root=body.output_root,
        )
        if body.keyword_pack_path is not None:
            row.keyword_pack_path = body.keyword_pack_path
            session.commit()
            session.refresh(row)
        return customer_to_dict(row)
    finally:
        session.close()


@app.patch("/customers/{customer_id}")
def patch_customer(customer_id: int, body: CustomerUpdate) -> dict[str, Any]:
    session = get_session()
    try:
        row = session.get(Customer, customer_id)
        if not row:
            raise HTTPException(404, "客户不存在")
        if body.library_root is not None:
            row.library_root = body.library_root
        if body.library_roots is not None:
            row.library_roots = body.library_roots
        if body.output_root is not None:
            row.output_root = body.output_root
        if body.keyword_pack_path is not None:
            row.keyword_pack_path = body.keyword_pack_path
        if body.active is not None:
            row.active = body.active
        session.commit()
        session.refresh(row)
        return customer_to_dict(row)
    finally:
        session.close()


@app.post("/customers/activate")
def activate_customer(body: CustomerActivate) -> dict[str, Any]:
    global watcher
    settings = load_settings()
    session = get_session()
    try:
        row = resolve_customer(session, body.name)
        if not row:
            raise HTTPException(404, f"客户不存在: {body.name}")
        settings.active_customer = row.name
        save_settings(settings)
        if watcher:
            watcher.reload()
        scoped = settings_with_customer_paths(settings, row)
        return {
            "active_customer": row.name,
            "active_customer_id": row.id,
            "paths": scoped.paths.model_dump(mode="json"),
        }
    finally:
        session.close()


@app.get("/assets")
def list_assets(category: str | None = None) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        q = select(Asset).where(Asset.customer_id == customer.id).order_by(Asset.id.desc())
        if category:
            q = q.where(Asset.category == category)
        rows = session.scalars(q).all()
        return [
            {
                "id": a.id,
                "uuid": a.uuid,
                "customer_id": a.customer_id,
                "category": a.category,
                "status": a.status,
                "duration_sec": a.duration_sec,
                "width": a.width,
                "height": a.height,
                "source_path": a.source_path,
            }
            for a in rows
        ]
    finally:
        session.close()


@app.post("/assets/scan")
def scan_assets(limit: int | None = 20, background: bool = True) -> dict[str, Any]:
    """Scan libraries. Default limit=20; pass limit=0 for full scan.
    By default runs in a background thread so the API stays responsive.
    """
    global watcher
    import threading

    if not watcher:
        raise HTTPException(503, "Watcher not ready")
    if _scan_state.get("running"):
        return {"status": "already_running", **{k: _scan_state.get(k) for k in ("ingested", "limit", "started_at")}}
    real_limit = None if limit == 0 else limit
    if background:
        t = threading.Thread(target=_run_scan_background, args=(real_limit,), daemon=True, name="montage-scan")
        t.start()
        return {"status": "started", "limit": real_limit, "background": True}
    count = watcher.scan_existing(limit=real_limit)
    return {"ingested": count, "limit": real_limit, "status": "completed"}


@app.get("/assets/scan/status")
def scan_status() -> dict[str, Any]:
    return dict(_scan_state)


@app.post("/assets/reconcile")
def reconcile_assets(
    limit: int = 50,
    use_vision: bool = True,
    mode: str = "incremental",
) -> dict[str, Any]:
    """Backfill cliplets/embeddings — default incremental (gaps only).

    mode=incremental (default): keep existing cliplets/embeddings; only fill gaps.
    mode=rebuild: force re-slice cliplets (destructive; not used by first-enable).
    """
    from engine.ingest.pipeline import reconcile_pending_assets

    settings = load_settings()
    if not settings.vectorization_enabled:
        raise HTTPException(
            400,
            "向量化未开启。请在速影 App「运维」中手动开启后再补齐索引。",
        )
    if mode not in ("incremental", "rebuild"):
        raise HTTPException(400, "mode 须为 incremental 或 rebuild")
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        return reconcile_pending_assets(
            session,
            settings,
            customer_id=customer.id,
            limit=limit,
            use_vision=use_vision,
            mode=mode,
        )
    finally:
        session.close()


@app.get("/assets/vectorization-status")
def vectorization_status() -> dict[str, Any]:
    """Gap report for current customer — used before/after enabling vectorization."""
    from engine.ingest.pipeline import vectorization_gap_report

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        report = vectorization_gap_report(session, customer_id=customer.id)
        report["vectorization_enabled"] = settings.vectorization_enabled
        report["vectorization_mode"] = settings.vectorization_mode
        report["customer_id"] = customer.id
        report["customer_name"] = customer.name
        return report
    finally:
        session.close()


@app.post("/vectorization/enable")
def enable_vectorization_endpoint(
    kick_reconcile: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Flip vectorization on (incremental) and return immediately.

    Gap reporting + optional reconcile run in a daemon thread so SQLite locks
    or vision work cannot freeze the App.
    """
    import threading

    from engine.config.settings import enable_vectorization

    settings = enable_vectorization()
    settings.vectorization_mode = "incremental"
    save_settings(settings)

    if kick_reconcile:

        def _bg() -> None:
            from engine.ingest.pipeline import reconcile_pending_assets, vectorization_gap_report

            s = load_settings()
            sess = get_session()
            try:
                _, customer, _ = _active_scope(sess, s)
                gaps = vectorization_gap_report(sess, customer_id=customer.id)
                if gaps.get("gap_assets", 0) > 0:
                    reconcile_pending_assets(
                        sess,
                        s,
                        customer_id=customer.id,
                        limit=limit,
                        use_vision=True,
                        mode="incremental",
                    )
            except Exception:
                import logging

                logging.getLogger("montage.api").exception("background incremental reconcile failed")
            finally:
                sess.close()

        threading.Thread(target=_bg, name="vec-enable-reconcile", daemon=True).start()

    return {
        "enabled": True,
        "mode": "incremental",
        "message": "已开启增量向量化：已有向量保留，仅补齐缺口；后台将逐步处理缺口。",
        "gaps": None,
        "reconcile_started": bool(kick_reconcile),
        "reconcile": None,
    }

@app.post("/assets/proxies/backfill")
def backfill_asset_proxies(limit: int = 500) -> dict[str, Any]:
    """Generate low-bitrate proxies for analysis on existing assets."""
    from engine.ingest.metadata import backfill_proxies

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        return backfill_proxies(session, settings, limit=limit, customer_id=customer.id)
    finally:
        session.close()


@app.post("/keywords/import")
def import_keywords(customer_name: str, path: str) -> dict[str, Any]:
    from pathlib import Path

    session = get_session()
    try:
        pack = import_keyword_pack(session, customer_name, parse_keyword_file(Path(path)))
        return {"id": pack.id, "name": pack.name, "customer_id": pack.customer_id}
    finally:
        session.close()


@app.post("/keywords/import-file")
async def import_keywords_file(customer_name: str = "default", file: UploadFile = File(...)) -> dict[str, Any]:
    import json
    import tempfile
    from pathlib import Path

    content = await file.read()
    session = get_session()
    try:
        text = content.decode("utf-8")
        if text.strip().startswith("{"):
            data = json.loads(text)
        else:
            tmp = Path(tempfile.gettempdir()) / file.filename
            tmp.write_bytes(content)
            data = parse_keyword_file(tmp)
        pack = import_keyword_pack(session, customer_name, data)
        return {"id": pack.id, "name": pack.name}
    finally:
        session.close()


@app.post("/keywords/reload-from-path")
def reload_keywords_from_path(customer_id: int | None = None) -> dict[str, Any]:
    """Reload keyword pack from Customer.keyword_pack_path."""
    from pathlib import Path

    settings = load_settings()
    session = get_session()
    try:
        if customer_id is not None:
            row = session.get(Customer, customer_id)
            if not row:
                raise HTTPException(404, "客户不存在")
        else:
            _, row, _ = _active_scope(session, settings)
        path_str = row.keyword_pack_path
        if not path_str:
            raise HTTPException(400, "未配置词池路径 keyword_pack_path")
        path = Path(path_str)
        if not path.exists():
            raise HTTPException(400, f"词池文件不存在: {path}")
        pack = import_keyword_pack(session, row.name, parse_keyword_file(path))
        return {
            "id": pack.id,
            "name": pack.name,
            "customer_id": pack.customer_id,
            "path": str(path),
            "version": pack.version,
        }
    finally:
        session.close()


@app.get("/templates")
def list_templates() -> list[dict[str, Any]]:
    session = get_session()
    try:
        from engine.jobs.queue import ensure_default_template

        ensure_default_template(session)
        rows = session.scalars(select(Template)).all()
        if not rows:
            from engine.template.engine import BUILTIN_TEMPLATES

            return [t.model_dump() for t in BUILTIN_TEMPLATES.values()]
        return [{"name": t.name, "definition": t.definition_json, "active": t.active} for t in rows]
    finally:
        session.close()


@app.post("/dry-run")
def dry_run(body: DryRunRequest) -> dict[str, Any]:
    import random

    settings = load_settings()
    session = get_session()
    try:
        from engine.jobs.queue import ensure_default_template
        from engine.template.engine import BUILTIN_TEMPLATES, template_for_theme

        ensure_default_template(session)
        customer = _resolve_job_customer(session, settings, body.customer_name)
        from engine.catalog.industry_pack import pack_id_for_customer

        pack_id = pack_id_for_customer(customer.name, customer.profile_json)
        tpl_name = body.template_name or "default-vertical"
        if tpl_name == "default-vertical":
            mapped = template_for_theme(body.theme, pack_id=pack_id)
            if mapped != tpl_name:
                tpl_name = mapped
        tpl_row = session.scalar(select(Template).where(Template.name == tpl_name))
        builtin = BUILTIN_TEMPLATES.get(tpl_name, DEFAULT_TEMPLATE)
        template = TemplateDefinition(
            **(tpl_row.definition_json if tpl_row else builtin.model_dump())
        )
        seed = body.seed if body.seed is not None else random.randint(1, 2_000_000_000)
        plan = build_plan(
            session,
            template,
            customer_name=customer.name,
            theme=body.theme,
            category=body.category,
            seed=seed,
        )
        out = plan_to_dict(plan)
        out["resolved_template"] = tpl_name
        return out
    finally:
        session.close()


@app.get("/jobs")
def list_jobs() -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        rows = session.scalars(
            select(Job).where(Job.customer_id == customer.id).order_by(Job.id.desc())
        ).all()
        return [
            {
                "id": j.id,
                "customer_id": j.customer_id,
                "status": j.status,
                "mode": j.mode,
                "target_count": j.target_count,
                "target_duration_sec": j.target_duration_sec,
                "produced_count": j.produced_count,
                "template_name": j.template_name,
                "theme": j.theme,
                "category": j.category,
                "consecutive_failures": j.consecutive_failures,
            }
            for j in rows
        ]
    finally:
        session.close()


@app.post("/jobs")
def post_job(body: CreateJobRequest) -> dict[str, Any]:
    try:
        assert_production_ready()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    settings = load_settings()
    session = get_session()
    try:
        if body.customer_name is None:
            body.customer_name = settings.active_customer
        customer = _resolve_job_customer(session, settings, body.customer_name)
        body.customer_name = customer.name
        job = create_job(session, body, customer_id=customer.id)
        return {"id": job.id, "status": job.status, "customer_id": job.customer_id}
    finally:
        session.close()


@app.post("/jobs/{job_id}/pause")
def post_pause(job_id: int) -> dict[str, Any]:
    session = get_session()
    try:
        job = pause_job(session, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return {"id": job.id, "status": job.status}
    finally:
        session.close()


@app.post("/jobs/{job_id}/resume")
def post_resume(job_id: int) -> dict[str, Any]:
    session = get_session()
    try:
        job = resume_job(session, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return {"id": job.id, "status": job.status}
    finally:
        session.close()


@app.get("/calendar")
def calendar_list(from_day: str | None = None, to_day: str | None = None) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        return [entry_to_dict(e) for e in list_entries(session, customer.id, from_day, to_day)]
    finally:
        session.close()


@app.get("/calendar/today")
def calendar_today(day: str | None = None) -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        row = today_plan(session, customer.id, day)
        if not row:
            raise HTTPException(404, "今日无日历计划（或已停用）")
        return entry_to_dict(row)
    finally:
        session.close()


@app.put("/calendar/{day}")
def calendar_upsert(day: str, body: CalendarEntryIn) -> dict[str, Any]:
    settings = load_settings()
    body.day = day
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        body.customer_name = customer.name
        row = upsert_entry(session, customer.id, body)
        return entry_to_dict(row)
    finally:
        session.close()


@app.delete("/calendar/{day}")
def calendar_delete(day: str) -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        ok = delete_entry(session, customer.id, day)
        if not ok:
            raise HTTPException(404, "calendar entry not found")
        return {"deleted": day}
    finally:
        session.close()


@app.post("/jobs/from-calendar")
def post_job_from_calendar(day: str | None = None) -> dict[str, Any]:
    """Create a count job using today's (or given day's) calendar quota/theme."""
    try:
        assert_production_ready()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    session = get_session()
    try:
        settings = load_settings()
        _, customer, _ = _active_scope(session, settings)
        row = today_plan(session, customer.id, day)
        if not row:
            raise HTTPException(404, "无可用日历计划")
        from engine.catalog.industry_pack import pack_id_for_customer
        from engine.template.engine import template_for_theme

        # Prefer calendar template; if still default, bind by theme (Sprint B)
        pack_id = pack_id_for_customer(customer.name, customer.profile_json)
        tpl_name = row.template_name or "default-vertical"
        if tpl_name == "default-vertical":
            mapped = template_for_theme(row.theme, pack_id=pack_id)
            if mapped != tpl_name:
                tpl_name = mapped
        req = CreateJobRequest(
            mode="count",
            target_count=row.quota,
            template_name=tpl_name,
            theme=row.theme,
            category=row.category,
            customer_name=row.customer_name,
        )
        job = create_job(session, req, customer_id=customer.id)
        return {
            "id": job.id,
            "status": job.status,
            "calendar_day": row.day,
            "theme": row.theme,
            "template_name": tpl_name,
            "quota": row.quota,
        }
    finally:
        session.close()


@app.get("/outputs")
def list_outputs(state: str | None = None) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        q = (
            select(RenderOutput)
            .join(Job, RenderOutput.job_id == Job.id)
            .where(Job.customer_id == customer.id)
            .order_by(RenderOutput.id.desc())
        )
        if state:
            q = q.where(RenderOutput.state == state)
        rows = session.scalars(q).all()
        result = []
        for r in rows:
            title = ""
            covers: list[str] = []
            if isinstance(r.qc_json, dict):
                covers = list(r.qc_json.get("covers") or [])
            if r.sidecar_path:
                try:
                    import json
                    from pathlib import Path

                    data = json.loads(Path(r.sidecar_path).read_text(encoding="utf-8"))
                    title = str(data.get("title") or "")
                    if not covers:
                        covers = list(data.get("covers") or [])
                except Exception:
                    pass
            result.append(
                {
                    "id": r.id,
                    "job_id": r.job_id,
                    "state": r.state,
                    "seed": r.seed,
                    "title": title,
                    "output_path": r.output_path,
                    "sidecar_path": r.sidecar_path,
                    "covers": covers,
                    "qc": r.qc_json,
                }
            )
        return result
    finally:
        session.close()


@app.post("/outputs/{output_id}/publish-pack")
def export_output_publish_pack(output_id: int) -> dict[str, Any]:
    """G3: export ready output into a publish_pack folder beside the mp4."""
    from pathlib import Path

    from engine.catalog.keyword_pack import get_active_pack
    from engine.pack.publish import export_publish_pack

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        out = session.get(RenderOutput, output_id)
        if not out:
            raise HTTPException(404, "output not found")
        # scope: output must belong to active customer via job
        if out.job_id:
            job = session.get(Job, out.job_id)
            if job and job.customer_id and job.customer_id != customer.id:
                raise HTTPException(403, "output 不属于当前客户")
        pack_row = get_active_pack(session, customer.name)
        pack_data = pack_row.data_json if pack_row else None
        try:
            manifest = export_publish_pack(
                output_path=Path(out.output_path),
                sidecar_path=Path(out.sidecar_path) if out.sidecar_path else None,
                profile=customer.profile_json if isinstance(customer.profile_json, dict) else {},
                pack_data=pack_data,
            )
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        return {"ok": True, "output_id": output_id, "manifest": manifest}
    finally:
        session.close()


class NarrationPreviewRequest(BaseModel):
    """G4: script → mock/say TTS → real durations → template slot budget."""

    script: str = Field(..., min_length=1)
    lang: str = "zh"
    provider: str = "mock"  # mock | say
    voice: str | None = None
    template_name: str = "default-vertical"


@app.post("/pack/narration/preview")
def pack_narration_preview(body: NarrationPreviewRequest) -> dict[str, Any]:
    """Offline-friendly narration preview (default provider=mock)."""
    import uuid
    from pathlib import Path

    from engine.pack.narration_plan import duration_budget_from_narration, template_from_narration
    from engine.pack.tts import synthesize_script
    from engine.template.engine import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE

    settings = load_settings()
    provider = (body.provider or getattr(settings, "tts_provider", "mock") or "mock").lower()
    if provider not in ("mock", "say"):
        raise HTTPException(400, "provider 仅支持 mock 或 say")
    lang = (body.lang or "zh").lower()
    out_dir = Path(settings.paths.cache_root) / "tts" / f"preview_{uuid.uuid4().hex[:12]}"
    try:
        narr = synthesize_script(
            body.script,
            out_dir,
            lang=lang,
            provider=provider,  # type: ignore[arg-type]
            voice=body.voice or (settings.tts_voice or None) or None,
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e

    base = BUILTIN_TEMPLATES.get(body.template_name, DEFAULT_TEMPLATE)
    timed = template_from_narration(base, narr)
    budget = duration_budget_from_narration(narr)
    return {
        "ok": True,
        "provider": narr.provider,
        "lang": narr.lang,
        "total_duration_sec": narr.total_duration_sec,
        "duration_budget": budget,
        "manifest_path": narr.manifest_path,
        "out_dir": narr.out_dir,
        "segments": [
            {
                "index": s.index,
                "text": s.text,
                "duration_sec": s.duration_sec,
                "audio_path": s.audio_path,
            }
            for s in narr.segments
        ],
        "template": {
            "name": timed.name,
            "target_duration": timed.target_duration,
            "duration_from_tts": timed.duration_from_tts,
            "slots": [s.model_dump() for s in timed.slots],
        },
    }


class ReachEnqueueRequest(BaseModel):
    """G5: enqueue a human-in-the-loop publish task (no auto-post)."""

    platform: str
    title: str = ""
    body: str = ""
    video_path: str = ""
    pack_dir: str | None = None
    output_id: int | None = None
    note: str = ""


class ReachStatusRequest(BaseModel):
    status: str
    note: str | None = None
    error: str | None = None


@app.post("/reach/queue")
def reach_enqueue(body: ReachEnqueueRequest) -> dict[str, Any]:
    from engine.reach.limits import assert_can_enqueue
    from engine.reach.queue import enqueue, item_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        try:
            assert_can_enqueue(
                session,
                customer_id=customer.id,
                daily_quota=int(getattr(settings, "reach_daily_quota", 5) or 5),
                fail_threshold=int(getattr(settings, "reach_fail_threshold", 3) or 3),
                platform=body.platform,
            )
            item = enqueue(
                session,
                customer_id=customer.id,
                platform=body.platform,
                title=body.title,
                body=body.body,
                video_path=body.video_path,
                pack_dir=body.pack_dir,
                output_id=body.output_id,
                note=body.note,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "item": item_to_dict(item), "auto_publish": False}
    finally:
        session.close()


@app.get("/reach/queue")
def reach_list(status: str | None = None, platform: str | None = None, limit: int = 100) -> dict[str, Any]:
    from engine.reach.queue import item_to_dict, list_items

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        rows = list_items(
            session,
            customer_id=customer.id,
            status=status,
            platform=platform,
            limit=limit,
        )
        return {
            "ok": True,
            "items": [item_to_dict(r) for r in rows],
            "human_in_loop": True,
            "auto_publish": False,
        }
    finally:
        session.close()


@app.get("/reach/queue/{item_id}")
def reach_get(item_id: int) -> dict[str, Any]:
    from engine.reach.queue import get_item, item_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        item = get_item(session, item_id, customer_id=customer.id)
        if not item:
            raise HTTPException(404, "reach item not found")
        return {"ok": True, "item": item_to_dict(item)}
    finally:
        session.close()


@app.post("/reach/queue/{item_id}/status")
def reach_set_status(item_id: int, body: ReachStatusRequest) -> dict[str, Any]:
    from engine.reach.queue import get_item, item_to_dict, set_status

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        item = get_item(session, item_id, customer_id=customer.id)
        if not item:
            raise HTTPException(404, "reach item not found")
        try:
            item = set_status(session, item, body.status, note=body.note, error=body.error)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "item": item_to_dict(item)}
    finally:
        session.close()


class ReachFromPackRequest(BaseModel):
    pack_dir: str
    platforms: list[str] | None = None
    locale: str = "zh"
    output_id: int | None = None
    mark_ready: bool = True


@app.post("/reach/queue/from-pack")
def reach_enqueue_from_pack(body: ReachFromPackRequest) -> dict[str, Any]:
    """G5: prefill queue rows from a publish_pack directory (human still clicks publish)."""
    from pathlib import Path

    from engine.reach.limits import assert_can_enqueue
    from engine.reach.prefill import enqueue_from_pack
    from engine.reach.queue import item_to_dict

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        quota = int(getattr(settings, "reach_daily_quota", 5) or 5)
        fail_th = int(getattr(settings, "reach_fail_threshold", 3) or 3)
        try:
            # Check once before batch; still human-in-loop
            assert_can_enqueue(
                session,
                customer_id=customer.id,
                daily_quota=quota,
                fail_threshold=fail_th,
            )
            items = enqueue_from_pack(
                session,
                customer_id=customer.id,
                pack_dir=Path(body.pack_dir),
                platforms=body.platforms,
                locale=body.locale,
                output_id=body.output_id,
                mark_ready=body.mark_ready,
            )
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {
            "ok": True,
            "count": len(items),
            "items": [item_to_dict(i) for i in items],
            "auto_publish": False,
            "human_in_loop": True,
        }
    finally:
        session.close()


@app.get("/reach/quota")
def reach_quota(platform: str | None = None) -> dict[str, Any]:
    from engine.reach.limits import quota_status

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        st = quota_status(
            session,
            customer_id=customer.id,
            daily_quota=int(getattr(settings, "reach_daily_quota", 5) or 5),
            fail_threshold=int(getattr(settings, "reach_fail_threshold", 3) or 3),
            platform=platform,
        )
        return {"ok": True, **st}
    finally:
        session.close()


class ReachOpenRequest(BaseModel):
    dry_run: bool = False


@app.post("/reach/queue/{item_id}/open")
def reach_open_official(item_id: int, body: ReachOpenRequest | None = None) -> dict[str, Any]:
    """Open official creator URL + write paste card. Never auto-publishes."""
    from engine.reach.browser import prepare_and_open
    from engine.reach.queue import get_item, item_to_dict

    body = body or ReachOpenRequest()
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        item = get_item(session, item_id, customer_id=customer.id)
        if not item:
            raise HTTPException(404, "reach item not found")
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        try:
            result = prepare_and_open(
                session,
                item,
                profile=profile,
                dry_run=bool(body.dry_run),
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        result["item"] = item_to_dict(item)
        return result
    finally:
        session.close()


@app.get("/reach/platforms")
def reach_platforms() -> dict[str, Any]:
    from engine.reach.browser import OFFICIAL_ENTRY

    return {
        "ok": True,
        "platforms": [
            {"id": k, "label": v["label"], "url": v["url"]} for k, v in OFFICIAL_ENTRY.items()
        ],
        "auto_publish": False,
        "human_in_loop": True,
    }


@app.get("/reach/inbox")
def reach_inbox(limit: int = 50) -> dict[str, Any]:
    """G5 message hub: local unread summaries + official deep links (no auto-reply)."""
    from engine.reach.inbox import build_inbox

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        return build_inbox(session, customer_id=customer.id, limit=limit)
    finally:
        session.close()


@app.get("/logs/events")
def list_events(limit: int = 200) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        rows = session.scalars(
            select(JobEvent)
            .join(Job, JobEvent.job_id == Job.id)
            .where(Job.customer_id == customer.id)
            .order_by(JobEvent.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": e.id,
                "job_id": e.job_id,
                "level": e.level,
                "message": e.message,
                "payload": e.payload_json,
                "created_at": e.created_at.isoformat(),
            }
            for e in rows
        ]
    finally:
        session.close()


@app.post("/export/events.csv")
def export_events() -> dict[str, str]:
    settings = load_settings()
    out = settings.paths.data_root / "exports" / "job_events.csv"
    session = get_session()
    try:
        export_job_events_csv(session, out)
        return {"path": str(out)}
    finally:
        session.close()


@app.post("/export/renders.csv")
def export_renders() -> dict[str, str]:
    settings = load_settings()
    out = settings.paths.data_root / "exports" / "render_outputs.csv"
    session = get_session()
    try:
        export_renders_csv(session, out)
        return {"path": str(out)}
    finally:
        session.close()


@app.post("/index/cliplets")
def build_cliplets(force: bool = False, limit: int = 50, use_vision: bool = True) -> dict[str, Any]:
    """Create cliplets for ready assets then embed them."""
    from engine.catalog.vector_index import index_cliplet

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        assets = list(
            session.scalars(
                select(Asset).where(Asset.status == "ready", Asset.customer_id == customer.id).limit(limit)
            ).all()
        )
        created = 0
        for asset in assets:
            rows = create_cliplets_for_asset(session, asset, force=force, use_vision=use_vision)
            created += len(rows)
            for row in rows:
                if row.embedding_json is None:
                    index_cliplet(session, row)
        pending = index_pending(session, limit=500)
        cliplet_rows = list(
            session.scalars(
                select(Cliplet).join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer.id)
            ).all()
        )
        count = len(cliplet_rows)
        return {"assets": len(assets), "cliplets_created": created, "indexed": pending, "cliplet_total": count, "ok": count > 0}
    finally:
        session.close()


@app.post("/index/captions")
def rebuild_captions(limit: int = 500, use_vision: bool = True) -> dict[str, Any]:
    """Re-describe existing cliplets with vision model and re-embed."""
    session = get_session()
    try:
        result = recaption_existing_cliplets(session, limit=limit, use_vision=use_vision)
        return result
    finally:
        session.close()


@app.get("/cliplets")
def list_cliplets(limit: int = 50) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        rows = session.scalars(
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(Asset.customer_id == customer.id)
            .order_by(Cliplet.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": c.id,
                "asset_uuid": c.asset_uuid,
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "duration_sec": c.duration_sec,
                "category": c.category,
                "theme": c.theme,
                "theme_score": c.theme_score,
                "description": c.description,
                "indexed": c.embedding_json is not None,
            }
            for c in rows
        ]
    finally:
        session.close()


@app.post("/cliplets/themes/backfill")
def backfill_cliplet_themes_api(limit: int = 2000, force: bool = False) -> dict[str, Any]:
    """Tag existing cliplets with content themes (配送/仓配/门店/…)."""
    from engine.catalog.theme_tags import backfill_cliplet_themes

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        return backfill_cliplet_themes(
            session, customer_id=customer.id, limit=limit, force=force
        )
    finally:
        session.close()


@app.post("/cliplets/quality/backfill")
def backfill_cliplet_quality_api(
    limit: int = 2000,
    force: bool = False,
    only_unscored: bool = True,
) -> dict[str, Any]:
    """Sprint B3: recompute visual quality scores (default: only legacy score=1.0)."""
    from engine.ingest.quality import backfill_cliplet_quality

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        return backfill_cliplet_quality(
            session,
            customer_id=customer.id,
            limit=limit,
            force=force,
            only_unscored=only_unscored,
        )
    finally:
        session.close()


@app.get("/search")
def semantic_search(q: str, category: str | None = None, top_k: int = 10) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        hits = search_cliplets(session, q, category=category, top_k=top_k, customer_id=customer.id)
        return [
            {
                "id": c.id,
                "score": round(score, 4),
                "asset_uuid": c.asset_uuid,
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "description": c.description,
                "category": c.category,
            }
            for c, score in hits
        ]
    finally:
        session.close()


@app.get("/reports/summary")
def report_summary() -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        job_ids = [
            j.id for j in session.scalars(select(Job).where(Job.customer_id == customer.id)).all()
        ]
        if job_ids:
            outputs = list(session.scalars(select(RenderOutput).where(RenderOutput.job_id.in_(job_ids))).all())
        else:
            outputs = []
        assets = list(session.scalars(select(Asset).where(Asset.customer_id == customer.id)).all())
        cliplets = list(
            session.scalars(
                select(Cliplet).join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer.id)
            ).all()
        )
        usages = list(
            session.scalars(
                select(KeywordUsage)
                .where(KeywordUsage.customer_id == customer.id)
                .order_by(KeywordUsage.id.desc())
                .limit(500)
            ).all()
        )
        output_ids = {o.id for o in outputs}
        if output_ids:
            reviews = list(
                session.scalars(select(ReviewItem).where(ReviewItem.render_output_id.in_(output_ids))).all()
            )
        else:
            reviews = []

        by_state: dict[str, int] = {}
        for o in outputs:
            by_state[o.state] = by_state.get(o.state, 0) + 1
        total_out = len(outputs)
        ready = by_state.get("ready", 0)
        failed = by_state.get("failed", 0)
        failure_rate = round(failed / total_out, 4) if total_out else 0.0

        # duplicate asset rate across ready sidecars
        asset_hits: dict[str, int] = {}
        for o in outputs:
            if o.state != "ready" or not o.sidecar_path:
                continue
            try:
                import json
                from pathlib import Path

                data = json.loads(Path(o.sidecar_path).read_text(encoding="utf-8"))
                for c in data.get("clips", []):
                    uid = c.get("asset_uuid")
                    if uid:
                        asset_hits[uid] = asset_hits.get(uid, 0) + 1
            except Exception:
                continue
        reused = sum(1 for n in asset_hits.values() if n > 1)
        dup_rate = round(reused / len(asset_hits), 4) if asset_hits else 0.0

        theme_dist: dict[str, int] = {}
        keyword_dist: dict[str, int] = {}
        for u in usages:
            theme_dist[u.theme] = theme_dist.get(u.theme, 0) + 1
            keyword_dist[u.keyword] = keyword_dist.get(u.keyword, 0) + 1

        review_dist: dict[str, int] = {}
        for r in reviews:
            review_dist[r.status] = review_dist.get(r.status, 0) + 1

        top_keywords = sorted(keyword_dist.items(), key=lambda x: -x[1])[:15]
        return {
            "assets": len(assets),
            "cliplets": len(cliplets),
            "cliplets_indexed": sum(1 for c in cliplets if c.embedding_json),
            "outputs": by_state,
            "ready": ready,
            "failed": failed,
            "ready_rate": round(ready / total_out, 4) if total_out else 0.0,
            "failure_rate": failure_rate,
            "duplicate_asset_rate": dup_rate,
            "theme_dist": theme_dist,
            "theme_distribution": theme_dist,
            "top_keywords": top_keywords,
            "keyword_top": [{"keyword": k, "count": n} for k, n in top_keywords],
            "reviews": review_dist,
        }
    finally:
        session.close()


@app.get("/reports/quality")
def report_quality() -> dict[str, Any]:
    """Sprint C quality daily: ready rate, reject reason top, hook/title repeat hints."""
    from collections import Counter
    import json
    from pathlib import Path

    from engine.catalog.review_learn import REJECT_REASONS, parse_reject_reason

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        job_ids = [j.id for j in session.scalars(select(Job).where(Job.customer_id == customer.id)).all()]
        outputs = (
            list(session.scalars(select(RenderOutput).where(RenderOutput.job_id.in_(job_ids))).all())
            if job_ids
            else []
        )
        by_state: dict[str, int] = {}
        for o in outputs:
            by_state[o.state] = by_state.get(o.state, 0) + 1
        total = len(outputs)
        ready = by_state.get("ready", 0)
        output_ids = {o.id for o in outputs}
        reviews = (
            list(session.scalars(select(ReviewItem).where(ReviewItem.render_output_id.in_(output_ids))).all())
            if output_ids
            else []
        )
        reason_counter: Counter[str] = Counter()
        for r in reviews:
            if r.status != "rejected":
                continue
            code = parse_reject_reason(r.note or "")
            reason_counter[code] += 1
        titles: Counter[str] = Counter()
        hooks: Counter[str] = Counter()
        for o in outputs:
            if o.state != "ready" or not o.sidecar_path:
                continue
            try:
                data = json.loads(Path(o.sidecar_path).read_text(encoding="utf-8"))
            except Exception:
                continue
            title = (data.get("title") or "").strip()
            if title:
                titles[title] += 1
                hooks[title.split("\n")[0].strip()] += 1
        reject_top = [
            {"code": c, "label": REJECT_REASONS.get(c, c), "count": n}
            for c, n in reason_counter.most_common(10)
        ]
        return {
            "customer_id": customer.id,
            "customer_name": customer.name,
            "outputs": by_state,
            "ready_rate": round(ready / total, 4) if total else 0.0,
            "reject_reason_top": reject_top,
            "hook_repeat_top": [{"hook": h, "count": n} for h, n in hooks.most_common(10) if n > 1],
            "title_repeat_top": [{"title": t, "count": n} for t, n in titles.most_common(10) if n > 1],
            "reason_codes": REJECT_REASONS,
        }
    finally:
        session.close()


@app.get("/reports/golden")
def report_golden() -> dict[str, Any]:
    """L0 golden sample scores (must be in DB, not only docs)."""
    from engine.catalog.golden import list_golden

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        rows = list_golden(session, customer.id)
        signed = [r for r in rows if r.get("signed") and r.get("look_score") is not None]
        return {
            "customer_id": customer.id,
            "customer_name": customer.name,
            "samples": rows,
            "signed_count": len(signed),
            "complete": len(signed) >= 2 and all(
                (r.get("look_score") or 0) >= 4 and r.get("shippable") for r in signed if r.get("code") in {"G1", "G2"}
            ),
        }
    finally:
        session.close()


@app.post("/reports/golden/seed")
def seed_golden() -> dict[str, Any]:
    """Upsert frozen G1/G2 scores from GOLDEN_SAMPLES baseline into authority DB."""
    from engine.catalog.db import init_db
    from engine.catalog.golden import list_golden, upsert_frozen_golden

    settings = load_settings()
    init_db(settings)
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        seeded = upsert_frozen_golden(session, settings, customer_id=customer.id)
        return {
            "customer_id": customer.id,
            "customer_name": customer.name,
            "seeded": seeded,
            "samples": list_golden(session, customer.id),
        }
    finally:
        session.close()


@app.get("/reviews")
def list_reviews(limit: int = 100) -> list[dict[str, Any]]:
    session = get_session()
    try:
        rows = session.scalars(select(ReviewItem).order_by(ReviewItem.id.desc()).limit(limit)).all()
        return [
            {
                "id": r.id,
                "render_output_id": r.render_output_id,
                "status": r.status,
                "note": r.note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()


@app.get("/review/reasons")
def review_reasons() -> dict[str, Any]:
    from engine.catalog.review_learn import REJECT_REASONS

    return {"reasons": [{"code": k, "label": v} for k, v in REJECT_REASONS.items()]}


@app.post("/review/{output_id}")
def review_output(
    output_id: int,
    status: str = "approved",
    note: str = "",
    reason: str = "",
    downweight: bool = True,
    rerender: bool = False,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    from engine.catalog.review_learn import (
        create_rerender_job,
        downweight_cliplets_for_reject,
        parse_reject_reason,
    )

    if status not in {"approved", "rejected", "pending"}:
        raise HTTPException(400, "status must be approved/rejected/pending")
    session = get_session()
    try:
        out = session.get(RenderOutput, output_id)
        if not out:
            raise HTTPException(404, "output not found")
        reason_code = parse_reject_reason(note, reason or None) if status == "rejected" else ""
        note_store = note
        if status == "rejected" and reason_code and f"[{reason_code}]" not in (note or ""):
            note_store = f"[{reason_code}] {note}".strip()
        item = ReviewItem(
            render_output_id=output_id,
            status=status,
            note=note_store,
            decided_at=datetime.now(timezone.utc),
        )
        session.add(item)
        learn: dict[str, Any] = {}
        rerender_job: dict[str, Any] | None = None
        if status == "rejected":
            out.state = "review"
            session.commit()
            if downweight:
                learn = downweight_cliplets_for_reject(session, out, reason=reason_code or "other")
            if rerender:
                try:
                    job = create_rerender_job(session, out, reason=reason_code or "other")
                    rerender_job = {"id": job.id, "status": job.status}
                except ValueError as e:
                    raise HTTPException(409, str(e)) from e
        else:
            session.commit()
        return {
            "id": item.id,
            "status": item.status,
            "output_id": output_id,
            "state": out.state,
            "reason": reason_code or None,
            "learn": learn,
            "rerender_job": rerender_job,
        }
    finally:
        session.close()


@app.post("/review/{output_id}/rerender")
def review_rerender(output_id: int, reason: str = "") -> dict[str, Any]:
    """One-click re-render from an existing (usually rejected) output."""
    from engine.catalog.review_learn import create_rerender_job, parse_reject_reason

    try:
        assert_production_ready()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    session = get_session()
    try:
        out = session.get(RenderOutput, output_id)
        if not out:
            raise HTTPException(404, "output not found")
        # Prefer reason from latest reject note
        note = ""
        last = session.scalar(
            select(ReviewItem)
            .where(ReviewItem.render_output_id == output_id, ReviewItem.status == "rejected")
            .order_by(ReviewItem.id.desc())
            .limit(1)
        )
        if last:
            note = last.note or ""
        reason_code = parse_reject_reason(note, reason or None)
        try:
            job = create_rerender_job(session, out, reason=reason_code)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {
            "output_id": output_id,
            "reason": reason_code,
            "job": {"id": job.id, "status": job.status, "theme": job.theme, "category": job.category},
        }
    finally:
        session.close()


@app.get("/ops/disk")
def ops_disk() -> dict[str, Any]:
    return disk_report()


@app.post("/ops/cache/clean")
def ops_cache_clean(older_than_hours: float = 24.0) -> dict[str, Any]:
    return clean_cache(older_than_hours=older_than_hours)


@app.get("/ops/scheduler")
def ops_scheduler_status() -> dict[str, Any]:
    settings = load_settings()
    return {
        "auto_daily_enabled": settings.auto_daily_enabled,
        "auto_daily_hour": settings.auto_daily_hour,
        "scheduler_running": scheduler._thread.is_alive() if scheduler._thread else False,
        "state": load_scheduler_state(settings),
    }


@app.post("/ops/scheduler/run-now")
def ops_scheduler_run_now(force: bool = False) -> dict[str, Any]:
    """Manually trigger daily calendar job logic (ignores hour when force=true)."""
    settings = load_settings()
    if force:
        # temporarily pretend current hour matches
        settings.auto_daily_enabled = True
        from datetime import datetime

        settings.auto_daily_hour = datetime.now().astimezone().hour
        # clear last_auto_day so it can fire
        from engine.ops.maintenance import save_scheduler_state

        st = load_scheduler_state(settings)
        st["last_auto_day"] = None
        save_scheduler_state(settings, st)
    result = maybe_run_daily_job(settings)
    if not result:
        return {"triggered": False, "reason": "条件未满足（未开启/未到点/今日已跑）"}
    return {"triggered": True, **result}


@app.get("/ops/services")
def ops_services_status() -> dict[str, Any]:
    settings = load_settings()
    from engine.ops.scan_state import scan_state

    return {
        "product_name": settings.product_name,
        "watcher": bool(watcher and watcher.is_alive()),
        "worker": worker.is_alive(),
        "scheduler": scheduler.is_alive(),
        "auto_daily_enabled": settings.auto_daily_enabled,
        "auto_daily_hour": settings.auto_daily_hour,
        "vectorization_enabled": settings.vectorization_enabled,
        "vectorization_enabled_at": settings.vectorization_enabled_at,
        "onboarded": settings.onboarded,
        "scan": dict(scan_state),
    }


@app.post("/ops/services/{name}/{action}")
def ops_service_control(name: str, action: str) -> dict[str, Any]:
    """Start/stop watcher | worker | scheduler."""
    global watcher
    name = name.lower().strip()
    action = action.lower().strip()
    if action not in ("start", "stop"):
        raise HTTPException(400, "action 必须是 start 或 stop")
    if name == "watcher":
        if not watcher:
            raise HTTPException(503, "Watcher 未初始化")
        if action == "start":
            watcher.start()
        else:
            watcher.stop()
        return {"name": name, "action": action, "running": watcher.is_alive()}
    if name == "worker":
        if action == "start":
            worker.start()
        else:
            worker.stop()
        return {"name": name, "action": action, "running": worker.is_alive()}
    if name == "scheduler":
        if action == "start":
            scheduler.start()
        else:
            scheduler.stop()
        return {"name": name, "action": action, "running": scheduler.is_alive()}
    raise HTTPException(404, f"未知服务: {name}")


class ZSpaceEnsureCustomer(BaseModel):
    name: str
    register: bool = True


class ZSpaceBind(BaseModel):
    username: str
    nas_id: str
    nas_name: str = ""


@app.get("/ops/zspace-sync/status")
def ops_zspace_sync_status() -> dict[str, Any]:
    from engine.ops.suying_sync import service_status

    return service_status()


@app.get("/ops/zspace-sync/accounts")
def ops_zspace_sync_accounts() -> dict[str, Any]:
    from engine.ops.suying_sync import check_zspace_bind

    return check_zspace_bind()


@app.post("/ops/zspace-sync/bind")
def ops_zspace_sync_bind(body: ZSpaceBind) -> dict[str, Any]:
    from engine.ops.suying_sync import bind_zspace_account

    try:
        return bind_zspace_account(
            username=body.username,
            nas_id=body.nas_id,
            nas_name=body.nas_name,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/ops/zspace-sync/install")
def ops_zspace_sync_install() -> dict[str, Any]:
    from engine.ops.suying_sync import install_service

    try:
        return install_service()
    except Exception as e:
        raise HTTPException(500, str(e)) from e


@app.post("/ops/zspace-sync/ensure-customer")
def ops_zspace_sync_ensure_customer(body: ZSpaceEnsureCustomer) -> dict[str, Any]:
    """Create 速影客户/<name>/{01-片库,02-成片,03-词池} and register sync map."""
    from engine.config.settings import PathConfig, load_settings, save_settings
    from engine.ops.suying_sync import ensure_customer_dirs

    try:
        result = ensure_customer_dirs(body.name, register=body.register)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    paths = result["paths"]
    settings = load_settings()
    dumped = settings.paths.model_dump()
    dumped.update(
        {
            "data_root": paths["data_root"],
            "cache_root": paths["cache_root"],
            "render_root": paths["render_root"],
            "external_required": True,
        }
    )
    settings.paths = PathConfig(**dumped)
    save_settings(settings)
    return result
