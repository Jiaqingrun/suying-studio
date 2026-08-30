from __future__ import annotations

import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.customer_scope import settings_with_customer_paths
from engine.catalog.db import Cliplet, ClipletUsage, Job, RenderOutput, get_session, log_event
from engine.catalog.keyword_pack import get_active_pack, get_pack_by_id, record_keyword_usage
from engine.config.paths import check_paths, ensure_layout, is_readonly_fs_error
from engine.config.settings import AppSettings, load_settings
from engine.export.sidecar import build_copywriting, write_sidecar
from engine.qc.gates import run_qc
from engine.render.covers import extract_cover_candidates
from engine.render.ffmpeg import render_plan
from engine.template.engine import DEFAULT_TEMPLATE, TemplateDefinition, build_plan, record_cliplet_usage


# Slightly above resource_gate's tts lease sweep ceiling (600s) so an orphaned
# lease from a crashed process gets reclaimed by the sweep before we'd time out.
TTS_SLOT_WAIT_DEADLINE_SEC = 700.0
# render has no default lease sweep (long ffmpeg is valid); bound the wait to
# enter the render phase so a wedged holder cannot park a job forever.
RENDER_SLOT_WAIT_DEADLINE_SEC = 1800.0
# Single-item wall: if an attempt exceeds this at stage checkpoints, skip and
# continue toward target_count (Edge TTS has its own 45s killable clock).
# Applied before/during plan and while waiting for the render slot — NOT after
# a successful render_plan (clone TTS + 跟镜精品 routinely exceeds 150s by then;
# discarding a finished encode burned consecutive paper-slip retries → circuit).
ITEM_WALL_SEC = 150.0
# clone/F5 多句配音冷启动常超过 150s；跟镜精品五镜 per_sentence 给更宽墙钟
ITEM_WALL_TTS_SEC = 360.0


def infra_backoff_seconds(consecutive_infra: int) -> float:
    """Exponential backoff for Ollama/infra waits: 10s, 20s, 40s… capped at 300s."""
    base = 10.0
    cap = 300.0
    return min(cap, base * (2 ** max(0, int(consecutive_infra) - 1)))


def job_next_attempt_ready(job: Job, *, now: datetime | None = None) -> bool:
    """False when a job's config_snapshot next_attempt_at is still in the future."""
    snap = job.config_snapshot_json or {}
    raw = snap.get("next_attempt_at")
    if not raw:
        return True
    try:
        next_at = datetime.fromisoformat(str(raw))
    except ValueError:
        return True
    if next_at.tzinfo is None:
        next_at = next_at.replace(tzinfo=timezone.utc)
    return next_at <= (now or datetime.now(timezone.utc))


def seed_for_output(configured_seed: Any, produced_count: int, retry_count: int = 0) -> int:
    """Treat a configured seed as a reproducible job seed, not one repeated output seed."""
    if configured_seed is None:
        return random.randint(1, 2_000_000_000)
    base = int(configured_seed)
    # A count job must not replay the same plan/narration for every output.
    output_index = max(0, int(produced_count)) + max(0, int(retry_count))
    return ((base - 1 + output_index * 1_000_003) % 2_000_000_000) + 1


def park_job_path_unwritable(
    session: Session,
    job: Job,
    *,
    detail: str,
) -> None:
    """Stop this job permanently (status=paused) when production roots are read-only.

    Does not burn consecutive_failures toward circuit_open empty-retries.
    """
    from engine.catalog.paper_slip import release_job_reservations

    release_job_reservations(session, job.id)
    job.status = "paused"
    snap = dict(job.config_snapshot_json or {})
    snap["_pipeline_phase"] = "blocked_path_readonly"
    snap["path_readonly_detail"] = detail
    job.config_snapshot_json = snap
    job.updated_at = datetime.now(timezone.utc)
    log_event(
        session,
        job.id,
        "error",
        "外置路径不可写，已停本任务（请恢复盘可写后再恢复/新建）",
        {"detail": detail, "path_readonly": True},
    )
    session.commit()


def skip_current_item(
    session: Session,
    job: Job,
    *,
    reason: str,
    stage: str,
    error: str | None = None,
    infra: bool = False,
    elapsed_sec: float | None = None,
    reservation_key: str | None = None,
) -> None:
    """Abandon this attempt only; keep status running so the worker can fill target_count.

    Does not bump consecutive_non_ready (timeouts/stalls are not quality failures).
    Infra skips schedule short backoff; after ``ollama_infra_pause_threshold`` consecutive
    infra skips the job is paused (readable) so it cannot idle-render for tens of minutes.
    """
    from engine.catalog.paper_slip import release_paper_slip
    from engine.config.settings import load_settings
    from engine.runtime.resource_gate import gate as resource_gate

    if reservation_key:
        try:
            release_paper_slip(session, reservation_key, customer_id=job.customer_id)
        except Exception:  # noqa: BLE001
            pass
    try:
        resource_gate.release("tts", f"job:{job.id}:tts")
        resource_gate.release("render", f"job:{job.id}")
        resource_gate.release_all(f"job:{job.id}")
        resource_gate.release_all(f"job:{job.id}:tts")
    except Exception:  # noqa: BLE001
        pass

    snap = dict(job.config_snapshot_json or {})
    snap["item_skip_count"] = int(snap.get("item_skip_count") or 0) + 1
    snap["item_retry_count"] = int(snap.get("item_retry_count") or 0) + 1
    paused_for_infra = False
    consecutive_infra = 0
    if infra:
        consecutive_infra = int(snap.get("consecutive_ollama_infra") or 0) + 1
        snap["consecutive_ollama_infra"] = consecutive_infra
        pause_threshold = int(
            getattr(load_settings(), "ollama_infra_pause_threshold", 3) or 3
        )
        if consecutive_infra >= max(1, pause_threshold):
            paused_for_infra = True
            snap.pop("next_attempt_at", None)
            snap["_pipeline_phase"] = "paused_ollama_infra"
            snap["ollama_infra_pause_reason"] = (
                "旁白基建不可用，请预热 Ollama 后 resume"
            )
            job.status = "paused"
        else:
            backoff_sec = infra_backoff_seconds(consecutive_infra)
            next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
            snap["next_attempt_at"] = next_attempt_at.isoformat()
    else:
        # Non-infra recoverable skip: no quality counter, no forced circuit.
        snap.pop("next_attempt_at", None)
    job.config_snapshot_json = snap
    job.updated_at = datetime.now(timezone.utc)
    log_event(
        session,
        job.id,
        "warning" if not paused_for_infra else "error",
        (
            "旁白基建不可用，请预热后 resume"
            if paused_for_infra
            else "跳过本条，继续生产"
        ),
        {
            "stage": stage,
            "reason": reason if not paused_for_infra else "ollama_infra_pause",
            "error": (error or "")[:400] or None,
            "elapsed_sec": round(float(elapsed_sec), 2) if elapsed_sec is not None else None,
            "produced_count": int(job.produced_count or 0),
            "target_count": job.target_count,
            "item_skip_count": snap["item_skip_count"],
            "item_retry_count": snap["item_retry_count"],
            "infra": bool(infra),
            "consecutive_ollama_infra": consecutive_infra if infra else snap.get("consecutive_ollama_infra"),
            "next_attempt_at": snap.get("next_attempt_at"),
            "paused_for_infra": paused_for_infra,
        },
    )
    session.commit()


def _elapsed_item(started: float) -> float:
    return max(0.0, time.monotonic() - started)


def _item_wall_exceeded(started: float, *, limit_sec: float | None = None) -> bool:
    return _elapsed_item(started) > float(ITEM_WALL_SEC if limit_sec is None else limit_sec)


def _error_looks_infra(error: str | None) -> bool:
    text = str(error or "").lower()
    needles = (
        "timeout",
        "timed out",
        "edge_tts_timeout",
        "edge_tts_empty",
        "tts_slot_wait_timeout",
        "cancelled",
        "connection",
        "refused",
        "unreachable",
        "wall-clock",
        "wall clock",
    )
    return any(n in text for n in needles)


def build_item_label_cues(
    plan: Any,
    cliplets: dict[int, Cliplet],
    frozen_rules: dict[str, Any] | None,
    topic_intent: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Gate item names on frozen official evidence plus current strict frames."""
    from engine.ingest.semantic_gate import semantic_gate_passed

    rules = frozen_rules if isinstance(frozen_rules, dict) else {}
    topic = topic_intent if isinstance(topic_intent, dict) else {}
    if not rules.get("item_label_enabled") or topic.get("strict_semantic_v1") is not True:
        return []
    eligible_cliplet_ids = {int(value) for value in topic.get("cliplet_ids") or []}

    def _canonical_chinese(value: Any) -> str:
        compact = "".join(str(value or "").split())
        return compact if compact and all("\u4e00" <= ch <= "\u9fff" for ch in compact) else ""

    official: list[dict[str, Any]] = []
    for item in topic.get("official_evidence") or []:
        if not isinstance(item, dict):
            continue
        canonical = _canonical_chinese(item.get("canonical_name"))
        if (
            canonical
            and item.get("id") is not None
            and str(item.get("url_hash") or "").strip()
            and str(item.get("content_sha256") or "").strip()
        ):
            official.append({**item, "canonical_name": canonical})
    cues: list[dict[str, Any]] = []
    timeline = 0.0
    for clip in plan.clips:
        duration = float(clip.duration_sec or 0)
        row = cliplets.get(int(clip.cliplet_id)) if clip.cliplet_id else None
        if (
            row is None
            or int(clip.cliplet_id or 0) not in eligible_cliplet_ids
            or not semantic_gate_passed(row)
        ):
            timeline += duration
            continue
        semantic = row.semantic_json if row and isinstance(row.semantic_json, dict) else {}
        visible_products = [
            product
            for product in semantic.get("products") or []
            if (
                isinstance(product, dict)
                and _canonical_chinese(product.get("name"))
                and product.get("evidence_frame_ids")
            )
        ]
        matched = next(
            (
                (evidence, product)
                for evidence in official
                for product in visible_products
                if evidence["canonical_name"] == _canonical_chinese(product.get("name"))
            ),
            None,
        )
        if matched:
            evidence, product = matched
            cues.append(
                {
                    "text": evidence["canonical_name"],
                    "side": str(rules.get("item_label_side") or "left"),
                    "font_size": int(rules.get("item_label_font_size") or 56),
                    "safe_top": int(rules.get("item_label_safe_top") or 360),
                    "safe_bottom": int(rules.get("item_label_safe_bottom") or 1320),
                    "start": timeline,
                    "end": timeline + duration,
                    "cliplet_id": clip.cliplet_id,
                    "evidence": {
                        "strict_semantic_v1": True,
                        "visual_evidence_frame_ids": list(product.get("evidence_frame_ids") or []),
                        "official_evidence_id": evidence.get("id"),
                        "official_url_hash": evidence.get("url_hash"),
                        "official_content_sha256": evidence.get("content_sha256"),
                    },
                }
            )
        timeline += duration
    return cues


class JobWorker:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running_job_id: int | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="montage-job-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def running_job_id(self) -> int | None:
        with self._lock:
            return self._running_job_id

    def _loop(self) -> None:
        while not self._stop.is_set():
            from engine.runtime.pause_coordinator import coordinator as pause_coordinator
            from engine.runtime.resource_gate import gate as resource_gate

            resource_gate.sync_from_settings()
            try:
                resource_gate.sweep_default_leases()
            except Exception:  # noqa: BLE001
                pass

            if not pause_coordinator.should_claim_jobs():
                # Drain: finish nothing new; if a running job is mid-output, wait until next tick
                # after current process returns. Mark running jobs as paused_system when idle.
                session = get_session()
                try:
                    job = session.scalar(
                        select(Job).where(Job.status == "running").order_by(Job.id).limit(1)
                    )
                    if job and self._running_job_id is None:
                        job.status = "paused_system"
                        snap = dict(job.config_snapshot_json or {})
                        snap["_system_pause_hold"] = {
                            "owner": "runtime",
                            "generation": int(pause_coordinator.snapshot().get("generation") or 0),
                        }
                        job.config_snapshot_json = snap
                        log_event(session, job.id, "info", "系统事件暂停：任务已挂起")
                        session.commit()
                finally:
                    session.close()
                time.sleep(0.5)
                continue

            settings = load_settings()
            session = get_session()
            try:
                from engine.catalog.paper_slip import release_reservations_for_inactive_jobs
                from engine.jobs.stale import reap_stale_running_jobs

                try:
                    if release_reservations_for_inactive_jobs(session):
                        session.commit()
                except Exception:  # noqa: BLE001
                    session.rollback()
                try:
                    reap_stale_running_jobs(
                        session,
                        held_job_id=self.running_job_id(),
                    )
                except Exception:  # noqa: BLE001
                    session.rollback()
                # Fetch a small batch so a job backed off with next_attempt_at
                # (infra retry) does not block other eligible jobs behind it.
                candidates = session.scalars(
                    select(Job).where(Job.status.in_(("queued", "running"))).order_by(Job.id).limit(20)
                ).all()
                job = next((c for c in candidates if job_next_attempt_ready(c)), None)
                if not job:
                    time.sleep(1.0)
                    continue
                token = f"job:{job.id}"
                # "render" is no longer held for the whole job — only acquired
                # inside _process_job right before the actual FFmpeg phase, so
                # planning/narration/TTS never pin a render slot unnecessarily.
                if job.status == "queued":
                    job.status = "running"
                    try:
                        from engine.runtime.workload_yield import acquire_workload

                        acquire_workload(f"produce:{job.id}")
                    except Exception:
                        pass
                    snap = dict(job.config_snapshot_json or {})
                    snap["_pipeline_phase"] = "producing"
                    job.config_snapshot_json = snap
                    job.updated_at = datetime.now(timezone.utc)
                    session.commit()
                else:
                    job.updated_at = datetime.now(timezone.utc)
                    session.commit()
                with self._lock:
                    self._running_job_id = job.id
                try:
                    self._process_job(session, settings, job)
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    job = session.get(Job, job.id)
                    if job:
                        if is_readonly_fs_error(exc):
                            try:
                                park_job_path_unwritable(
                                    session,
                                    job,
                                    detail=f"{type(exc).__name__}: {exc}",
                                )
                            except Exception:  # noqa: BLE001
                                session.rollback()
                        else:
                            from engine.catalog.paper_slip import release_job_reservations

                            release_job_reservations(session, job.id)
                            job.consecutive_failures = int(job.consecutive_failures or 0) + 1
                            log_event(
                                session,
                                job.id,
                                "error",
                                "Worker 处理异常，任务保留待重试",
                                {"error": f"{type(exc).__name__}: {exc}"},
                            )
                            session.commit()
                finally:
                    resource_gate.release_all(token)
                    resource_gate.release_all(f"job:{job.id}:tts")
                    try:
                        from engine.runtime.workload_yield import release_workload

                        release_workload(f"produce:{job.id}")
                    except Exception:
                        pass
                    with self._lock:
                        self._running_job_id = None
                try:
                    session.refresh(job)
                    if job.status in ("completed", "circuit_open", "cancelled", "paused"):
                        from engine.jobs.queue import resume_jobs_paused_for_rush

                        resume_jobs_paused_for_rush(session, by_job_id=job.id)
                except Exception:  # noqa: BLE001
                    session.rollback()
                # After finishing one output, if system pause requested, park the job
                if not pause_coordinator.should_claim_jobs():
                    session.refresh(job)
                    if job.status == "running":
                        job.status = "paused_system"
                        snap = dict(job.config_snapshot_json or {})
                        snap["_system_pause_hold"] = {
                            "owner": "runtime",
                            "generation": int(pause_coordinator.snapshot().get("generation") or 0),
                        }
                        job.config_snapshot_json = snap
                        log_event(session, job.id, "info", "系统事件暂停：当前成片已收尾")
                        session.commit()
            finally:
                session.close()
            time.sleep(0.2)

    def _process_job(self, session: Session, settings: AppSettings, job: Job) -> None:
        from engine.catalog.db import Customer

        log_event(
            session,
            job.id,
            "info",
            "开始处理任务",
            {
                "theme": job.theme,
                "template_name": job.template_name,
                "produced_count": job.produced_count,
                "target_count": job.target_count,
            },
        )
        snap0 = dict(job.config_snapshot_json or {})
        snap0["_pipeline_phase"] = "producing"
        job.config_snapshot_json = snap0
        session.commit()

        customer_row = session.get(Customer, job.customer_id) if job.customer_id else None
        if customer_row:
            settings = settings_with_customer_paths(settings, customer_row)
        ensure_layout(settings)
        # Fail closed when production roots are read-only (avoid 5× empty retry → circuit).
        path_health = check_paths(settings, force_write_probe=True)
        if not path_health.writable or not path_health.ok:
            write_errs = [
                str(c.get("detail") or "").strip()
                for c in (path_health.write_checks or [])
                if not c.get("ok") and c.get("detail")
            ]
            detail = "; ".join(write_errs) if write_errs else "; ".join(path_health.errors)
            if not detail:
                detail = "生产路径不可写"
            park_job_path_unwritable(session, job, detail=detail)
            return
        snap = dict(job.config_snapshot_json or {})
        # 僵尸成功态：已达目标且最新成片 pack ready，但 status 卡在 paused/running
        if (
            job.mode == "count"
            and job.target_count is not None
            and int(job.produced_count or 0) >= int(job.target_count)
            and job.status in ("paused", "running", "queued")
        ):
            latest = session.scalars(
                select(RenderOutput)
                .where(RenderOutput.job_id == job.id)
                .order_by(RenderOutput.id.desc())
                .limit(1)
            ).first()
            if (
                latest
                and str(latest.state or "") == "ready"
                and str(latest.pack_status or "") == "ready"
            ):
                snap["_pipeline_phase"] = "completed"
                job.config_snapshot_json = snap
                job.status = "completed"
                log_event(
                    session,
                    job.id,
                    "info",
                    "收尾核对：已达目标且物料就绪，标记完成",
                    {"produced_count": job.produced_count, "output_id": latest.id},
                )
                session.commit()
                return
        template_data = snap.get("template") or DEFAULT_TEMPLATE.model_dump()
        template = TemplateDefinition(**template_data)
        # snap.customer_name may be null for automation-created jobs; always
        # prefer the bound Customer row so paper-slip filtering uses the same
        # customer_id as reserve_paper_slip(job.customer_id).
        customer = (
            (customer_row.name if customer_row else None)
            or (str(snap.get("customer_name") or "").strip() or None)
            or "default"
        )
        if customer_row and snap.get("customer_name") != customer_row.name:
            snap["customer_name"] = customer_row.name
            job.config_snapshot_json = snap
            session.commit()
        theme = job.theme
        category = job.category
        content_category = str(snap.get("content_category") or category or "default").strip() or "default"
        asset_category = str(snap.get("asset_category") or "").strip() or None
        if isinstance(pr := snap.get("production_rules"), dict):
            frozen_cat = str(pr.get("content_category") or "").strip()
            if frozen_cat:
                content_category = frozen_cat

        if job.consecutive_failures >= settings.circuit_breaker_threshold:
            from engine.catalog.paper_slip import release_job_reservations

            release_job_reservations(session, job.id)
            job.status = "circuit_open"
            log_event(session, job.id, "error", "连续失败熔断，任务暂停")
            session.commit()
            return

        consecutive_non_ready = int(snap.get("consecutive_non_ready", 0) or 0)
        if consecutive_non_ready >= settings.quality_circuit_threshold:
            from engine.catalog.paper_slip import release_job_reservations

            release_job_reservations(session, job.id)
            job.status = "circuit_open"
            log_event(
                session,
                job.id,
                "error",
                "质量熔断：连续非 ready 过多，请抽检后恢复任务",
                {"consecutive_non_ready": consecutive_non_ready},
            )
            session.commit()
            return

        if job.mode == "duration" and job.target_duration_sec:
            if (datetime.now(timezone.utc) - job.created_at).total_seconds() >= job.target_duration_sec:
                job.status = "completed"
                log_event(session, job.id, "info", "达到目标运行时长，任务完成")
                session.commit()
                return

        if job.mode == "count" and job.target_count is not None:
            if job.produced_count >= job.target_count:
                job.status = "completed"
                log_event(session, job.id, "info", "达到目标条数，任务完成")
                session.commit()
                return

        # Fail closed once when brand lock needs clone but runtime cannot deliver.
        try:
            from engine.pack.video_lock import load_video_lock
            from engine.pack.voice_clone import (
                assert_clone_runtime_ready,
                is_clone_provider,
            )

            _profile_probe = (customer_row.profile_json if customer_row else None) or {}
            _profile_probe = _profile_probe if isinstance(_profile_probe, dict) else {}
            _lock_probe = load_video_lock(
                str(customer),
                output_root=getattr(customer_row, "output_root", None) or settings.paths.output_root,
                profile=_profile_probe,
            )
            _voice_probe = (
                _lock_probe.get("voice") if isinstance(_lock_probe.get("voice"), dict) else {}
            )
            if is_clone_provider(str(_voice_probe.get("provider") or "")):
                assert_clone_runtime_ready()
        except ValueError as exc:
            from engine.catalog.paper_slip import release_job_reservations

            release_job_reservations(session, job.id)
            job.status = "circuit_open"
            snap = dict(job.config_snapshot_json or {})
            snap["_pipeline_phase"] = "blocked_clone_runtime"
            job.config_snapshot_json = snap
            log_event(
                session,
                job.id,
                "error",
                f"克隆旁白运行时不可用，已阻断（不连熔）：{exc}",
                {"error": str(exc)},
            )
            session.commit()
            return

        configured_seed = snap.get("seed")
        attempt_started = time.monotonic()
        seed = seed_for_output(
            configured_seed,
            int(job.produced_count or 0),
            int(snap.get("item_retry_count") or 0),
        )
        reservation_key = f"job:{job.id}:seed:{seed}"
        # Q8: assets already used earlier in this job
        job_used_assets: set[str] = set()
        for usage in session.scalars(
            select(ClipletUsage.asset_uuid).where(ClipletUsage.job_id == job.id)
        ).all():
            job_used_assets.add(str(usage))
        exclude_cliplet_ids = {
            int(x) for x in (snap.get("exclude_cliplet_ids") or []) if x is not None
        }
        exclude_asset_uuids = {
            str(x) for x in (snap.get("exclude_asset_uuids") or []) if x
        }
        strict_semantic_v1 = bool(snap.get("strict_semantic_v1", False))
        orientation = str(snap.get("orientation") or job.orientation or "portrait")
        frozen_pack = snap.get("keyword_pack") if isinstance(snap.get("keyword_pack"), dict) else {}
        frozen_pack_id = int(frozen_pack["id"]) if frozen_pack.get("id") else None
        frozen_rules = None
        pr = snap.get("production_rules")
        if isinstance(pr, dict) and isinstance(pr.get("effective_rules"), dict):
            frozen_rules = pr["effective_rules"]
        # 任务开始即对齐 VIDEO_LOCK，避免标题色分叉连败熔断
        if customer_row is not None and not snap.get("_video_lock_rules_synced"):
            from engine.pack.video_lock import sync_video_lock_into_rules

            pr_obj = dict(pr) if isinstance(pr, dict) else {}
            er0 = (
                dict(pr_obj.get("effective_rules") or {})
                if isinstance(pr_obj.get("effective_rules"), dict)
                else dict(frozen_rules or {})
            )
            er1 = sync_video_lock_into_rules(
                er0,
                customer_name=str(customer),
                profile=(
                    customer_row.profile_json
                    if isinstance(customer_row.profile_json, dict)
                    else None
                ),
                output_root=getattr(customer_row, "output_root", None),
            )
            if er1 != er0:
                pr_obj["effective_rules"] = er1
                snap["production_rules"] = pr_obj
                snap["_video_lock_rules_synced"] = True
                job.config_snapshot_json = dict(snap)
                frozen_rules = er1
                pr = pr_obj
                log_event(
                    session,
                    job.id,
                    "info",
                    "已将 VIDEO_LOCK 标题/音色同步进本任务冻结规则",
                    {
                        "title_color": er1.get("title_color"),
                        "title_stroke_width": er1.get("title_stroke_width"),
                    },
                )
                session.commit()
            else:
                snap["_video_lock_rules_synced"] = True
                job.config_snapshot_json = dict(snap)
                session.commit()
        log_event(
            session,
            job.id,
            "info",
            "规划选片开始",
            {
                "seed": seed,
                "orientation": orientation,
                "use_semantic": bool(getattr(template, "use_semantic", True)),
                "strict_semantic_v1": strict_semantic_v1,
            },
        )
        session.commit()
        plan = None
        reuse_raw = snap.get("reuse_montage_plan")
        if isinstance(reuse_raw, dict) and reuse_raw.get("clips"):
            try:
                from engine.template.engine import plan_from_dict

                plan = plan_from_dict(reuse_raw)
                log_event(
                    session,
                    job.id,
                    "info",
                    "复用 MontagePlan（画面轨）",
                    {
                        "clips": len(plan.clips or []),
                        "source_output_id": snap.get("source_output_id")
                        or snap.get("rerender_of_output_id"),
                        "plan_lang_reuse": True,
                    },
                )
                session.commit()
            except ValueError as exc:
                log_event(
                    session,
                    job.id,
                    "warning",
                    "MontagePlan 复用失败，回退重规划",
                    {"error": str(exc)[:200]},
                )
                session.commit()
                plan = None
        if plan is None:
            _cat = content_category
            if isinstance(pr, dict):
                _cat = pr.get("content_category") or _cat
            if not _cat:
                _cat = snap.get("content_category") or snap.get("category") or category
            if str(_cat) == "scene_tour" and customer_row is not None:
                from engine.pack.scene_tour import build_scene_tour_plan

                plan = build_scene_tour_plan(
                    session,
                    customer=customer_row,
                    seed=seed,
                    orientation=orientation,
                    production_rules=frozen_rules if isinstance(frozen_rules, dict) else None,
                    use_ollama=True,
                )
                log_event(
                    session,
                    job.id,
                    "info",
                    "跟镜精品规划",
                    {
                        "blocked": bool(plan.blocked),
                        "clips": len(plan.clips or []),
                        "reasons": list(plan.block_reasons or [])[:6],
                    },
                )
                session.commit()
            else:
                from engine.jobs.job_categories import plan_category_for_cliplet_filter

                plan = build_plan(
                    session,
                    template,
                    customer_name=customer,
                    theme=theme,
                    category=plan_category_for_cliplet_filter(
                        content_category, asset_category or ""
                    ),
                    seed=seed,
                    job_used_assets=job_used_assets,
                    exclude_cliplet_ids=exclude_cliplet_ids or None,
                    exclude_asset_uuids=exclude_asset_uuids or None,
                    strict_semantic_v1=strict_semantic_v1,
                    production_rules=frozen_rules,
                    keyword_pack_id=frozen_pack_id,
                    topic_intent=(
                        snap.get("topic_intent")
                        if isinstance(snap.get("topic_intent"), dict)
                        else None
                    ),
                    paper_slip_reservation_key=reservation_key,
                    customer_id=job.customer_id,
                    orientation=orientation,
                    asset_category=asset_category,
                )
        log_event(
            session,
            job.id,
            "info",
            "规划选片完成",
            {
                "clips": len(plan.clips or []),
                "blocked": bool(plan.blocked),
                "warnings": list(plan.warnings or [])[:8],
                "block_reasons": list(plan.block_reasons or [])[:8],
            },
        )
        session.commit()
        if _item_wall_exceeded(attempt_started):
            skip_current_item(
                session,
                job,
                reason="item_wall",
                stage="plan",
                error=f"elapsed>{ITEM_WALL_SEC:.0f}s after plan",
                infra=True,
                elapsed_sec=_elapsed_item(attempt_started),
                reservation_key=reservation_key,
            )
            return

        if plan.blocked or not plan.clips:
            reasons = list(plan.block_reasons or []) + list(plan.warnings or [])
            log_event(
                session,
                job.id,
                "warning",
                "Dry-run 阻塞",
                {"reasons": reasons},
            )
            from engine.catalog.paper_slip import release_job_reservations

            release_job_reservations(session, job.id)
            # 跟镜：规划门禁失败不得连烧成 circuit_open（缺素材/写词问题应 paused）
            if str(job.category or "") == "scene_tour":
                snap_st = dict(job.config_snapshot_json or {})
                reason_text = "；".join(str(r) for r in (plan.block_reasons or reasons) if r)
                coverage_hit = any(
                    k in reason_text
                    for k in (
                        "可用素材不足",
                        "没有可用素材",
                        "必选场景",
                        "场景覆盖",
                        "按库存可排站位不足",
                        "镜头过少",
                    )
                ) or (not plan.clips)
                diction_hit = any(
                    k in reason_text
                    for k in ("缺少画面要点", "旁白与画面不符")
                )
                snap_st["block_reasons"] = list(plan.block_reasons or reasons)[:12]
                if coverage_hit or not diction_hit:
                    # 缺素材/覆盖，或非写词类阻塞：立即暂停，不计入连续失败
                    snap_st["_pipeline_phase"] = "blocked_plan"
                    job.config_snapshot_json = snap_st
                    job.status = "paused"
                    log_event(
                        session,
                        job.id,
                        "info",
                        "跟镜精品规划门禁未过，已暂停（不熔断）",
                        {"reasons": snap_st["block_reasons"], "coverage": coverage_hit},
                    )
                    session.commit()
                    return
                retries = int(snap_st.get("scene_tour_plan_retries") or 0) + 1
                snap_st["scene_tour_plan_retries"] = retries
                if retries >= 3:
                    snap_st["_pipeline_phase"] = "blocked_plan"
                    job.config_snapshot_json = snap_st
                    job.status = "paused"
                    log_event(
                        session,
                        job.id,
                        "info",
                        "跟镜精品写词/画面门禁重试用尽，已暂停（不熔断）",
                        {"retries": retries, "reasons": snap_st["block_reasons"]},
                    )
                    session.commit()
                    return
                job.config_snapshot_json = snap_st
                session.commit()
                return
            job.consecutive_failures += 1
            session.commit()
            return

        semantic_source_audit: list[dict[str, Any]] = []
        if strict_semantic_v1:
            from engine.ingest.semantic_gate import semantic_gate_passed
            from engine.ingest.quality import effective_min_quality_score, is_usable_quality

            quality_floor = float(effective_min_quality_score())
            selected_ids = [clip.cliplet_id for clip in plan.clips]
            rows = {
                row.id: row
                for row in session.scalars(
                    select(Cliplet).where(Cliplet.id.in_([int(cid) for cid in selected_ids if cid]))
                ).all()
            }
            invalid: list[str] = []
            for clip in plan.clips:
                row = rows.get(int(clip.cliplet_id)) if clip.cliplet_id else None
                passed = bool(
                    row
                    and row.status == "usable"
                    and row.embedding_json is not None
                    and is_usable_quality(float(row.score or 0.0))
                    and float(row.score or 0.0) >= quality_floor
                    and semantic_gate_passed(row)
                )
                semantic_source_audit.append(
                    {
                        "cliplet_id": clip.cliplet_id,
                        "status": row.status if row else None,
                        "quality_score": float(row.score or 0.0) if row else None,
                        "has_embedding": bool(row and row.embedding_json is not None),
                        "semantic_schema_version": row.semantic_schema_version if row else None,
                        "semantic_v1_passed": passed,
                    }
                )
                if not passed:
                    invalid.append(f"cliplet_id={clip.cliplet_id or 'whole_asset'}")
            if invalid:
                job.consecutive_failures += 1
                log_event(
                    session,
                    job.id,
                    "error",
                    "严格 semantic v1 来源审计失败，禁止渲染",
                    {"invalid_sources": invalid, "audit": semantic_source_audit},
                )
                session.commit()
                return

        from engine.catalog.paper_slip import (
            assert_reservation_active,
            release_paper_slip,
            reserve_paper_slip,
        )

        try:
            slip_reservation = reserve_paper_slip(
                session,
                reservation_key=reservation_key,
                job_id=job.id,
                cliplet_ids=[c.cliplet_id for c in plan.clips if c.cliplet_id],
                phrases=list(plan.copy_components or []),
                title=plan.title,
                customer_id=job.customer_id,
            )
            session.commit()
        except Exception as exc:  # SQLite trigger also closes concurrent races
            session.rollback()
            job = session.get(Job, job.id)
            if job:
                from engine.catalog.paper_slip import release_job_reservations

                release_job_reservations(session, job.id)
                reason = str(exc)
                # Recoverable concurrency / stale-release conflict — do not burn
                # consecutive_failures toward circuit_open (was the live melt cause).
                skip_current_item(
                    session,
                    job,
                    reason="paper_slip_reserve",
                    stage="plan",
                    error=reason,
                    infra=True,
                    elapsed_sec=_elapsed_item(attempt_started),
                    reservation_key=reservation_key,
                )
                log_event(
                    session,
                    job.id,
                    "warning",
                    "纸片规则规划预留失败",
                    {"reasons": [reason], "reservation_key": reservation_key},
                )
                session.commit()
            return

        attempt = uuid.uuid4().hex[:8]
        rendering_dir = settings.paths.render_root
        rendering_dir.mkdir(parents=True, exist_ok=True)
        temp_out = rendering_dir / f"job{job.id}_{attempt}.mp4"

        render_meta: dict[str, Any] = {}
        if frozen_pack:
            render_meta["keyword_pack"] = dict(frozen_pack)
        if isinstance(pr, dict):
            render_meta["production_rules"] = {
                "rule_profile_id": pr.get("rule_profile_id"),
                "revision": pr.get("revision"),
                "name": pr.get("name"),
                "content_category": pr.get("content_category"),
                "schema_version": pr.get("schema_version"),
                "source_schema_version": pr.get("source_schema_version"),
                "frozen_at": pr.get("frozen_at"),
                "effective_rules": dict(frozen_rules or {}),
            }
        try:
            from engine.template.rule_schema import bgm_fade_out_seconds

            render_meta["bgm_fade_out_sec"] = bgm_fade_out_seconds(
                frozen_rules if isinstance(frozen_rules, dict) else None
            )
            if isinstance(frozen_rules, dict) and frozen_rules.get("bgm_volume") is not None:
                render_meta["bgm_volume"] = float(frozen_rules["bgm_volume"])
        except Exception:
            render_meta["bgm_fade_out_sec"] = 3.0
        if isinstance(snap.get("topic_intent"), dict):
            render_meta["topic_intent"] = dict(snap["topic_intent"])
        if strict_semantic_v1:
            render_meta["strict_semantic_v1"] = True
            render_meta["semantic_source_audit"] = semantic_source_audit
        profile = (customer_row.profile_json if customer_row else None) or {}
        profile = profile if isinstance(profile, dict) else {}
        # One-shot job expression overrides (from POST /jobs body) — not persisted
        snap_expr = snap.get("expression") if isinstance(snap.get("expression"), dict) else None
        if snap_expr:
            from engine.pack.expression import resolve_expression_prefs

            prefs = resolve_expression_prefs(profile, overrides=snap_expr)
            profile = {**profile, "expression": prefs}
            render_meta["expression_overrides"] = dict(prefs)

        # Frozen product video-template lock.
        from engine.pack.video_lock import load_video_lock, logo_enabled_from_lock
        from engine.render.voice_subtitle import burn_subtitles_inplace, prepare_narration_for_plan

        video_lock = load_video_lock(
            str(customer),
            output_root=getattr(customer_row, "output_root", None) or settings.paths.output_root,
            profile=profile,
        )
        render_meta["video_lock"] = {
            "locked": True,
            "locked_at": video_lock.get("locked_at"),
            "strip_all_punctuation": video_lock.get("strip_all_punctuation"),
            "reference_title": video_lock.get("reference_title"),
        }

        # Force logo off when lock says so (unless profile explicitly enables)
        if not logo_enabled_from_lock(video_lock, profile):
            brand_prof = dict(profile.get("brand") or {}) if isinstance(profile.get("brand"), dict) else {}
            brand_prof["logo_enabled"] = False
            profile = {**profile, "brand": brand_prof}

        brand = "品牌"
        if isinstance(profile.get("brand"), dict):
            brand = str(profile["brand"].get("display_name") or brand)
        pack = (
            get_pack_by_id(session, frozen_pack_id, customer_id=job.customer_id)
            if frozen_pack_id
            else get_active_pack(session, customer)
        )
        if pack and isinstance(pack.data_json, dict):
            brand = str(
                (pack.data_json.get("company_info") or {}).get("display_name_preferred") or brand
            )

        voice_dir = rendering_dir / f"job{job.id}_{attempt}_voice"
        # Recheck immediately before TTS; an expired/released lease must fail closed.
        try:
            assert_reservation_active(
                session,
                reservation_key,
                customer_id=job.customer_id,
                stage="TTS前",
            )
        except ValueError as exc:
            release_paper_slip(session, reservation_key, customer_id=job.customer_id)
            job.consecutive_failures += 1
            log_event(
                session,
                job.id,
                "warning",
                "纸片规则 TTS 前阻断",
                {"reasons": [str(exc)], "reservation": slip_reservation},
            )
            session.commit()
            return

        from engine.runtime.resource_gate import gate as resource_gate

        tts_token = f"job:{job.id}:tts"
        snap = dict(job.config_snapshot_json or {})
        snap["_pipeline_phase"] = "rendering"
        job.config_snapshot_json = snap
        session.commit()
        ollama_on = bool(getattr(settings, "ollama_narration_enabled", False))
        # 跟镜精品锁定旁白：不走 Ollama，心跳直接进 TTS
        scene_tour_job = str(job.theme or "") == "scene_tour" or str(job.category or "") == "scene_tour"
        if scene_tour_job:
            ollama_on = False
        if ollama_on:
            try:
                from engine.pack.ollama_narration import resolve_narration_model

                narr_model = resolve_narration_model(settings)
            except Exception:  # noqa: BLE001
                narr_model = str(getattr(settings, "ollama_narration_model", "") or "")
            log_event(
                session,
                job.id,
                "info",
                "旁白改写开始（Ollama）",
                {"model": narr_model},
            )
            session.commit()

        # Heartbeat while narration+TTS may block; phase_holder distinguishes stages.
        heartbeat_stop = threading.Event()
        phase_holder: dict[str, str] = {"phase": "narration" if ollama_on else "tts"}

        def _narration_heartbeat() -> None:
            while not heartbeat_stop.wait(30.0):
                try:
                    hb_session = get_session()
                    try:
                        phase = str(phase_holder.get("phase") or "narration")
                        label = "旁白改写进行中" if phase == "narration" else "TTS 合成进行中"
                        log_event(
                            hb_session,
                            job.id,
                            "info",
                            label,
                            {"phase": phase},
                        )
                        hb_session.commit()
                    finally:
                        hb_session.close()
                except Exception:  # noqa: BLE001
                    pass

        hb_thread = threading.Thread(
            target=_narration_heartbeat,
            name=f"narr-hb-{job.id}",
            daemon=True,
        )
        hb_thread.start()

        # Pause/system-stop cancellation: narration rewrite + TTS-slot wait both
        # observe this so a pause/stop does not have to wait out a hung Ollama
        # call or an indefinite TTS-slot queue before the job can be parked.
        from engine.runtime.pause_coordinator import coordinator as pause_coordinator

        narration_cancel_event = threading.Event()
        pause_monitor_stop = threading.Event()

        def _pause_monitor() -> None:
            while not pause_monitor_stop.wait(1.0):
                try:
                    if not pause_coordinator.should_claim_jobs() or self._stop.is_set():
                        narration_cancel_event.set()
                        return
                except Exception:  # noqa: BLE001
                    return

        pause_monitor_thread = threading.Thread(
            target=_pause_monitor,
            name=f"narr-cancel-{job.id}",
            daemon=True,
        )
        pause_monitor_thread.start()
        voice_info: dict[str, Any]
        exclude_openers: list[str] = []
        recent_copy_records: list[Any] = []
        try:
            from engine.pack.copy_diversity_gate import ROLLING_WINDOW, load_recent_copy_records
            from engine.pack.narration_script import recent_narration_openers

            recent_copy_records = load_recent_copy_records(
                session, getattr(job, "customer_id", None), limit=ROLLING_WINDOW
            )
            exclude_openers = recent_narration_openers(
                session,
                getattr(job, "customer_id", None),
                limit_outputs=ROLLING_WINDOW,
            )
            # TTS slot acquired inside prepare_narration_for_plan only around synth
            # (after Ollama rewrite) so hung rewrite cannot pin the machine TTS slot.
            voice_info = prepare_narration_for_plan(
                settings,
                plan,
                profile=profile,
                work_dir=voice_dir,
                brand=brand,
                video_lock=video_lock,
                production_rules=frozen_rules,
                variation_seed=seed,
                compliance_policy=(
                    pack.data_json.get("compliance")
                    if pack and isinstance(pack.data_json.get("compliance"), dict)
                    else None
                ),
                tts_token=tts_token,
                cancel_event=narration_cancel_event,
                tts_wait_deadline_sec=TTS_SLOT_WAIT_DEADLINE_SEC,
                phase_holder=phase_holder,
                exclude_openers=exclude_openers,
                recent_copy_records=recent_copy_records,
            )
        except (TimeoutError, RuntimeError) as exc:
            err = f"{type(exc).__name__}: {exc}"
            skip_current_item(
                session,
                job,
                reason="narration_or_tts_abort",
                stage=str(phase_holder.get("phase") or "tts"),
                error=err,
                infra=_error_looks_infra(err) or True,
                elapsed_sec=_elapsed_item(attempt_started),
                reservation_key=reservation_key,
            )
            return
        finally:
            heartbeat_stop.set()
            if hb_thread is not None:
                hb_thread.join(timeout=1.0)
            pause_monitor_stop.set()
            pause_monitor_thread.join(timeout=1.0)
            resource_gate.release("tts", tts_token)
        if _item_wall_exceeded(attempt_started, limit_sec=ITEM_WALL_TTS_SEC):
            skip_current_item(
                session,
                job,
                reason="item_wall",
                stage=str(phase_holder.get("phase") or "narration_or_tts"),
                error=f"elapsed>{ITEM_WALL_TTS_SEC:.0f}s after narration/TTS",
                infra=True,
                elapsed_sec=_elapsed_item(attempt_started),
                reservation_key=reservation_key,
            )
            return
        if voice_info.get("compliance_blocked"):
            release_paper_slip(session, reservation_key, customer_id=job.customer_id)
            job.consecutive_failures += 1
            log_event(
                session,
                job.id,
                "error",
                "文案合规门禁失败，已在 TTS 前阻断",
                {"compliance": voice_info.get("compliance") or {}},
            )
            session.commit()
            return
        # Ollama 旁白开启时：改写失败不得用底稿继续整轮渲染（READY 必打回 → 空转）。
        # 跟镜精品（scene_tour）故意跳过 Ollama、使用锁定旁白，不得当成 rewrite_failed。
        if (
            ollama_on
            and str(voice_info.get("voice_lang") or "zh") != "none"
            and not voice_info.get("ollama_narration")
            and not voice_info.get("scene_tour")
        ):
            from engine.pack.ollama_narration import is_ollama_infra_error

            err = str(voice_info.get("ollama_narration_error") or "rewrite_failed")
            infra = bool(
                voice_info.get("ollama_narration_infra")
                if "ollama_narration_infra" in voice_info
                else is_ollama_infra_error(err)
            )
            # Quality (bad rewrite) vs infra: quality still tracks consecutive_non_ready
            # via circuit path; infra / hangs just skip toward target_count.
            if infra or _error_looks_infra(err):
                skip_current_item(
                    session,
                    job,
                    reason="ollama_infra",
                    stage="narration",
                    error=err,
                    infra=True,
                    elapsed_sec=_elapsed_item(attempt_started),
                    reservation_key=reservation_key,
                )
                return
            release_paper_slip(session, reservation_key, customer_id=job.customer_id)
            snap = dict(job.config_snapshot_json or {})
            consecutive_non_ready = int(snap.get("consecutive_non_ready") or 0)
            snap["consecutive_non_ready"] = consecutive_non_ready + 1
            snap["consecutive_ollama_infra"] = 0
            snap.pop("next_attempt_at", None)
            snap["item_skip_count"] = int(snap.get("item_skip_count") or 0) + 1
            snap["item_retry_count"] = int(snap.get("item_retry_count") or 0) + 1
            job.config_snapshot_json = snap
            log_event(
                session,
                job.id,
                "warning",
                "跳过本条，继续生产",
                {
                    "stage": "narration",
                    "reason": "rewrite_quality",
                    "error": err,
                    "model": voice_info.get("ollama_narration_model"),
                    "attempts": voice_info.get("ollama_narration_attempts"),
                    "consecutive_non_ready": snap.get("consecutive_non_ready"),
                    "produced_count": int(job.produced_count or 0),
                    "target_count": job.target_count,
                    "item_skip_count": snap["item_skip_count"],
                },
            )
            if int(snap.get("consecutive_non_ready") or 0) >= settings.quality_circuit_threshold:
                job.status = "circuit_open"
                log_event(session, job.id, "error", "质量熔断：连续旁白改写失败过多")
            session.commit()
            return
        if voice_info.get("ollama_narration"):
            log_event(
                session,
                job.id,
                "info",
                "旁白改写完成",
                {
                    "model": voice_info.get("ollama_narration_model"),
                    "attempts": voice_info.get("ollama_narration_attempts"),
                    "reused": bool(voice_info.get("ollama_narration_reused")),
                },
            )
            session.commit()
        # VIDEO_LOCK pinned TTS: retry Edge once on network flake; clone has no silent fallback
        from engine.pack.voice_clone import is_clone_provider

        lock_voice = video_lock.get("voice") if isinstance(video_lock.get("voice"), dict) else {}
        lock_provider = str(
            (
                frozen_rules.get("tts_provider")
                if isinstance(frozen_rules, dict)
                else None
            )
            or lock_voice.get("provider")
            or ""
        ).lower()
        requires_edge = lock_provider in ("edge", "xiaoxiao")
        requires_clone = is_clone_provider(lock_provider)
        vlang = str(voice_info.get("voice_lang") or "zh")
        edge_label = "Edge 晓晓" if vlang.startswith("zh") else f"Edge（{vlang}）"
        clone_label = str(lock_voice.get("label") or lock_voice.get("voice_pack") or "本地克隆音色")
        if requires_edge and (
            voice_info.get("error")
            or voice_info.get("provider") not in (None, "edge")
            or (voice_info.get("voice_lang") != "none" and not voice_info.get("narration_path"))
        ):
            log_event(
                session,
                job.id,
                "warning",
                f"锁死音色 {edge_label} 失败，重试 TTS（复用已成功的旁白文案，不再调用 Ollama）",
                {"error": voice_info.get("error"), "provider": voice_info.get("provider")},
            )
            import time as _time

            _time.sleep(2.5)
            # This retry is a TTS-transport flake, not a narration failure — reuse
            # the already-successful Ollama script instead of re-running rewrite.
            reuse_rewrite = (
                {
                    "script": voice_info.get("script"),
                    "script_display": voice_info.get("script_display"),
                    "emoji_cues": voice_info.get("emoji_cues"),
                    "ollama_narration_model": voice_info.get("ollama_narration_model"),
                    "ollama_narration_attempts": voice_info.get("ollama_narration_attempts"),
                }
                if voice_info.get("ollama_narration")
                else None
            )
            # Reuse same work_dir so per-sentence WAV resume can skip finished cues
            phase_holder["phase"] = "tts"
            voice_info = prepare_narration_for_plan(
                settings,
                plan,
                profile=profile,
                work_dir=voice_dir,
                brand=brand,
                video_lock=video_lock,
                production_rules=frozen_rules,
                variation_seed=seed,
                compliance_policy=(
                    pack.data_json.get("compliance")
                    if pack and isinstance(pack.data_json.get("compliance"), dict)
                    else None
                ),
                tts_token=tts_token,
                reuse_rewrite=reuse_rewrite,
                cancel_event=narration_cancel_event,
                tts_wait_deadline_sec=TTS_SLOT_WAIT_DEADLINE_SEC,
                phase_holder=phase_holder,
                exclude_openers=exclude_openers,
                recent_copy_records=recent_copy_records,
            )
            vlang = str(voice_info.get("voice_lang") or "zh")
            edge_label = "Edge 晓晓" if vlang.startswith("zh") else f"Edge（{vlang}）"
        if requires_edge and voice_info.get("voice_lang") != "none":
            if voice_info.get("error") or voice_info.get("provider") != "edge" or not voice_info.get(
                "narration_path"
            ):
                err = str(voice_info.get("error") or "edge_tts_failed")
                skip_current_item(
                    session,
                    job,
                    reason="edge_tts",
                    stage="tts",
                    error=err,
                    infra=_error_looks_infra(err) or True,
                    elapsed_sec=_elapsed_item(attempt_started),
                    reservation_key=reservation_key,
                )
                return
        if requires_clone and voice_info.get("voice_lang") != "none":
            if voice_info.get("error") or voice_info.get("provider") != "clone" or not voice_info.get(
                "narration_path"
            ):
                err = str(voice_info.get("error") or "clone_tts_failed")
                skip_current_item(
                    session,
                    job,
                    reason="clone_tts",
                    stage="tts",
                    error=err,
                    infra=_error_looks_infra(err),
                    elapsed_sec=_elapsed_item(attempt_started),
                    reservation_key=reservation_key,
                )
                return

        # Non-locked TTS error: still skipable for timeout/empty, else soft continue.
        if voice_info.get("error") and _error_looks_infra(str(voice_info.get("error"))):
            skip_current_item(
                session,
                job,
                reason="tts_infra",
                stage="tts",
                error=str(voice_info.get("error")),
                infra=True,
                elapsed_sec=_elapsed_item(attempt_started),
                reservation_key=reservation_key,
            )
            return

        if voice_info.get("narration_path") and not voice_info.get("error"):
            log_event(
                session,
                job.id,
                "info",
                "TTS 完成",
                {
                    "provider": voice_info.get("provider"),
                    "mode": voice_info.get("tts_mode"),
                },
            )
            session.commit()

        narr_path = voice_info.get("narration_path")
        if voice_info.get("compliance"):
            render_meta["copy_compliance"] = voice_info["compliance"]
        if narr_path:
            render_meta["narration_path"] = narr_path
            render_meta["tts_provider"] = voice_info.get("provider")
            render_meta["tts_voice"] = voice_info.get("voice")
            render_meta["tts_mode"] = voice_info.get("tts_mode")
            render_meta["tts_rate"] = voice_info.get("tts_rate")
            render_meta["tts_pitch"] = voice_info.get("tts_pitch")
            render_meta["tts_volume"] = voice_info.get("tts_volume")
            if voice_info.get("clone_pack"):
                render_meta["clone_pack"] = voice_info.get("clone_pack")
            if voice_info.get("ollama_narration_error"):
                render_meta["ollama_narration_error"] = voice_info.get("ollama_narration_error")
            render_meta["voice_lang"] = voice_info.get("voice_lang")
            render_meta["subtitle_lang"] = voice_info.get("subtitle_lang")
            render_meta["subtitle_burn"] = voice_info.get("subtitle_burn")
            render_meta["dual_secondary_lang"] = voice_info.get("dual_secondary_lang")
            render_meta["inter_sentence_gap_sec"] = voice_info.get("inter_sentence_gap_sec")
            render_meta["narration_script"] = voice_info.get("script_display") or voice_info.get("script")
            if voice_info.get("subtitle_aligned_to_voice") is not None:
                render_meta["subtitle_aligned_to_voice"] = bool(
                    voice_info.get("subtitle_aligned_to_voice")
                )
            if voice_info.get("subtitle_tail_trim_sec") is not None:
                render_meta["subtitle_tail_trim_sec"] = voice_info.get("subtitle_tail_trim_sec")
            if voice_info.get("subtitle_align_pass"):
                render_meta["subtitle_align_pass"] = voice_info.get("subtitle_align_pass")
            if voice_info.get("scene_tour"):
                render_meta["scene_tour"] = True
            if voice_info.get("scene_tour_timing") is not None:
                render_meta["scene_tour_timing"] = voice_info.get("scene_tour_timing")
            if voice_info.get("scene_tour_clip_aligned") is not None:
                render_meta["scene_tour_clip_aligned"] = voice_info.get("scene_tour_clip_aligned")
        if voice_info.get("error"):
            log_event(session, job.id, "warning", "旁白合成失败，继续无旁白渲染", {"error": voice_info["error"]})

        selected_cliplets = {
            row.id: row
            for row in session.scalars(
                select(Cliplet).where(
                    Cliplet.id.in_([int(c.cliplet_id) for c in plan.clips if c.cliplet_id])
                )
            ).all()
        }
        item_label_cues = build_item_label_cues(
            plan,
            selected_cliplets,
            frozen_rules,
            snap.get("topic_intent") if isinstance(snap.get("topic_intent"), dict) else None,
        )
        # "render" is acquired only for this FFmpeg-heavy stretch (encode →
        # subtitle burn → cover extraction) and released right after, so
        # planning/narration/TTS never pin the slot (see resource_gate.py).
        render_token = f"job:{job.id}"
        render_wait_deadline = time.monotonic() + RENDER_SLOT_WAIT_DEADLINE_SEC
        while not resource_gate.try_acquire("render", render_token):
            if time.monotonic() >= render_wait_deadline:
                skip_current_item(
                    session,
                    job,
                    reason="render_slot_wait",
                    stage="render",
                    error=f"render_slot_wait_timeout ({RENDER_SLOT_WAIT_DEADLINE_SEC:.0f}s)",
                    infra=True,
                    elapsed_sec=_elapsed_item(attempt_started),
                    reservation_key=reservation_key,
                )
                return
            if _item_wall_exceeded(attempt_started):
                skip_current_item(
                    session,
                    job,
                    reason="item_wall",
                    stage="render_wait",
                    error=f"elapsed>{ITEM_WALL_SEC:.0f}s waiting for render slot",
                    infra=True,
                    elapsed_sec=_elapsed_item(attempt_started),
                    reservation_key=reservation_key,
                )
                return
            time.sleep(0.5)
        # NOTE: if any statement between here and the release_render() call below
        # raises, the outer worker loop's `finally: resource_gate.release_all(token)`
        # (token == render_token, same "job:<id>" string) is the safety net that
        # still frees this slot — see JobWorker._loop.
        # 渲染前再对齐一次标题色（跟镜曾只 apply_lock、未叠冻结规则 → 黄字连败熔断）
        try:
            from engine.pack.video_lock import resolve_title_style

            plan.title_style = resolve_title_style(
                dict(plan.title_style or {}),
                customer_name=str(customer),
                production_rules=frozen_rules if isinstance(frozen_rules, dict) else None,
                profile=profile if isinstance(profile, dict) else None,
                output_root=getattr(customer_row, "output_root", None)
                if customer_row is not None
                else None,
                orientation=orientation,
            )
        except Exception:  # noqa: BLE001
            pass
        ok = render_plan(
            settings,
            plan,
            temp_out,
            template.model_dump(),
            render_meta=render_meta,
            profile=profile,
            customer_name=str(customer),
            narration_path=Path(narr_path) if narr_path else None,
            object_label_cues=item_label_cues,
        )
        # 跟镜精品：片尾大段冻帧 + 旁白仍在说 = 事故，不得进 ready
        if ok and voice_info.get("scene_tour"):
            freeze_pad = float(render_meta.get("freeze_pad_sec") or 0)
            if freeze_pad > 0.5:
                ok = False
                log_event(
                    session,
                    job.id,
                    "warning",
                    "声画不同步：片尾冻帧过长，打回重做",
                    {
                        "freeze_pad_sec": freeze_pad,
                        "scene_tour_timing": voice_info.get("scene_tour_timing"),
                        "narration_fit": voice_info.get("narration_fit_to_picture"),
                        "narration_fit_at_mux": render_meta.get("narration_fit_at_mux"),
                    },
                )
                session.commit()
        if not ok:
            resource_gate.release("render", render_token)
            missing = [
                c.source_path
                for c in (plan.clips or [])
                if not Path(str(c.source_path)).is_file()
            ]
            # Missing media is fail-closed (not skip-and-fill); timeout/generic render skip.
            if missing:
                release_paper_slip(session, reservation_key, customer_id=job.customer_id)
                job.consecutive_failures += 1
                log_event(
                    session,
                    job.id,
                    "error",
                    "渲染失败",
                    {
                        "missing_sources": missing[:5],
                        "missing_count": len(missing),
                        "clip_count": len(plan.clips or []),
                        "hint": "素材路径不存在时请检查片库挂载或 /Users/xlf→本机用户路径迁移",
                    },
                )
                session.commit()
                return
            skip_current_item(
                session,
                job,
                reason="render_failed",
                stage="render",
                error="render_plan returned False",
                infra=True,
                elapsed_sec=_elapsed_item(attempt_started),
                reservation_key=reservation_key,
            )
            return

        # Intentionally no ITEM_WALL after successful render_plan: clone TTS +
        # scene_tour already spent most of the budget on narration; throwing away
        # a finished encode caused paper-slip re-reserve conflicts and circuit_open.

        # Subtitles: always keep SRT sidecar when present; burn only for burn_mono / burn_dual
        srt_path = voice_info.get("srt_path")
        burn_mode = str(voice_info.get("subtitle_burn") or "external")
        render_meta["subtitle_burn"] = burn_mode
        render_meta["ollama_narration"] = bool(voice_info.get("ollama_narration"))
        if voice_info.get("emoji_cues"):
            render_meta["emoji_cues"] = voice_info.get("emoji_cues")
        sub_on = voice_info.get("subtitle_enabled")
        if sub_on is None:
            sub_on = (frozen_rules or {}).get("subtitle_enabled")
        if sub_on is None:
            sub_on = True
        render_meta["subtitle_enabled"] = bool(sub_on)
        if srt_path and voice_info.get("subtitle_lang") != "none" and bool(sub_on):
            render_meta["subtitle_path"] = srt_path
            render_meta["dual_secondary_lang"] = voice_info.get("dual_secondary_lang")
            if burn_mode in ("burn_mono", "burn_dual"):
                burned = burn_subtitles_inplace(
                    temp_out,
                    Path(srt_path),
                    work_dir=voice_dir,
                    font_size=int(voice_info.get("subtitle_font_size") or 64),
                    bottom_padding_px=int(voice_info.get("subtitle_bottom_padding_px") or 420),
                    color=str(voice_info.get("subtitle_color") or "#FFFFFF"),
                    stroke_color=str(
                        voice_info.get("subtitle_stroke_color") or "#000000"
                    ),
                    stroke_width=(
                        0
                        if (
                            voice_info.get("subtitle_stroke_enabled") is False
                            or (frozen_rules or {}).get("subtitle_stroke_enabled") is False
                        )
                        else int(
                            voice_info.get("subtitle_stroke_width")
                            if voice_info.get("subtitle_stroke_width") is not None
                            else 3
                        )
                    ),
                    align=str(voice_info.get("subtitle_align") or "center"),
                    subtitle_layout=str(
                        voice_info.get("subtitle_layout")
                        or (frozen_rules or {}).get("subtitle_layout")
                        or "horizontal"
                    ),
                    subtitle_vertical_side=str(
                        voice_info.get("subtitle_vertical_side")
                        or (frozen_rules or {}).get("subtitle_vertical_side")
                        or "left"
                    ),
                    narration_text_effect=str(
                        voice_info.get("narration_text_effect")
                        or (frozen_rules or {}).get("narration_text_effect")
                        or "none"
                    ),
                    font_family=str(
                        voice_info.get("subtitle_font_family")
                        or (frozen_rules or {}).get("subtitle_font_family")
                        or "default"
                    ),
                    center_x_pct=(
                        float(voice_info["subtitle_x_pct"])
                        if voice_info.get("subtitle_x_pct") is not None
                        else float((frozen_rules or {})["subtitle_x_pct"])
                        if (frozen_rules or {}).get("subtitle_x_pct") is not None
                        else None
                    ),
                    center_y_pct=(
                        float(voice_info["subtitle_y_pct"])
                        if voice_info.get("subtitle_y_pct") is not None
                        else float((frozen_rules or {})["subtitle_y_pct"])
                        if (frozen_rules or {}).get("subtitle_y_pct") is not None
                        else None
                    ),
                )
                render_meta["subtitle_burned"] = bool(burned.get("ok"))
                render_meta["subtitle_style_effective"] = {
                    "font_size": int(voice_info.get("subtitle_font_size") or 64),
                    "color": str(voice_info.get("subtitle_color") or "#FFFFFF"),
                    "stroke_color": str(
                        voice_info.get("subtitle_stroke_color") or "#000000"
                    ),
                    "stroke_width": int(
                        voice_info.get("subtitle_stroke_width")
                        if voice_info.get("subtitle_stroke_width") is not None
                        else 3
                    ),
                    "glyph_bottom_px": int(
                        voice_info.get("subtitle_bottom_padding_px") or 420
                    ),
                    "align": str(voice_info.get("subtitle_align") or "center"),
                    "effect": str(voice_info.get("subtitle_effect") or "none"),
                }
                if not burned.get("ok"):
                    log_event(
                        session,
                        job.id,
                        "warning",
                        "字幕烧录失败，成片仍保留无字幕版（SRT 已生成）",
                        {"error": burned.get("error"), "mode": burn_mode},
                    )
                else:
                    render_meta["subtitle_burn_method"] = burned.get("method")
            else:
                render_meta["subtitle_burned"] = False
                log_event(
                    session,
                    job.id,
                    "info",
                    "字幕方式为外挂 SRT，未烧录进成片",
                    {"srt": srt_path},
                )

        # HARD (2026-07-26): 禁止标题附近浮动表情贴纸；表情只进字幕行内
        render_meta["title_stickers_disabled"] = True
        render_meta["emoji_in_subtitle"] = bool(voice_info.get("emoji_in_subtitle"))
        render_meta["subtitle_has_emoji"] = bool(voice_info.get("subtitle_has_emoji"))
        if voice_info.get("emoji_cues"):
            render_meta["emoji_cues"] = voice_info.get("emoji_cues")
        # Legacy sticker burn permanently off for 成片 (emoji live in subtitle burn)
        render_meta["emoji_burned"] = False
        render_meta["emoji_burn_count"] = 0
        if voice_info.get("subtitle_has_emoji") and burn_mode in ("burn_mono", "burn_dual"):
            render_meta["emoji_in_subtitle_burned"] = bool(render_meta.get("subtitle_burned"))
        elif voice_info.get("subtitle_has_emoji"):
            render_meta["emoji_in_subtitle_burned"] = False
            log_event(
                session,
                job.id,
                "warning",
                "字幕含表情但未烧录进成片（应 force burn_mono）",
                {"burn_mode": burn_mode},
            )

        paper_slip_ready_error: str | None = None
        try:
            paper_slip_ready_check = assert_reservation_active(
                session,
                reservation_key,
                customer_id=job.customer_id,
                stage="READY_GATE前",
            )
        except ValueError as exc:
            paper_slip_ready_error = str(exc)
            paper_slip_ready_check = {
                "stage": "READY_GATE前",
                "error": paper_slip_ready_error,
            }
            plan.block_reasons.append(paper_slip_ready_error)
        render_meta["paper_slip"] = {
            "reservation": slip_reservation,
            "ready_check": paper_slip_ready_check,
        }

        qc = run_qc(temp_out, plan, require_audio=bool(settings.require_audio))
        # pack already loaded above for brand
        copy = build_copywriting(plan, pack.data_json if pack else None, music_credit=render_meta.get("music_credit"))

        cover_count = int(getattr(template, "cover_count", 3) or 3)
        cover_dir = Path(str(temp_out.with_suffix("")) + "_covers")
        covers = extract_cover_candidates(temp_out, cover_dir, count=cover_count)
        cover_paths = [str(p) for p in covers]
        # FFmpeg-heavy stretch (encode → subtitle burn → cover extraction) is done —
        # free "render" so a next job's render phase is not blocked on this job's
        # remaining (lightweight) sidecar/QC/publish-trail bookkeeping.
        resource_gate.release("render", render_token)

        sidecar = write_sidecar(
            temp_out,
            plan,
            job_id=job.id,
            qc={"passed": qc.passed, "reasons": qc.reasons, "metrics": qc.metrics},
            copywriting=copy,
            covers=cover_paths,
            meta={
                "reframe_mode": getattr(template, "reframe_mode", "smart"),
                "consistency_score": plan.consistency_score,
                "needs_review": plan.needs_review,
                "title_style": plan.title_style,
                **render_meta,
            },
        )

        # Homology: mux may tempo-fit the VO bed again after SRT was built.
        # Re-clamp cues to the final narration wav READY_GATE will hear.
        if (
            srt_path
            and Path(str(srt_path)).is_file()
            and narr_path
            and Path(str(narr_path)).is_file()
            and voice_info.get("subtitle_lang") not in (None, "", "none")
        ):
            try:
                from engine.pack.narration_script import tighten_srt_to_voiceover

                trim_gate = float(
                    voice_info.get("subtitle_tail_trim_sec")
                    or render_meta.get("subtitle_tail_trim_sec")
                    or 0.12
                )
                if trim_gate < 0.08:
                    trim_gate = 0.12
                srt_file = Path(str(srt_path))
                body = srt_file.read_text(encoding="utf-8")
                tightened = tighten_srt_to_voiceover(
                    body,
                    Path(str(narr_path)),
                    tail_trim_seconds=trim_gate,
                )
                if tightened != body:
                    srt_file.write_text(tightened, encoding="utf-8")
                render_meta["subtitle_aligned_to_voice"] = True
                render_meta["subtitle_align_pass"] = "pre_ready_gate"
                render_meta["subtitle_tail_trim_sec"] = trim_gate
            except Exception as exc:  # noqa: BLE001
                render_meta["subtitle_align_error"] = str(exc)[:240]

        # QC fail → failed; READY_GATE fail → failed; consistency hard fail → review; else ready
        # 强制参考 docs/READY_GATE.md — 门禁未全过不得进成品库
        from engine.qc.ready_gate import evaluate_ready_gate

        mock_vo = (
            str(render_meta.get("tts_provider") or "").lower() == "mock"
            and voice_info.get("voice_lang") not in (None, "", "none")
            and bool(getattr(settings, "tts_provider", "edge") != "mock")
        )
        gate = evaluate_ready_gate(
            temp_out,
            require_ollama=(
                bool(getattr(settings, "ollama_narration_enabled", False))
                and not bool(voice_info.get("scene_tour"))
            ),
        )
        if paper_slip_ready_error:
            gate = {
                **gate,
                "ok": False,
                "fail_count": int(gate.get("fail_count") or 0) + 1,
                "fails": list(gate.get("fails") or []) + [paper_slip_ready_error],
                "checks": {
                    **dict(gate.get("checks") or {}),
                    "paper_slip": paper_slip_ready_check,
                },
            }
        # Persist gate result onto sidecar before move
        if sidecar.exists():
            import json as _json

            try:
                _data = _json.loads(sidecar.read_text(encoding="utf-8"))
                _data["ready_gate"] = {
                    "ok": gate.get("ok"),
                    "fails": gate.get("fails") or [],
                    "fail_count": gate.get("fail_count") or 0,
                    "checks": gate.get("checks") or {},
                }
                sidecar.write_text(_json.dumps(_data, indent=2, ensure_ascii=False), encoding="utf-8")
            except (OSError, _json.JSONDecodeError, TypeError):
                pass
        if not qc.passed:
            final_state = "failed"
        elif not gate.get("ok"):
            final_state = "failed"
            snap = dict(job.config_snapshot_json or {})
            snap["last_ready_gate_fails"] = list(gate.get("fails") or [])[:12]
            snap["last_ready_gate_ok"] = False
            job.config_snapshot_json = snap
            log_event(
                session,
                job.id,
                "warning",
                "READY_GATE 未通过，打回重做（不成 ready）",
                {"fails": gate.get("fails") or [], "fail_count": gate.get("fail_count")},
            )
        elif mock_vo:
            # READY_GATE already forbids mock; keep review as a hard safety net.
            final_state = "review"
            log_event(
                session,
                job.id,
                "warning",
                "旁白为 mock 静音占位，降级 review（生产默认 Edge）",
                {"tts_provider": render_meta.get("tts_provider")},
            )
        else:
            # Gate pass → ready + mandatory auto-approve. Soft consistency
            # (needs_review) is logged only; it must not park outputs in the
            # human review queue forever.
            final_state = "ready"
            if plan.needs_review:
                log_event(
                    session,
                    job.id,
                    "warning",
                    "标题-画面一致性偏低，仍按 READY_GATE 合格入库并自动过审",
                    {
                        "consistency_score": plan.consistency_score,
                        "needs_review": True,
                    },
                )
            log_event(
                session,
                job.id,
                "info",
                "READY_GATE 全过 → ready（强制参考 HARD_LOCKS/VIDEO_LOCK）",
                {"title_color": (plan.title_style or {}).get("color")},
            )
        final_dir = settings.paths.output_root / final_state / datetime.now().strftime("%Y-%m-%d")
        final_dir.mkdir(parents=True, exist_ok=True)
        final_out = final_dir / f"montage_{job.id}_{seed}.mp4"
        temp_out.rename(final_out)
        sidecar_new = final_out.with_suffix(".json")
        if sidecar.exists():
            sidecar.rename(sidecar_new)
        if srt_path and Path(srt_path).is_file():
            try:
                import shutil

                src_srt = Path(srt_path)
                # Keep dual / lang tag from work SRT name (e.g. subtitle.dual.srt → .dual.srt)
                tag = src_srt.stem.replace("subtitle.", "", 1) if src_srt.stem.startswith("subtitle.") else "sub"
                shutil.copy2(src_srt, final_out.with_name(f"{final_out.stem}.{tag}.srt"))
            except OSError:
                pass
        if narr_path and Path(str(narr_path)).is_file():
            try:
                import shutil

                shutil.copy2(narr_path, final_out.with_suffix(".voice.wav"))
            except OSError:
                pass

        # move covers next to final output
        final_cover_dir = final_dir / f"montage_{job.id}_{seed}_covers"
        final_cover_dir.mkdir(parents=True, exist_ok=True)
        final_covers: list[str] = []
        for p in covers:
            dest = final_cover_dir / p.name
            try:
                p.replace(dest)
                final_covers.append(str(dest))
            except OSError:
                final_covers.append(str(p))
        from engine.catalog.serial import (
            allocate_display_no,
            allocate_render_serial,
            format_display_no,
            resolve_customer_key,
        )

        pack_data = pack.data_json if pack and isinstance(pack.data_json, dict) else None
        profile_json = (
            customer_row.profile_json
            if customer_row and isinstance(customer_row.profile_json, dict)
            else None
        )
        serial = allocate_render_serial(
            session,
            customer_id=job.customer_id,
            customer_key=resolve_customer_key(
                customer_name=customer,
                pack_data=pack_data,
                profile_json=profile_json,
            ),
        )
        display_no = allocate_display_no(session, customer_id=job.customer_id)
        display_label = format_display_no(display_no)

        if sidecar_new.exists():
            import json

            data = json.loads(sidecar_new.read_text(encoding="utf-8"))
            if final_covers:
                data["covers"] = final_covers
            data["needs_review"] = plan.needs_review
            data["consistency_score"] = plan.consistency_score
            data["serial"] = serial
            data["display_no"] = display_no
            data["display_label"] = display_label
            sidecar_new.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        out_row = RenderOutput(
            job_id=job.id,
            output_path=str(final_out),
            state=final_state,
            seed=seed,
            serial=serial,
            display_no=display_no,
            orientation=orientation,
            sidecar_path=str(sidecar_new),
            qc_json={
                "passed": qc.passed and bool(gate.get("ok")),
                "reasons": list(qc.reasons) + list(gate.get("fails") or []),
                "metrics": qc.metrics,
                "covers": final_covers,
                "consistency_score": plan.consistency_score,
                "needs_review": plan.needs_review,
                "reframe_mode": getattr(template, "reframe_mode", "smart"),
                "serial": serial,
                "display_no": display_no,
                "display_label": display_label,
                "ready_gate": {
                    "ok": gate.get("ok"),
                    "fail_count": gate.get("fail_count") or 0,
                    "fails": gate.get("fails") or [],
                },
            },
        )
        session.add(out_row)
        session.flush()
        # ClipletUsage 仍记全部产出（模板冷却用）；纸片滚动避重仅 ready 记账
        record_cliplet_usage(session, plan, job_id=job.id, render_output_id=out_row.id, customer_id=job.customer_id)

        pack_result: dict[str, Any] = {"ok": False, "skipped": "not_ready"}
        if final_state == "ready":
            from engine.catalog.review_auto import ensure_output_decision

            decision = ensure_output_decision(
                session, customer_row, out_row, source="worker", export_pack=True
            )
            pack_result = dict(decision.get("pack") or {})

        if final_state == "ready" and pack_result.get("ok"):
            job.produced_count += 1
            job.consecutive_failures = 0
            snap["consecutive_non_ready"] = 0
            snap["consecutive_ollama_infra"] = 0
            snap["item_retry_count"] = 0
            snap.pop("next_attempt_at", None)
            job.config_snapshot_json = dict(snap)
            record_keyword_usage(session, plan.title, theme, job.id, customer_id=job.customer_id)
            # PAPER_SLIP：仅 ready 成功才记账（滚动避重；强制参考 docs/PAPER_SLIP_LOCK.md）
            from engine.catalog.paper_slip import commit_paper_slip_for_ready

            slip = commit_paper_slip_for_ready(
                session,
                reservation_key=reservation_key,
                job_id=job.id,
                cliplet_ids=[c.cliplet_id for c in plan.clips if c.cliplet_id],
                phrases=list(plan.copy_components or []),
                title=plan.title,
                customer_id=job.customer_id,
            )
            log_event(
                session,
                job.id,
                "info",
                "渲染成功",
                {"path": str(final_out), "seed": seed, "paper_slip": slip},
            )
            if (
                job.mode == "count"
                and job.target_count is not None
                and int(job.produced_count or 0) >= int(job.target_count)
            ):
                snap["_pipeline_phase"] = "completed"
                job.config_snapshot_json = dict(snap)
                job.status = "completed"
                log_event(session, job.id, "info", "达到目标条数，任务完成")
        elif final_state == "ready":
            release_paper_slip(session, reservation_key, customer_id=job.customer_id)
            job.consecutive_failures += 1
            snap["consecutive_non_ready"] = consecutive_non_ready + 1
            job.config_snapshot_json = dict(snap)
            log_event(
                session,
                job.id,
                "error",
                "READY 成片发布物料生成失败，已转 asset_blocked",
                {
                    "output_id": out_row.id,
                    "pack_status": out_row.pack_status,
                    "error": out_row.pack_error,
                },
            )
        elif final_state == "review":
            release_paper_slip(session, reservation_key, customer_id=job.customer_id)
            job.produced_count += 1  # counts toward quota but needs human check
            job.consecutive_failures = 0
            snap["consecutive_non_ready"] = consecutive_non_ready + 1
            job.config_snapshot_json = dict(snap)
            record_keyword_usage(session, plan.title, theme, job.id, customer_id=job.customer_id)
            log_event(
                session,
                job.id,
                "warning",
                "成片进抽检(一致性不足)",
                {
                    "path": str(final_out),
                    "consistency": plan.consistency_score,
                    "consecutive_non_ready": snap["consecutive_non_ready"],
                },
            )
            if snap["consecutive_non_ready"] >= settings.quality_circuit_threshold:
                job.status = "circuit_open"
                log_event(session, job.id, "error", "质量熔断：连续进审过多")
        else:
            release_paper_slip(session, reservation_key, customer_id=job.customer_id)
            gate_fails = list(gate.get("fails") or [])
            from engine.pack.video_lock import (
                is_title_style_lock_desync,
                sync_video_lock_into_rules,
            )

            # 标题色/描边分叉属配置问题（规则黄/实渲奶油，或相反），连败计入熔断无意义
            if is_title_style_lock_desync(gate_fails):
                pr = (
                    dict(snap.get("production_rules") or {})
                    if isinstance(snap.get("production_rules"), dict)
                    else {}
                )
                er = (
                    dict(pr.get("effective_rules") or {})
                    if isinstance(pr.get("effective_rules"), dict)
                    else {}
                )
                er = sync_video_lock_into_rules(
                    er,
                    customer_name=str(customer),
                    profile=profile if isinstance(profile, dict) else None,
                    output_root=getattr(customer_row, "output_root", None)
                    if customer_row
                    else None,
                )
                pr["effective_rules"] = er
                snap["production_rules"] = pr
                snap["_video_lock_rules_synced"] = True
                job.config_snapshot_json = dict(snap)
                log_event(
                    session,
                    job.id,
                    "warning",
                    "标题样式与 VIDEO_LOCK/冻结规则不一致，已同步（本条不计入质量熔断）",
                    {
                        "fails": gate_fails,
                        "title_color": er.get("title_color"),
                        "title_stroke_color": er.get("title_stroke_color"),
                        "title_stroke_width": er.get("title_stroke_width"),
                    },
                )
            else:
                job.consecutive_failures += 1
                snap["consecutive_non_ready"] = consecutive_non_ready + 1
                job.config_snapshot_json = dict(snap)
                log_event(
                    session,
                    job.id,
                    "warning",
                    "质检/READY_GATE 未通过",
                    {
                        "reasons": list(qc.reasons) + gate_fails,
                        "ready_gate_ok": gate.get("ok"),
                        "consecutive_non_ready": snap["consecutive_non_ready"],
                    },
                )
                if snap["consecutive_non_ready"] >= settings.quality_circuit_threshold:
                    job.status = "circuit_open"
                    log_event(session, job.id, "error", "质量熔断：连续质检失败过多")

        if final_state != "ready":
            from engine.catalog.review_auto import ensure_output_decision

            ensure_output_decision(
                session, customer_row, out_row, source="worker", export_pack=False
            )
        job.updated_at = datetime.now(timezone.utc)
        session.commit()


worker = JobWorker()
