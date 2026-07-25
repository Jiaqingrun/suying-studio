from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
from engine.ops.cursor_key import (
    apply_cursor_api_key_env,
    public_cursor_key_fields,
    redact_settings_payload,
    verify_cursor_api_key,
)
from engine.ops.cursor_agent import cancel_session as cursor_cancel_session
from engine.ops.cursor_agent import chat as cursor_chat
from engine.ops.cursor_agent import iter_chat_events as cursor_iter_chat_events
from engine.ops.cursor_agent import list_session_summary as cursor_session_summary
from engine.ops.cursor_agent import reset_session as cursor_reset_session
from engine.ops.cursor_agent import sse_bytes as cursor_sse_bytes
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
            "attempted": 0,
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
# Note: allow_origins=["*"] cannot be combined with allow_credentials=True (browser rejects).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length", "Content-Type"],
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
    # None = leave unchanged; "" = clear; non-empty = set
    cursor_api_key: str | None = None


class CursorKeyBody(BaseModel):
    """Optional key for verify-before-save; omit to use stored key."""

    cursor_api_key: str | None = None


class CursorChatBody(BaseModel):
    message: str
    session_id: str = "default"
    customer: str | None = None


class CursorSessionBody(BaseModel):
    session_id: str = "default"


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
    brand: dict[str, Any] | None = None
    expression: dict[str, Any] | None = None


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
        **public_cursor_key_fields(settings.cursor_api_key),
    }


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    return redact_settings_payload(load_settings().model_dump(mode="json"))


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
    if body.cursor_api_key is not None:
        settings.cursor_api_key = body.cursor_api_key.strip()
    save_settings(settings)
    ensure_layout(settings)
    if data_root_changed:
        from engine.catalog.db import reset_engine

        reset_engine()
        init_db(settings)
    if watcher:
        watcher.reload()
    return redact_settings_payload(settings.model_dump(mode="json"))


@app.post("/cursor/verify")
def cursor_verify(body: CursorKeyBody | None = None) -> dict[str, Any]:
    """Verify a Cursor API key (body key or already-saved settings)."""
    settings = load_settings()
    candidate = ""
    if body and body.cursor_api_key is not None and body.cursor_api_key.strip():
        candidate = body.cursor_api_key.strip()
    else:
        candidate = (settings.cursor_api_key or "").strip()
    if not candidate:
        raise HTTPException(400, "请先填写 Cursor API Key")
    result = verify_cursor_api_key(candidate)
    if result.get("ok"):
        # Persist only when verifying a newly typed key (not empty overwrite).
        if body and body.cursor_api_key and body.cursor_api_key.strip():
            settings.cursor_api_key = body.cursor_api_key.strip()
            save_settings(settings)
        else:
            apply_cursor_api_key_env(candidate)
        return {
            "ok": True,
            "message": "API Key 有效，已可用",
            **public_cursor_key_fields(settings.cursor_api_key or candidate),
            "me": result.get("me"),
        }
    err = result.get("error") or "验证失败"
    detail = result.get("detail")
    msg = f"API Key 验证失败: {err}"
    if detail:
        msg = f"{msg} — {detail}"
    raise HTTPException(400, msg)


@app.get("/cursor/chat")
def cursor_chat_get(session_id: str = "default") -> dict[str, Any]:
    return cursor_session_summary(session_id)


@app.post("/cursor/chat")
def cursor_chat_post(body: CursorChatBody) -> dict[str, Any]:
    try:
        return cursor_chat(
            message=body.message,
            session_id=body.session_id or "default",
            customer=body.customer,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"助手调用失败: {e}") from e


@app.post("/cursor/chat/stream")
def cursor_chat_stream(body: CursorChatBody) -> StreamingResponse:
    """SSE stream: delta / tool / status / done / error."""

    def gen():
        try:
            yield from cursor_sse_bytes(
                cursor_iter_chat_events(
                    message=body.message,
                    session_id=body.session_id or "default",
                    customer=body.customer,
                )
            )
        except Exception as e:
            payload = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {payload}\n\n".encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/cursor/chat/reset")
def cursor_chat_reset(body: CursorSessionBody | None = None) -> dict[str, Any]:
    sid = (body.session_id if body else None) or "default"
    return cursor_reset_session(sid)


@app.post("/cursor/chat/cancel")
def cursor_chat_cancel(body: CursorSessionBody | None = None) -> dict[str, Any]:
    """Clear stuck busy lock after client abort / UI stop."""
    sid = (body.session_id if body else None) or "default"
    return cursor_cancel_session(sid)


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
        if body.brand is not None:
            from sqlalchemy.orm.attributes import flag_modified

            from engine.render.logo import merge_brand_patch

            row.profile_json = merge_brand_patch(
                row.profile_json if isinstance(row.profile_json, dict) else {},
                body.brand,
            )
            flag_modified(row, "profile_json")
        if body.expression is not None:
            from sqlalchemy.orm.attributes import flag_modified

            from engine.pack.expression import resolve_expression_prefs

            profile = dict(row.profile_json) if isinstance(row.profile_json, dict) else {}
            prefs = resolve_expression_prefs(profile, overrides=body.expression)
            profile["expression"] = prefs
            row.profile_json = profile
            flag_modified(row, "profile_json")
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


@app.get("/keywords/active-summary")
def keywords_active_summary(customer_id: int | None = None) -> dict[str, Any]:
    """Active keyword pack summary including on-screen title_pool."""
    from engine.catalog.keyword_pack import get_active_pack, list_title_pool, title_pool_meta

    settings = load_settings()
    session = get_session()
    try:
        if customer_id is not None:
            row = session.get(Customer, customer_id)
            if not row:
                raise HTTPException(404, "客户不存在")
            customer_name = row.name
            pack_path = row.keyword_pack_path
        else:
            _, row, _ = _active_scope(session, settings)
            customer_name = row.name
            pack_path = row.keyword_pack_path
        pack = get_active_pack(session, customer_name)
        if not pack:
            return {
                "customer": customer_name,
                "keyword_pack_path": pack_path,
                "loaded": False,
                "title_pool_count": 0,
                "title_pool_sample": [],
                "hooks_count": 0,
            }
        titles = list_title_pool(pack)
        hooks = (pack.data_json.get("keyword_pool") or {}).get("hooks") or []
        meta = title_pool_meta(pack)
        return {
            "customer": customer_name,
            "keyword_pack_path": pack_path,
            "loaded": True,
            "pack_id": pack.id,
            "version": pack.version,
            "title_pool_count": len(titles),
            "title_pool_sample": titles[:40],
            "title_pool_meta": meta,
            "hooks_count": len(hooks) if isinstance(hooks, list) else 0,
            "max_chars_per_line": int(meta.get("max_chars_per_line") or 12),
        }
    finally:
        session.close()


@app.post("/keywords/reload-from-path")
def reload_keywords_from_path(customer_id: int | None = None) -> dict[str, Any]:
    """Reload keyword pack from Customer.keyword_pack_path."""
    from pathlib import Path

    from engine.catalog.keyword_pack import list_title_pool

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
        titles = list_title_pool(pack)
        return {
            "id": pack.id,
            "name": pack.name,
            "customer_id": pack.customer_id,
            "path": str(path),
            "version": pack.version,
            "title_pool_count": len(titles),
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
def list_outputs(
    state: str | None = None,
    missing_voice: bool = False,
    missing_subtitle: bool = False,
    noncompliant_tts: bool = False,
) -> list[dict[str, Any]]:
    from pathlib import Path

    from engine.ops.publish_trail import (
        merge_publish_trail,
        tts_lock_violation,
        tts_provider_from_meta,
        voice_flags_from_meta,
    )
    from engine.pack.video_lock import load_video_lock

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        video_lock = load_video_lock(customer.name, output_root=customer.output_root)
        q = (
            select(RenderOutput)
            .join(Job, RenderOutput.job_id == Job.id)
            .where(Job.customer_id == customer.id)
            .order_by(RenderOutput.id.desc())
        )
        if state:
            q = q.where(RenderOutput.state == state)
        rows = session.scalars(q).all()
        from engine.catalog.db import ReviewItem

        result = []
        for r in rows:
            title = ""
            covers: list[str] = []
            copywriting: dict[str, Any] = {}
            if isinstance(r.qc_json, dict):
                covers = list(r.qc_json.get("covers") or [])
            if r.sidecar_path:
                try:
                    import json

                    data = json.loads(Path(r.sidecar_path).read_text(encoding="utf-8"))
                    title = str(data.get("title") or "")
                    if not covers:
                        covers = list(data.get("covers") or [])
                    if isinstance(data.get("copywriting"), dict):
                        copywriting = data["copywriting"]
                    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                except Exception:
                    meta = {}
            else:
                meta = {}
            has_voice, subtitle_burned = voice_flags_from_meta(meta)
            violation = tts_lock_violation(meta, video_lock)
            if missing_voice and has_voice:
                continue
            if missing_subtitle and subtitle_burned:
                continue
            if noncompliant_tts and not violation:
                continue
            media_ok = bool(r.output_path and Path(r.output_path).is_file())
            last_review = session.scalars(
                select(ReviewItem)
                .where(ReviewItem.render_output_id == r.id)
                .order_by(ReviewItem.id.desc())
                .limit(1)
            ).first()
            publish_trail = merge_publish_trail(session, r.id, meta)
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
                    "media_ok": media_ok,
                    "cover_count": len(covers),
                    "review_status": last_review.status if last_review else None,
                    "has_voice": has_voice,
                    "subtitle_burned": subtitle_burned,
                    "tts_provider": tts_provider_from_meta(meta) or None,
                    "tts_compliant": violation is None,
                    "tts_noncompliant": violation,
                    "copywriting": copywriting,
                    "publish_trail": publish_trail,
                    "published": bool(publish_trail),
                }
            )
        return result
    finally:
        session.close()


@app.get("/outputs/{output_id}/publish-card")
def output_publish_card(output_id: int) -> dict[str, Any]:
    """Title + per-platform copy + local paths for the Publish desk tab."""
    from pathlib import Path

    from engine.pack.publish import build_platform_copy

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        out = _scoped_output(session, output_id, customer.id)
        side: dict[str, Any] = {}
        if out.sidecar_path and Path(out.sidecar_path).is_file():
            try:
                import json

                side = json.loads(Path(out.sidecar_path).read_text(encoding="utf-8"))
            except Exception:
                side = {}
        title = str(side.get("title") or f"成片 #{out.id}")
        copy0 = side.get("copywriting") if isinstance(side.get("copywriting"), dict) else {}
        brand = "品牌"
        if customer.profile_json and isinstance(customer.profile_json, dict):
            brand = str((customer.profile_json.get("brand") or {}).get("display_name") or brand)
        pack = None
        try:
            from engine.catalog.keyword_pack import get_active_pack

            pack = get_active_pack(session, customer.name)
            if pack and isinstance(pack.data_json, dict):
                brand = str(
                    (pack.data_json.get("company_info") or {}).get("display_name_preferred") or brand
                )
        except Exception:
            pass
        platforms = build_platform_copy(
            title=title,
            brand=brand,
            theme=str(side.get("theme") or "default"),
            hashtags=list(copy0.get("hashtags") or []),
            music_credit=str(copy0.get("music_credit") or ""),
            description=str(copy0.get("description") or ""),
        )
        meta = side.get("meta") if isinstance(side.get("meta"), dict) else {}
        from engine.ops.publish_trail import merge_publish_trail

        publish_trail = merge_publish_trail(session, out.id, meta)
        return {
            "ok": True,
            "id": out.id,
            "state": out.state,
            "title": title,
            "description": str(copy0.get("description") or ""),
            "hashtags": list(copy0.get("hashtags") or []),
            "platforms": platforms.get("platforms") or platforms,
            "output_path": out.output_path,
            "media_ok": bool(out.output_path and Path(out.output_path).is_file()),
            "has_narration": bool(meta.get("narration_path")),
            "subtitle_burned": bool(meta.get("subtitle_burned")),
            "publish_trail": publish_trail,
            "published": bool(publish_trail),
            "video_url": f"/outputs/{out.id}/video",
        }
    finally:
        session.close()


def _scoped_output(session, output_id: int, customer_id: int) -> RenderOutput:
    out = session.get(RenderOutput, output_id)
    if not out:
        raise HTTPException(404, "output not found")
    if out.job_id:
        job = session.get(Job, out.job_id)
        if job and job.customer_id and job.customer_id != customer_id:
            raise HTTPException(403, "output 不属于当前客户")
    return out


def _output_cover_paths(out: RenderOutput) -> list[str]:
    covers: list[str] = []
    if isinstance(out.qc_json, dict):
        covers = [str(c) for c in (out.qc_json.get("covers") or []) if c]
    if not covers and out.sidecar_path:
        try:
            import json
            from pathlib import Path

            data = json.loads(Path(out.sidecar_path).read_text(encoding="utf-8"))
            covers = [str(c) for c in (data.get("covers") or []) if c]
        except Exception:
            covers = []
    return covers


@app.api_route("/outputs/{output_id}/video", methods=["GET", "HEAD"])
def stream_output_video(output_id: int):
    """Stream ready mp4 for in-app review preview (GET+HEAD + Range).

    Local desktop engine: stream by output id + file existence.
    Do not gate on active customer — switching customers briefly leaves stale
    UI cards that would otherwise get 403 and poison the webview media cache.
    Listing endpoints remain customer-scoped.
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    session = get_session()
    try:
        out = session.get(RenderOutput, output_id)
        if not out:
            raise HTTPException(404, "output not found")
        path = Path(out.output_path or "")
        if not path.is_file():
            raise HTTPException(404, f"成片文件不存在: {path}")
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=path.name,
            content_disposition_type="inline",
            headers={
                "Accept-Ranges": "bytes",
                # Avoid no-store: some WebKit builds refuse range playback with it
                "Cache-Control": "private, max-age=0, must-revalidate",
            },
        )
    finally:
        session.close()


@app.api_route("/outputs/{output_id}/cover/{index}", methods=["GET", "HEAD"])
def stream_output_cover(output_id: int, index: int = 0):
    """Serve cover frame for review thumbnails (GET+HEAD)."""
    from pathlib import Path

    from fastapi.responses import FileResponse

    session = get_session()
    try:
        out = session.get(RenderOutput, output_id)
        if not out:
            raise HTTPException(404, "output not found")
        covers = _output_cover_paths(out)
        if index < 0 or index >= len(covers):
            raise HTTPException(404, "封面不存在")
        path = Path(covers[index])
        if not path.is_file():
            raise HTTPException(404, f"封面文件不存在: {path}")
        suffix = path.suffix.lower()
        media = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")
        return FileResponse(
            path,
            media_type=media,
            filename=path.name,
            content_disposition_type="inline",
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=0, must-revalidate",
            },
        )
    finally:
        session.close()


@app.get("/expression/languages")
def list_expression_languages() -> dict[str, Any]:
    """Global language catalog for voice / subtitle selectors (物料页)."""
    from engine.pack.languages import languages_for_api

    rows = languages_for_api(include_none=True)
    regions: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        regions.setdefault(str(row.get("region") or "其他"), []).append(row)
    return {"languages": rows, "regions": regions}


@app.post("/outputs/{output_id}/publish-pack")
def export_output_publish_pack(
    output_id: int,
    voice_lang: str | None = None,
    subtitle_lang: str | None = None,
    subtitle_burn: str | None = None,
    dual_secondary_lang: str | None = None,
) -> dict[str, Any]:
    """G3: export ready output into a publish_pack folder beside the mp4.

    Optional query overrides: voice_lang / subtitle_lang / subtitle_burn / dual_secondary_lang.
    """
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
        overrides = {
            k: v
            for k, v in {
                "voice_lang": voice_lang,
                "subtitle_lang": subtitle_lang,
                "subtitle_burn": subtitle_burn,
                "dual_secondary_lang": dual_secondary_lang,
            }.items()
            if v is not None
        }
        try:
            manifest = export_publish_pack(
                output_path=Path(out.output_path),
                sidecar_path=Path(out.sidecar_path) if out.sidecar_path else None,
                profile=customer.profile_json if isinstance(customer.profile_json, dict) else {},
                pack_data=pack_data,
                expression_overrides=overrides or None,
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
                platform_quotas=getattr(settings, "reach_platform_quotas", None) or {},
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
    from engine.ops.publish_trail import append_publish_trail
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
        trail: list[dict[str, Any]] = []
        if body.status == "published" and item.output_id:
            out = session.get(RenderOutput, item.output_id)
            if out:
                trail = append_publish_trail(
                    out,
                    platform=str(item.platform or ""),
                    reach_item_id=item.id,
                    note=body.note or "",
                )
        return {"ok": True, "item": item_to_dict(item), "publish_trail": trail}
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
        plat_quotas = getattr(settings, "reach_platform_quotas", None) or {}
        try:
            from engine.reach.limits import count_today_attempts
            from engine.reach.queue import PLATFORMS as _PLATS

            wanted = [p.lower() for p in (body.platforms or list(_PLATS))]
            wanted = [p for p in wanted if p in _PLATS]
            used = count_today_attempts(session, customer_id=customer.id)
            if used + len(wanted) > quota:
                raise ValueError(
                    f"触达日配额不足：今日已用 {used}/{quota}，本次需入队 {len(wanted)} 条"
                )
            for plat in wanted:
                assert_can_enqueue(
                    session,
                    customer_id=customer.id,
                    daily_quota=quota,
                    fail_threshold=fail_th,
                    platform=plat,
                    platform_quotas=plat_quotas,
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
            platform_quotas=getattr(settings, "reach_platform_quotas", None) or {},
        )
        return {"ok": True, **st}
    finally:
        session.close()


class ReachQuotaUpdate(BaseModel):
    daily_quota: int | None = None
    platform_quotas: dict[str, int] | None = None
    fail_threshold: int | None = None


@app.put("/reach/quota")
def reach_quota_update(body: ReachQuotaUpdate) -> dict[str, Any]:
    """Set daily total + per-platform quotas (sum of platforms must be <= total)."""
    from engine.reach.limits import quota_status, validate_quota_config
    from engine.reach.queue import PLATFORMS

    settings = load_settings()
    daily = int(settings.reach_daily_quota if body.daily_quota is None else body.daily_quota)
    if daily < 0 or daily > 500:
        raise HTTPException(400, "daily_quota must be 0-500")
    plats_raw = (
        settings.reach_platform_quotas
        if body.platform_quotas is None
        else body.platform_quotas
    )
    try:
        plats = validate_quota_config(daily, plats_raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # When client sends platform_quotas, empty dict clears per-platform caps (total only).
    # Non-empty: persist full known map (missing → 0) for clear UX.
    if body.platform_quotas is not None:
        if len(body.platform_quotas) == 0:
            plats = {}
        else:
            plats = {p: int(plats.get(p, 0)) for p in PLATFORMS}
            try:
                plats = validate_quota_config(daily, plats)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
    settings.reach_daily_quota = daily
    settings.reach_platform_quotas = plats
    if body.fail_threshold is not None:
        th = int(body.fail_threshold)
        if th < 1 or th > 50:
            raise HTTPException(400, "fail_threshold must be 1-50")
        settings.reach_fail_threshold = th
    save_settings(settings)

    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        st = quota_status(
            session,
            customer_id=customer.id,
            daily_quota=settings.reach_daily_quota,
            fail_threshold=settings.reach_fail_threshold,
            platform_quotas=settings.reach_platform_quotas,
        )
        return {"ok": True, "saved": True, **st}
    finally:
        session.close()


class ReachOpenRequest(BaseModel):
    dry_run: bool = False
    chrome_profile: str | None = None


class ReachChromeOpenRequest(BaseModel):
    name: str
    platform: str | None = None
    dry_run: bool = False


class ReachChromeSelectRequest(BaseModel):
    name: str
    platform: str | None = None


class ReachChromeCreateRequest(BaseModel):
    platform: str
    count: int = 1
    name_prefix: str | None = None


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
                chrome_profile=body.chrome_profile,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        result["item"] = item_to_dict(item)
        return result
    finally:
        session.close()


@app.get("/reach/chrome-profiles")
def reach_chrome_profiles() -> dict[str, Any]:
    """List local Chrome user-data-dir profiles (no cookies; serial switch only)."""
    from engine.reach.browser import list_chrome_profiles

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        listed = list_chrome_profiles()
        reach = (profile.get("reach") or {}) if isinstance(profile, dict) else {}
        selected = str(reach.get("last_chrome_profile") or "").strip()
        selected_platform = str(reach.get("last_chrome_platform") or "").strip() or None
        if not selected:
            # fall back to any platform's chrome_profile_name
            browsers = reach.get("browsers") or {}
            if isinstance(browsers, dict):
                for cfg in browsers.values():
                    if isinstance(cfg, dict) and cfg.get("chrome_profile_name"):
                        selected = str(cfg.get("chrome_profile_name")).strip()
                        break
        return {
            **listed,
            "selected": selected or None,
            "selected_platform": selected_platform,
        }
    finally:
        session.close()


@app.post("/reach/chrome-profiles/create")
def reach_chrome_profiles_create(body: ReachChromeCreateRequest) -> dict[str, Any]:
    """Create N empty Chrome profiles bound to a platform. Does not launch Chrome."""
    from engine.reach.browser import create_chrome_profiles, set_selected_chrome_profile
    from sqlalchemy.orm.attributes import flag_modified

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        try:
            result = create_chrome_profiles(
                platform=body.platform,
                count=int(body.count or 1),
                name_prefix=body.name_prefix,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        selected = result.get("selected")
        if selected:
            profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
            customer.profile_json = set_selected_chrome_profile(
                profile, name=str(selected), platform=body.platform
            )
            flag_modified(customer, "profile_json")
            session.commit()
        return result
    finally:
        session.close()


@app.post("/reach/chrome-profiles/open")
def reach_chrome_profiles_open(body: ReachChromeOpenRequest) -> dict[str, Any]:
    """Open official entry in an isolated Chrome user-data-dir. Never auto-publishes."""
    from engine.reach.browser import entry_for_platform, open_chrome_profile, resolve_profile_platform

    try:
        plat = resolve_profile_platform(body.name, body.platform)
        entry = entry_for_platform(plat)
        info = open_chrome_profile(body.name, url=entry["url"], dry_run=bool(body.dry_run))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "ok": True,
        "platform": plat,
        "label": entry["label"],
        "open": info,
        "auto_publish": False,
        "human_in_loop": True,
        "disclaimer": "仅打开本机 Chrome 独立配置 + 对应平台官方入口；登录与发布须本人完成。",
    }


@app.post("/reach/chrome-profiles/select")
def reach_chrome_profiles_select(body: ReachChromeSelectRequest) -> dict[str, Any]:
    """Remember selected Chrome profile on active customer (for queue open)."""
    from engine.reach.browser import list_chrome_profiles, resolve_profile_platform, set_selected_chrome_profile

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        try:
            plat = resolve_profile_platform(body.name, body.platform)
            updated = set_selected_chrome_profile(profile, name=body.name, platform=plat)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        customer.profile_json = updated
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(customer, "profile_json")
        session.commit()
        session.refresh(customer)
        listed = list_chrome_profiles()
        return {
            "ok": True,
            "selected": body.name,
            "platform": plat,
            "profiles": listed.get("profiles"),
            "root": listed.get("root"),
            "auto_publish": False,
            "human_in_loop": True,
        }
    finally:
        session.close()


class ReachAutoUploadStartRequest(BaseModel):
    chrome_profile: str | None = None
    queue_id: int | None = None
    platform: str | None = None
    accept_risk: bool = False
    timeout_sec: float = 300
    dry_run: bool = False


@app.post("/reach/auto-upload/start")
def reach_auto_upload_start(body: ReachAutoUploadStartRequest) -> dict[str, Any]:
    """G5.V: open Chrome profile → wait login ready → auto upload (douyin) or handoff."""
    from engine.reach.auto_upload import pick_queue_item, start_job
    from engine.reach.browser import resolve_profile_platform
    from engine.reach.queue import item_to_dict

    if not body.accept_risk:
        raise HTTPException(400, "须 accept_risk=true（G5.V 风控自负）")

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        reach = profile.get("reach") or {}
        browsers = reach.get("browsers") or {}
        plat_hint = (body.platform or "").strip() or None
        # Prefer App customer browser binding for platform before last_chrome_profile
        chrome_name = (body.chrome_profile or "").strip()
        if not chrome_name and plat_hint:
            bcfg = browsers.get(plat_hint) if isinstance(browsers, dict) else None
            if isinstance(bcfg, dict):
                chrome_name = str(
                    bcfg.get("chrome_profile_name") or bcfg.get("profile_dir") or ""
                ).strip()
        if not chrome_name:
            chrome_name = str(reach.get("last_chrome_profile") or "").strip()
        if not chrome_name:
            raise HTTPException(400, "未指定 Chrome 配置名（请在 App 客户配置中绑定平台浏览器）")
        try:
            # Resolve platform: body → profile binding → queue item later
            plat = resolve_profile_platform(chrome_name, plat_hint)
            item = pick_queue_item(
                session,
                customer.id,
                body.queue_id,
                platform=plat,
            )
            # Queue item platform wins when explicit queue_id
            if body.queue_id is not None:
                plat = item.platform
            if chrome_name and plat and item.platform != plat and body.queue_id is not None:
                # Allow but warn via message in job — still use item.platform for upload path
                plat = item.platform
            status = start_job(
                chrome_profile=chrome_name,
                queue_id=int(item.id),
                customer_id=customer.id,
                platform=plat,
                title=item.title or "",
                body=item.body or "",
                video_path=item.video_path or "",
                pack_dir=item.pack_dir,
                accept_risk=True,
                timeout_sec=float(body.timeout_sec or 300),
                dry_run=bool(body.dry_run),
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        status["item"] = item_to_dict(item)
        return status
    finally:
        session.close()


@app.get("/reach/auto-upload/status")
def reach_auto_upload_status() -> dict[str, Any]:
    from engine.reach.auto_upload import get_status

    return get_status()


@app.post("/reach/auto-upload/cancel")
def reach_auto_upload_cancel() -> dict[str, Any]:
    from engine.reach.auto_upload import cancel_job

    return cancel_job()


@app.get("/reach/platforms")
def reach_platforms() -> dict[str, Any]:
    from engine.reach.browser import OFFICIAL_ENTRY
    from engine.reach.cover_templates import DEFAULT_SLOT_COUNTS, PLATFORM_COVER_SPECS

    return {
        "ok": True,
        "platforms": [
            {
                "id": k,
                "label": v["label"],
                "url": v["url"],
                "short": v.get("short") or k,
                "cover_slots": int(DEFAULT_SLOT_COUNTS.get(k, 1)),
            }
            for k, v in OFFICIAL_ENTRY.items()
        ],
        "slot_specs": {p: [dict(s) for s in specs] for p, specs in PLATFORM_COVER_SPECS.items()},
        "auto_publish": False,
        "human_in_loop": True,
    }


class CoverTemplateCreateRequest(BaseModel):
    name: str = "未命名封面套"


class CoverTemplateRenameRequest(BaseModel):
    name: str


class CoverTemplateSelectRequest(BaseModel):
    template_id: str


class CoverTemplateSlotRequest(BaseModel):
    platform: str
    slot_index: int = 0
    source_path: str = ""


class CoverTemplateSeedRequest(BaseModel):
    pack_dir: str
    platforms: list[str] | None = None


class CoverResolveRequest(BaseModel):
    platform: str
    pack_dir: str | None = None
    template_id: str | None = None


@app.get("/reach/cover-templates")
def reach_cover_templates_list() -> dict[str, Any]:
    from engine.reach.cover_templates import list_templates, resolve_cover_store_for_settings

    settings = load_settings()
    return list_templates(resolve_cover_store_for_settings(settings))


@app.post("/reach/cover-templates")
def reach_cover_templates_create(body: CoverTemplateCreateRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import create_template, resolve_cover_store_for_settings

    settings = load_settings()
    store = resolve_cover_store_for_settings(settings)
    tpl = create_template(store, name=body.name)
    return {"ok": True, "template": tpl, "store": str(store)}


@app.post("/reach/cover-templates/{template_id}/rename")
def reach_cover_templates_rename(template_id: str, body: CoverTemplateRenameRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import rename_template, resolve_cover_store_for_settings

    settings = load_settings()
    try:
        tpl = rename_template(resolve_cover_store_for_settings(settings), template_id, body.name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True, "template": tpl}


@app.delete("/reach/cover-templates/{template_id}")
def reach_cover_templates_delete(template_id: str) -> dict[str, Any]:
    from engine.reach.cover_templates import delete_template, resolve_cover_store_for_settings

    settings = load_settings()
    try:
        return delete_template(resolve_cover_store_for_settings(settings), template_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/reach/cover-templates/select")
def reach_cover_templates_select(body: CoverTemplateSelectRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import select_template, resolve_cover_store_for_settings

    settings = load_settings()
    try:
        return select_template(resolve_cover_store_for_settings(settings), body.template_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/reach/cover-templates/{template_id}/slot")
def reach_cover_templates_set_slot(template_id: str, body: CoverTemplateSlotRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import resolve_cover_store_for_settings, set_slot_image

    settings = load_settings()
    try:
        tpl = set_slot_image(
            resolve_cover_store_for_settings(settings),
            template_id=template_id,
            platform=body.platform,
            slot_index=int(body.slot_index),
            source_path=body.source_path,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "template": tpl}


@app.post("/reach/cover-templates/{template_id}/slot/clear")
def reach_cover_templates_clear_slot(template_id: str, body: CoverTemplateSlotRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import clear_slot, resolve_cover_store_for_settings

    settings = load_settings()
    try:
        tpl = clear_slot(
            resolve_cover_store_for_settings(settings),
            template_id=template_id,
            platform=body.platform,
            slot_index=int(body.slot_index),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "template": tpl}


@app.post("/reach/cover-templates/{template_id}/seed")
def reach_cover_templates_seed(template_id: str, body: CoverTemplateSeedRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import resolve_cover_store_for_settings, seed_template_from_pack

    settings = load_settings()
    try:
        tpl = seed_template_from_pack(
            resolve_cover_store_for_settings(settings),
            template_id=template_id,
            pack_dir=body.pack_dir,
            platforms=body.platforms,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "template": tpl}


@app.post("/reach/cover-templates/resolve")
def reach_cover_templates_resolve(body: CoverResolveRequest) -> dict[str, Any]:
    from engine.reach.cover_templates import resolve_cover_store_for_settings, resolve_covers
    from engine.reach.publish_assets import PublishAssetsError, require_publish_assets

    settings = load_settings()
    store = resolve_cover_store_for_settings(settings)
    resolved = resolve_covers(
        store,
        platform=body.platform,
        pack_dir=body.pack_dir,
        template_id=body.template_id,
    )
    gate: dict[str, Any] | None = None
    if body.pack_dir:
        try:
            gate = require_publish_assets(
                platform=body.platform,
                pack_dir=body.pack_dir,
                data_root=store,
                template_id=body.template_id,
            )
        except PublishAssetsError as e:
            gate = {"ok": False, "error": str(e)}
    return {"ok": True, "resolve": resolved, "gate": gate, "store": str(store)}


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
                "scene": c.scene,
                "objects": c.objects_json or [],
                "actions": c.actions_json or [],
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
def semantic_search(
    q: str,
    category: str | None = None,
    theme: str | None = None,
    scene: str | None = None,
    object: str | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        hits = search_cliplets(
            session,
            q,
            category=category,
            theme=theme,
            scene=scene,
            object_tag=object,
            top_k=top_k,
            customer_id=customer.id,
        )
        return [
            {
                "id": c.id,
                "score": round(score, 4),
                "asset_uuid": c.asset_uuid,
                "start_sec": c.start_sec,
                "end_sec": c.end_sec,
                "description": c.description,
                "category": c.category,
                "theme": c.theme,
                "scene": c.scene,
                "objects": c.objects_json or [],
                "actions": c.actions_json or [],
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


class BatchRerenderRequest(BaseModel):
    output_ids: list[int] | None = None
    missing_voice: bool = True
    missing_subtitle: bool = False
    noncompliant_tts: bool = False
    limit: int = 20
    reason: str = "ops_voice_subtitle"


@app.post("/outputs/batch-rerender")
def outputs_batch_rerender(body: BatchRerenderRequest) -> dict[str, Any]:
    """G6: queue re-renders for outputs missing voice/subs or lock TTS violations (max 20)."""
    from engine.catalog.review_learn import create_rerender_job
    from engine.ops.publish_trail import (
        read_sidecar,
        stamp_tts_noncompliant,
        tts_lock_violation,
        voice_flags_from_meta,
    )
    from engine.pack.video_lock import load_video_lock

    try:
        assert_production_ready()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e

    limit = max(1, min(int(body.limit or 20), 20))
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        video_lock = load_video_lock(customer.name, output_root=customer.output_root)
        ids = list(body.output_ids or [])
        reason = body.reason or "ops_voice_subtitle"
        if body.noncompliant_tts and reason in ("ops_voice_subtitle", "other", ""):
            reason = "ops_tts_noncompliant"
        if not ids:
            rows = session.scalars(
                select(RenderOutput)
                .join(Job, RenderOutput.job_id == Job.id)
                .where(Job.customer_id == customer.id, RenderOutput.state.in_(["ready", "review"]))
                .order_by(RenderOutput.id.desc())
                .limit(200)
            ).all()
            for r in rows:
                meta = (read_sidecar(r.sidecar_path).get("meta") or {}) if r.sidecar_path else {}
                if not isinstance(meta, dict):
                    meta = {}
                has_voice, has_sub = voice_flags_from_meta(meta)
                violation = tts_lock_violation(meta, video_lock)
                need = False
                if body.noncompliant_tts and violation:
                    need = True
                    if r.sidecar_path:
                        stamp_tts_noncompliant(r.sidecar_path, violation)
                    if r.state == "ready":
                        r.state = "review"
                        session.add(r)
                if body.missing_voice and not has_voice:
                    need = True
                if body.missing_subtitle and not has_sub:
                    need = True
                # Pure TTS-lock batch: ignore missing_* unless also requested with empty voice
                if body.noncompliant_tts and not body.missing_voice and not body.missing_subtitle:
                    need = bool(violation)
                if need:
                    ids.append(r.id)
                if len(ids) >= limit:
                    break
            session.commit()
        ids = ids[:limit]
        queued: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for oid in ids:
            try:
                out = _scoped_output(session, oid, customer.id)
                job = create_rerender_job(session, out, reason=reason)
                queued.append({"output_id": oid, "job_id": job.id, "status": job.status})
            except HTTPException as e:
                errors.append({"output_id": oid, "error": str(e.detail)})
            except ValueError as e:
                errors.append({"output_id": oid, "error": str(e)})
        return {
            "ok": True,
            "queued": queued,
            "errors": errors,
            "count": len(queued),
            "limit": limit,
            "reason": reason,
        }
    finally:
        session.close()


@app.get("/reports/ops")
def report_ops() -> dict[str, Any]:
    """G6 ops dashboard: ready/fail/voice coverage + TTS lock hard-fails + reach quota."""
    from pathlib import Path

    from engine.catalog.db import ReachQueueItem
    from engine.ops.maintenance import disk_report
    from engine.ops.publish_trail import (
        read_sidecar,
        stamp_tts_noncompliant,
        tts_lock_violation,
        tts_provider_from_meta,
        voice_flags_from_meta,
    )
    from engine.pack.video_lock import load_video_lock
    from engine.reach.limits import quota_status

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        video_lock = load_video_lock(customer.name, output_root=customer.output_root)
        job_ids = [j.id for j in session.scalars(select(Job).where(Job.customer_id == customer.id)).all()]
        outputs = (
            list(session.scalars(select(RenderOutput).where(RenderOutput.job_id.in_(job_ids))).all())
            if job_ids
            else []
        )

        tts_noncompliant = 0
        tts_say = 0
        tts_edge_ok = 0
        demoted = 0
        # Pass 1: stamp + demote lock TTS violations (say/mock under Edge lock)
        for o in outputs:
            if o.state not in ("ready", "review"):
                continue
            meta: dict[str, Any] = {}
            if o.sidecar_path:
                side = read_sidecar(o.sidecar_path)
                meta = side.get("meta") if isinstance(side.get("meta"), dict) else {}
            provider = tts_provider_from_meta(meta)
            hv, _hs = voice_flags_from_meta(meta)
            violation = tts_lock_violation(meta, video_lock)
            if violation:
                tts_noncompliant += 1
                if provider == "say" or "say" in violation:
                    tts_say += 1
                if o.sidecar_path:
                    stamp_tts_noncompliant(o.sidecar_path, violation)
                if o.state == "ready":
                    o.state = "review"
                    session.add(o)
                    demoted += 1
            elif hv and provider == "edge":
                tts_edge_ok += 1
        if demoted:
            session.commit()

        by_state: dict[str, int] = {}
        ready_with_voice = 0
        ready_with_sub = 0
        ready_published = 0
        ready_n = 0
        for o in outputs:
            by_state[o.state] = by_state.get(o.state, 0) + 1
            if o.state != "ready":
                continue
            ready_n += 1
            meta = {}
            if o.sidecar_path:
                side = read_sidecar(o.sidecar_path)
                meta = side.get("meta") if isinstance(side.get("meta"), dict) else {}
            hv, hs = voice_flags_from_meta(meta)
            violation = tts_lock_violation(meta, video_lock)
            # say under lock never counts as voice coverage
            if hv and not violation:
                ready_with_voice += 1
            if hs:
                ready_with_sub += 1
            trail = meta.get("publish_trail") if isinstance(meta, dict) else None
            if trail:
                ready_published += 1
            else:
                hit = session.scalar(
                    select(ReachQueueItem.id).where(
                        ReachQueueItem.output_id == o.id,
                        ReachQueueItem.status == "published",
                    )
                )
                if hit:
                    ready_published += 1

        total = len(outputs)
        ready = by_state.get("ready", 0)
        failed = by_state.get("failed", 0)
        quota = quota_status(
            session,
            customer_id=customer.id,
            daily_quota=int(getattr(settings, "reach_daily_quota", 5) or 5),
            fail_threshold=int(getattr(settings, "reach_fail_threshold", 3) or 3),
            platform_quotas=getattr(settings, "reach_platform_quotas", None) or {},
        )

        try:
            disk = disk_report(settings)
            path_h = disk.get("path_health") or {}
            if hasattr(path_h, "__dict__"):
                path_h = dict(path_h)
            disk_ok = bool(disk.get("ok"))
            free = (disk.get("volumes") or {}).get("output", {}).get("free_gb")
            if free is None:
                free = path_h.get("free_disk_gb")
            err_list = list(disk.get("errors") or []) + list(path_h.get("errors") or [])
        except Exception:
            disk_ok = False
            path_h = {"ok": False, "errors": ["disk_report unavailable"], "warnings": []}
            free = None
            err_list = ["disk_report unavailable"]

        lib = Path(str(customer.library_root or "")) if customer.library_root else None
        out_root = Path(str(customer.output_root or "")) if customer.output_root else None
        lib_ok = bool(lib and lib.exists())
        out_ok = bool(out_root and out_root.exists())
        parts: list[str] = []
        if disk_ok and lib_ok and out_ok:
            parts.append("路径正常")
        else:
            if not lib_ok:
                parts.append("片库缺失")
            if not out_ok:
                parts.append("成片目录缺失")
            for e in err_list[:2]:
                parts.append(str(e))
        if free is not None:
            parts.append(f"磁盘 {float(free):.0f} GB")
        if tts_noncompliant:
            parts.append(f"音色违规 {tts_noncompliant}")
        health_line = " · ".join(parts) if parts else "状态未知"

        return {
            "ok": True,
            "customer_id": customer.id,
            "customer_name": customer.name,
            "outputs": by_state,
            "ready": ready,
            "failed": failed,
            "ready_rate": round(ready / total, 4) if total else 0.0,
            "failure_rate": round(failed / total, 4) if total else 0.0,
            "voice_coverage": round(ready_with_voice / ready_n, 4) if ready_n else 0.0,
            "subtitle_coverage": round(ready_with_sub / ready_n, 4) if ready_n else 0.0,
            "ready_with_voice": ready_with_voice,
            "ready_with_subtitle": ready_with_sub,
            "ready_count": ready_n,
            "published_ready": ready_published,
            "missing_voice": max(0, ready_n - ready_with_voice),
            "tts_noncompliant": tts_noncompliant,
            "tts_say": tts_say,
            "tts_edge_ok": tts_edge_ok,
            "tts_lock_hard_fail": tts_noncompliant,
            "tts_demoted_to_review": demoted,
            "quota": quota,
            "health_line": health_line,
            "path_health": path_h,
            "library_ok": lib_ok,
            "output_ok": out_ok,
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
