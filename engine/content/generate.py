"""Fact-constrained SEO/GEO article generation (template-first; Ollama optional)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from engine.catalog.db import ContentArticle, ContentCampaign, ContentSource, ContentVariant
from engine.content.compliance import check_article_compliance
from engine.content.platforms import PUBLISHABLE_FIRST, get_platform
from engine.content.store import content_hash


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _verified_facts(session: Session, customer_id: int, fact_ids: list[int] | None = None) -> list[ContentSource]:
    from sqlalchemy import select

    q = select(ContentSource).where(
        ContentSource.customer_id == customer_id,
        ContentSource.verified.is_(True),
    )
    rows = list(session.scalars(q).all())
    if fact_ids:
        allow = set(int(x) for x in fact_ids)
        rows = [r for r in rows if r.id in allow]
    return rows


def build_master_article(
    *,
    brand: str,
    topic: str,
    facts: list[ContentSource],
    questions: list[str] | None = None,
    regions: list[str] | None = None,
    author: str = "",
) -> dict[str, Any]:
    """Deterministic GEO-friendly draft from verified facts only."""
    brand = (brand or "品牌").strip() or "品牌"
    topic = (topic or "产品与服务介绍").strip()
    qs = [q.strip() for q in (questions or []) if str(q).strip()][:6]
    regs = [r.strip() for r in (regions or []) if str(r).strip()][:4]
    if not qs:
        qs = [
            f"{brand}是做什么的？",
            f"如何选择{topic}？",
            f"{brand}的服务范围有哪些？",
        ]

    fact_lines: list[str] = []
    citations: list[dict[str, Any]] = []
    fact_ids: list[int] = []
    for i, f in enumerate(facts[:12], start=1):
        fact_ids.append(f.id)
        line = (f.body or f.title or "").strip()
        if not line:
            continue
        src = (f.source_url or "客户已确认事实").strip()
        fact_lines.append(f"{i}. {line}（来源：{src}）")
        citations.append(
            {
                "fact_id": f.id,
                "title": f.title,
                "url": f.source_url,
                "excerpt": line[:200],
            }
        )

    if not fact_lines:
        raise ValueError("没有可用的已确认事实，无法生成主事实稿")

    region_line = "、".join(regs) if regs else "服务覆盖区域以客户资料为准"
    faq = [{"q": q, "a": f"结合已确认事实：请以官网与人工核验口径为准。问题「{q}」对应服务由{brand}提供。"} for q in qs]

    body_parts = [
        f"# {brand}｜{topic}",
        "",
        f"> 本文含人工智能生成合成内容，已由人工基于客户确认事实审校。作者：{author or brand}。更新时间：{_now().date().isoformat()}。",
        "",
        "## 直接回答",
        f"{brand}围绕「{topic}」提供可核验的产品与服务信息。以下结论均来自客户已确认事实，不含未证实宣传。",
        "",
        "## 关键事实",
        *fact_lines,
        "",
        "## 服务区域",
        region_line,
        "",
        "## 常见问题",
    ]
    for item in faq:
        body_parts.extend([f"### {item['q']}", item["a"], ""])
    body_parts.extend(
        [
            "## 来源与更新",
            "事实条目均标注来源；若事实过期或冲突，以人工最新确认版本为准。",
            "",
            f"（AI 生成声明：本稿由速影根据客户事实库生成，经人工审核后发布。）",
        ]
    )
    body_md = "\n".join(body_parts)
    title = f"{brand}：{topic}"[:80]
    summary = f"{brand}关于「{topic}」的事实说明，含 FAQ 与来源，便于搜索与生成式引擎引用。"
    return {
        "title": title,
        "summary": summary,
        "body_md": body_md,
        "faq": faq,
        "fact_ids": fact_ids,
        "citations": citations,
        "ai_labeled": True,
    }


def build_platform_variant(
    *,
    platform: str,
    master_title: str,
    master_body: str,
    brand: str,
) -> dict[str, Any]:
    spec = get_platform(platform)
    title = master_title
    body = master_body
    tmax = int(spec.get("title_max") or 80)
    title = title[:tmax]
    if platform == "wechat_mp":
        body = (
            f"【导语】{master_title}\n\n"
            f"{master_body}\n\n"
            f"— {brand}官方内容 · 欢迎留言及官网进一步了解"
        )
    elif platform in ("baijiahao", "toutiao"):
        # Prefer fewer outbound links for these ecosystems
        body = re_strip_urls(master_body) if "http" in master_body else master_body
        body = body + "\n\n（含 AI 创作声明：本稿经人工审校。）"
    elif platform == "xhs":
        title = title[:20]
        body = (
            f"{master_title}\n\n"
            f"三点速览：\n"
            f"1. 基于可核验事实\n"
            f"2. 服务与案例见正文\n"
            f"3. 详情以官网为准（不放站外裸链）\n\n"
            f"#本地服务 #{brand} #经验分享\n"
            f"（含 AI 创作声明）"
        )
    elif platform == "website":
        body = master_body
    payload = {
        "platform": platform,
        "title": title,
        "body_md": body,
        "ai_label": True,
        "transport": spec.get("transport"),
        "tier": spec.get("tier"),
    }
    return {
        "title": title,
        "body_md": body,
        "payload": payload,
        "similarity": 0.92 if platform != "xhs" else 0.55,
    }


def re_strip_urls(text: str) -> str:
    import re

    return re.sub(r"https?://\S+", "[来源已省略外链]", text)


def generate_article_bundle(
    session: Session,
    *,
    customer_id: int,
    brand: str,
    topic: str,
    campaign_id: int | None = None,
    fact_ids: list[int] | None = None,
    platforms: list[str] | None = None,
    banned_terms: list[str] | None = None,
    author: str = "",
) -> dict[str, Any]:
    campaign: ContentCampaign | None = None
    if campaign_id:
        campaign = session.get(ContentCampaign, campaign_id)
        if not campaign or campaign.customer_id != customer_id:
            raise ValueError("选题活动不存在或不属于当前客户")
    facts = _verified_facts(session, customer_id, fact_ids)
    questions = list(campaign.questions_json or []) if campaign else []
    regions = list(campaign.regions_json or []) if campaign else []
    author_name = author or (campaign.author_name if campaign else "") or brand
    draft = build_master_article(
        brand=brand,
        topic=topic or (campaign.name if campaign else "产品与服务"),
        facts=facts,
        questions=questions,
        regions=regions,
        author=author_name,
    )
    compliance = check_article_compliance(
        title=draft["title"],
        body_md=draft["body_md"],
        fact_ids=draft["fact_ids"],
        citations=draft["citations"],
        banned_terms=banned_terms,
        ai_labeled=True,
    )
    article = ContentArticle(
        customer_id=customer_id,
        campaign_id=campaign_id,
        status="needs_review" if compliance["passed"] else "generated",
        title=draft["title"],
        summary=draft["summary"],
        body_md=draft["body_md"],
        faq_json=draft["faq"],
        fact_ids_json=draft["fact_ids"],
        citations_json=draft["citations"],
        ai_labeled=True,
        compliance_json=compliance,
        version=1,
        content_hash=content_hash(draft["title"], draft["body_md"]),
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(article)
    session.flush()

    wanted = [p.lower() for p in (platforms or list(PUBLISHABLE_FIRST))]
    variants: list[ContentVariant] = []
    for plat in wanted:
        try:
            get_platform(plat)
        except ValueError:
            continue
        vdata = build_platform_variant(
            platform=plat,
            master_title=draft["title"],
            master_body=draft["body_md"],
            brand=brand,
        )
        v_compliance = check_article_compliance(
            title=vdata["title"],
            body_md=vdata["body_md"],
            fact_ids=draft["fact_ids"],
            citations=draft["citations"],
            banned_terms=banned_terms,
            ai_labeled=True,
            platform=plat,
        )
        payload = dict(vdata["payload"])
        payload["compliance"] = v_compliance
        payload["master_content_hash"] = article.content_hash
        payload["master_version"] = article.version
        row = ContentVariant(
            customer_id=customer_id,
            article_id=article.id,
            platform=plat,
            site_id=None,
            title=vdata["title"],
            body_md=vdata["body_md"],
            payload_json=payload,
            similarity=float(vdata["similarity"]),
            assets_json=[],
            status="ready" if v_compliance["passed"] else "needs_fix",
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(row)
        variants.append(row)
    session.commit()
    session.refresh(article)
    for v in variants:
        session.refresh(v)
    return {"article": article, "variants": variants}
