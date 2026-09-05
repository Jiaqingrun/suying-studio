from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Customer, Job, Template, log_event
from engine.template.engine import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE, TemplateDefinition


class TopicIntentRequest(BaseModel):
    mode: str = Field(pattern="^(single_product|same_category_products)$")
    cluster_ids: list[int] = Field(min_length=1, max_length=20)
    official_evidence_ids: list[int] = Field(min_length=1, max_length=20)
    similarity_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    requested_uses: list[str] = Field(default_factory=list, max_length=20)


class CreateJobRequest(BaseModel):
    mode: str = Field(default="count", pattern="^(count|duration)$")
    target_count: int | None = 10
    target_duration_sec: int | None = None
    template_name: str = "default-vertical"
    theme: str = "default"
    # Legacy: may be a rule slot OR (rarely) an asset folder — normalized below.
    category: str = "default"
    # Rule slot: default | premium | scene_tour | store_culture
    content_category: str | None = None
    # Optional Asset.category folder filter (Camera / DJI Album / …); empty = no filter
    asset_category: str | None = None
    orientation: str = Field(default="portrait", pattern="^(portrait|landscape)$")
    customer_name: str | None = None
    strict_semantic_v1: bool = False
    seed: int | None = Field(default=None, ge=1, le=2_000_000_000)
    # One-shot expression overrides (not persisted to customer profile)
    expression: dict[str, Any] | None = None
    # GVideoRules: freeze a specific approved rule, or use active if use_active_rule
    rule_profile_id: int | None = None
    use_active_rule: bool = True
    # None = inherit customer default (ON). True = pick from rotation pool.
    rule_rotation: bool | None = None
    topic_intent: TopicIntentRequest | None = None
    # When true, pause other queued jobs for this customer so this one starts next.
    rush: bool = False


def ensure_default_template(session: Session) -> None:
    """Upsert all built-in rhythm templates (default / fast-ship / stable-product)."""
    for name, tpl in BUILTIN_TEMPLATES.items():
        existing = session.scalar(select(Template).where(Template.name == name))
        if not existing:
            session.add(
                Template(
                    name=name,
                    definition_json=tpl.model_dump(),
                    active=True,
                )
            )
        else:
            existing.definition_json = tpl.model_dump()
            existing.active = True
    session.commit()


def create_job(session: Session, req: CreateJobRequest, *, customer_id: int | None = None) -> Job:
    from engine.jobs.job_categories import normalize_job_categories, snapshot_category_fields
    from engine.template.rule_schema import apply_rules_to_template, resolve_job_inputs
    from engine.template.rule_store import freeze_for_job, list_rotation_pool, pick_rule_for_job

    ensure_default_template(session)

    if not req.use_active_rule:
        raise ValueError("正式生产必须使用当前启用规则（use_active_rule 不可为 false）")
    if req.rule_profile_id is not None:
        raise ValueError("正式生产禁止指定历史规则 ID；请先在规则实验室启用目标规则")

    cats = normalize_job_categories(
        category=req.category,
        content_category=req.content_category,
        asset_category=req.asset_category,
    )
    content_category = cats["content_category"]
    asset_category = cats["asset_category"]

    rule_row = None
    picked_by = "active_slot"
    pool_size = 0
    customer_row = session.get(Customer, customer_id) if customer_id is not None else None
    if customer_id is not None:
        if customer_row is None:
            raise ValueError("客户不存在")
        rule_row, picked_by = pick_rule_for_job(
            session,
            customer=customer_row,
            content_category=content_category,
            orientation=req.orientation,
            rotation=req.rule_rotation,
        )
        pool_size = len(
            list_rotation_pool(
                session,
                customer_id=customer_id,
                orientation=req.orientation,
                content_category=content_category,
            )
        )

    from engine.catalog.keyword_pack import get_active_pack, resolve_rule_facet

    pack = get_active_pack(session, customer_row.name) if customer_row else None
    facet_meta = None
    if rule_row is not None:
        facet_meta = resolve_rule_facet(
            pack,
            rule_row.effective_json if isinstance(rule_row.effective_json, dict) else {},
        )

    frozen_rule = freeze_for_job(
        rule_row,
        picked_by=picked_by,
        rotation_pool_size=pool_size,
        facet_meta=facet_meta,
    )
    # VIDEO_LOCK 与规则实验室标题色分叉 → READY_GATE 连败熔断；创建任务时对齐
    if frozen_rule and customer_row is not None:
        from engine.pack.video_lock import sync_video_lock_into_rules

        eff0 = (
            frozen_rule.get("effective_rules")
            if isinstance(frozen_rule.get("effective_rules"), dict)
            else {}
        )
        profile0 = (
            customer_row.profile_json
            if isinstance(customer_row.profile_json, dict)
            else {}
        )
        frozen_rule = dict(frozen_rule)
        frozen_rule["effective_rules"] = sync_video_lock_into_rules(
            eff0,
            customer_name=str(customer_row.name),
            profile=profile0,
            output_root=getattr(customer_row, "output_root", None),
        )
    eff_rules = frozen_rule["effective_rules"] if frozen_rule else None
    resolved = resolve_job_inputs(
        theme=req.theme,
        category=content_category,
        template_name=req.template_name,
        strict_semantic_v1=req.strict_semantic_v1 or req.topic_intent is not None,
        rules=eff_rules,
    )
    tpl_name = resolved["template_name"]
    theme = resolved["theme"]
    # Job.category stores rule-slot content_category (never Asset folder name).
    category = content_category

    tpl = session.scalar(select(Template).where(Template.name == tpl_name))
    builtin = BUILTIN_TEMPLATES.get(tpl_name)
    template_def = TemplateDefinition(
        **(tpl.definition_json if tpl else (builtin or DEFAULT_TEMPLATE).model_dump())
    )
    if req.orientation == "landscape":
        template_def.output_width = 1920
        template_def.output_height = 1080
    else:
        template_def.output_width = 1080
        template_def.output_height = 1920
    template_def = apply_rules_to_template(template_def, resolved["effective_rules"])
    # Prefer DB customer name so paper-slip / pack lookup never sees null when
    # callers (automation / schedules) omit CreateJobRequest.customer_name.
    resolved_customer_name = (
        (customer_row.name if customer_row else None)
        or (str(req.customer_name or "").strip() or None)
    )
    snap: dict[str, Any] = {
        "customer_name": resolved_customer_name,
        "template": template_def.model_dump(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strict_semantic_v1": bool(resolved["strict_semantic_v1"]),
        "seed": req.seed,
        "orientation": req.orientation,
        "canvas": {
            "width": template_def.output_width,
            "height": template_def.output_height,
        },
        "production_rules": frozen_rule,
        "production_rules_resolved": {
            "theme": theme,
            "category": category,
            "content_category": content_category,
            "asset_category": asset_category or None,
            "template_name": tpl_name,
            "sources": resolved["sources"],
            "rejected": resolved["rejected"],
            "clamped": resolved["clamped"],
        },
        **snapshot_category_fields(cats),
    }
    if pack:
        snap["keyword_pack"] = {
            "id": pack.id,
            "revision": int(pack.revision or pack.version or 1),
            "sha256": str(pack.content_sha256 or ""),
            "schema": str(pack.schema_version or ""),
        }
    if req.topic_intent is not None:
        if customer_id is None:
            raise ValueError("专题生产必须绑定 active customer")
        from engine.catalog.semantic_ops import freeze_topic_intent

        snap["topic_intent"] = freeze_topic_intent(
            session,
            customer_id=customer_id,
            **req.topic_intent.model_dump(),
        )
    if isinstance(req.expression, dict) and req.expression:
        raise ValueError("任务级语言/字幕覆盖已禁用；请在规则实验室调整并启用规则")

    job = Job(
        status="queued",
        customer_id=customer_id,
        mode=req.mode,
        target_count=req.target_count,
        target_duration_sec=req.target_duration_sec,
        template_name=tpl_name,
        theme=theme,
        category=category,
        orientation=req.orientation,
        config_snapshot_json=snap,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    paused_for_rush = 0
    if req.rush and customer_id is not None:
        paused_for_rush = pause_queued_for_rush(
            session, customer_id=customer_id, except_job_id=job.id
        )
    log_event(
        session,
        job.id,
        "info",
        "任务已创建",
        {
            "mode": req.mode,
            "rule_profile_id": rule_row.id if rule_row else None,
            "rule_revision": rule_row.revision if rule_row else None,
            "orientation": req.orientation,
            "rush": bool(req.rush),
            "paused_for_rush": paused_for_rush,
        },
    )
    return job


def pause_queued_for_rush(
    session: Session, *, customer_id: int, except_job_id: int
) -> int:
    """Park other queued jobs so a rush job can start after the current runner."""
    rows = session.scalars(
        select(Job).where(
            Job.customer_id == customer_id,
            Job.status == "queued",
            Job.id != except_job_id,
        )
    ).all()
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    # Avoid N× log_event writes under SQLite lock while worker holds the DB
    # during TTS/ffmpeg — one commit, no per-row events.
    for job in rows:
        job.status = "paused"
        snap = dict(job.config_snapshot_json or {})
        snap["_paused_for_rush"] = {
            "by_job_id": except_job_id,
            "at": now.isoformat(),
        }
        job.config_snapshot_json = snap
        job.updated_at = now
    session.commit()
    log_event(
        session,
        except_job_id,
        "info",
        "立即生产：已暂停其他排队任务",
        {"paused_count": len(rows)},
    )
    session.commit()
    return len(rows)


def resume_jobs_paused_for_rush(session: Session, *, by_job_id: int) -> int:
    """Requeue jobs parked by ``pause_queued_for_rush`` once the rush job ends.

    Only jobs whose ``_paused_for_rush.by_job_id`` matches ``by_job_id`` are
    resumed — jobs paused for other reasons (manual pause, system pause) are
    left untouched.
    """
    rows = session.scalars(
        select(Job).where(Job.status == "paused")
    ).all()
    resumed = 0
    now = datetime.now(timezone.utc)
    for job in rows:
        snap = dict(job.config_snapshot_json or {})
        hold = snap.get("_paused_for_rush")
        if not isinstance(hold, dict) or int(hold.get("by_job_id") or 0) != int(by_job_id):
            continue
        job.status = "queued"
        snap.pop("_paused_for_rush", None)
        job.config_snapshot_json = snap
        job.updated_at = now
        resumed += 1
    if resumed:
        session.commit()
        log_event(
            session,
            by_job_id,
            "info",
            "立即生产结束：已恢复被暂停的排队任务",
            {"resumed_count": resumed},
        )
        session.commit()
    return resumed


def pause_job(session: Session, job_id: int) -> Job | None:
    job = session.get(Job, job_id)
    if not job:
        return None
    job.status = "paused"
    session.commit()
    log_event(session, job.id, "info", "任务已暂停")
    return job


def resume_job(session: Session, job_id: int) -> Job | None:
    job = session.get(Job, job_id)
    if not job:
        return None
    if job.status not in ("paused", "paused_system", "circuit_open"):
        raise ValueError(f"任务状态 {job.status} 不可恢复")
    # 僵尸成功态：已达目标且最新成片物料就绪 → 直接完成，勿再入队空转
    if (
        job.mode == "count"
        and job.target_count is not None
        and int(job.produced_count or 0) >= int(job.target_count)
        and job.status == "paused"
    ):
        from sqlalchemy import select

        from engine.catalog.db import RenderOutput

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
            snap = dict(job.config_snapshot_json or {})
            snap["_pipeline_phase"] = "completed"
            job.config_snapshot_json = snap
            job.status = "completed"
            session.commit()
            log_event(
                session,
                job.id,
                "info",
                "收尾核对：已达目标且物料就绪，标记完成",
                {"produced_count": job.produced_count, "output_id": latest.id},
            )
            return job
    job.status = "queued"
    job.consecutive_failures = 0
    # Quality/infra circuits live in config_snapshot; leaving them at threshold
    # (or a future next_attempt_at) would instant-retrip on the next worker tick.
    snap = dict(job.config_snapshot_json or {})
    changed = False
    for key in ("consecutive_non_ready", "consecutive_ollama_infra"):
        if int(snap.get(key, 0) or 0) > 0:
            snap[key] = 0
            changed = True
    if snap.pop("next_attempt_at", None) is not None:
        changed = True
    # 跟镜规划暂停重开：清写词重试计数，便于补素材后重跑
    if snap.pop("scene_tour_plan_retries", None) is not None:
        changed = True
    if snap.get("_pipeline_phase") == "blocked_plan":
        snap["_pipeline_phase"] = "producing"
        changed = True
    if changed:
        job.config_snapshot_json = snap
    session.commit()
    log_event(session, job.id, "info", "任务已恢复")
    return job
