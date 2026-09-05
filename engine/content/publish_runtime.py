"""Durable publish job state machine for article content."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import ContentArticle, ContentPublishJob, ContentVariant
from engine.content.platforms import get_platform
from engine.content.store import append_job_log, job_to_dict, record_attempt


def _now() -> datetime:
    return datetime.now(timezone.utc)


ALLOWED: dict[str, set[str]] = {
    "queued": {"running", "cancelled", "blocked"},
    "running": {"validated", "need_human", "pending_review", "published", "failed", "blocked"},
    "validated": {"running", "need_human", "published", "blocked", "cancelled"},
    "need_human": {"queued", "running", "published", "blocked", "cancelled", "failed"},
    "pending_review": {"published", "failed", "cancelled"},
    "published": set(),
    "failed": {"queued", "cancelled"},
    "cancelled": set(),
    "blocked": {"queued", "cancelled"},
}


def _validated_publish_snapshot(
    session: Session,
    *,
    customer_id: int,
    variant_id: int,
) -> tuple[ContentArticle, ContentVariant]:
    variant = session.get(ContentVariant, variant_id)
    if not variant or variant.customer_id != customer_id:
        raise ValueError("变体不存在")
    article = session.get(ContentArticle, variant.article_id)
    if not article or article.customer_id != customer_id:
        raise ValueError("文章不存在")
    if article.status != "approved":
        raise ValueError("文章未人工审核通过，禁止入队或执行发布")
    if not bool((article.compliance_json or {}).get("passed")):
        raise ValueError("主稿合规门禁未通过，禁止入队或执行发布")
    if variant.status != "ready":
        raise ValueError("平台变体已失效或合规未通过，请从当前主稿重新生成")
    payload = variant.payload_json if isinstance(variant.payload_json, dict) else {}
    if not article.content_hash or str(payload.get("master_content_hash") or "") != article.content_hash:
        raise ValueError("平台变体不是当前主稿版本，请重新生成后审核")
    variant_compliance = payload.get("compliance")
    if not isinstance(variant_compliance, dict) or not variant_compliance.get("passed"):
        raise ValueError("平台变体合规门禁未通过，禁止入队或执行发布")
    return article, variant


def make_idempotency_key(
    *,
    customer_id: int,
    variant_id: int,
    platform: str,
    content_hash: str,
) -> str:
    raw = f"{customer_id}:{variant_id}:{platform}:{content_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


def enqueue_publish(
    session: Session,
    *,
    customer_id: int,
    variant_id: int,
    profile_name: str = "",
) -> ContentPublishJob:
    article, variant = _validated_publish_snapshot(
        session, customer_id=customer_id, variant_id=variant_id
    )
    plat = get_platform(variant.platform)
    if plat.get("tier") == "human_only":
        raise ValueError(f"{plat.get('label')} 仅支持人工打开官方入口，不可自动入队")
    profile = (profile_name or "").strip()
    if profile:
        from engine.reach.browser import (
            assert_explicit_content_profile,
            resolve_chrome_user_data_dir,
            resolve_profile_platform,
        )
        from engine.reach.business_scope import SCOPE_CONTENT

        # Fail-closed: article publish may only use content-scoped Chrome dirs.
        assert_explicit_content_profile(profile, customer_id=customer_id)
        resolve_chrome_user_data_dir(
            profile, customer_id=customer_id, business_scope=SCOPE_CONTENT
        )
        bound_platform = resolve_profile_platform(
            profile,
            None,
            customer_id=customer_id,
            business_scope=SCOPE_CONTENT,
        )
        if bound_platform != variant.platform:
            raise ValueError(
                f"软文账号绑定平台 {bound_platform} 与文章变体平台 {variant.platform} 不匹配"
            )
    elif variant.platform not in {"website", "cms", "baidu_ziyuan", "so_360"}:
        raise ValueError("该文章平台须选择显式创建的软文账号")
    key = make_idempotency_key(
        customer_id=customer_id,
        variant_id=variant.id,
        platform=variant.platform,
        content_hash=article.content_hash or str(article.id),
    )
    existing = session.scalar(
        select(ContentPublishJob).where(
            ContentPublishJob.customer_id == customer_id,
            ContentPublishJob.idempotency_key == key,
        )
    )
    if existing and existing.status not in ("cancelled", "failed"):
        return existing
    job = ContentPublishJob(
        customer_id=customer_id,
        article_id=article.id,
        variant_id=variant.id,
        platform=variant.platform,
        profile_name=(profile_name or "").strip(),
        business_scope="content",
        status="queued",
        idempotency_key=key,
        adapter_version=str(plat.get("adapter_version") or "1"),
        attempt_count=0,
        log_json=[],
        created_at=_now(),
        updated_at=_now(),
    )
    append_job_log(job, "enqueued", platform=variant.platform)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def set_job_status(
    session: Session,
    job: ContentPublishJob,
    new_status: str,
    *,
    error: str | None = None,
    published_url: str | None = None,
    review_id: str | None = None,
    human_resume_url: str | None = None,
) -> ContentPublishJob:
    new_status = (new_status or "").strip().lower()
    if new_status not in ALLOWED:
        raise ValueError(f"非法状态: {new_status}")
    if new_status != job.status and new_status not in ALLOWED.get(job.status, set()):
        raise ValueError(f"状态不可从 {job.status} → {new_status}")
    if new_status == "published":
        final_url = (published_url if published_url is not None else job.published_url or "").strip()
        final_review = (review_id if review_id is not None else job.review_id or "").strip()
        if not final_url and not final_review:
            raise ValueError("缺少可核验发布 URL 或平台审核号，禁止标记 published")
    old = job.status
    job.status = new_status
    job.updated_at = _now()
    if error is not None:
        job.error = error
    if published_url is not None:
        job.published_url = published_url
    if review_id is not None:
        job.review_id = review_id
    if human_resume_url is not None:
        job.human_resume_url = human_resume_url
    append_job_log(job, "status", from_status=old, to_status=new_status)
    session.commit()
    session.refresh(job)
    return job


def run_publish_job(
    session: Session,
    job: ContentPublishJob,
    *,
    dry_run: bool = True,
    resume: bool = False,
) -> dict[str, Any]:
    """Execute one attempt. Default dry_run validates payload without browser."""
    from engine.reach.article_publishers import publisher_for

    if job.status in ("published", "cancelled"):
        return {"ok": True, "job": job_to_dict(job), "skipped": True}
    try:
        _, variant = _validated_publish_snapshot(
            session,
            customer_id=job.customer_id,
            variant_id=job.variant_id,
        )
    except ValueError as exc:
        set_job_status(session, job, "blocked", error=str(exc))
        return {"ok": False, "blocked": True, "job": job_to_dict(job)}
    if job.status == "need_human" and not resume:
        return {
            "ok": False,
            "need_human": True,
            "job": job_to_dict(job),
            "message": "等待人工验证后点击继续",
        }
    if not dry_run:
        # Live mode is deliberately human-in-the-loop.  This endpoint never
        # clicks the final publish action; verified URL/review evidence must be
        # recorded separately before a job can become published.
        if job.status == "validated":
            set_job_status(session, job, "running")
        elif job.status != "running":
            set_job_status(session, job, "running")
        set_job_status(
            session,
            job,
            "need_human",
            error="Live 模式不会自动发布；请在官方页面人工发布并回填 URL 或审核号",
        )
        return {
            "ok": True,
            "need_human": True,
            "live": True,
            "auto_publish": False,
            "job": job_to_dict(job),
        }

    set_job_status(session, job, "running")
    job.attempt_count = int(job.attempt_count or 0) + 1
    session.commit()

    publisher = publisher_for(job.platform)
    try:
        result = publisher.publish(
            title=variant.title,
            body_md=variant.body_md,
            payload=variant.payload_json or {},
            profile_name=job.profile_name,
            dry_run=dry_run,
            resume=resume,
        )
    except Exception as exc:  # noqa: BLE001
        record_attempt(
            session,
            customer_id=job.customer_id,
            job=job,
            phase="publish",
            outcome="error",
            detail=str(exc),
        )
        session.commit()
        set_job_status(session, job, "failed", error=str(exc)[:500])
        return {"ok": False, "job": job_to_dict(job), "error": str(exc)}

    outcome = str(result.get("outcome") or "failed")
    record_attempt(
        session,
        customer_id=job.customer_id,
        job=job,
        phase=str(result.get("phase") or "publish"),
        outcome=outcome,
        detail=str(result.get("detail") or ""),
        evidence=result.get("evidence") if isinstance(result.get("evidence"), dict) else {},
    )
    session.commit()

    if outcome == "need_human":
        set_job_status(
            session,
            job,
            "need_human",
            error=str(result.get("detail") or "需要人工登录或验证"),
            human_resume_url=str(result.get("resume_url") or ""),
        )
        return {"ok": False, "need_human": True, "job": job_to_dict(job), "result": result}
    if outcome == "pending_review":
        set_job_status(
            session,
            job,
            "pending_review",
            review_id=str(result.get("review_id") or ""),
        )
        return {"ok": True, "pending_review": True, "job": job_to_dict(job), "result": result}
    if outcome == "published" and dry_run:
        set_job_status(session, job, "validated")
        return {
            "ok": True,
            "validated": True,
            "dry_run": True,
            "job": job_to_dict(job),
            "result": result,
        }
    if outcome == "published":
        url = str(result.get("published_url") or "").strip()
        review_id = str(result.get("review_id") or "").strip()
        if not url and not review_id:
            set_job_status(session, job, "failed", error="未拿到已发布 URL 或审核号，拒绝记成功")
            return {"ok": False, "job": job_to_dict(job), "result": result}
        set_job_status(
            session,
            job,
            "published",
            published_url=url,
            review_id=review_id,
        )
        return {"ok": True, "job": job_to_dict(job), "result": result}

    set_job_status(session, job, "failed", error=str(result.get("detail") or "发布失败"))
    return {"ok": False, "job": job_to_dict(job), "result": result}
