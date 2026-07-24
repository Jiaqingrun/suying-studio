from __future__ import annotations

import random
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.customer_scope import require_active_customer, resolve_customer, settings_with_customer_paths
from engine.catalog.db import ClipletUsage, Job, RenderOutput, get_session, log_event
from engine.catalog.keyword_pack import get_active_pack, record_keyword_usage
from engine.config.paths import ensure_layout
from engine.config.settings import AppSettings, load_settings
from engine.export.sidecar import build_copywriting, write_sidecar
from engine.qc.gates import run_qc
from engine.render.covers import extract_cover_candidates
from engine.render.ffmpeg import render_plan
from engine.template.engine import DEFAULT_TEMPLATE, TemplateDefinition, build_plan, record_cliplet_usage


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

    def _loop(self) -> None:
        while not self._stop.is_set():
            settings = load_settings()
            session = get_session()
            try:
                job = session.scalar(
                    select(Job).where(Job.status.in_(("queued", "running"))).order_by(Job.id).limit(1)
                )
                if not job:
                    time.sleep(1.0)
                    continue
                if job.status == "queued":
                    job.status = "running"
                    session.commit()
                with self._lock:
                    self._running_job_id = job.id
                self._process_job(session, settings, job)
                with self._lock:
                    self._running_job_id = None
            finally:
                session.close()
            time.sleep(0.2)

    def _process_job(self, session: Session, settings: AppSettings, job: Job) -> None:
        from engine.catalog.db import Customer

        customer_row = session.get(Customer, job.customer_id) if job.customer_id else None
        if customer_row:
            settings = settings_with_customer_paths(settings, customer_row)
        ensure_layout(settings)
        snap = job.config_snapshot_json or {}
        template_data = snap.get("template") or DEFAULT_TEMPLATE.model_dump()
        template = TemplateDefinition(**template_data)
        customer = snap.get("customer_name", "default")
        theme = job.theme
        category = job.category

        if job.consecutive_failures >= settings.circuit_breaker_threshold:
            job.status = "circuit_open"
            log_event(session, job.id, "error", "连续失败熔断，任务暂停")
            session.commit()
            return

        consecutive_non_ready = int(snap.get("consecutive_non_ready", 0) or 0)
        if consecutive_non_ready >= settings.quality_circuit_threshold:
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

        seed = random.randint(1, 2_000_000_000)
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
        plan = build_plan(
            session,
            template,
            customer_name=customer,
            theme=theme,
            category=category,
            seed=seed,
            job_used_assets=job_used_assets,
            exclude_cliplet_ids=exclude_cliplet_ids or None,
            exclude_asset_uuids=exclude_asset_uuids or None,
        )

        if plan.blocked or not plan.clips:
            job.consecutive_failures += 1
            log_event(session, job.id, "warning", "Dry-run 阻塞", {"reasons": plan.block_reasons + plan.warnings})
            session.commit()
            return

        attempt = uuid.uuid4().hex[:8]
        rendering_dir = settings.paths.render_root
        rendering_dir.mkdir(parents=True, exist_ok=True)
        temp_out = rendering_dir / f"job{job.id}_{attempt}.mp4"

        render_meta: dict[str, Any] = {}
        profile = (customer_row.profile_json if customer_row else None) or {}
        ok = render_plan(
            settings,
            plan,
            temp_out,
            template.model_dump(),
            render_meta=render_meta,
            profile=profile if isinstance(profile, dict) else {},
            customer_name=str(customer),
        )
        if not ok:
            job.consecutive_failures += 1
            log_event(session, job.id, "error", "渲染失败")
            session.commit()
            return

        qc = run_qc(temp_out, plan, require_audio=bool(settings.require_audio))
        pack = get_active_pack(session, customer)
        copy = build_copywriting(plan, pack.data_json if pack else None, music_credit=render_meta.get("music_credit"))

        cover_count = int(getattr(template, "cover_count", 3) or 3)
        cover_dir = Path(str(temp_out.with_suffix("")) + "_covers")
        covers = extract_cover_candidates(temp_out, cover_dir, count=cover_count)
        cover_paths = [str(p) for p in covers]

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

        # QC fail → failed; consistency hard fail → review; else ready
        if not qc.passed:
            final_state = "failed"
        elif plan.needs_review:
            final_state = "review"
        else:
            final_state = "ready"
        final_dir = settings.paths.output_root / final_state / datetime.now().strftime("%Y-%m-%d")
        final_dir.mkdir(parents=True, exist_ok=True)
        final_out = final_dir / f"montage_{job.id}_{seed}.mp4"
        temp_out.rename(final_out)
        sidecar_new = final_out.with_suffix(".json")
        if sidecar.exists():
            sidecar.rename(sidecar_new)

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
        if final_covers and sidecar_new.exists():
            import json

            data = json.loads(sidecar_new.read_text(encoding="utf-8"))
            data["covers"] = final_covers
            data["needs_review"] = plan.needs_review
            data["consistency_score"] = plan.consistency_score
            sidecar_new.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        out_row = RenderOutput(
            job_id=job.id,
            output_path=str(final_out),
            state=final_state,
            seed=seed,
            sidecar_path=str(sidecar_new),
            qc_json={
                "passed": qc.passed,
                "reasons": qc.reasons,
                "metrics": qc.metrics,
                "covers": final_covers,
                "consistency_score": plan.consistency_score,
                "needs_review": plan.needs_review,
                "reframe_mode": getattr(template, "reframe_mode", "smart"),
            },
        )
        session.add(out_row)
        session.flush()
        record_cliplet_usage(session, plan, job_id=job.id, render_output_id=out_row.id, customer_id=job.customer_id)

        if final_state == "ready":
            job.produced_count += 1
            job.consecutive_failures = 0
            snap["consecutive_non_ready"] = 0
            job.config_snapshot_json = dict(snap)
            record_keyword_usage(session, plan.title, theme, job.id, customer_id=job.customer_id)
            log_event(session, job.id, "info", "渲染成功", {"path": str(final_out), "seed": seed})
        elif final_state == "review":
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
            job.consecutive_failures += 1
            snap["consecutive_non_ready"] = consecutive_non_ready + 1
            job.config_snapshot_json = dict(snap)
            log_event(
                session,
                job.id,
                "warning",
                "质检未通过",
                {"reasons": qc.reasons, "consecutive_non_ready": snap["consecutive_non_ready"]},
            )
            if snap["consecutive_non_ready"] >= settings.quality_circuit_threshold:
                job.status = "circuit_open"
                log_event(session, job.id, "error", "质量熔断：连续质检失败过多")

        job.updated_at = datetime.now(timezone.utc)
        session.commit()


worker = JobWorker()
