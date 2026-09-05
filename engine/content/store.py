"""CRUD helpers and serializers for GContent tables."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import (
    ContentArticle,
    ContentCampaign,
    ContentInteraction,
    ContentPublishAttempt,
    ContentPublishJob,
    ContentSource,
    ContentVariant,
    ManagedSite,
    ReplyDraft,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def content_hash(title: str, body: str) -> str:
    raw = f"{title.strip()}\n{body.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def source_to_dict(row: ContentSource) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "source_url": row.source_url,
        "evidence_hash": row.evidence_hash,
        "verified": row.verified,
        "verified_by": row.verified_by,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "meta": row.meta_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def site_to_dict(row: ManagedSite) -> dict[str, Any]:
    secret_ref = (row.secret_ref or "").strip()
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "domain": row.domain,
        "role": row.role,
        "cms_type": row.cms_type,
        "publish_url": row.publish_url,
        "canonical_group": row.canonical_group,
        "sitemap_url": row.sitemap_url,
        "robots_url": row.robots_url,
        "search_verify": row.search_verify_json or {},
        # A secret reference is write-only.  Returning the pointer itself can
        # leak keychain account names or local secret paths to logs/UI.
        "secret_ref_configured": bool(secret_ref),
        "enabled": row.enabled,
        "meta": row.meta_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def campaign_to_dict(row: ContentCampaign) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "name": row.name,
        "audience": row.audience,
        "keywords": row.keywords_json or [],
        "questions": row.questions_json or [],
        "regions": row.regions_json or [],
        "goal": row.goal,
        "tone": row.tone,
        "author_name": row.author_name,
        "weekly_quota": row.weekly_quota,
        "enabled": row.enabled,
        "meta": row.meta_json or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def article_to_dict(row: ContentArticle) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "campaign_id": row.campaign_id,
        "status": row.status,
        "title": row.title,
        "summary": row.summary,
        "body_md": row.body_md,
        "faq": row.faq_json or [],
        "fact_ids": row.fact_ids_json or [],
        "citations": row.citations_json or [],
        "ai_labeled": row.ai_labeled,
        "compliance": row.compliance_json or {},
        "version": row.version,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "approved_by": row.approved_by,
        "content_hash": row.content_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def variant_to_dict(row: ContentVariant) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "article_id": row.article_id,
        "platform": row.platform,
        "site_id": row.site_id,
        "title": row.title,
        "body_md": row.body_md,
        "payload": row.payload_json or {},
        "similarity": row.similarity,
        "assets": row.assets_json or [],
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def job_to_dict(row: ContentPublishJob) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "article_id": row.article_id,
        "variant_id": row.variant_id,
        "platform": row.platform,
        "profile_name": row.profile_name,
        "business_scope": getattr(row, "business_scope", None) or "content",
        "status": row.status,
        "idempotency_key": row.idempotency_key,
        "published_url": row.published_url,
        "review_id": row.review_id,
        "error": row.error,
        "human_resume_url": row.human_resume_url,
        "adapter_version": row.adapter_version,
        "attempt_count": row.attempt_count,
        "log": row.log_json or [],
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def reply_draft_to_dict(row: ReplyDraft) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "business_scope": getattr(row, "business_scope", None) or "content",
        "message_id": row.message_id,
        "interaction_id": row.interaction_id,
        "body": row.body,
        "based_on_summary_only": row.based_on_summary_only,
        "context_note": row.context_note,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def profile_completeness(
    session: Session,
    *,
    customer_id: int,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hard gate checklist before publish is allowed."""
    sources = list(
        session.scalars(
            select(ContentSource).where(
                ContentSource.customer_id == customer_id,
                ContentSource.verified.is_(True),
            )
        ).all()
    )
    sites = list(
        session.scalars(
            select(ManagedSite).where(
                ManagedSite.customer_id == customer_id,
                ManagedSite.enabled.is_(True),
            )
        ).all()
    )
    brand = (profile or {}).get("brand") or {}
    display_name = str(brand.get("display_name") or "").strip()
    checks = {
        "brand_name": bool(display_name),
        "verified_facts": len(sources) >= 3,
        "managed_site": len(sites) >= 1,
        "ai_policy": True,  # default on; toggle later in profile
    }
    missing: list[str] = []
    if not checks["brand_name"]:
        missing.append("品牌显示名（设置→品牌，或软文页顶部）")
    if not checks["verified_facts"]:
        missing.append("至少 3 条已确认事实")
    if not checks["managed_site"]:
        missing.append("至少一个已启用官网/域名")
    score = int(100 * sum(1 for v in checks.values() if v) / max(1, len(checks)))
    return {
        "score": score,
        "checks": checks,
        "missing": missing,
        "can_publish": len(missing) == 0,
        "verified_fact_count": len(sources),
        "site_count": len(sites),
    }


def append_job_log(job: ContentPublishJob, event: str, **payload: Any) -> None:
    logs = list(job.log_json or [])
    logs.append({"at": _now().isoformat(), "event": event, **payload})
    job.log_json = logs
    job.updated_at = _now()


def record_attempt(
    session: Session,
    *,
    customer_id: int,
    job: ContentPublishJob,
    phase: str,
    outcome: str,
    detail: str = "",
    evidence: dict[str, Any] | None = None,
) -> ContentPublishAttempt:
    row = ContentPublishAttempt(
        customer_id=customer_id,
        job_id=job.id,
        phase=phase,
        outcome=outcome,
        detail=(detail or "")[:2000],
        evidence_json=evidence or {},
        created_at=_now(),
    )
    session.add(row)
    append_job_log(job, "attempt", phase=phase, outcome=outcome, detail=detail[:300])
    return row
