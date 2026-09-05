"""GContent API routes — SEO/GEO articles, sites, publish jobs, reply drafts."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from engine.catalog.db import (
    ContentArticle,
    ContentCampaign,
    ContentPublishJob,
    ContentSource,
    ContentVariant,
    ManagedSite,
    ReplyDraft,
    get_session,
)
from engine.catalog.customer_scope import require_active_customer, settings_with_customer_paths
from engine.config.settings import load_settings
from engine.content.compliance import check_article_compliance
from engine.content.generate import generate_article_bundle
from engine.content.platforms import list_platforms
from engine.content.publish_runtime import enqueue_publish, run_publish_job, set_job_status
from engine.content.reply_drafts import create_reply_drafts_for_message
from engine.content.store import (
    article_to_dict,
    campaign_to_dict,
    job_to_dict,
    profile_completeness,
    reply_draft_to_dict,
    site_to_dict,
    source_to_dict,
    variant_to_dict,
)

router = APIRouter(prefix="/content", tags=["content"])


def _scope():
    settings = load_settings()
    session = get_session()
    customer = require_active_customer(session, settings)
    scoped = settings_with_customer_paths(settings, customer)
    return settings, session, customer, scoped


class SourceIn(BaseModel):
    kind: str = "fact"
    title: str = ""
    body: str = ""
    source_url: str = ""
    verified: bool = False
    verified_by: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class SiteIn(BaseModel):
    domain: str
    role: str = "primary"
    cms_type: str = "generic"
    publish_url: str = ""
    canonical_group: str = "main"
    sitemap_url: str = ""
    robots_url: str = ""
    secret_ref: str = ""
    enabled: bool = True
    search_verify: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


_SECRET_REF = re.compile(r"^(?:keychain|secret|env)://[A-Za-z0-9][A-Za-z0-9._/@:-]{0,240}$")


class CampaignIn(BaseModel):
    name: str
    audience: str = ""
    keywords: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    goal: str = "brand_authority"
    tone: str = "professional"
    author_name: str = ""
    weekly_quota: int = 3
    enabled: bool = True


class GenerateIn(BaseModel):
    topic: str
    campaign_id: int | None = None
    fact_ids: list[int] | None = None
    platforms: list[str] | None = None
    author: str = ""


class ArticlePatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    body_md: str | None = None
    faq: list[Any] | None = None


class ApproveIn(BaseModel):
    approved_by: str = "operator"


class EnqueueIn(BaseModel):
    variant_id: int
    profile_name: str = ""


class RunJobIn(BaseModel):
    dry_run: bool = True
    resume: bool = False


class ReplyDraftIn(BaseModel):
    message_id: int
    extra_context: str = ""
    brand: str = ""


@router.get("/platforms")
def content_platforms(include_experimental: bool = True) -> dict[str, Any]:
    return {"ok": True, "platforms": list_platforms(include_experimental=include_experimental)}


@router.get("/completeness")
def content_completeness() -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        return {"ok": True, **profile_completeness(session, customer_id=customer.id, profile=profile)}
    finally:
        session.close()


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        rows = session.scalars(
            select(ContentSource)
            .where(ContentSource.customer_id == customer.id)
            .order_by(ContentSource.id.desc())
        ).all()
        return {"ok": True, "sources": [source_to_dict(r) for r in rows]}
    finally:
        session.close()


@router.post("/sources")
def create_source(body: SourceIn) -> dict[str, Any]:
    import hashlib

    _, session, customer, _ = _scope()
    try:
        evidence = hashlib.sha256(f"{body.title}\n{body.body}\n{body.source_url}".encode()).hexdigest()[:40]
        row = ContentSource(
            customer_id=customer.id,
            kind=body.kind[:32],
            title=body.title[:256],
            body=body.body,
            source_url=body.source_url,
            evidence_hash=evidence,
            verified=body.verified,
            verified_by=body.verified_by[:128],
            meta_json=body.meta,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"ok": True, "source": source_to_dict(row)}
    finally:
        session.close()


@router.post("/sources/{source_id}/verify")
def verify_source(source_id: int, verified_by: str = "operator") -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = session.scalar(
            select(ContentSource).where(
                ContentSource.id == source_id,
                ContentSource.customer_id == customer.id,
            )
        )
        if not row:
            raise HTTPException(404, "事实不存在")
        row.verified = True
        row.verified_by = verified_by[:128]
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return {"ok": True, "source": source_to_dict(row)}
    finally:
        session.close()


@router.get("/sites")
def list_sites() -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        rows = session.scalars(
            select(ManagedSite).where(ManagedSite.customer_id == customer.id).order_by(ManagedSite.id)
        ).all()
        return {"ok": True, "sites": [site_to_dict(r) for r in rows]}
    finally:
        session.close()


@router.post("/sites")
def create_site(body: SiteIn) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        domain = body.domain.strip().lower()
        if not domain:
            raise HTTPException(400, "域名不能为空")
        secret_ref = body.secret_ref.strip()
        if secret_ref and not _SECRET_REF.fullmatch(secret_ref):
            raise HTTPException(
                400,
                "secret_ref 只接受 keychain://、secret:// 或 env:// 引用，禁止提交明文密钥",
            )
        row = ManagedSite(
            customer_id=customer.id,
            domain=domain[:255],
            role=body.role[:32],
            cms_type=body.cms_type[:64],
            publish_url=body.publish_url,
            canonical_group=body.canonical_group[:64],
            sitemap_url=body.sitemap_url,
            robots_url=body.robots_url,
            secret_ref=secret_ref,
            enabled=body.enabled,
            search_verify_json=body.search_verify,
            meta_json=body.meta,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"ok": True, "site": site_to_dict(row)}
    finally:
        session.close()


@router.get("/campaigns")
def list_campaigns() -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        rows = session.scalars(
            select(ContentCampaign)
            .where(ContentCampaign.customer_id == customer.id)
            .order_by(ContentCampaign.id.desc())
        ).all()
        return {"ok": True, "campaigns": [campaign_to_dict(r) for r in rows]}
    finally:
        session.close()


@router.post("/campaigns")
def create_campaign(body: CampaignIn) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = ContentCampaign(
            customer_id=customer.id,
            name=body.name[:128],
            audience=body.audience,
            keywords_json=body.keywords,
            questions_json=body.questions,
            regions_json=body.regions,
            goal=body.goal[:64],
            tone=body.tone[:64],
            author_name=body.author_name[:128],
            weekly_quota=max(0, min(body.weekly_quota, 50)),
            enabled=body.enabled,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"ok": True, "campaign": campaign_to_dict(row)}
    finally:
        session.close()


@router.get("/articles")
def list_articles(limit: int = 50) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        rows = session.scalars(
            select(ContentArticle)
            .where(ContentArticle.customer_id == customer.id)
            .order_by(ContentArticle.id.desc())
            .limit(max(1, min(limit, 200)))
        ).all()
        return {"ok": True, "articles": [article_to_dict(r) for r in rows]}
    finally:
        session.close()


@router.post("/articles/generate")
def generate_article(body: GenerateIn) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        brand = str((profile.get("brand") or {}).get("display_name") or customer.name or "品牌")
        banned = list((profile.get("compliance") or {}).get("banned_terms") or [])
        try:
            result = generate_article_bundle(
                session,
                customer_id=customer.id,
                brand=brand,
                topic=body.topic,
                campaign_id=body.campaign_id,
                fact_ids=body.fact_ids,
                platforms=body.platforms,
                banned_terms=banned,
                author=body.author,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "ok": True,
            "article": article_to_dict(result["article"]),
            "variants": [variant_to_dict(v) for v in result["variants"]],
        }
    finally:
        session.close()


@router.get("/articles/{article_id}")
def get_article(article_id: int) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = session.scalar(
            select(ContentArticle).where(
                ContentArticle.id == article_id,
                ContentArticle.customer_id == customer.id,
            )
        )
        if not row:
            raise HTTPException(404, "文章不存在")
        variants = session.scalars(
            select(ContentVariant).where(
                ContentVariant.article_id == row.id,
                ContentVariant.customer_id == customer.id,
            )
        ).all()
        return {
            "ok": True,
            "article": article_to_dict(row),
            "variants": [variant_to_dict(v) for v in variants],
        }
    finally:
        session.close()


@router.patch("/articles/{article_id}")
def patch_article(article_id: int, body: ArticlePatch) -> dict[str, Any]:
    from engine.content.store import content_hash

    _, session, customer, _ = _scope()
    try:
        row = session.scalar(
            select(ContentArticle).where(
                ContentArticle.id == article_id,
                ContentArticle.customer_id == customer.id,
            )
        )
        if not row:
            raise HTTPException(404, "文章不存在")
        if body.title is not None:
            row.title = body.title
        if body.summary is not None:
            row.summary = body.summary
        if body.body_md is not None:
            row.body_md = body.body_md
        if body.faq is not None:
            row.faq_json = body.faq
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        banned = list((profile.get("compliance") or {}).get("banned_terms") or [])
        row.compliance_json = check_article_compliance(
            title=row.title,
            body_md=row.body_md,
            fact_ids=list(row.fact_ids_json or []),
            citations=list(row.citations_json or []),
            banned_terms=banned,
            ai_labeled=bool(row.ai_labeled),
        )
        row.content_hash = content_hash(row.title, row.body_md)
        row.version = int(row.version or 1) + 1
        row.status = "needs_review"
        row.approved_at = None
        row.approved_by = ""
        row.updated_at = datetime.now(timezone.utc)
        variants = session.scalars(
            select(ContentVariant).where(
                ContentVariant.article_id == row.id,
                ContentVariant.customer_id == customer.id,
            )
        ).all()
        for variant in variants:
            variant.status = "stale"
            payload = dict(variant.payload_json or {})
            payload["invalidated_reason"] = "master_content_changed"
            payload["invalidated_at"] = row.updated_at.isoformat()
            variant.payload_json = payload
            variant.updated_at = row.updated_at
        session.commit()
        session.refresh(row)
        return {"ok": True, "article": article_to_dict(row)}
    finally:
        session.close()


@router.post("/articles/{article_id}/approve")
def approve_article(article_id: int, body: ApproveIn) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        row = session.scalar(
            select(ContentArticle).where(
                ContentArticle.id == article_id,
                ContentArticle.customer_id == customer.id,
            )
        )
        if not row:
            raise HTTPException(404, "文章不存在")
        compliance = row.compliance_json or {}
        if not compliance.get("passed"):
            raise HTTPException(400, "合规未通过，禁止审核通过")
        fact_ids = [int(x) for x in (row.fact_ids_json or []) if str(x).isdigit()]
        verified_ids = set(
            session.scalars(
                select(ContentSource.id).where(
                    ContentSource.customer_id == customer.id,
                    ContentSource.id.in_(fact_ids or [-1]),
                    ContentSource.verified.is_(True),
                )
            ).all()
        )
        if not fact_ids or verified_ids != set(fact_ids):
            raise HTTPException(400, "主稿引用的事实已缺失或未确认，禁止审核通过")
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        gate = profile_completeness(session, customer_id=customer.id, profile=profile)
        if not gate.get("can_publish"):
            raise HTTPException(400, f"资料不完整：{', '.join(gate.get('missing') or [])}")
        row.status = "approved"
        row.approved_at = datetime.now(timezone.utc)
        row.approved_by = body.approved_by[:128]
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return {"ok": True, "article": article_to_dict(row)}
    finally:
        session.close()


@router.get("/jobs")
def list_jobs(limit: int = 50) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        rows = session.scalars(
            select(ContentPublishJob)
            .where(ContentPublishJob.customer_id == customer.id)
            .order_by(ContentPublishJob.id.desc())
            .limit(max(1, min(limit, 200)))
        ).all()
        return {"ok": True, "jobs": [job_to_dict(r) for r in rows]}
    finally:
        session.close()


@router.post("/jobs")
def create_job(body: EnqueueIn) -> dict[str, Any]:
    from engine.runtime.pause_coordinator import assert_runtime_active

    assert_runtime_active("content_publish_enqueue")
    _, session, customer, _ = _scope()
    try:
        try:
            job = enqueue_publish(
                session,
                customer_id=customer.id,
                variant_id=body.variant_id,
                profile_name=body.profile_name,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "job": job_to_dict(job)}
    finally:
        session.close()


@router.post("/jobs/{job_id}/run")
def run_job(job_id: int, body: RunJobIn) -> dict[str, Any]:
    from engine.runtime.pause_coordinator import assert_runtime_active
    from engine.reach.browser import list_chrome_profiles
    from engine.reach.business_scope import SCOPE_CONTENT

    assert_runtime_active("content_publish")
    _, session, customer, _ = _scope()
    try:
        job = session.scalar(
            select(ContentPublishJob).where(
                ContentPublishJob.id == job_id,
                ContentPublishJob.customer_id == customer.id,
            )
        )
        if not job:
            raise HTTPException(404, "发布任务不存在")
        if not body.dry_run:
            profiles = list_chrome_profiles(
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
            ).get("profiles") or []
            selected = next(
                (profile for profile in profiles if profile.get("name") == job.profile_name),
                None,
            )
            if not selected or selected.get("login_status") != "verified_logged_in":
                raise HTTPException(409, "软文发布账号须由当前受管 Chrome 实时确认已登录")
        result = run_publish_job(session, job, dry_run=body.dry_run, resume=body.resume)
        return {"ok": bool(result.get("ok")), **result}
    finally:
        session.close()


@router.post("/jobs/{job_id}/status")
def job_status(
    job_id: int,
    status: str,
    error: str = "",
    published_url: str = "",
    review_id: str = "",
) -> dict[str, Any]:
    _, session, customer, _ = _scope()
    try:
        job = session.scalar(
            select(ContentPublishJob).where(
                ContentPublishJob.id == job_id,
                ContentPublishJob.customer_id == customer.id,
            )
        )
        if not job:
            raise HTTPException(404, "发布任务不存在")
        try:
            job = set_job_status(
                session,
                job,
                status,
                error=error or None,
                published_url=published_url or None,
                review_id=review_id or None,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "job": job_to_dict(job)}
    finally:
        session.close()


@router.post("/reply-drafts")
def create_reply_drafts(body: ReplyDraftIn) -> dict[str, Any]:
    """Content-scope reply drafts only; never auto-send."""
    from engine.reach.business_scope import SCOPE_CONTENT

    _, session, customer, _ = _scope()
    try:
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        brand = body.brand or str((profile.get("brand") or {}).get("display_name") or customer.name)
        try:
            drafts = create_reply_drafts_for_message(
                session,
                customer_id=customer.id,
                message_id=body.message_id,
                brand=brand,
                extra_context=body.extra_context,
                business_scope=SCOPE_CONTENT,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "ok": True,
            "drafts": drafts,
            "business_scope": SCOPE_CONTENT,
            "warning": "基于脱敏摘要生成，上下文可能不完整；不会自动发送",
        }
    finally:
        session.close()


@router.get("/reply-drafts")
def list_reply_drafts(message_id: int | None = None, limit: int = 50) -> dict[str, Any]:
    from engine.reach.business_scope import SCOPE_CONTENT

    _, session, customer, _ = _scope()
    try:
        q = select(ReplyDraft).where(
            ReplyDraft.customer_id == customer.id,
            ReplyDraft.business_scope == SCOPE_CONTENT,
        )
        if message_id is not None:
            q = q.where(ReplyDraft.message_id == message_id)
        rows = session.scalars(q.order_by(ReplyDraft.id.desc()).limit(max(1, min(limit, 200)))).all()
        return {"ok": True, "drafts": [reply_draft_to_dict(r) for r in rows], "business_scope": SCOPE_CONTENT}
    finally:
        session.close()


@router.post("/reply-drafts/{draft_id}/copied")
def mark_reply_copied(draft_id: int) -> dict[str, Any]:
    from engine.reach.business_scope import SCOPE_CONTENT

    _, session, customer, _ = _scope()
    try:
        row = session.scalar(
            select(ReplyDraft).where(
                ReplyDraft.id == draft_id,
                ReplyDraft.customer_id == customer.id,
                ReplyDraft.business_scope == SCOPE_CONTENT,
            )
        )
        if not row:
            raise HTTPException(404, "回复草稿不存在")
        row.status = "copied"
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return {"ok": True, "draft": reply_draft_to_dict(row)}
    finally:
        session.close()


# --- Content-scoped Chrome + messages (isolated from video Reach) ---


class ContentChromeCreate(BaseModel):
    platform: str
    count: int = 1
    name_prefix: str | None = None
    custom_platform_name: str | None = None
    custom_platform_url: str | None = None


class ContentChromeOpen(BaseModel):
    name: str
    platform: str | None = None
    dry_run: bool = False


class ContentChromeSelect(BaseModel):
    name: str
    platform: str | None = None


class ContentChromeConfirm(BaseModel):
    name: str
    platform: str


class ContentChromeRename(BaseModel):
    old_name: str
    new_name: str


class ContentChromeUpdate(BaseModel):
    name: str
    new_name: str | None = None
    platform: str | None = None
    custom_platform_name: str | None = None
    custom_platform_url: str | None = None


class ContentChromeDelete(BaseModel):
    names: list[str]


class ContentMessageAccountCreate(BaseModel):
    platform: str
    profile_name: str = ""
    display_name: str = ""
    enabled: bool = True
    message_url: str = ""


class ContentMessageAccountPatch(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None
    message_url: str | None = None


class ContentMessageScanIn(BaseModel):
    account_id: int | None = None
    dry_run: bool = False


class ContentMessageOpenIn(BaseModel):
    dry_run: bool = False


class ContentMessagesBulkReadIn(BaseModel):
    ids: list[int] = Field(default_factory=list)
    account_id: int | None = None
    platform: str | None = None
    kind: str | None = None


@router.get("/chrome-profiles")
def content_chrome_profiles() -> dict[str, Any]:
    from engine.reach.browser import list_chrome_profiles, selected_chrome_profile_from_customer
    from engine.reach.business_scope import SCOPE_CONTENT

    _, session, customer, _ = _scope()
    try:
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        listed = list_chrome_profiles(customer_id=customer.id, business_scope=SCOPE_CONTENT)
        selected, selected_platform = selected_chrome_profile_from_customer(
            profile, business_scope=SCOPE_CONTENT
        )
        return {**listed, "selected": selected, "selected_platform": selected_platform}
    finally:
        session.close()


@router.post("/chrome-profiles/create")
def content_chrome_create(body: ContentChromeCreate) -> dict[str, Any]:
    from engine.reach.browser import create_chrome_profiles, set_selected_chrome_profile
    from engine.reach.business_scope import SCOPE_CONTENT
    from sqlalchemy.orm.attributes import flag_modified

    _, session, customer, _ = _scope()
    try:
        try:
            result = create_chrome_profiles(
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
                platform=body.platform,
                count=int(body.count or 1),
                name_prefix=body.name_prefix,
                custom_platform_name=body.custom_platform_name,
                custom_platform_url=body.custom_platform_url,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        selected = result.get("selected")
        if selected:
            profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
            customer.profile_json = set_selected_chrome_profile(
                profile,
                name=str(selected),
                platform=str(result.get("platform") or body.platform),
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
            )
            flag_modified(customer, "profile_json")
            session.commit()
        return result
    finally:
        session.close()


@router.post("/chrome-profiles/open")
def content_chrome_open(body: ContentChromeOpen) -> dict[str, Any]:
    from engine.reach.browser import entry_for_platform, open_chrome_profile, resolve_profile_platform
    from engine.reach.business_scope import SCOPE_CONTENT

    _, session, customer, _ = _scope()
    try:
        try:
            plat = resolve_profile_platform(
                body.name,
                body.platform,
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
            )
            entry = entry_for_platform(
                plat, customer_id=customer.id, business_scope=SCOPE_CONTENT
            )
            info = open_chrome_profile(
                body.name,
                url=entry["url"],
                dry_run=bool(body.dry_run),
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "ok": True,
            "platform": plat,
            "label": entry["label"],
            "open": info,
            "business_scope": SCOPE_CONTENT,
            "auto_publish": False,
            "human_in_loop": True,
        }
    finally:
        session.close()


@router.post("/chrome-profiles/select")
def content_chrome_select(body: ContentChromeSelect) -> dict[str, Any]:
    from engine.reach.browser import list_chrome_profiles, resolve_profile_platform, set_selected_chrome_profile
    from engine.reach.business_scope import SCOPE_CONTENT
    from sqlalchemy.orm.attributes import flag_modified

    _, session, customer, _ = _scope()
    try:
        profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
        try:
            plat = resolve_profile_platform(
                body.name,
                body.platform,
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
            )
            updated = set_selected_chrome_profile(
                profile,
                name=body.name,
                platform=plat,
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        customer.profile_json = updated
        flag_modified(customer, "profile_json")
        session.commit()
        listed = list_chrome_profiles(customer_id=customer.id, business_scope=SCOPE_CONTENT)
        return {
            "ok": True,
            "selected": body.name,
            "platform": plat,
            "business_scope": SCOPE_CONTENT,
            "profiles": listed.get("profiles"),
            "root": listed.get("root"),
        }
    finally:
        session.close()


@router.post("/chrome-profiles/confirm-legacy")
def content_chrome_confirm_legacy(body: ContentChromeConfirm) -> dict[str, Any]:
    from engine.reach.browser import confirm_content_profile

    _, session, customer, _ = _scope()
    try:
        try:
            result = confirm_content_profile(
                body.name, platform=body.platform, customer_id=customer.id
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return result
    finally:
        session.close()


@router.post("/chrome-profiles/rename")
def content_chrome_rename(body: ContentChromeRename) -> dict[str, Any]:
    from engine.catalog.db import ReachMessageAccount
    from engine.reach.browser import (
        rename_chrome_profile,
        replace_chrome_profile_name,
    )
    from engine.reach.business_scope import SCOPE_CONTENT
    from sqlalchemy.orm.attributes import flag_modified

    _, session, customer, _ = _scope()
    renamed = False
    try:
        try:
            result = rename_chrome_profile(
                body.old_name,
                body.new_name,
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
            )
            renamed = True
            profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
            customer.profile_json = replace_chrome_profile_name(
                profile,
                old_name=body.old_name,
                new_name=body.new_name,
                business_scope=SCOPE_CONTENT,
            )
            flag_modified(customer, "profile_json")
            accounts = session.scalars(
                select(ReachMessageAccount).where(
                    ReachMessageAccount.customer_id == customer.id,
                    ReachMessageAccount.business_scope == SCOPE_CONTENT,
                    ReachMessageAccount.profile_name == body.old_name,
                )
            ).all()
            for account in accounts:
                account.profile_name = body.new_name
                if account.display_name == body.old_name:
                    account.display_name = body.new_name
            jobs = session.scalars(
                select(ContentPublishJob).where(
                    ContentPublishJob.customer_id == customer.id,
                    ContentPublishJob.business_scope == SCOPE_CONTENT,
                    ContentPublishJob.profile_name == body.old_name,
                    ContentPublishJob.status.in_(
                        ("queued", "running", "need_human", "pending_review", "blocked")
                    ),
                )
            ).all()
            for job in jobs:
                job.profile_name = body.new_name
            session.commit()
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception:
            session.rollback()
            if renamed:
                try:
                    rename_chrome_profile(
                        body.new_name,
                        body.old_name,
                        customer_id=customer.id,
                        business_scope=SCOPE_CONTENT,
                    )
                except Exception:
                    pass
            raise
    finally:
        session.close()


@router.patch("/chrome-profiles")
def content_chrome_update(body: ContentChromeUpdate) -> dict[str, Any]:
    """Edit a soft-article account name and official platform entry."""
    from engine.catalog.db import ReachMessageAccount
    from engine.reach.browser import (
        rename_chrome_profile,
        replace_chrome_profile_name,
        set_selected_chrome_profile,
        update_chrome_profile_account,
    )
    from engine.reach.business_scope import SCOPE_CONTENT
    from sqlalchemy.orm.attributes import flag_modified

    _, session, customer, _ = _scope()
    old_name = body.name
    current_name = old_name
    renamed = False
    try:
        try:
            if body.new_name and body.new_name.strip() != old_name:
                result = rename_chrome_profile(
                    old_name,
                    body.new_name.strip(),
                    customer_id=customer.id,
                    business_scope=SCOPE_CONTENT,
                )
                current_name = result["name"]
                renamed = True
                profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
                customer.profile_json = replace_chrome_profile_name(
                    profile,
                    old_name=old_name,
                    new_name=current_name,
                    business_scope=SCOPE_CONTENT,
                )
                flag_modified(customer, "profile_json")
                for account in session.scalars(
                    select(ReachMessageAccount).where(
                        ReachMessageAccount.customer_id == customer.id,
                        ReachMessageAccount.business_scope == SCOPE_CONTENT,
                        ReachMessageAccount.profile_name == old_name,
                    )
                ).all():
                    account.profile_name = current_name
                    if account.display_name == old_name:
                        account.display_name = current_name
                for job in session.scalars(
                    select(ContentPublishJob).where(
                        ContentPublishJob.customer_id == customer.id,
                        ContentPublishJob.business_scope == SCOPE_CONTENT,
                        ContentPublishJob.profile_name == old_name,
                        ContentPublishJob.status.in_(
                            ("queued", "running", "need_human", "pending_review", "blocked")
                        ),
                    )
                ).all():
                    job.profile_name = current_name

            platform_result: dict[str, Any] | None = None
            if body.platform is not None or body.custom_platform_name or body.custom_platform_url:
                active_job = session.scalar(
                    select(ContentPublishJob.id).where(
                        ContentPublishJob.customer_id == customer.id,
                        ContentPublishJob.business_scope == SCOPE_CONTENT,
                        ContentPublishJob.profile_name == current_name,
                        ContentPublishJob.status.in_(
                            ("queued", "running", "need_human", "pending_review", "blocked")
                        ),
                    )
                )
                if active_job is not None:
                    raise ValueError("存在未完成软文发布，不能修改账号平台")
                platform_result = update_chrome_profile_account(
                    current_name,
                    customer_id=customer.id,
                    business_scope=SCOPE_CONTENT,
                    platform=body.platform or "",
                    custom_platform_name=body.custom_platform_name,
                    custom_platform_url=body.custom_platform_url,
                )
                profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
                customer.profile_json = set_selected_chrome_profile(
                    profile,
                    name=current_name,
                    platform=str(platform_result["platform"]),
                    customer_id=customer.id,
                    business_scope=SCOPE_CONTENT,
                )
                flag_modified(customer, "profile_json")
                for account in session.scalars(
                    select(ReachMessageAccount).where(
                        ReachMessageAccount.customer_id == customer.id,
                        ReachMessageAccount.business_scope == SCOPE_CONTENT,
                        ReachMessageAccount.profile_name == current_name,
                    )
                ).all():
                    if account.platform != platform_result["platform"]:
                        account.enabled = False
                        account.platform = str(platform_result["platform"])
            session.commit()
            return {
                "ok": True,
                "name": current_name,
                "platform": (platform_result or {}).get("platform"),
                "official_url": (platform_result or {}).get("official_url"),
            }
        except ValueError as exc:
            session.rollback()
            if renamed:
                try:
                    rename_chrome_profile(
                        current_name,
                        old_name,
                        customer_id=customer.id,
                        business_scope=SCOPE_CONTENT,
                    )
                except Exception:
                    pass
            raise HTTPException(409, str(exc)) from exc
    finally:
        session.close()


@router.delete("/chrome-profiles")
def content_chrome_delete(body: ContentChromeDelete) -> dict[str, Any]:
    """Delete soft-article accounts while preserving publish/message history."""
    from engine.reach.account_management import delete_chrome_accounts
    from engine.reach.business_scope import SCOPE_CONTENT

    _, session, customer, _ = _scope()
    try:
        try:
            return delete_chrome_accounts(
                session,
                customer,
                names=body.names,
                business_scope=SCOPE_CONTENT,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    finally:
        session.close()


@router.get("/message-accounts")
def content_message_accounts() -> dict[str, Any]:
    from engine.catalog.db import ReachMessageAccount, ReachMessageScan
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.message_sync import (
        account_to_dict,
        is_active_message_account,
    )

    _, session, customer, _ = _scope()
    try:
        rows = session.scalars(
            select(ReachMessageAccount)
            .where(
                ReachMessageAccount.customer_id == customer.id,
                ReachMessageAccount.business_scope == SCOPE_CONTENT,
                ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
                ReachMessageAccount.provisioning_status == "explicit",
            )
            .order_by(ReachMessageAccount.id)
        ).all()
        accounts = []
        for row in rows:
            if not is_active_message_account(row):
                continue
            item = account_to_dict(row)
            latest = session.scalar(
                select(ReachMessageScan)
                .where(ReachMessageScan.account_id == row.id)
                .order_by(ReachMessageScan.id.desc())
                .limit(1)
            )
            item["last_status"] = latest.status if latest else None
            item["last_error"] = latest.error if latest and latest.error else None
            item["last_error_code"] = (
                (latest.error_code or None) if latest else None
            )
            accounts.append(item)
        return {
            "ok": True,
            "accounts": accounts,
            "business_scope": SCOPE_CONTENT,
            "empty_state": (
                {
                    "code": "no_message_account",
                    "title": "先创建软文消息账号",
                    "action": "create_message_account",
                }
                if not accounts
                else None
            ),
        }
    finally:
        session.close()


@router.post("/message-accounts")
def content_message_account_create(body: ContentMessageAccountCreate) -> dict[str, Any]:
    from engine.reach.business_scope import CONTENT_MESSAGE_PLATFORMS, SCOPE_CONTENT
    from engine.reach.message_sync import account_to_dict, create_message_account

    _, session, customer, _ = _scope()
    try:
        try:
            if body.platform.strip().lower() not in CONTENT_MESSAGE_PLATFORMS:
                raise ValueError(f"软文消息暂不支持平台: {body.platform}")
            row, profile = create_message_account(
                session,
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
                platform=body.platform,
                profile_name=body.profile_name,
                display_name=body.display_name,
                enabled=body.enabled,
                message_url=body.message_url,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        session.commit()
        session.refresh(row)
        return {
            "ok": True,
            "account": account_to_dict(row),
            "created": True,
            "chrome_profile": (profile.get("created") or [None])[0],
        }
    finally:
        session.close()


@router.patch("/message-accounts/{account_id}")
def content_message_account_patch(account_id: int, body: ContentMessageAccountPatch) -> dict[str, Any]:
    from engine.catalog.db import ReachMessageAccount
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.message_sync import account_to_dict
    from engine.reach.messages_adapters import validate_official_url

    _, session, customer, _ = _scope()
    try:
        row = session.scalar(
            select(ReachMessageAccount).where(
                ReachMessageAccount.id == account_id,
                ReachMessageAccount.customer_id == customer.id,
                ReachMessageAccount.business_scope == SCOPE_CONTENT,
                ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
                ReachMessageAccount.provisioning_status == "explicit",
            )
        )
        if not row:
            raise HTTPException(404, "软文消息账号不存在")
        if body.display_name is not None:
            row.display_name = body.display_name[:128]
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.message_url is not None:
            try:
                row.message_url = validate_official_url(row.platform, body.message_url)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return {"ok": True, "account": account_to_dict(row)}
    finally:
        session.close()


class ContentMessageAccountDelete(BaseModel):
    ids: list[int] = Field(default_factory=list, min_length=1)


@router.delete("/message-accounts")
def content_message_accounts_delete(body: ContentMessageAccountDelete) -> dict[str, Any]:
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.message_sync import delete_message_accounts

    _, session, customer, _ = _scope()
    try:
        try:
            return delete_message_accounts(
                session,
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
                account_ids=body.ids,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        session.close()


@router.post("/message-accounts/{account_id}/open")
def content_message_account_open(
    account_id: int, body: ContentMessageOpenIn | None = None
) -> dict[str, Any]:
    """Open the account's official inbox, never the article publishing page."""
    from engine.catalog.db import ReachMessageAccount
    from engine.reach.browser import open_chrome_profile
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.messages_adapters import canonical_message_url, validate_official_url

    _, session, customer, _ = _scope()
    try:
        account = session.scalar(
            select(ReachMessageAccount).where(
                ReachMessageAccount.id == account_id,
                ReachMessageAccount.customer_id == customer.id,
                ReachMessageAccount.business_scope == SCOPE_CONTENT,
                ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
            )
        )
        if not account:
            raise HTTPException(404, "软文消息账号不存在")
        try:
            url = validate_official_url(
                account.platform,
                account.message_url or canonical_message_url(account.platform),
            )
            opened = open_chrome_profile(
                account.profile_name,
                url=url,
                dry_run=bool(body.dry_run) if body else False,
                customer_id=customer.id,
                business_scope=SCOPE_CONTENT,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "opened": opened, "message_url": url}
    finally:
        session.close()


@router.get("/messages")
def content_messages_list(
    account_id: int | None = None,
    unread: bool | None = True,
    history: bool = False,
    platform: str | None = None,
    kind: str | None = None,
    before_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    from sqlalchemy import func

    from engine.catalog.db import ReachMessage, ReachMessageAccount
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.message_sync import message_to_dict

    _, session, customer, _ = _scope()
    try:
        query = select(ReachMessage).where(
            ReachMessage.customer_id == customer.id,
            ReachMessage.business_scope == SCOPE_CONTENT,
            ReachMessage.purged_at.is_(None),
        )
        if account_id is not None:
            query = query.where(ReachMessage.account_id == account_id)
        if not history and unread is not None:
            query = query.where(ReachMessage.unread.is_(unread))
        if platform:
            query = query.where(ReachMessage.platform == platform.strip().lower())
        if kind:
            query = query.where(ReachMessage.kind == kind.strip().lower())
        if before_id is not None:
            query = query.where(ReachMessage.id < before_id)
        rows = session.scalars(
            query.order_by(
                func.coalesce(
                    ReachMessage.platform_event_at,
                    ReachMessage.last_seen_at,
                    ReachMessage.first_seen_at,
                ).desc(),
                ReachMessage.id.desc(),
            ).limit(max(1, min(limit, 500)))
        ).all()
        unread_query = (
            select(func.count())
            .select_from(ReachMessage)
            .where(
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == SCOPE_CONTENT,
                ReachMessage.purged_at.is_(None),
                ReachMessage.unread.is_(True),
            )
        )
        if account_id is not None:
            unread_query = unread_query.where(ReachMessage.account_id == account_id)
        if platform:
            unread_query = unread_query.where(
                ReachMessage.platform == platform.strip().lower()
            )
        if kind:
            unread_query = unread_query.where(ReachMessage.kind == kind.strip().lower())
        unread_count = session.scalar(unread_query)
        return {
            "ok": True,
            "messages": [message_to_dict(row) for row in rows],
            "business_scope": SCOPE_CONTENT,
            "unread_count": int(unread_count or 0),
            "next_before_id": rows[-1].id if len(rows) >= max(1, min(limit, 500)) else None,
            "empty_state": (
                {
                    "code": "no_message_account",
                    "title": "先创建软文消息账号",
                    "action": "create_message_account",
                }
                if not session.scalar(
                    select(ReachMessageAccount.id).where(
                        ReachMessageAccount.customer_id == customer.id,
                        ReachMessageAccount.business_scope == SCOPE_CONTENT,
                        ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
                        ReachMessageAccount.provisioning_status == "explicit",
                    )
                )
                else (
                    {
                        "code": "scanned_no_unread",
                        "title": "已扫描，暂无未读消息",
                    }
                    if not rows
                    else None
                )
            ),
        }
    finally:
        session.close()


@router.post("/messages/scan")
def content_messages_scan(body: ContentMessageScanIn) -> dict[str, Any]:
    from engine.catalog.db import ReachMessageAccount
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.message_sync import message_sync

    _, session, customer, _ = _scope()
    try:
        query = select(ReachMessageAccount.id).where(
            ReachMessageAccount.customer_id == customer.id,
            ReachMessageAccount.business_scope == SCOPE_CONTENT,
            ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
            ReachMessageAccount.provisioning_status == "explicit",
            ReachMessageAccount.enabled.is_(True),
        )
        if body.account_id is not None:
            query = query.where(ReachMessageAccount.id == body.account_id)
        account_ids = list(session.scalars(query).all())
        if body.account_id is not None and not account_ids:
            raise HTTPException(404, "软文消息账号不存在或未启用")
        if not account_ids:
            raise HTTPException(400, "当前客户没有已启用的软文消息账号")
        return {"ok": True, **message_sync.enqueue(account_ids, dry_run=body.dry_run)}
    finally:
        session.close()


@router.get("/messages/status")
def content_messages_status() -> dict[str, Any]:
    from engine.catalog.db import ReachMessageAccount, ReachMessageScan
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.message_sync import message_sync

    _, session, customer, _ = _scope()
    try:
        scans = session.scalars(
            select(ReachMessageScan)
            .where(ReachMessageScan.customer_id == customer.id)
            .order_by(ReachMessageScan.id.desc())
            .limit(40)
        ).all()
        content_account_ids = set(
            session.scalars(
                select(ReachMessageAccount.id).where(
                    ReachMessageAccount.customer_id == customer.id,
                    ReachMessageAccount.business_scope == SCOPE_CONTENT,
                    ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
                    ReachMessageAccount.provisioning_status == "explicit",
                )
            ).all()
        )
        scans = [s for s in scans if s.account_id in content_account_ids]
        worker_status = message_sync.status()
        worker_account_id = worker_status.get("account_id")
        if worker_account_id is not None and worker_account_id not in content_account_ids:
            worker_status = {
                **worker_status,
                "account_id": None,
                "platform": None,
                "task_id": None,
                "error": None,
                "error_code": None,
                "phase": "other_scope_active" if worker_status.get("active") else "idle",
            }
        return {
            "ok": True,
            "business_scope": SCOPE_CONTENT,
            "worker": worker_status,
            "scans": [
                {
                    "id": row.id,
                    "account_id": row.account_id,
                    "status": row.status,
                    "source": row.source,
                    "found_count": row.found_count,
                    "error_code": row.error_code,
                    "error": row.error,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                }
                for row in scans[:20]
            ],
        }
    finally:
        session.close()


@router.post("/messages/cancel")
def content_messages_cancel() -> dict[str, Any]:
    from engine.catalog.db import ReachMessageAccount
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.message_sync import message_sync

    _, session, customer, _ = _scope()
    try:
        account_ids = set(
            session.scalars(
                select(ReachMessageAccount.id).where(
                    ReachMessageAccount.customer_id == customer.id,
                    ReachMessageAccount.business_scope == SCOPE_CONTENT,
                    ReachMessageAccount.profile_role.in_(("message", "shared_legacy")),
                    ReachMessageAccount.provisioning_status == "explicit",
                )
            ).all()
        )
        return {"ok": True, **message_sync.cancel(account_ids)}
    finally:
        session.close()


@router.post("/messages/{message_id}/read")
def content_messages_mark_read(message_id: int) -> dict[str, Any]:
    """Preview-ack: mark soft-article message read when user expands preview."""
    from engine.catalog.db import ReachMessage
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.message_sync import message_to_dict

    _, session, customer, _ = _scope()
    try:
        row = session.scalar(
            select(ReachMessage).where(
                ReachMessage.id == message_id,
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == SCOPE_CONTENT,
            )
        )
        if not row:
            raise HTTPException(404, "软文消息不存在")
        row.unread = False
        row.read_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(row)
        return {"ok": True, "message": message_to_dict(row)}
    finally:
        session.close()


@router.post("/messages/read-all")
@router.post("/messages/bulk-read")
def content_messages_bulk_read(body: ContentMessagesBulkReadIn) -> dict[str, Any]:
    from sqlalchemy import func, update

    from engine.catalog.db import ReachMessage
    from engine.reach.business_scope import SCOPE_CONTENT

    _, session, customer, _ = _scope()
    try:
        query = (
            update(ReachMessage)
            .where(
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == SCOPE_CONTENT,
                ReachMessage.purged_at.is_(None),
                ReachMessage.unread.is_(True),
            )
            .values(unread=False, read_at=datetime.now(timezone.utc))
        )
        ids = sorted({int(value) for value in body.ids if int(value) > 0})
        if ids:
            query = query.where(ReachMessage.id.in_(ids))
        elif body.account_id is not None:
            query = query.where(ReachMessage.account_id == body.account_id)
        if body.platform:
            query = query.where(ReachMessage.platform == body.platform.strip().lower())
        if body.kind:
            query = query.where(ReachMessage.kind == body.kind.strip().lower())
        result = session.execute(query)
        session.commit()
        unread_count = session.scalar(
            select(func.count())
            .select_from(ReachMessage)
            .where(
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == SCOPE_CONTENT,
                ReachMessage.purged_at.is_(None),
                ReachMessage.unread.is_(True),
            )
        )
        return {
            "ok": True,
            "updated": int(result.rowcount or 0),
            "unread_count": int(unread_count or 0),
        }
    finally:
        session.close()


@router.post("/messages/{message_id}/open")
def content_messages_open(message_id: int, body: ContentMessageOpenIn | None = None) -> dict[str, Any]:
    from engine.catalog.db import ReachMessage, ReachMessageAccount
    from engine.reach.browser import open_chrome_profile
    from engine.reach.business_scope import SCOPE_CONTENT
    from engine.reach.chrome_runtime import ChromeRuntimeError, operation
    from engine.reach.messages_adapters import validate_official_url

    _, session, customer, _ = _scope()
    try:
        row = session.scalar(
            select(ReachMessage).where(
                ReachMessage.id == message_id,
                ReachMessage.customer_id == customer.id,
                ReachMessage.business_scope == SCOPE_CONTENT,
            )
        )
        if not row:
            raise HTTPException(404, "软文消息不存在")
        account = session.get(ReachMessageAccount, row.account_id)
        if (
            not account
            or account.customer_id != customer.id
            or getattr(account, "business_scope", None) != SCOPE_CONTENT
        ):
            raise HTTPException(404, "软文消息账号不存在")
        if getattr(account, "profile_role", "") == "archived":
            raise HTTPException(409, "该软文消息账号已归档，只能查看历史摘要")
        try:
            url = validate_official_url(row.platform, row.reply_url)
            with operation("content_message_open"):
                opened = open_chrome_profile(
                    account.profile_name,
                    url=url,
                    dry_run=bool(body.dry_run) if body else False,
                    customer_id=customer.id,
                    business_scope=SCOPE_CONTENT,
                )
        except (ValueError, ChromeRuntimeError) as exc:
            raise HTTPException(409 if isinstance(exc, ChromeRuntimeError) else 400, str(exc)) from exc
        return {"ok": True, "opened": opened, "auto_reply": False, "human_in_loop": True}
    finally:
        session.close()
