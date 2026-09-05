from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from engine.catalog.db import (
    Asset,
    CalendarEntry,
    Cliplet,
    Customer,
    Job,
    JobEvent,
    KeywordUsage,
    ReachMessage,
    ReachMessageAccount,
    ReachMessageScan,
    ReachNotificationEvent,
    RenderOutput,
    ReplyDraft,
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
from engine.catalog.keyword_pack import install_keyword_pack
from engine.catalog.vector_index import search_cliplets
from engine.config.paths import check_paths, ensure_layout
from engine.config.settings import (
    AppSettings,
    PathConfig,
    effective_semantic_full_backfill_enabled,
    load_settings,
    save_settings,
    set_semantic_full_backfill_lab,
)
from engine.ops.settings_redact import redact_settings_payload
from engine.export.csv_export import export_job_events_csv, export_renders_csv
from engine.ingest.cliplet import (
    clear_semantic_claims,
    recaption_existing_cliplets,
    semantic_backfill_progress,
    verify_cliplets_on_demand,
)
from engine.ingest.watcher import IngestWatcher
from engine.jobs.queue import CreateJobRequest, TopicIntentRequest, create_job, pause_job, resume_job
from engine.jobs.worker import worker
from engine.ops.maintenance import assert_production_ready, clean_cache, disk_report, load_scheduler_state, maybe_run_daily_job
from engine.ops.scheduler import scheduler
from engine.template.engine import DEFAULT_TEMPLATE, TemplateDefinition, build_plan, plan_to_dict

from engine.ops.scan_state import scan_state as _scan_state
from engine.runtime import services as runtime_services
from engine.runtime.pause_coordinator import (
    SystemEventControl,
    assert_runtime_active,
    coordinator as pause_coordinator,
    get_system_event_control_from_settings,
)
from engine.version import ENGINE_VERSION

watcher: IngestWatcher | None = None
_workspace_services_started = False


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


def try_start_workspace_services(settings: AppSettings | None = None) -> bool:
    """Start watcher/worker/scheduler if workspace is ready and not yet started."""
    global watcher, _workspace_services_started
    from engine.config.workspace import STATE_LOCAL, STATE_READY, ensure_identity_on_ready, probe_workspace
    from engine.runtime import boot_state

    settings = settings or load_settings()
    probe = probe_workspace(settings)
    if probe.state not in (STATE_READY, STATE_LOCAL) or not probe.can_init_db:
        return False
    if not _workspace_services_started:
        ensure_layout(settings)
        init_db(settings)
        if probe.is_external and probe.state == STATE_READY:
            ensure_identity_on_ready(settings, volume_uuid=probe.volume_uuid)
            save_settings(settings)
        try:
            pause_coordinator.ensure_system_token()
        except HTTPException:
            pass
        session = get_session()
        try:
            settings = load_settings()
            customer = require_active_customer(session, settings)
            seed_week_if_empty(session, customer.id, customer.name)
            from engine.catalog.keyword_pack import refresh_canonical_pack

            refresh_canonical_pack(session, customer)
            from engine.reach.publication_lifecycle import retry_pending_archives

            retry_pending_archives(session)
        finally:
            session.close()
        watcher = IngestWatcher(settings)
        runtime_services.register("watcher", watcher)
        from engine.catalog.vectorization_runtime import executor as vectorization_executor

        runtime_services.register("vector_reconcile", vectorization_executor)
        vectorization_executor.start()
        from engine.reach.message_sync import message_sync

        if pause_coordinator.is_active_for_new_work():
            watcher.start()
            worker.start()
            scheduler.start()
            message_sync.start_scheduler()
        try:
            from engine.ops.semantic_backfill_runtime import semantic_backfill_runtime

            semantic_backfill_runtime.ensure_started()
        except Exception:
            logging.getLogger(__name__).exception("semantic backfill runtime start failed")
        _workspace_services_started = True
        boot_state.mark_ready()
        return True
    return True


def _boot_workspace_services() -> None:
    """Heavy startup after HTTP bind — keep /health available during this work."""
    global watcher, _workspace_services_started
    from engine.config.workspace import STATE_LOCAL, STATE_READY, ensure_identity_on_ready, probe_workspace
    from engine.runtime import boot_state

    log = logging.getLogger(__name__)
    try:
        settings = load_settings()
        probe = probe_workspace(settings)
        if probe.can_init_db and probe.state in (STATE_READY, STATE_LOCAL):
            ensure_layout(settings)
            init_db(settings)
            if probe.is_external and probe.state == STATE_READY:
                ensure_identity_on_ready(settings, volume_uuid=probe.volume_uuid)
                try:
                    save_settings(settings)
                except OSError:
                    pass
            session = get_session()
            try:
                settings = load_settings()
                customer = require_active_customer(session, settings)
                seed_week_if_empty(session, customer.id, customer.name)
                from engine.catalog.keyword_pack import refresh_canonical_pack

                refresh_canonical_pack(session, customer)
                cleared = clear_semantic_claims(session)
                if cleared:
                    log.info("cleared %s stale semantic claim(s) on startup", cleared)
                from engine.reach.publish_sources import release_all_content_reservations

                released = release_all_content_reservations(session)
                if released:
                    session.commit()
                    log.info(
                        "released %s leftover content reservation(s) (policy_no_reserve)",
                        released,
                    )
            finally:
                session.close()
            if boot_state.is_shutting_down():
                boot_state.mark_blocked("shutdown_during_boot")
                return
            watcher = IngestWatcher(settings)
            runtime_services.register("watcher", watcher)
            from engine.catalog.vectorization_runtime import executor as vectorization_executor

            runtime_services.register("vector_reconcile", vectorization_executor)
            vectorization_executor.start()
            from engine.reach.message_sync import message_sync

            if pause_coordinator.is_active_for_new_work():
                watcher.start()
                worker.start()
                scheduler.start()
                message_sync.start_scheduler()
            try:
                from engine.ops.semantic_backfill_runtime import semantic_backfill_runtime

                semantic_backfill_runtime.ensure_started()
            except Exception:
                log.exception("semantic backfill runtime start failed")
            _workspace_services_started = True
            boot_state.mark_ready()
        else:
            _workspace_services_started = False
            watcher = None
            reasons = ",".join(probe.reasons) if getattr(probe, "reasons", None) else probe.state
            boot_state.mark_blocked(f"workspace:{reasons or probe.state}")
    except Exception as exc:  # noqa: BLE001 — surface boot failure without killing HTTP
        log.exception("engine background boot failed")
        boot_state.mark_failed(f"{type(exc).__name__}: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global watcher, _workspace_services_started
    from engine.runtime import boot_state

    boot_state.mark_starting()
    try:
        pause_coordinator.ensure_system_token()
    except HTTPException:
        pass

    # Boot services before accepting traffic for DB consistency.
    # Heavy Chrome workspace merge is already deferred (see REMOTE_DEPLOY /
    # SUYING_SKIP_WORKSPACE_CHROME_MIGRATE). Empty-lab boot is ~tens of ms.
    # Optional early-bind path for future cold-start profiling:
    # SUYING_ASYNC_ENGINE_BOOT=1 runs workers in a background thread and
    # exposes /health status=starting until complete.
    async_boot = os.environ.get("SUYING_ASYNC_ENGINE_BOOT", "").strip() in {
        "1",
        "true",
        "yes",
    }
    boot_thread: threading.Thread | None = None
    if async_boot:
        boot_thread = threading.Thread(
            target=_boot_workspace_services,
            name="suying-engine-boot",
            daemon=True,
        )
        boot_thread.start()
    else:
        _boot_workspace_services()

    yield

    boot_state.request_shutdown()
    log = logging.getLogger(__name__)
    if boot_thread is not None:
        boot_thread.join(timeout=30)
    try:
        from engine.reach.message_sync import message_sync

        message_sync.stop_scheduler()
    except Exception:
        log.exception("shutdown: message_sync.stop_scheduler failed")
    try:
        scheduler.stop()
    except Exception:
        log.exception("shutdown: scheduler.stop failed")
    try:
        worker.stop()
    except Exception:
        log.exception("shutdown: worker.stop failed")
    if watcher:
        try:
            watcher.stop()
        except Exception:
            log.exception("shutdown: watcher.stop failed")
    try:
        from engine.catalog.vectorization_runtime import executor as vectorization_executor

        vectorization_executor.stop()
    except Exception:
        log.exception("shutdown: vectorization_executor.stop failed")
    try:
        from engine.ops.semantic_backfill_runtime import semantic_backfill_runtime

        semantic_backfill_runtime.stop(join_timeout=1.0)
    except Exception:
        log.exception("shutdown: semantic_backfill_runtime.stop failed")
    runtime_services.clear()
    _workspace_services_started = False


app = FastAPI(title="Montage Studio Engine", version=ENGINE_VERSION, lifespan=lifespan)
# Local desktop engine: never grant arbitrary web origins access to privileged
# localhost operations (carrier/update/settings write paths).
# Tauri 2 macOS/Windows webview origin is http(s)://tauri.localhost (not tauri://).
# JSON POST/PUT/PATCH require CORS preflight; missing origin → fetch fails as "无法连接引擎"
# while GET /health still looks fine in some WebViews / via Rust engine_status.
_TAURI_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost",
    "http://127.0.0.1",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_TAURI_ORIGINS,
    allow_origin_regex=(
        r"^(tauri://localhost|https?://tauri\.localhost|"
        r"https?://(localhost|127\.0\.0\.1)(:\d+)?)$"
    ),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Range", "X-Suying-System-Token"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length", "Content-Type"],
)


@app.middleware("http")
async def _count_health_hits(request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path == "/health":
        from engine.runtime.health_metrics import health_metrics

        health_metrics.record()
    return await call_next(request)


@app.middleware("http")
async def _enforce_packaged_entitlement(request, call_next):  # type: ignore[no-untyped-def]
    """A running packaged engine must lock every business API at trial expiry."""
    if request.method == "OPTIONS" or request.url.path in {
        "/health",
        "/license/status",
        "/readiness",
    }:
        return await call_next(request)
    from engine.security.license import current_runtime_license, is_packaged_runtime

    if is_packaged_runtime():
        try:
            current_runtime_license()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=423,
                content={
                    "detail": str(exc),
                    "code": "LICENSE_LOCKED",
                },
            )
    return await call_next(request)


from engine.api.reach_publish_routes import router as reach_publish_router
from engine.api.content_routes import router as content_router
from engine.api.production_rule_routes import router as production_rules_router
from engine.api.scene_tour_routes import router as scene_tour_router
from engine.api.semantic_ops_routes import router as semantic_ops_router
from engine.api.system_routes import router as system_router
from engine.api.vectorization_routes import router as vectorization_router
from engine.api.workspace_routes import router as workspace_router
from engine.api.maintenance_routes import router as maintenance_router
from engine.api.job_routes import router as job_router
from engine.api.catalog_routes import router as catalog_router
from engine.api.reach_console_routes import router as reach_console_router
from engine.api.library_review_routes import router as library_review_router

app.include_router(reach_publish_router)
app.include_router(content_router)
app.include_router(production_rules_router)
app.include_router(scene_tour_router)
app.include_router(semantic_ops_router)
app.include_router(system_router)
app.include_router(vectorization_router)
app.include_router(workspace_router)
app.include_router(maintenance_router)
app.include_router(job_router)
app.include_router(catalog_router)
app.include_router(reach_console_router)
app.include_router(library_review_router)


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
    ollama_embed_model: str | None = None
    ollama_vision_model: str | None = None
    ollama_vision_escalate_model: str | None = None
    ollama_vision_cascade: bool | None = None
    semantic_analysis_mode: str | None = None
    semantic_full_backfill_enabled: bool | None = None
    ollama_narration_enabled: bool | None = None
    ollama_narration_model: str | None = None
    ollama_narration_burn_emoji: bool | None = None
    ollama_rule_model: str | None = None
    system_event_control: dict[str, Any] | None = None
    # GVoiceClone
    tts_provider: str | None = None
    tts_clone_pack: str | None = None
    tts_clone_speed: float | None = None
    tts_chars_per_sec_zh: float | None = None


class TtsPreferenceUpdate(BaseModel):
    """App Settings · 旁白音色：同步 settings + 当前客户 VIDEO_LOCK.voice。"""

    provider: str  # edge | clone
    voice_pack: str | None = None
    # Edge Neural ShortName, e.g. zh-CN-XiaoyiNeural（provider=edge 时生效）
    voice: str | None = None
    clone_speed: float | None = Field(default=None, ge=0.5, le=1.5)
    update_video_lock: bool = True


def _active_customer_scoped_settings() -> tuple[Any, Any]:
    """Return (scoped_settings, customer) for the current active tenant."""
    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        return settings_with_customer_paths(settings, customer), customer
    finally:
        session.close()


def _list_voice_packs(settings: Any | None = None) -> list[dict[str, Any]]:
    from engine.pack.voice_clone import (
        bundled_voice_packs_root,
        customer_brand_voice_dir,
        _load_pack_dir,
    )

    # Always resolve brand voice packs under the *active* customer's output root.
    if settings is None:
        settings, _customer = _active_customer_scoped_settings()
    packs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(root: Path, *, custom: bool) -> None:
        if not root.is_dir():
            return
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            pack = _load_pack_dir(child, pack_id=child.name)
            if pack is None or pack.id in seen:
                continue
            seen.add(pack.id)
            from engine.pack.voice_clone import pack_confirmed

            packs.append(
                {
                    "id": pack.id,
                    "label": pack.label,
                    "chars_per_sec_zh": pack.chars_per_sec_zh,
                    "speed": pack.speed,
                    "engine": pack.engine,
                    "custom": custom,
                    "readonly": not custom,
                    "confirmed": pack_confirmed(pack.root, custom=custom),
                }
            )

    brand = customer_brand_voice_dir(settings)
    if brand:
        _add(brand / "packs", custom=True)
        # Legacy single-pack brand/voice root
        legacy = _load_pack_dir(brand, pack_id=brand.name)
        if legacy and legacy.id not in seen:
            seen.add(legacy.id)
            from engine.pack.voice_clone import pack_confirmed

            packs.append(
                {
                    "id": legacy.id,
                    "label": legacy.label,
                    "chars_per_sec_zh": legacy.chars_per_sec_zh,
                    "speed": legacy.speed,
                    "engine": legacy.engine,
                    "custom": True,
                    "readonly": False,
                    "confirmed": pack_confirmed(legacy.root, custom=True),
                }
            )
    _add(bundled_voice_packs_root(), custom=False)
    return packs


def _apply_voice_to_video_lock(
    *,
    customer_name: str,
    output_root: Path | str,
    provider: str,
    voice_pack: str,
    clone_speed: float,
    edge_voice: str | None = None,
) -> dict[str, Any]:
    """Update on-disk VIDEO_LOCK.voice for the active customer (05-品牌; never App bundle)."""
    from engine.pack.edge_voices import edge_voice_label
    from engine.pack.video_lock import load_video_lock
    from engine.pack.voice_clone import is_clone_provider

    lock = load_video_lock(customer_name, output_root=output_root)
    voice = dict(lock.get("voice") if isinstance(lock.get("voice"), dict) else {})
    if is_clone_provider(provider):
        voice.update(
            {
                "provider": "clone",
                "voice_pack": voice_pack,
                "voice": voice_pack,
                "label": "阿姨慢速旁白" if voice_pack == "aunt_slow" else voice_pack,
                "clone_speed": float(clone_speed),
                "chars_per_sec_zh": 3.3 if voice_pack == "aunt_slow" else float(voice.get("chars_per_sec_zh") or 3.3),
                "rate": "clone",
                "pitch": "clone",
                "volume": "clone",
                "emotion_boost": False,
            }
        )
    else:
        voice_id = (edge_voice or "").strip()
        if not voice_id:
            prev = str(voice.get("voice") or "").strip()
            voice_id = prev if "Neural" in prev else "zh-CN-XiaoxiaoNeural"
        if "Neural" not in voice_id:
            voice_id = "zh-CN-XiaoxiaoNeural"
        # Switching off clone: "clone" is not a valid Edge SSML rate/pitch/volume.
        prev_rate = str(voice.get("rate") or "").strip()
        prev_pitch = str(voice.get("pitch") or "").strip()
        prev_volume = str(voice.get("volume") or "").strip()
        if prev_rate in {"", "clone"} or not prev_rate.endswith("%"):
            prev_rate = "-8%"
        if prev_pitch in {"", "clone"}:
            prev_pitch = "+18Hz"
        if prev_volume in {"", "clone"}:
            prev_volume = "+10%"
        voice.update(
            {
                "provider": "edge",
                "voice": voice_id,
                "label": edge_voice_label(voice_id),
                "rate": prev_rate,
                "pitch": prev_pitch,
                "volume": prev_volume,
                "chars_per_sec_zh": 3.2 if prev_rate == "-8%" else float(voice.get("chars_per_sec_zh") or 3.8),
                "emotion_boost": True,
            }
        )
        voice.pop("voice_pack", None)
        voice.pop("clone_speed", None)
    lock["voice"] = voice

    from engine.pack.video_lock import writable_lock_paths_for_customer

    written: list[str] = []
    for path in writable_lock_paths_for_customer(customer_name, output_root=output_root):
        # Only write VIDEO_LOCK.json (not style_lock). Never write into the signed
        # App runtime — that creates RUNTIME_MANIFEST extras and blocks engine start.
        if path.name != "VIDEO_LOCK.json":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(str(path))
    return {"voice": voice, "written": written}


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
    review: dict[str, Any] | None = None
    industry_pack: str | None = None


class CustomerActivate(BaseModel):
    name: str


class DryRunRequest(BaseModel):
    template_name: str = "default-vertical"
    theme: str = "default"
    category: str = "default"
    orientation: str = Field(default="portrait", pattern="^(portrait|landscape)$")
    customer_name: str | None = None
    seed: int | None = None
    strict_semantic_v1: bool = False
    rule_profile_id: int | None = None
    use_active_rule: bool = True
    topic_intent: TopicIntentRequest | None = None


class VerifyClipletsRequest(BaseModel):
    cliplet_ids: list[int] = Field(min_length=1, max_length=10)
    force: bool = False


def _active_scope(session, settings: AppSettings | None = None):
    from engine.api.scope import active_scope

    return active_scope(session, settings)


def _resolve_job_customer(session, settings: AppSettings, customer_name: str | None):
    from engine.api.scope import resolve_job_customer

    return resolve_job_customer(session, settings, customer_name)


@app.get("/readiness")
def engine_readiness() -> dict[str, Any]:
    """Business readiness: license + workspace + runtime integrity (not just HTTP alive)."""
    from engine.api.readiness import build_readiness_snapshot

    return build_readiness_snapshot()


@app.get("/license/status")
def runtime_license_status() -> dict[str, Any]:
    """License status for the desktop control plane and ops remote status."""
    from engine.security.license import (
        is_packaged_runtime,
        license_status,
    )

    if not is_packaged_runtime():
        return {
            "authorized": True,
            "development_build": True,
            "license_kind": "development",
            "locked_reason": "",
            "code": "",
            "issue_seq": None,
            "expires_at": None,
            "device_key_id": None,
            "license_id": None,
            "delivery_id": None,
            "customer_ref": None,
            "remaining_sec": None,
            "trial_remaining_sec": None,
            "ops_unlock_allowed": False,
        }
    try:
        status = license_status(
            allow_cached_device_binding=os.environ.get("SUYING_ALLOW_CACHED_LICENSE_BINDING") == "1"
        )
        status.setdefault("development_build", False)
        status.setdefault("code", status.get("code") or "")
        return status
    except Exception as exc:
        return {
            "authorized": False,
            "development_build": False,
            "license_kind": "",
            "locked_reason": str(exc),
            "code": "INVALID",
            "issue_seq": None,
            "expires_at": None,
            "device_key_id": None,
            "license_id": None,
            "delivery_id": None,
            "customer_ref": None,
            "remaining_sec": None,
            "trial_remaining_sec": None,
            "ops_unlock_allowed": False,
        }


@app.get("/health")
def health() -> dict[str, Any]:
    from engine.config.workspace import probe_workspace
    from engine.runtime import boot_state

    settings = load_settings()
    boot = boot_state.snapshot()
    # Early bind only while lifespan is actively starting services (not module default).
    if boot.get("boot_phase") == "starting":
        return {
            "status": "starting",
            "boot": boot,
            "product_name": settings.product_name,
            "engine_version": ENGINE_VERSION,
            "active_customer": settings.active_customer,
            "worker_running": worker.is_alive(),
            "scheduler_running": scheduler.is_alive(),
            "watcher_running": bool(watcher and watcher.is_alive()),
        }
    if boot.get("boot_phase") == "failed":
        return {
            "status": "failed",
            "boot": boot,
            "product_name": settings.product_name,
            "engine_version": ENGINE_VERSION,
            "active_customer": settings.active_customer,
            "error": boot.get("boot_error"),
        }

    probe = probe_workspace(settings)
    rt = pause_coordinator.snapshot()
    from engine.catalog.ollama_status import check_ollama

    ollama = check_ollama()

    if not probe.can_init_db or probe.state not in ("ready", "local"):
        return {
            "status": "blocked",
            "boot": boot,
            "workspace_state": probe.state,
            "workspace": probe.to_dict(),
            "product_name": settings.product_name,
            "engine_version": ENGINE_VERSION,
            "active_customer": settings.active_customer,
            "active_customer_id": None,
            "paths": settings.paths.model_dump(mode="json"),
            "frames_root": str(settings.paths.frames_root()),
            "render_root": str(settings.paths.render_root),
            "vector_store": {
                "kind": "sqlite",
                "db": str(settings.paths.data_root / "montage.db"),
                "table": "cliplets",
                "column": "embedding_json",
                "note": "外接工作区未就绪；拒绝创建空库",
            },
            "path_health": {
                "ok": False,
                "library_mounted": False,
                "output_mounted": False,
                "free_disk_gb": 0,
                "warnings": list(probe.warnings),
                "errors": list(probe.reasons),
                "libraries": [],
            },
            "disk": {"ok": False, "errors": list(probe.reasons)},
            "auto_daily_enabled": settings.auto_daily_enabled,
            "auto_daily_hour": settings.auto_daily_hour,
            "vectorization_enabled": settings.vectorization_enabled,
            "onboarded": settings.onboarded,
            "setup_complete": False,
            "worker_running": worker.is_alive(),
            "scheduler_running": scheduler.is_alive(),
            "watcher_running": bool(watcher and watcher.is_alive()),
            "runtime_state": rt.get("state"),
            "pause_reasons": rt.get("pause_reasons"),
            "system_event_generation": rt.get("generation"),
            "resume_blockers": rt.get("resume_blockers"),
            "system_event_control": get_system_event_control_from_settings(settings).model_dump(),
            "ollama": {
                "reachable": ollama.get("reachable"),
                "ready": ollama.get("ready"),
                "message": ollama.get("message"),
            },
        }

    session = get_session()
    try:
        _, customer, scoped = _active_scope(session, settings)
        ensure_layout(scoped)
        health_info = check_paths(scoped)
        from engine.ingest.orientation import orientation_lock_snapshot

        orientation_lock = orientation_lock_snapshot(session, customer_id=customer.id)
    finally:
        session.close()
    disks = disk_report(settings)
    status = "ok" if health_info.ok else "degraded"
    return {
        "status": status,
        "boot": boot,
        "workspace_state": probe.state,
        "workspace": probe.to_dict(),
        "product_name": settings.product_name,
            "engine_version": ENGINE_VERSION,
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
        "orientation_hard_lock": True,
        "orientation_lock": orientation_lock,
        "semantic_analysis_mode": settings.semantic_analysis_mode,
        "semantic_full_backfill_enabled": effective_semantic_full_backfill_enabled(settings),
        "ollama_narration_enabled": bool(getattr(settings, "ollama_narration_enabled", False)),
        "ollama_narration_model": getattr(settings, "ollama_narration_model", "") or "",
        "ollama_narration_burn_emoji": bool(getattr(settings, "ollama_narration_burn_emoji", True)),
        "ollama_rule_model": getattr(settings, "ollama_rule_model", "") or "",
        "onboarded": settings.onboarded,
        "setup_complete": bool(customer.library_root and customer.output_root),
        "worker_running": worker.is_alive(),
        "scheduler_running": scheduler.is_alive(),
        "watcher_running": bool(watcher and watcher.is_alive()),
        "runtime_state": rt.get("state"),
        "pause_reasons": rt.get("pause_reasons"),
        "system_event_generation": rt.get("generation"),
        "resume_blockers": rt.get("resume_blockers"),
        "system_event_control": get_system_event_control_from_settings(settings).model_dump(),
        "ollama": {
            "reachable": ollama.get("reachable"),
            "embed_ready": ollama.get("embed_ready"),
            "vision_ready": ollama.get("vision_ready"),
            "escalate_ready": ollama.get("escalate_ready"),
            "ready": ollama.get("ready"),
            "message": ollama.get("message"),
            "embed_model": ollama.get("embed_model"),
            "vision_model": ollama.get("vision_model"),
            "escalate_model": ollama.get("escalate_model"),
            "cascade": ollama.get("cascade"),
            "vision_timeout_sec": ollama.get("vision_timeout_sec"),
            "escalate_timeout_sec": ollama.get("escalate_timeout_sec"),
            "vision_policy": ollama.get("vision_policy"),
            "host": ollama.get("host"),
            "recommended": ollama.get("recommended"),
            "setup_steps": ollama.get("setup_steps"),
            "install_url": ollama.get("install_url"),
            "pull": ollama.get("pull"),
        },
    }


@app.get("/health/ollama")
def health_ollama() -> dict[str, Any]:
    """Local AI dependency status without triggering expensive inference on every poll.

    Four distinct layers so the UI never shows false green when only tag
    listing succeeded: service reachable (/api/tags) → model present (exact
    tag match) → inference available (cached functional chat/embed probe,
    refreshed by background warmup/recovery, not by this endpoint) → circuit
    open (machine-wide gate tripped by recent failures).
    """
    from engine.catalog.ollama_runtime import ollama_health_snapshot
    from engine.catalog.ollama_status import check_ollama

    base = check_ollama()
    gateway = ollama_health_snapshot()
    circuit = gateway.get("circuit") or {}
    circuit_open = str(circuit.get("state") or "") == "open"
    inference_available = bool(gateway.get("chat_probe_ok")) and not circuit_open
    from engine.config.settings import load_settings
    from engine.pack.ollama_narration import resolve_narration_model

    settings = load_settings()
    narration_model = resolve_narration_model(settings)
    model_names = {str(n) for n in (base.get("models") or [])}
    short_names = {str(n).split(":")[0] for n in model_names}
    want = str(narration_model or "").strip()
    narration_model_missing = bool(
        want
        and base.get("reachable")
        and want not in model_names
        and want.split(":")[0] not in short_names
    )
    return {
        **base,
        "embed_gateway": gateway,
        "circuit": circuit,
        "circuit_open": circuit_open,
        "inference_available": inference_available,
        "ollama_narration_enabled": bool(getattr(settings, "ollama_narration_enabled", False)),
        "ollama_narration_model": narration_model,
        "chat_probe_ok": bool(gateway.get("chat_probe_ok")),
        "narration_model_missing": narration_model_missing,
        "narration_model_warning": (
            f"旁白模型未安装: {narration_model}" if narration_model_missing else ""
        ),
        "status_layers": {
            "service_reachable": bool(base.get("reachable")),
            "model_present": bool(base.get("ready")),
            "inference_available": inference_available,
            "circuit_open": circuit_open,
        },
    }


class InstallPlanRequest(BaseModel):
    plan_id: str = ""
    include_vision: bool | None = None
    include_narration: bool = False


class ApprovedInstallPlanRequest(BaseModel):
    plan_id: str


@app.get("/setup/status")
def setup_status() -> dict[str, Any]:
    """First-run environment: Python / FFmpeg / Ollama / recommended models."""
    from engine.catalog.host_profile import host_dict, probe_host, recommend_models
    from engine.catalog.ollama_status import check_ollama

    host = probe_host()
    ollama = check_ollama()
    rec = recommend_models(host)
    return {
        "host": host_dict(host),
        "recommended": rec,
        "ollama": ollama,
        "install_url": ollama.get("install_url"),
        "setup_steps": ollama.get("setup_steps") or [],
        "ready_for_vectorization": bool(ollama.get("embed_ready")),
    }


@app.get("/setup/install-plan")
def setup_install_plan(
    include_vision: bool | None = None,
    include_narration: bool = False,
) -> dict[str, Any]:
    """Preview the deterministic machine install plan without changing state."""
    from engine.ops.install_profile import build_install_plan

    return build_install_plan(
        include_vision=include_vision,
        include_narration=include_narration,
    )


@app.get("/setup/install-state")
def setup_install_state() -> dict[str, Any]:
    from engine.ops.install_profile import read_install_state

    return read_install_state()


@app.post("/setup/install-plan")
def setup_approve_install_plan(body: InstallPlanRequest) -> dict[str, Any]:
    """Approve and persist the exact plan; model downloads are a separate step."""
    from datetime import datetime, timezone

    from engine.ops.install_profile import build_install_plan, persist_install_plan

    assert_runtime_active("install_plan")
    plan = build_install_plan(
        include_vision=body.include_vision,
        include_narration=body.include_narration,
    )
    if not body.plan_id or body.plan_id != plan["plan_id"]:
        raise HTTPException(409, "PLAN_STALE：机器配置或模型选择已变化，请重新预览并确认")
    plan["approved_at"] = datetime.now(timezone.utc).isoformat()
    persist_install_plan(plan)
    return plan


@app.post("/setup/install-plan/apply")
def setup_apply_install_plan(body: ApprovedInstallPlanRequest) -> dict[str, Any]:
    """Apply an already approved immutable plan, then pull selected models."""
    from engine.catalog.ollama_status import start_pull_models
    from engine.ops.install_profile import (
        apply_install_plan_settings,
        load_approved_install_plan,
        mark_install_plan_stage,
    )

    assert_runtime_active("install_models")
    try:
        plan = load_approved_install_plan(body.plan_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    blocking = [
        str(gate["id"])
        for gate in plan["gates"]
        if gate["id"] in {"platform", "architecture", "python", "ffmpeg", "disk", "ollama"}
        and not gate["ok"]
    ]
    if blocking:
        raise HTTPException(409, f"安装门禁未通过：{', '.join(blocking)}")
    try:
        applied = apply_install_plan_settings(body.plan_id)
        plan = mark_install_plan_stage(body.plan_id, "configured")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    pull = start_pull_models(list(plan["selected_models"]))
    if not pull.get("ok"):
        raise HTTPException(409, str(pull.get("message") or "模型安装未启动"))
    plan = mark_install_plan_stage(body.plan_id, "models_started")
    return {"ok": True, "plan": plan, "settings": applied, "pull": pull}


@app.post("/setup/install-plan/configure")
def setup_configure_install_plan(body: ApprovedInstallPlanRequest) -> dict[str, Any]:
    """Apply an approved plan without downloading; used by remote installer."""
    from engine.ops.install_profile import (
        apply_install_plan_settings,
        load_approved_install_plan,
        mark_install_plan_stage,
    )

    assert_runtime_active("install_models")
    try:
        plan = load_approved_install_plan(body.plan_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    blocking = [
        str(gate["id"])
        for gate in plan["gates"]
        if gate["id"] in {"platform", "architecture", "disk"} and not gate["ok"]
    ]
    if blocking:
        raise HTTPException(409, f"安装门禁未通过：{', '.join(blocking)}")
    try:
        settings = apply_install_plan_settings(body.plan_id)
        plan = mark_install_plan_stage(body.plan_id, "configured")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "plan": plan, "settings": settings}


@app.post("/ollama/pull")
def ollama_pull(model: str = "nomic-embed-text") -> dict[str, Any]:
    """Start background `ollama pull <model>` (embed or vision)."""
    from engine.catalog.ollama_status import allowed_pull_models, start_pull

    assert_runtime_active("install_models")
    model = (model or "nomic-embed-text").strip()
    allowed = allowed_pull_models()
    if model not in allowed and model.split(":")[0] not in {a.split(":")[0] for a in allowed}:
        raise HTTPException(400, f"不允许拉取的模型: {model}；允许: {sorted(allowed)}")
    result = start_pull(model)
    if not result.get("ok") and "不允许" in str(result.get("message") or ""):
        raise HTTPException(400, result["message"])
    return result


@app.post("/ollama/pull-recommended")
def ollama_pull_recommended() -> dict[str, Any]:
    """Detect host RAM tier, persist model choices, pull embed + vision."""
    from engine.catalog.ollama_status import start_pull_recommended

    assert_runtime_active("install_models")
    return start_pull_recommended()


@app.post("/setup/apply-recommended-models")
def setup_apply_recommended_models() -> dict[str, Any]:
    """Only write recommended model names into settings (no download)."""
    from engine.catalog.ollama_status import apply_recommended_to_settings

    assert_runtime_active("install_models")
    return apply_recommended_to_settings()


@app.get("/settings")
def get_settings() -> dict[str, Any]:
    settings = load_settings()
    payload = redact_settings_payload(settings.model_dump(mode="json"))
    payload["semantic_full_backfill_enabled"] = effective_semantic_full_backfill_enabled(
        settings
    )
    return payload


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
    if body.ollama_embed_model is not None:
        settings.ollama_embed_model = body.ollama_embed_model.strip() or "nomic-embed-text"
    if body.ollama_vision_model is not None:
        settings.ollama_vision_model = body.ollama_vision_model.strip()
    if body.ollama_vision_escalate_model is not None:
        settings.ollama_vision_escalate_model = body.ollama_vision_escalate_model.strip()
    if body.ollama_vision_cascade is not None:
        settings.ollama_vision_cascade = bool(body.ollama_vision_cascade)
    if body.semantic_analysis_mode is not None:
        mode = str(body.semantic_analysis_mode).strip().lower()
        if mode not in {"off", "on_demand"}:
            raise HTTPException(400, "semantic_analysis_mode 仅支持 off / on_demand")
        if mode == "on_demand" and not bool(getattr(settings, "semantic_toggle_enabled", True)):
            raise HTTPException(409, "当前机型档位未开放语义按需（lite 请升配或安装视觉包）")
        settings.semantic_analysis_mode = mode
    lab_backfill_toggle: bool | None = None
    if body.semantic_full_backfill_enabled is not None:
        want = bool(body.semantic_full_backfill_enabled)
        if want and not bool(getattr(settings, "semantic_toggle_enabled", True)):
            raise HTTPException(
                409,
                "当前机型档位未开放严格语义全库回填（lite 请升配或安装视觉包）",
            )
        # Never persist True into settings.json (fleet-safe); lab env + worker only.
        lab_backfill_toggle = want
        settings.semantic_full_backfill_enabled = False
    if body.ollama_narration_enabled is not None:
        settings.ollama_narration_enabled = bool(body.ollama_narration_enabled)
    if body.ollama_narration_model is not None:
        settings.ollama_narration_model = body.ollama_narration_model.strip()
    if body.ollama_narration_burn_emoji is not None:
        settings.ollama_narration_burn_emoji = bool(body.ollama_narration_burn_emoji)
    if body.ollama_rule_model is not None:
        settings.ollama_rule_model = body.ollama_rule_model.strip()
    if body.system_event_control is not None:
        current = get_system_event_control_from_settings(settings).model_dump()
        current.update({k: v for k, v in body.system_event_control.items() if v is not None})
        settings.system_event_control = SystemEventControl.model_validate(current)
    if body.tts_provider is not None:
        from engine.pack.voice_clone import is_clone_provider

        raw = body.tts_provider.strip().lower()
        if is_clone_provider(raw):
            settings.tts_provider = "clone"
        elif raw in ("edge", "xiaoxiao", "say", "mock", "auto"):
            settings.tts_provider = "edge" if raw == "xiaoxiao" else raw
        else:
            raise HTTPException(400, f"不支持的 tts_provider: {body.tts_provider}")
    if body.tts_clone_pack is not None:
        settings.tts_clone_pack = body.tts_clone_pack.strip() or "aunt_slow"
    if body.tts_clone_speed is not None:
        settings.tts_clone_speed = float(body.tts_clone_speed)
    if body.tts_chars_per_sec_zh is not None:
        settings.tts_chars_per_sec_zh = float(body.tts_chars_per_sec_zh)
    save_settings(settings)
    if lab_backfill_toggle is not None:
        set_semantic_full_backfill_lab(lab_backfill_toggle)
        from engine.ops.semantic_backfill_runtime import semantic_backfill_runtime

        semantic_backfill_runtime.apply_lab_enabled(lab_backfill_toggle)
    ensure_layout(settings)
    if data_root_changed:
        from engine.catalog.db import reset_engine

        reset_engine()
        init_db(settings)
    if watcher:
        watcher.reload()
    payload = redact_settings_payload(settings.model_dump(mode="json"))
    payload["semantic_full_backfill_enabled"] = effective_semantic_full_backfill_enabled(
        settings
    )
    return payload


@app.get("/voice/tts")
def get_tts_voice_status() -> dict[str, Any]:
    """Settings UI: effective TTS provider + available clone packs."""
    from engine.pack.edge_voices import edge_voice_label
    from engine.pack.video_lock import load_video_lock
    from engine.pack.voice_clone import f5_available, is_clone_provider

    settings = load_settings()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        scoped = settings_with_customer_paths(settings, customer)
        lock = load_video_lock(customer.name, output_root=customer.output_root)
        customer_name = customer.name
        customer_id = customer.id
    finally:
        session.close()
    voice = lock.get("voice") if isinstance(lock.get("voice"), dict) else {}
    lock_provider = str(voice.get("provider") or "").strip().lower() or None
    settings_provider = str(getattr(settings, "tts_provider", "edge") or "edge").lower()
    if lock_provider:
        effective = (
            "clone"
            if is_clone_provider(lock_provider)
            else ("edge" if lock_provider in ("edge", "xiaoxiao") else lock_provider)
        )
    else:
        effective = "clone" if is_clone_provider(settings_provider) else settings_provider
    raw_voice = str(voice.get("voice") or "").strip()
    settings_voice = str(getattr(settings, "tts_voice", "") or "").strip()
    edge_voice = settings_voice or "zh-CN-XiaoxiaoNeural"
    if effective != "clone":
        if raw_voice and ("Neural" in raw_voice or "-" in raw_voice):
            # Skip obvious clone pack ids (no locale dashes), keep Neural ShortNames
            if raw_voice != str(voice.get("voice_pack") or ""):
                edge_voice = raw_voice
        label = str(voice.get("label") or "") or edge_voice_label(edge_voice)
    else:
        label = str(voice.get("label") or "")
    return {
        "ok": True,
        "customer": customer_name,
        "customer_id": customer_id,
        "settings_provider": settings_provider,
        "lock_provider": lock_provider,
        "effective_provider": effective,
        "voice_pack": str(voice.get("voice_pack") or getattr(settings, "tts_clone_pack", "") or "aunt_slow"),
        "edge_voice": edge_voice,
        "tts_voice": edge_voice,
        "label": label,
        "clone_speed": float(voice.get("clone_speed") or getattr(settings, "tts_clone_speed", 1.0) or 1.0),
        "chars_per_sec_zh": float(
            voice.get("chars_per_sec_zh") or getattr(settings, "tts_chars_per_sec_zh", 3.8) or 3.8
        ),
        "clone_available": f5_available(),
        "packs": _list_voice_packs(scoped),
        "hint": (
            "当前客户 VIDEO_LOCK 已锁定旁白音色；设置页保存会同步改锁。"
            if lock_provider
            else "未锁 VIDEO_LOCK 音色时，以本机 settings.tts_provider 为准。"
        ),
    }


@app.get("/voice/edge-voices")
def get_edge_voices(
    lang: str | None = None,
    locale: str | None = None,
    all_locales: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    """Full Edge Neural catalog for rule lab / settings selection."""
    from engine.pack.edge_voices import list_edge_voices

    return list_edge_voices(
        lang=lang,
        locale=locale,
        all_locales=bool(all_locales),
        prefer_live=bool(refresh),
    )


class VoicePackImportBody(BaseModel):
    source_path: str
    label: str
    ref_text: str
    pack_id: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=1.5)
    confirm_authorized: bool = False


class VoicePackPatchBody(BaseModel):
    label: str | None = None
    ref_text: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=1.5)


@app.post("/voice/packs/import")
def import_voice_pack(body: VoicePackImportBody) -> dict[str, Any]:
    from engine.pack.voice_clone import f5_available, import_customer_voice_pack

    settings = load_settings()
    if not bool(getattr(settings, "custom_voice_import_allowed", True)):
        raise HTTPException(409, "当前机型档位未开放自定义克隆音色导入")
    if not f5_available():
        raise HTTPException(400, "本机未安装 f5-tts，无法导入克隆音色")
    if not body.confirm_authorized:
        raise HTTPException(400, "请确认参考音已获本人/客户授权")
    scoped, _customer = _active_customer_scoped_settings()
    try:
        pack = import_customer_voice_pack(
            scoped,
            source_audio=body.source_path,
            label=body.label,
            ref_text=body.ref_text,
            pack_id=body.pack_id,
            speed=float(body.speed),
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "pack": pack.to_dict(), "packs": _list_voice_packs(scoped)}


@app.patch("/voice/packs/{pack_id}")
def patch_voice_pack(pack_id: str, body: VoicePackPatchBody) -> dict[str, Any]:
    from engine.pack.voice_clone import patch_customer_voice_pack

    scoped, _customer = _active_customer_scoped_settings()
    try:
        pack = patch_customer_voice_pack(
            scoped,
            pack_id,
            label=body.label,
            ref_text=body.ref_text,
            speed=body.speed,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "pack": pack.to_dict(), "packs": _list_voice_packs(scoped)}


@app.delete("/voice/packs/{pack_id}")
def delete_voice_pack(pack_id: str) -> dict[str, Any]:
    from engine.pack.voice_clone import delete_customer_voice_pack

    scoped, _customer = _active_customer_scoped_settings()
    try:
        delete_customer_voice_pack(scoped, pack_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "packs": _list_voice_packs(scoped)}


@app.post("/voice/packs/{pack_id}/confirm")
def confirm_voice_pack(pack_id: str) -> dict[str, Any]:
    from engine.pack.voice_clone import confirm_customer_voice_pack

    scoped, _customer = _active_customer_scoped_settings()
    try:
        pack = confirm_customer_voice_pack(scoped, pack_id)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "pack": pack.to_dict(), "packs": _list_voice_packs(scoped)}


class VoicePreviewBody(BaseModel):
    text: str = Field(default="您好，这是自定义音色试听。", min_length=2, max_length=80)
    voice_pack: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=1.5)


@app.post("/voice/packs/preview")
def preview_voice_pack(body: VoicePreviewBody) -> dict[str, Any]:
    """Synthesize a short clone preview clip (TTS single-slot)."""
    assert_runtime_active("voice_preview")
    import shutil
    import subprocess
    import sys

    from engine.pack.tts import narration_bed_from_result, synthesize_script
    from engine.pack.voice_clone import f5_available

    settings = load_settings()
    if not bool(getattr(settings, "clone_tts_allowed", True)):
        raise HTTPException(409, "当前机型档位未开放本地克隆 TTS")
    if not f5_available():
        raise HTTPException(400, "本机未安装 f5-tts，无法试听克隆音色")
    pack = (body.voice_pack or getattr(settings, "tts_clone_pack", "") or "aunt_slow").strip()
    out_dir = Path(settings.paths.cache_root) / "tts" / f"voice_preview_{uuid.uuid4().hex[:12]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        narr = synthesize_script(
            body.text.strip(),
            out_dir,
            provider="clone",
            clone_pack_id=pack,
            clone_speed=float(body.speed),
            allow_fallback=False,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"试听失败: {exc}") from exc

    # synthesize_script returns NarrationResult (segments[]), not a flat audio_path.
    bed = out_dir / "preview.wav"
    playing = False
    try:
        narration_bed_from_result(narr, bed)
    except Exception as exc:  # noqa: BLE001
        segs = list(getattr(narr, "segments", None) or [])
        if not segs:
            raise HTTPException(400, f"试听无波形: {exc}") from exc
        shutil.copy2(segs[0].audio_path, bed)
    if not bed.is_file() or bed.stat().st_size < 100:
        raise HTTPException(400, "试听波形生成失败")
    audio = str(bed.resolve())
    duration = float(getattr(narr, "total_duration_sec", 0) or 0) or None

    # macOS desktop: system play so 试听 works even when App UI only shows a path.
    if sys.platform == "darwin" and shutil.which("afplay"):
        try:
            subprocess.Popen(  # noqa: S603
                ["afplay", audio],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            playing = True
        except OSError:
            playing = False

    return {
        "ok": True,
        "audio_path": audio,
        "pack": pack,
        "duration_sec": duration,
        "playing": playing,
    }


@app.put("/voice/tts")
def put_tts_voice_preference(body: TtsPreferenceUpdate) -> dict[str, Any]:
    from engine.pack.voice_clone import f5_available, is_clone_provider, pack_confirmed, resolve_voice_pack

    settings = load_settings()
    raw = (body.provider or "").strip().lower()
    if is_clone_provider(raw):
        provider = "clone"
        if not bool(getattr(settings, "clone_tts_allowed", True)):
            raise HTTPException(409, "当前机型档位未开放本地克隆 TTS")
        if not f5_available():
            raise HTTPException(400, "本机未安装 f5-tts，无法启用本地克隆音色（pip install f5-tts）")
    elif raw in ("edge", "xiaoxiao"):
        provider = "edge"
    else:
        raise HTTPException(400, "provider 仅支持 edge 或 clone")

    pack = (body.voice_pack or "aunt_slow").strip() or "aunt_slow"
    speed = float(body.clone_speed if body.clone_speed is not None else 1.0)
    edge_voice = (body.voice or "").strip() or str(getattr(settings, "tts_voice", "") or "").strip()
    session = get_session()
    try:
        customer = require_active_customer(session, settings)
        scoped = settings_with_customer_paths(settings, customer)
        if provider == "clone":
            try:
                resolved = resolve_voice_pack(pack_id=pack, settings=scoped)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(400, str(exc)) from exc
            brand = str(resolved.root or "")
            custom = "/packs/" in brand.replace("\\", "/")
            if custom and not pack_confirmed(resolved.root, custom=True):
                raise HTTPException(400, "请先试听并确认自定义音色后再启用")

        settings.tts_provider = provider
        if provider == "clone":
            settings.tts_clone_pack = pack
            settings.tts_clone_speed = speed
            settings.tts_chars_per_sec_zh = 3.3 if pack == "aunt_slow" else settings.tts_chars_per_sec_zh
        else:
            if edge_voice and "Neural" in edge_voice:
                settings.tts_voice = edge_voice
            settings.tts_chars_per_sec_zh = 3.8
        save_settings(settings)

        lock_result: dict[str, Any] = {}
        if body.update_video_lock:
            lock_result = _apply_voice_to_video_lock(
                customer_name=customer.name,
                output_root=customer.output_root,
                provider=provider,
                voice_pack=pack,
                clone_speed=speed,
                edge_voice=edge_voice if provider == "edge" else None,
            )
        customer_name = customer.name
    finally:
        session.close()

    status = get_tts_voice_status()
    status["saved"] = True
    status["customer"] = customer_name
    status["lock_written"] = lock_result.get("written") or []
    return status


# Customers/assets/keywords/templates/dry-run: engine.api.catalog_routes

# Jobs + calendar routes: engine.api.job_routes (include_router)



# Library/review: engine.api.library_review_routes

@app.get("/ops/disk")
def ops_disk() -> dict[str, Any]:
    return disk_report()


@app.get("/ops/resource-gate")
def ops_resource_gate() -> dict[str, Any]:
    """Who holds produce/render/tts/publish slots (overview / ops)."""
    from engine.runtime.resource_gate import gate as resource_gate

    return {"ok": True, **resource_gate.snapshot()}


class ResourceGateForceReleaseRequest(BaseModel):
    slot: str = "all"
    older_than_sec: float | None = None


@app.post("/ops/resource-gate/force-release")
def ops_resource_gate_force_release(
    body: ResourceGateForceReleaseRequest | None = None,
) -> dict[str, Any]:
    """Force-drop expired ResourceGate holders (ops escape hatch).

    Only holders older than ``older_than_sec`` (or slot default lease) are released.
    """
    from engine.runtime.resource_gate import gate as resource_gate

    payload = body or ResourceGateForceReleaseRequest()
    slot = str(payload.slot or "all").strip() or "all"
    older_f = float(payload.older_than_sec) if payload.older_than_sec is not None else None
    released = resource_gate.force_release_expired(
        None if slot == "all" else slot,
        older_than_sec=older_f,
    )
    return {
        "ok": True,
        "slot": slot,
        "older_than_sec": older_f,
        "released": released,
        "resource_gate": resource_gate.snapshot(),
    }


@app.get("/ops/runtime-health")
def ops_runtime_health() -> dict[str, Any]:
    """Control-plane health: pause, accept work, health QPS, resource slots."""
    from engine.runtime.health_metrics import health_metrics
    from engine.runtime.resource_gate import gate as resource_gate

    pause = pause_coordinator.snapshot()
    accepts = bool(pause_coordinator.is_active_for_new_work()) and bool(
        pause_coordinator.should_claim_jobs()
    )
    from engine.catalog.ollama_runtime import embed_gateway_snapshot
    from engine.ops.ollama_service import service_snapshot

    return {
        "ok": True,
        "accepts_new_work": accepts,
        "pause": pause,
        "health": health_metrics.snapshot(),
        "resource_gate": resource_gate.snapshot(),
        "ollama_embed_gateway": embed_gateway_snapshot(),
        "ollama_service": service_snapshot(),
        "worker_running": worker.is_alive(),
        "scheduler_running": scheduler.is_alive(),
        "watcher_running": bool(watcher and watcher.is_alive()),
    }


@app.post("/ops/chrome-profiles/migrate-workspace")
def ops_chrome_migrate_workspace(force: bool = False) -> dict[str, Any]:
    """Explicit workspace→APFS chrome merge (never runs as sync lifespan I/O).

    Without force=1, only reports what would be deferred / skipped.
    """
    from engine.reach.browser import migrate_legacy_chrome_profiles

    if not force:
        # Dry probe: run with skip env temporarily? Better: call with force=False
        # which still defers cross-volume; return last report from a non-force pass.
        report = migrate_legacy_chrome_profiles(force_workspace_migrate=False)
        return {
            "ok": True,
            "forced": False,
            "message": "未 force：异卷工作区 merge 仍延期。需要拷贝时 POST ?force=1",
            "report": report,
        }
    report = migrate_legacy_chrome_profiles(force_workspace_migrate=True)
    return {"ok": True, "forced": True, "report": report}


@app.get("/ops/carrier")
def ops_carrier() -> dict[str, Any]:
    """T2S carrier status (install/backup/update only — no media sync)."""
    from engine.ops.carrier import carrier_status
    from engine.ops.suying_sync import check_zspace_bind

    bind = check_zspace_bind()
    return carrier_status(bound_nas_ok=bool(bind.get("bound_ok") and bind.get("match")))


@app.get("/ops/keyword-stats")
def ops_keyword_stats() -> dict[str, Any]:
    from engine.ops.keyword_stats import keyword_stats

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        return keyword_stats(
            session,
            customer_id=customer.id,
            keyword_pack_path=customer.keyword_pack_path,
        )
    finally:
        session.close()


@app.get("/ops/app-update")
def ops_app_update_check() -> dict[str, Any]:
    from engine.ops.app_update import check_update

    return check_update()


class AppUpdateInstallRequest(BaseModel):
    apply: bool = False


@app.post("/ops/app-update/install")
def ops_app_update_install(body: AppUpdateInstallRequest) -> dict[str, Any]:
    """Click-to-install from carrier. apply=false only verifies sha256."""
    from engine.ops.app_update import install_update

    assert_runtime_active("app_update")
    return install_update(apply=bool(body.apply))


class CarrierBackupRequest(BaseModel):
    carrier_root: str | None = None


@app.post("/ops/carrier/backup")
def ops_carrier_backup(body: CarrierBackupRequest) -> dict[str, Any]:
    from engine.ops.carrier import (
        ensure_carrier_layout,
        export_config_backup,
        resolve_carrier_root,
    )

    assert_runtime_active("carrier_backup")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        try:
            root = resolve_carrier_root(body.carrier_root)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        ensure_carrier_layout(root)
        payload = {
            "customer": {
                "id": customer.id,
                "name": customer.name,
            },
            "settings": {
                "active_customer": settings.active_customer,
                "tts_provider": getattr(settings, "tts_provider", "edge"),
            },
        }
        return export_config_backup(
            carrier_root=root,
            customer_id=customer.id,
            customer_name=customer.name,
            payload=payload,
        )
    finally:
        session.close()


@app.post("/ops/carrier/ensure")
def ops_carrier_ensure(carrier_root: str | None = None) -> dict[str, Any]:
    from engine.ops.carrier import (
        ensure_carrier_layout,
        resolve_carrier_root,
        seed_industry_into_carrier,
    )

    assert_runtime_active("carrier_ensure")
    try:
        root = resolve_carrier_root(carrier_root)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    ensure_carrier_layout(root)
    repo = Path(__file__).resolve().parents[2]
    seeded = seed_industry_into_carrier(root, repo / "configs")
    return {"ok": True, "root": str(root), "seed": seeded}


@app.get("/ops/carrier/seeds")
def ops_carrier_seeds() -> dict[str, Any]:
    """Optional customer configuration seeds available on the T2S carrier."""
    from engine.ops.carrier import list_customer_seeds

    seeds = list_customer_seeds()
    for seed in seeds:
        seed.pop("_seed_dir", None)
        seed.pop("_carrier_root", None)
    return {"ok": True, "seeds": seeds}


class CarrierSeedImportRequest(BaseModel):
    seed_id: str
    overwrite: bool = False


@app.post("/ops/carrier/seeds/import")
def ops_carrier_seed_import(body: CarrierSeedImportRequest) -> dict[str, Any]:
    """Import one optional config-only seed into the active customer's folders."""
    from engine.ops.carrier import import_customer_seed

    assert_runtime_active("carrier_seed_import")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = _active_scope(session, settings)
        output_root = Path(customer.output_root or settings.paths.output_root).expanduser().resolve()
        customer_root = output_root.parent
        try:
            result = import_customer_seed(
                seed_id=body.seed_id.strip(),
                customer_root=customer_root,
                overwrite=bool(body.overwrite),
                session=session,
                customer=customer,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

        profile = dict(customer.profile_json or {})
        seed_profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
        for key, value in seed_profile.items():
            if isinstance(value, dict) and isinstance(profile.get(key), dict):
                profile[key] = {**profile[key], **value}
            else:
                profile[key] = value
        customer.profile_json = profile
        session.commit()
        result["customer_id"] = customer.id
        result["customer_name"] = customer.name
        result["customer_root"] = str(customer_root)
        result["keyword_pack_path"] = customer.keyword_pack_path
        return result
    finally:
        session.close()


class CarrierRestoreRequest(BaseModel):
    backup_path: str


@app.post("/ops/carrier/restore")
def ops_carrier_restore(body: CarrierRestoreRequest) -> dict[str, Any]:
    """Restore config-level backup from T2S carrier (no media)."""
    from engine.ops.carrier import resolve_carrier_backup, restore_config_backup

    assert_runtime_active("carrier_restore")
    try:
        return restore_config_backup(resolve_carrier_backup(body.backup_path))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/ops/carrier/install-update-agent")
def ops_carrier_install_update_agent() -> dict[str, Any]:
    """Install LaunchAgent that polls carrier app/latest.json (no media sync)."""
    import subprocess
    from pathlib import Path

    assert_runtime_active("carrier_update_agent")
    script = Path(__file__).resolve().parents[2] / "scripts" / "install-carrier-update-agent.sh"
    if not script.is_file():
        raise HTTPException(404, f"缺少脚本: {script}")
    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-1000:],
        "script": str(script),
    }


@app.post("/ops/ollama-narration/preview")
def ops_ollama_narration_preview(
    theme: str = "default",
    brand: str = "演示品牌",
    hint: str = "真实现场记录，人物认真工作，展示服务细节",
) -> dict[str, Any]:
    """Dry-run Ollama narration rewrite (ops / smoke)."""
    assert_runtime_active("narration_preview")
    from engine.config.settings import load_settings
    from engine.pack.ollama_narration import resolve_narration_model, rewrite_narration_with_ollama

    settings = load_settings()
    model = resolve_narration_model(settings)
    result = rewrite_narration_with_ollama(
        base_script="真实现场认真记录，用心服务更省心。",
        visual_hints=[hint],
        theme=theme,
        brand=brand,
        target_duration_sec=18.0,
        model=model,
        title="真实记录",
        timeout=120.0,
    )
    return {"enabled": bool(settings.ollama_narration_enabled), "model": model, **result}


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
    if action == "start":
        assert_runtime_active(f"start_service:{name}")
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
    model_config = {"populate_by_name": True}
    name: str
    # alias keeps API body `{register:true}`; avoid shadowing BaseModel.register
    do_register: bool = Field(True, alias="register")


class ZSpaceBind(BaseModel):
    username: str
    nas_id: str
    nas_name: str = ""


class ZSpaceMediaSetSource(BaseModel):
    """Team-space folder name -> local media library mapping (write into suying-sync.json only)."""

    customer_name: str
    remote_person: str
    remote_base: str = "手机相册备份"


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
    from engine.ops.suying_sync import ensure_customer_dirs

    try:
        result = ensure_customer_dirs(body.name, register=body.do_register)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return result


@app.get("/ops/zspace-sync/media-remote-folders")
def ops_zspace_sync_media_remote_folders(remote_base: str = "手机相册备份") -> dict[str, Any]:
    """List immediate remote folders under /public/<remote_base>/.

    Returns only folder names (no hardcoded customer names).
    """
    from engine.ops.suying_sync import list_remote_folders

    try:
        folders = list_remote_folders(remote_base)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "remote_base": remote_base, "folders": folders}


@app.post("/ops/zspace-sync/media-set-source")
def ops_zspace_sync_media_set_source(body: ZSpaceMediaSetSource) -> dict[str, Any]:
    """Upsert v3 media_sources mapping for a customer."""
    from engine.ops.suying_sync import upsert_media_source

    try:
        return upsert_media_source(
            customer_name=body.customer_name,
            remote_person=body.remote_person,
            remote_base_rel=body.remote_base,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/ops/zspace-sync/media-preview")
def ops_zspace_sync_media_preview(
    customer_name: str,
    remote_person: str,
    remote_base: str = "手机相册备份",
) -> dict[str, Any]:
    """Preview media source mapping change (no write)."""
    from engine.ops.suying_sync import preview_media_source

    try:
        return preview_media_source(
            customer_name=customer_name,
            remote_person=remote_person,
            remote_base_rel=remote_base,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/ops/zspace-sync/reconcile")
def ops_zspace_sync_reconcile() -> dict[str, Any]:
    from engine.ops.suying_sync import reconcile_sync_customers

    return reconcile_sync_customers()


@app.post("/ops/zspace-sync/dry-run")
def ops_zspace_sync_dry_run(pull_only: bool = True) -> dict[str, Any]:
    from engine.ops.suying_sync import run_sync_dry_run

    try:
        return run_sync_dry_run(pull_only=pull_only)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
