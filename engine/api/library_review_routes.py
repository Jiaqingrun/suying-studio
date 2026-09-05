"""Outputs, review, reports, index, cliplets, logs (zero-semantics extract)."""

from __future__ import annotations

import io
import json
import os
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from engine.api.scope import active_scope
from engine.catalog.db import (
    Asset,
    Cliplet,
    Job,
    JobEvent,
    KeywordUsage,
    RenderOutput,
    ReviewItem,
    get_session,
)
from engine.config.settings import load_settings
from engine.runtime.pause_coordinator import assert_runtime_active


from engine.ingest.cliplet import (
    clear_semantic_claims,
    recaption_existing_cliplets,
    semantic_backfill_progress,
    verify_cliplets_on_demand,
)
from engine.ops.maintenance import assert_production_ready
from engine.ops.semantic_backfill_runtime import CAPTIONS_BACKFILL_LOCK
from engine.export.csv_export import export_job_events_csv, export_renders_csv
from engine.catalog.vector_index import search_cliplets
from engine.jobs.queue import create_job, CreateJobRequest, TopicIntentRequest
from engine.jobs.worker import worker
from engine.template.engine import DEFAULT_TEMPLATE, TemplateDefinition, build_plan, plan_to_dict

router = APIRouter(tags=["library-review"])


class VerifyClipletsRequest(BaseModel):
    cliplet_ids: list[int] = Field(min_length=1, max_length=10)
    force: bool = False


def scoped_output(session, output_id: int, customer_id: int) -> RenderOutput:
    from engine.api.output_scope import scoped_output as impl
    return impl(session, output_id, customer_id)


def output_cover_paths(out: RenderOutput) -> list[str]:
    from engine.api.output_scope import output_cover_paths as impl
    return impl(out)


@router.get("/outputs")
def list_outputs(
    state: str | None = None,
    missing_voice: bool = False,
    missing_subtitle: bool = False,
    noncompliant_tts: bool = False,
) -> list[dict[str, Any]]:
    from pathlib import Path

    from engine.catalog.serial import backfill_display_nos, format_display_no
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
        _, customer, _ = active_scope(session, settings)
        backfill_display_nos(session, customer_id=customer.id)
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
            content_fingerprint: dict[str, Any] = {}
            shot_script: list[dict[str, Any]] = []
            side_theme = ""
            side_category = ""
            if r.sidecar_path:
                try:
                    import json

                    data = json.loads(Path(r.sidecar_path).read_text(encoding="utf-8"))
                    title = str(data.get("title") or "")
                    side_theme = str(data.get("theme") or "")
                    side_category = str(data.get("category") or "")
                    if not covers:
                        covers = list(data.get("covers") or [])
                    if isinstance(data.get("copywriting"), dict):
                        copywriting = data["copywriting"]
                    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                    fp = data.get("content_fingerprint")
                    if isinstance(fp, dict):
                        content_fingerprint = fp
                        raw_shots = fp.get("shot_script")
                        if isinstance(raw_shots, list):
                            for shot in raw_shots[:24]:
                                if not isinstance(shot, dict):
                                    continue
                                shot_script.append(
                                    {
                                        "bucket": str(shot.get("bucket") or ""),
                                        "role": str(shot.get("role") or ""),
                                        "text": str(shot.get("text") or ""),
                                        "anchors": [
                                            str(a)
                                            for a in (shot.get("anchors") or [])
                                            if str(a).strip()
                                        ][:6],
                                    }
                                )
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
                .where(
                    ReviewItem.render_output_id == r.id,
                    ReviewItem.is_current.is_(True),
                )
                .order_by(ReviewItem.id.desc())
                .limit(1)
            ).first()
            publish_trail = merge_publish_trail(session, r.id, meta)
            from engine.catalog.review_auto import ready_gate_snapshot

            gate_snap = ready_gate_snapshot(
                r.qc_json if isinstance(r.qc_json, dict) else None
            )
            result.append(
                {
                    "id": r.id,
                    "job_id": r.job_id,
                    "state": r.state,
                    "seed": r.seed,
                    "serial": getattr(r, "serial", None),
                    "display_no": getattr(r, "display_no", None),
                    "display_label": format_display_no(getattr(r, "display_no", None)) or None,
                    "orientation": getattr(r, "orientation", "portrait"),
                    "title": title,
                    "output_path": r.output_path,
                    "sidecar_path": r.sidecar_path,
                    "covers": covers,
                    "qc": r.qc_json,
                    "media_ok": media_ok,
                    "cover_count": len(covers),
                    "review_status": (
                        last_review.status
                        if last_review
                        else ("uncertain" if r.state == "review" else None)
                    ),
                    "review_note": last_review.note if last_review else None,
                    "review_source": last_review.decision_source if last_review else None,
                    "pack_status": r.pack_status,
                    "pack_dir": r.pack_dir,
                    "pack_error": r.pack_error,
                    "pack_version": r.pack_version,
                    "platform_asset_status": r.platform_asset_status or {},
                    "has_voice": has_voice,
                    "subtitle_burned": subtitle_burned,
                    "tts_provider": tts_provider_from_meta(meta) or None,
                    "tts_compliant": violation is None,
                    "tts_noncompliant": violation,
                    "copywriting": copywriting,
                    "publish_trail": publish_trail,
                    "published": bool(publish_trail),
                    "theme": side_theme or None,
                    "category": side_category or None,
                    "production_mode": (
                        str(content_fingerprint.get("production_mode") or "")
                        or None
                    ),
                    "shot_script": shot_script,
                    "validation_report": (
                        content_fingerprint.get("validation_report")
                        if isinstance(content_fingerprint.get("validation_report"), dict)
                        else None
                    ),
                    **gate_snap,
                }
            )
        return result
    finally:
        session.close()


@router.get("/outputs/{output_id}/publish-card")
def output_publish_card(output_id: int) -> dict[str, Any]:
    """Title + per-platform copy + local paths for the Publish desk tab."""
    from pathlib import Path

    from engine.pack.publish import build_platform_copy

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        out = scoped_output(session, output_id, customer.id)
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
            "serial": getattr(out, "serial", None) or side.get("serial"),
            "display_no": getattr(out, "display_no", None) or side.get("display_no"),
            "display_label": (
                f"#{int(out.display_no):03d}"
                if getattr(out, "display_no", None)
                else (str(side.get("display_label") or "") or None)
            ),
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


def scoped_output(session, output_id: int, customer_id: int) -> RenderOutput:
    out = session.get(RenderOutput, output_id)
    if not out:
        raise HTTPException(404, "output not found")
    if out.job_id:
        job = session.get(Job, out.job_id)
        if job and job.customer_id and job.customer_id != customer_id:
            raise HTTPException(403, "output 不属于当前客户")
    return out


def output_cover_paths(out: RenderOutput) -> list[str]:
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


@router.api_route("/outputs/{output_id}/video", methods=["GET", "HEAD"])
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


@router.api_route("/outputs/{output_id}/cover/{index}", methods=["GET", "HEAD"])
def stream_output_cover(output_id: int, index: int = 0):
    """Serve cover frame for review thumbnails (GET+HEAD)."""
    from pathlib import Path

    from fastapi.responses import FileResponse

    session = get_session()
    try:
        out = session.get(RenderOutput, output_id)
        if not out:
            raise HTTPException(404, "output not found")
        covers = output_cover_paths(out)
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


@router.get("/expression/languages")
def list_expression_languages() -> dict[str, Any]:
    """Global language catalog for voice / subtitle selectors (物料页)."""
    from engine.pack.languages import languages_for_api
    from engine.pack.locale_cache import ensure_locale_cache

    settings = load_settings()
    cache_info = ensure_locale_cache(settings.paths.data_root)
    rows = languages_for_api(include_none=True)
    regions: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        regions.setdefault(str(row.get("region") or "其他"), []).append(row)
    return {
        "languages": rows,
        "regions": regions,
        "locale_cache": {
            "ok": bool(cache_info.get("ok")),
            "root": cache_info.get("root"),
            "count": cache_info.get("count"),
        },
    }


@router.post("/expression/languages/cache")
def refresh_locale_language_cache() -> dict[str, Any]:
    """Rebuild offline locale pack cache under data_root/cache/locale_packs/."""
    from engine.pack.locale_cache import materialize_locale_packs

    settings = load_settings()
    return materialize_locale_packs(settings.paths.data_root)


@router.post("/outputs/{output_id}/publish-pack")
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
    from engine.catalog.review_auto import ensure_publish_pack, has_verified_ready_gate
    from engine.reach.publish_sources import has_current_approved_review

    assert_runtime_active("publish_pack")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        out = session.get(RenderOutput, output_id)
        if not out:
            raise HTTPException(404, "output not found")
        # scope: output must belong to active customer via job
        if out.job_id:
            job = session.get(Job, out.job_id)
            if job and job.customer_id and job.customer_id != customer.id:
                raise HTTPException(403, "output 不属于当前客户")
        if not has_verified_ready_gate(out.qc_json if isinstance(out.qc_json, dict) else None):
            raise HTTPException(409, "READY_GATE 未明确通过，禁止生成发布物料")
        if not has_current_approved_review(session, out.id):
            raise HTTPException(409, "成片尚无唯一当前 approved 决策，禁止生成发布物料")
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
        result = ensure_publish_pack(
            session,
            customer,
            out,
            force=bool(overrides),
            expression_overrides=overrides or None,
        )
        session.commit()
        if not result.get("ok"):
            raise HTTPException(
                409,
                {
                    "code": "pack_failed",
                    "message": result.get("error") or "发布物料生成失败",
                    "output_id": output_id,
                },
            )
        return {"ok": True, "output_id": output_id, "manifest": result}
    finally:
        session.close()


class NarrationPreviewRequest(BaseModel):
    """G4: script → mock/say/clone TTS → real durations → template slot budget."""

    script: str = Field(..., min_length=1)
    lang: str = "zh"
    provider: str = "mock"  # mock | say | clone
    voice: str | None = None
    template_name: str = "default-vertical"


@router.post("/pack/narration/preview")
def pack_narration_preview(body: NarrationPreviewRequest) -> dict[str, Any]:
    """Offline-friendly narration preview (default provider=mock)."""
    assert_runtime_active("narration_preview")
    import uuid
    from pathlib import Path

    from engine.pack.narration_plan import duration_budget_from_narration, template_from_narration
    from engine.pack.tts import synthesize_script
    from engine.pack.voice_clone import is_clone_provider
    from engine.template.engine import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE

    settings = load_settings()
    provider = (body.provider or getattr(settings, "tts_provider", "mock") or "mock").lower()
    if is_clone_provider(provider):
        provider = "clone"
    if provider not in ("mock", "say", "clone"):
        raise HTTPException(400, "provider 仅支持 mock、say 或 clone")
    lang = (body.lang or "zh").lower()
    out_dir = Path(settings.paths.cache_root) / "tts" / f"preview_{uuid.uuid4().hex[:12]}"
    try:
        narr = synthesize_script(
            body.script,
            out_dir,
            lang=lang,
            provider=provider,  # type: ignore[arg-type]
            voice=body.voice or (settings.tts_voice or None) or None,
            allow_fallback=provider != "clone",
            clone_pack_id=body.voice if provider == "clone" else None,
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(400, str(e)) from e

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


# Reach console: engine.api.reach_console_routes

@router.get("/logs/events")
def list_events(limit: int = 200) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
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


@router.post("/export/events.csv")
def export_events() -> dict[str, str]:
    settings = load_settings()
    out = settings.paths.data_root / "exports" / "job_events.csv"
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        export_job_events_csv(session, out, customer_id=customer.id)
        return {"path": str(out)}
    finally:
        session.close()


@router.post("/export/renders.csv")
def export_renders() -> dict[str, str]:
    settings = load_settings()
    out = settings.paths.data_root / "exports" / "render_outputs.csv"
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        export_renders_csv(session, out, customer_id=customer.id)
        return {"path": str(out)}
    finally:
        session.close()


@router.post("/index/cliplets")
def build_cliplets(force: bool = False, limit: int = 50, use_vision: bool = False) -> dict[str, Any]:
    """Compatibility entry: model work is owned by the unified executor."""
    assert_runtime_active("cliplet_index")
    if force or use_vision:
        raise HTTPException(
            400,
            "该兼容入口仅允许增量粗向量；严格候选请使用 /index/captions/verify",
        )
    from engine.api.vectorization_routes import (
        VectorizationRunCreate,
        vectorization_create_run,
    )

    result = vectorization_create_run(
        VectorizationRunCreate(mode="count", count=max(1, min(limit, 100000)))
    )
    return {"ok": True, "queued": result}


@router.post("/index/captions")
def rebuild_captions(limit: int = 10, use_vision: bool = False) -> dict[str, Any]:
    """Bounded, resumable strict-semantic backfill for the active customer."""
    assert_runtime_active("semantic_backfill")
    from engine.config.settings import allow_semantic_full_backfill_env

    settings = load_settings()
    if not (settings.semantic_full_backfill_enabled or allow_semantic_full_backfill_env()):
        raise HTTPException(
            status_code=409,
            detail="全库语义回填已停用；普通生产使用粗索引，候选片段请按需执行 9B 验证。",
        )
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    if not CAPTIONS_BACKFILL_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="全库语义回填正在执行中（单槽）；请等待当前批次完成。",
        )
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        result = recaption_existing_cliplets(
            session,
            customer_id=customer.id,
            limit=limit,
            use_vision=use_vision,
        )
        return result
    finally:
        session.close()
        CAPTIONS_BACKFILL_LOCK.release()


@router.post("/index/captions/verify")
def verify_captions_on_demand(body: VerifyClipletsRequest) -> dict[str, Any]:
    """One-shot 9B verification for selected production candidates only."""
    import time

    from engine.ops.audit_log import write_log

    assert_runtime_active("semantic_backfill")
    settings = load_settings()
    if settings.semantic_analysis_mode != "on_demand":
        raise HTTPException(
            status_code=409,
            detail="当前机型已关闭语义按需分析；请先在 App 顶栏开启后再验证候选片段。",
        )
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        started = time.monotonic()
        write_log(
            session,
            customer_id=customer.id,
            category="semantic",
            event="semantic_on_demand_started",
            message=f"开始验证 {len(body.cliplet_ids)} 个候选片段",
            stage="verify",
            source_type="cliplet_batch",
            correlation_id=f"semantic:{uuid.uuid4().hex}",
            details={"cliplet_ids": body.cliplet_ids},
            commit=True,
        )
        try:
            result = verify_cliplets_on_demand(
                session,
                customer_id=customer.id,
                cliplet_ids=body.cliplet_ids,
                force=bool(body.force),
            )
        except Exception as exc:
            session.rollback()
            write_log(
                session,
                customer_id=customer.id,
                category="semantic",
                event="semantic_on_demand_failed",
                message="候选片段验证失败",
                level="error",
                stage="failed",
                source_type="cliplet_batch",
                duration_ms=int((time.monotonic() - started) * 1000),
                details={"cliplet_ids": body.cliplet_ids, "error": str(exc)},
                commit=True,
            )
            raise
        write_log(
            session,
            customer_id=customer.id,
            category="semantic",
            event="semantic_on_demand_completed",
            message=(
                f"候选验证完成：通过 {int(result.get('passed') or 0)}，"
                f"未通过 {int(result.get('failed') or 0)}"
            ),
            stage="completed",
            source_type="cliplet_batch",
            duration_ms=int((time.monotonic() - started) * 1000),
            details={
                "cliplet_ids": body.cliplet_ids,
                "passed": result.get("passed"),
                "failed": result.get("failed"),
            },
            commit=True,
        )
        return result
    finally:
        session.close()


@router.get("/index/captions/status")
def captions_backfill_status() -> dict[str, Any]:
    """Auditable strict coverage; remaining rows are not an active backlog."""
    from engine.config.settings import allow_semantic_full_backfill_env

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        progress = semantic_backfill_progress(session, customer_id=customer.id)
        env_allow = allow_semantic_full_backfill_env()
        progress.update(
            {
                "mode": settings.semantic_analysis_mode,
                "full_backfill_enabled": bool(
                    settings.semantic_full_backfill_enabled or env_allow
                ),
                "full_backfill_env_override": env_allow,
                "primary_model": "qwen3.5:9b",
                "cascade": False,
                "worker": None,
            }
        )
        try:
            from engine.ops.semantic_backfill_runtime import semantic_backfill_runtime

            progress["worker"] = semantic_backfill_runtime.status()
        except Exception:
            pass
        return progress
    finally:
        session.close()


@router.get("/cliplets")
def list_cliplets(limit: int = 50) -> list[dict[str, Any]]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
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


@router.post("/cliplets/themes/backfill")
def backfill_cliplet_themes_api(limit: int = 2000, force: bool = False) -> dict[str, Any]:
    """Tag existing cliplets with themes from the active industry pack."""
    from engine.catalog.theme_tags import backfill_cliplet_themes

    assert_runtime_active("cliplet_theme_backfill")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return backfill_cliplet_themes(
            session, customer_id=customer.id, limit=limit, force=force
        )
    finally:
        session.close()


@router.post("/cliplets/quality/backfill")
def backfill_cliplet_quality_api(
    limit: int = 2000,
    force: bool = False,
    only_unscored: bool = True,
) -> dict[str, Any]:
    """Sprint B3: recompute visual quality scores (default: only legacy score=1.0)."""
    from engine.ingest.quality import backfill_cliplet_quality

    assert_runtime_active("cliplet_quality_backfill")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return backfill_cliplet_quality(
            session,
            customer_id=customer.id,
            limit=limit,
            force=force,
            only_unscored=only_unscored,
        )
    finally:
        session.close()


@router.post("/cliplets/quality/purge-blur")
def purge_blur_catalog_api(
    limit: int = 5000,
    rescore: bool = True,
    purge_assets: bool = True,
) -> dict[str, Any]:
    """QUALITY_LOCK: reject blur cliplets/assets; clear their embeddings (no vectorize)."""
    from engine.ingest.quality import purge_blur_from_catalog

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return purge_blur_from_catalog(
            session,
            customer_id=customer.id,
            limit=limit,
            rescore=rescore,
            purge_assets=purge_assets,
        )
    finally:
        session.close()


@router.get("/search")
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
        _, customer, _ = active_scope(session, settings)
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


@router.get("/reports/summary")
def report_summary() -> dict[str, Any]:
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        from engine.ops.reporting import build_report_snapshot

        facts = build_report_snapshot(
            session,
            customer_id=customer.id,
            output_root=customer.output_root or settings.paths.output_root,
        )
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
        theme_dist: dict[str, int] = {}
        keyword_dist: dict[str, int] = {}
        for u in usages:
            theme_dist[u.theme] = theme_dist.get(u.theme, 0) + 1
            keyword_dist[u.keyword] = keyword_dist.get(u.keyword, 0) + 1

        top_keywords = sorted(keyword_dist.items(), key=lambda x: -x[1])[:15]
        return {
            "generated_at": facts["generated_at"],
            "timezone": facts["timezone"],
            "business_date": facts["business_date"],
            "source": facts["source"],
            "customer_id": customer.id,
            "assets": len(assets),
            "cliplets": len(cliplets),
            "cliplets_indexed": sum(1 for c in cliplets if c.embedding_json),
            "outputs": facts["outputs"],
            "ready_available": facts["ready_available"],
            "production_passed": facts["production_passed"],
            "failed": facts["failed"],
            "quality_pass_rate": facts["quality_pass_rate"],
            "failure_rate": facts["failure_rate"],
            "auto_approved": facts["auto_approved"],
            "auto_rejected": facts["auto_rejected"],
            "uncertain_open": facts["uncertain_open"],
            "manual_decided": facts["manual_decided"],
            "published": facts["published"],
            "retired": facts["retired"],
            "theme_dist": theme_dist,
            "theme_distribution": theme_dist,
            "top_keywords": top_keywords,
            "keyword_top": [{"keyword": k, "count": n} for k, n in top_keywords],
            "reviews": facts["reviews"],
            "reconciliation": facts["reconciliation"],
        }
    finally:
        session.close()


@router.get("/reports/quality")
def report_quality() -> dict[str, Any]:
    """Current-decision quality report backed by the authority database."""
    from collections import Counter

    from engine.catalog.review_learn import REJECT_REASONS, parse_reject_reason
    from engine.ops.reporting import build_report_snapshot

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        facts = build_report_snapshot(
            session,
            customer_id=customer.id,
            output_root=customer.output_root or settings.paths.output_root,
        )
        job_ids = [j.id for j in session.scalars(select(Job).where(Job.customer_id == customer.id)).all()]
        outputs = (
            list(session.scalars(select(RenderOutput).where(RenderOutput.job_id.in_(job_ids))).all())
            if job_ids
            else []
        )
        output_ids = {o.id for o in outputs}
        reviews = (
            list(
                session.scalars(
                    select(ReviewItem).where(
                        ReviewItem.render_output_id.in_(output_ids),
                        ReviewItem.is_current.is_(True),
                    )
                ).all()
            )
            if output_ids
            else []
        )
        reason_counter: Counter[str] = Counter()
        for r in reviews:
            if r.status != "rejected":
                continue
            code = parse_reject_reason(r.note or "")
            reason_counter[code] += 1
        reject_top = [
            {"code": c, "label": REJECT_REASONS.get(c, c), "count": n}
            for c, n in reason_counter.most_common(10)
        ]
        return {
            "generated_at": facts["generated_at"],
            "timezone": facts["timezone"],
            "business_date": facts["business_date"],
            "source": facts["source"],
            "customer_id": customer.id,
            "customer_name": customer.name,
            "outputs": facts["outputs"],
            "ready_available": facts["ready_available"],
            "production_passed": facts["production_passed"],
            "failed": facts["failed"],
            "quality_pass_rate": facts["quality_pass_rate"],
            "failure_rate": facts["failure_rate"],
            "auto_approved": facts["auto_approved"],
            "auto_rejected": facts["auto_rejected"],
            "uncertain_open": facts["uncertain_open"],
            "manual_decided": facts["manual_decided"],
            "published": facts["published"],
            "retired": facts["retired"],
            "reject_reason_top": reject_top,
            "reason_codes": REJECT_REASONS,
            "reconciliation": facts["reconciliation"],
        }
    finally:
        session.close()


@router.get("/reports/golden")
def report_golden() -> dict[str, Any]:
    """L0 golden sample scores (must be in DB, not only docs)."""
    from engine.catalog.golden import list_golden

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
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


@router.post("/reports/golden/seed")
def seed_golden() -> dict[str, Any]:
    """Upsert frozen G1/G2 scores from GOLDEN_SAMPLES baseline into authority DB."""
    from engine.catalog.db import init_db
    from engine.catalog.golden import list_golden, upsert_frozen_golden

    settings = load_settings()
    init_db(settings)
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        seeded = upsert_frozen_golden(session, settings, customer_id=customer.id)
        return {
            "customer_id": customer.id,
            "customer_name": customer.name,
            "seeded": seeded,
            "samples": list_golden(session, customer.id),
        }
    finally:
        session.close()


@router.get("/reviews")
def list_reviews(limit: int = 100, scope: str = "history") -> list[dict[str, Any]]:
    from engine.catalog.serial import format_display_no

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        if scope not in {"history", "open", "current"}:
            raise HTTPException(400, "scope must be history/open/current")
        stmt = (
            select(ReviewItem, RenderOutput)
            .join(RenderOutput, ReviewItem.render_output_id == RenderOutput.id)
            .join(Job, RenderOutput.job_id == Job.id)
            .where(Job.customer_id == customer.id)
        )
        if scope == "open":
            stmt = stmt.where(
                ReviewItem.is_current.is_(True),
                ReviewItem.status == "uncertain",
            )
        elif scope == "current":
            stmt = stmt.where(ReviewItem.is_current.is_(True))
        rows = session.execute(
            stmt.order_by(ReviewItem.id.desc()).limit(limit)
        ).all()
        return [
            {
                "id": r.id,
                "render_output_id": r.render_output_id,
                "display_no": getattr(out, "display_no", None),
                "display_label": format_display_no(getattr(out, "display_no", None)) or None,
                "status": r.status,
                "note": r.note,
                "is_current": r.is_current,
                "decision_source": r.decision_source,
                "evidence": r.evidence_json or {},
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            }
            for r, out in rows
        ]
    finally:
        session.close()


@router.get("/review/reasons")
def review_reasons() -> dict[str, Any]:
    from engine.catalog.review_learn import REJECT_REASONS

    return {"reasons": [{"code": k, "label": v} for k, v in REJECT_REASONS.items()]}


@router.post("/review/auto-approve/backfill")
def review_auto_approve_backfill(limit: int = 50) -> dict[str, Any]:
    """Reconcile final outputs to mandatory decisions (bounded to 50)."""
    from engine.catalog.review_auto import BACKFILL_LIMIT_DEFAULT, backfill_auto_approve

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return backfill_auto_approve(
            session,
            customer,
            limit=min(max(1, int(limit or BACKFILL_LIMIT_DEFAULT)), BACKFILL_LIMIT_DEFAULT),
        )
    finally:
        session.close()


@router.post("/review/batch-approve")
def review_batch_approve(
    skip_known_issues: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """Human one-click approve for open uncertain outputs (READY_GATE fail-closed)."""
    from engine.catalog.review_auto import (
        BATCH_APPROVE_LIMIT_DEFAULT,
        batch_manual_approve,
    )
    from engine.pack.video_lock import load_video_lock

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        video_lock = load_video_lock(customer.name, output_root=customer.output_root)
        return batch_manual_approve(
            session,
            customer,
            skip_known_issues=bool(skip_known_issues),
            limit=min(
                max(1, int(limit or BATCH_APPROVE_LIMIT_DEFAULT)),
                BATCH_APPROVE_LIMIT_DEFAULT,
            ),
            video_lock=video_lock,
        )
    finally:
        session.close()


@router.post("/review/reconcile-gate")
def review_reconcile_gate_batch(
    limit: int = 50,
    archive_missing: bool = True,
    archive_gate_fail: bool = False,
) -> dict[str, Any]:
    """Re-run READY_GATE on open uncertain outputs and auto-approve only when ok."""
    from engine.catalog.review_auto import RECONCILE_LIMIT_DEFAULT, batch_reconcile_ready_gate

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        return batch_reconcile_ready_gate(
            session,
            customer,
            limit=min(
                max(1, int(limit or RECONCILE_LIMIT_DEFAULT)),
                RECONCILE_LIMIT_DEFAULT,
            ),
            archive_missing=bool(archive_missing),
            archive_gate_fail=bool(archive_gate_fail),
            export_pack=True,
        )
    finally:
        session.close()


@router.post("/review/{output_id}/reconcile-gate")
def review_reconcile_gate_one(output_id: int) -> dict[str, Any]:
    from engine.catalog.review_auto import reconcile_ready_gate_output

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        out = session.get(RenderOutput, output_id)
        if not out or not out.job_id:
            raise HTTPException(404, "output not found")
        job = session.get(Job, out.job_id)
        if not job or job.customer_id != customer.id:
            raise HTTPException(403, "output 不属于当前客户")
        result = reconcile_ready_gate_output(
            session,
            customer,
            out,
            archive_missing=False,
            archive_gate_fail=False,
            export_pack=True,
        )
        session.commit()
        return result
    finally:
        session.close()


@router.post("/review/{output_id}/archive-unusable")
def review_archive_unusable(
    output_id: int,
    reason: str = "manual",
) -> dict[str, Any]:
    """Drop dead uncertain rows from the human queue without granting approved."""
    from engine.catalog.review_auto import archive_unusable_output

    if reason not in {"manual", "missing", "gate_fail"}:
        raise HTTPException(400, "reason must be manual/missing/gate_fail")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        out = session.get(RenderOutput, output_id)
        if not out or not out.job_id:
            raise HTTPException(404, "output not found")
        job = session.get(Job, out.job_id)
        if not job or job.customer_id != customer.id:
            raise HTTPException(403, "output 不属于当前客户")
        result = archive_unusable_output(session, customer, out, reason=reason)
        session.commit()
        return result
    finally:
        session.close()


@router.post("/review/{output_id}")
def review_output(
    output_id: int,
    status: str = "approved",
    note: str = "",
    reason: str = "",
    downweight: bool = True,
    rerender: bool = False,
) -> dict[str, Any]:
    from engine.catalog.review_learn import (
        downweight_cliplets_for_reject,
        parse_reject_reason,
        rerender_chain_depth,
    )

    if status == "pending":
        status = "uncertain"
    if status not in {"approved", "rejected", "uncertain"}:
        raise HTTPException(400, "status must be approved/rejected/uncertain")
    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        out = session.get(RenderOutput, output_id)
        if not out or not out.job_id:
            raise HTTPException(404, "output not found")
        job = session.get(Job, out.job_id)
        if not job or job.customer_id != customer.id:
            raise HTTPException(403, "output 不属于当前客户")
        from engine.catalog.review_auto import (
            _emit_review_ops_log,
            ensure_publish_pack,
            has_verified_ready_gate,
            latest_review,
            record_review_decision,
        )

        prior_state = out.state
        prior_review = latest_review(session, out.id)
        already_approved = bool(prior_review and prior_review.status == "approved")
        # Product: 审片未通过 → 删成片、释放资源，禁止自动重渲。
        # ``rerender`` query param is ignored (legacy clients). Explicit
        # POST /review/{id}/rerender remains available for ops only.
        reject_depth = rerender_chain_depth(session, out) if status == "rejected" else 0
        auto_rerender = False
        human_after_rerender = False
        if status == "approved":
            if out.state not in {"ready", "review"}:
                raise HTTPException(409, "成片状态不可审核为通过")
            if not has_verified_ready_gate(
                out.qc_json if isinstance(out.qc_json, dict) else None
            ):
                # One-shot on-disk re-eval before hard reject (never invents ok).
                from engine.catalog.review_auto import reconcile_ready_gate_output

                recon = reconcile_ready_gate_output(
                    session,
                    customer,
                    out,
                    archive_missing=False,
                    archive_gate_fail=False,
                    export_pack=False,
                )
                session.flush()
                session.refresh(out)
                if not has_verified_ready_gate(
                    out.qc_json if isinstance(out.qc_json, dict) else None
                ):
                    # Persist written fails so the review card can show them.
                    session.commit()
                    fails = list(recon.get("ready_gate_fails") or [])[:5]
                    detail = "；".join(fails) if fails else "无磁盘可验证证据"
                    raise HTTPException(
                        409,
                        "出片门禁未明确通过，不能审核为通过。"
                        f"请先「重验门禁」或标未通过删除。详情：{detail}",
                    )
            out.state = "ready"
        elif status == "rejected":
            out.state = "failed"
        else:
            out.state = "review"
        reason_code = parse_reject_reason(note, reason or None) if status == "rejected" else ""
        note_store = note
        if status == "rejected" and reason_code and f"[{reason_code}]" not in (note or ""):
            note_store = f"[{reason_code}] {note}".strip()
        decision_status = status
        item, created = record_review_decision(
            session,
            out,
            status=decision_status,
            note=note_store,
            source="manual",
            evidence={
                "reason": reason_code or None,
                "ready_gate_ok": has_verified_ready_gate(
                    out.qc_json if isinstance(out.qc_json, dict) else None
                ),
                "rerender_depth": reject_depth if status == "rejected" else None,
                "auto_rerender": False,
                "purge_media": True if status == "rejected" else None,
            },
        )
        pack_result: dict[str, Any] | None = None
        if status == "approved":
            pack_result = ensure_publish_pack(session, customer, out)
        if created:
            _emit_review_ops_log(
                session,
                customer,
                out,
                status=status,
                source="manual",
                review_id=item.id,
                pack_result=pack_result,
            )
        # 纸片只在首次通过时记账。优先走 worker 的 reservation_key（幂等）；
        # 改 note 再次点通过不得重复 bump。
        if status == "approved" and created and not already_approved:
            import json
            from pathlib import Path

            from sqlalchemy.exc import IntegrityError

            from engine.catalog.paper_slip import commit_paper_slip_for_ready

            reservation_key = f"job:{job.id}:seed:{out.seed}"
            sidecar: dict[str, Any] = {}
            if out.sidecar_path and Path(out.sidecar_path).is_file():
                try:
                    sidecar = json.loads(
                        Path(out.sidecar_path).read_text(encoding="utf-8")
                    )
                except (OSError, ValueError, TypeError):
                    sidecar = {}
            cliplet_ids = [
                int(clip["cliplet_id"])
                for clip in (sidecar.get("clips") or [])
                if isinstance(clip, dict) and clip.get("cliplet_id")
            ]
            title = str(sidecar.get("title") or "")

            def _commit_via_reservation() -> None:
                commit_paper_slip_for_ready(
                    session,
                    job_id=job.id,
                    reservation_key=reservation_key,
                    customer_id=customer.id,
                )

            def _commit_via_items() -> None:
                commit_paper_slip_for_ready(
                    session,
                    job_id=job.id,
                    cliplet_ids=cliplet_ids,
                    title=title,
                    customer_id=customer.id,
                )

            try:
                with session.begin_nested():
                    _commit_via_reservation()
            except (ValueError, IntegrityError):
                # 无有效预留（未创建/已释放）：仅非 ready 成片走条目记账。
                # 已是 ready 的成片由 worker 负责，禁止无 key 重计重复记账。
                if prior_state == "ready":
                    pass
                else:
                    try:
                        with session.begin_nested():
                            _commit_via_items()
                    except IntegrityError as exc:
                        raise HTTPException(
                            409,
                            "纸片规则：记账冲突，不能审核通过",
                        ) from exc
                    except ValueError as exc:
                        raise HTTPException(409, str(exc)) from exc
        learn: dict[str, Any] = {}
        rerender_job: dict[str, Any] | None = None
        purge_result: dict[str, Any] = {}
        quota_release: dict[str, Any] = {}
        if status == "rejected" and created:
            from engine.catalog.keyword_pack import undo_keyword_usage
            from engine.catalog.output_purge import purge_output_media
            from engine.catalog.paper_slip import release_committed_paper_slip_for_job

            paper_n = release_committed_paper_slip_for_job(
                session, int(job.id), customer_id=customer.id
            )
            kw_n = undo_keyword_usage(session, int(job.id), customer_id=customer.id)
            quota_release = {"paper_slip_rows": paper_n, "keyword_usage_rows": kw_n}
            if downweight:
                learn = downweight_cliplets_for_reject(
                    session, out, reason=reason_code or "other"
                )
            # Drop the deliverable binary so disk/quota free immediately.
            purge_result = purge_output_media(
                session,
                out,
                reason=reason_code or "rejected",
                actor="manual_review",
            )
            session.commit()
        else:
            session.commit()
        return {
            "id": item.id,
            "status": item.status,
            "created": created,
            "is_current": item.is_current,
            "output_id": output_id,
            "display_no": getattr(out, "display_no", None),
            "state": out.state,
            "reason": reason_code or None,
            "learn": learn,
            "rerender_job": rerender_job,
            "purge": purge_result,
            "quota_release": quota_release,
            "human_review": human_after_rerender,
            "pack": pack_result,
            "pack_status": out.pack_status,
            "pack_error": out.pack_error,
        }
    finally:
        session.close()


@router.post("/review/{output_id}/rerender")
def review_rerender(output_id: int, reason: str = "") -> dict[str, Any]:
    """One-click re-render from an existing (usually rejected) output."""
    from engine.catalog.review_learn import create_rerender_job, parse_reject_reason

    try:
        assert_production_ready()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    session = get_session()
    try:
        settings = load_settings()
        _, customer, _ = active_scope(session, settings)
        out = session.get(RenderOutput, output_id)
        if not out or not out.job_id:
            raise HTTPException(404, "output not found")
        job = session.get(Job, out.job_id)
        if not job or job.customer_id != customer.id:
            raise HTTPException(403, "output 不属于当前客户")
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


class ExpressionVariantRequest(BaseModel):
    """GVisualPack2 V5: fork expression while reusing picture slots from source output."""

    voice_lang: str | None = None
    subtitle_lang: str | None = None
    subtitle_burn: str | None = None
    dual_secondary_lang: str | None = None
    # When false, requires frozen/active plan_lang_reuse=on on source job rules.
    force: bool = True


@router.post("/outputs/{output_id}/expression-variant")
def create_expression_variant(output_id: int, body: ExpressionVariantRequest) -> dict[str, Any]:
    """Reuse MontagePlan clips; re-fork title/VO/subs for a target language set."""
    from engine.catalog.review_learn import create_expression_variant_job

    try:
        assert_production_ready()
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    assert_runtime_active("expression_variant")
    session = get_session()
    try:
        settings = load_settings()
        _, customer, _ = active_scope(session, settings)
        out = session.get(RenderOutput, output_id)
        if not out or not out.job_id:
            raise HTTPException(404, "output not found")
        job_row = session.get(Job, out.job_id)
        if not job_row or job_row.customer_id != customer.id:
            raise HTTPException(403, "output 不属于当前客户")
        expression = {
            k: v
            for k, v in {
                "voice_lang": body.voice_lang,
                "subtitle_lang": body.subtitle_lang,
                "subtitle_burn": body.subtitle_burn,
                "dual_secondary_lang": body.dual_secondary_lang,
            }.items()
            if v is not None and str(v).strip() != ""
        }
        if not expression:
            raise HTTPException(400, "至少指定 voice_lang / subtitle_lang 之一")
        try:
            job = create_expression_variant_job(
                session,
                out,
                expression=expression,
                force_plan_reuse=bool(body.force),
            )
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {
            "ok": True,
            "output_id": output_id,
            "job": {
                "id": job.id,
                "status": job.status,
                "theme": job.theme,
                "category": job.category,
            },
            "expression": expression,
            "plan_reuse": True,
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


@router.post("/outputs/batch-rerender")
def outputs_batch_rerender(body: BatchRerenderRequest) -> dict[str, Any]:
    """G6: queue re-renders for outputs missing voice/subs or lock TTS violations (max 20)."""
    assert_runtime_active("batch_rerender")
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
        _, customer, _ = active_scope(session, settings)
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
                out = scoped_output(session, oid, customer.id)
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


@router.get("/reports/ops")
def report_ops() -> dict[str, Any]:
    """Read-only operations dashboard; SQLite is the sole business fact source."""
    from pathlib import Path

    from engine.ops.maintenance import disk_report
    from engine.ops.reporting import build_report_snapshot

    settings = load_settings()
    session = get_session()
    try:
        _, customer, _ = active_scope(session, settings)
        facts = build_report_snapshot(
            session,
            customer_id=customer.id,
            output_root=customer.output_root or settings.paths.output_root,
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

        from engine.catalog.db import db_path
        from engine.ops.db_health import db_health_phrase, inspect_montage_db

        db_info = inspect_montage_db(db_path(settings))
        db_phrase = db_health_phrase(db_info)
        if not db_info.get("ok") or db_info.get("empty"):
            parts.insert(0, db_phrase)
        else:
            parts.append(db_phrase)

        health_line = " · ".join(parts) if parts else "状态未知"

        return {
            "ok": True,
            "generated_at": facts["generated_at"],
            "timezone": facts["timezone"],
            "business_date": facts["business_date"],
            "source": facts["source"],
            "customer_id": customer.id,
            "customer_name": customer.name,
            "outputs": facts["outputs"],
            "ready_available": facts["ready_available"],
            "production_passed": facts["production_passed"],
            "failed": facts["failed"],
            "quality_pass_rate": facts["quality_pass_rate"],
            "failure_rate": facts["failure_rate"],
            "auto_approved": facts["auto_approved"],
            "auto_rejected": facts["auto_rejected"],
            "uncertain_open": facts["uncertain_open"],
            "manual_decided": facts["manual_decided"],
            "published": facts["published"],
            "retired": facts["retired"],
            "production_passed_today": facts["production_passed_today"],
            "published_today": facts["published_today"],
            "reconciliation": facts["reconciliation"],
            "health_line": health_line,
            "path_health": path_h,
            "library_ok": lib_ok,
            "output_ok": out_ok,
            "db_health": db_info,
            "db_ok": bool(db_info.get("ok")) and not bool(db_info.get("empty")),
        }
    finally:
        session.close()



