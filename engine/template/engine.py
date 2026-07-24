from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet, ClipletUsage, Customer, KeywordUsage
from engine.catalog.keyword_pack import check_compliance, get_active_pack, pick_keyword
from engine.catalog.theme_tags import content_to_pack_themes, resolve_job_themes
from engine.catalog.vector_index import cosine, embed_text, search_cliplets
from engine.template.hooks import pick_hook, query_for_role


class SlotDefinition(BaseModel):
    name: str
    min_duration: float = 2.0
    max_duration: float = 5.0
    category: str | None = None
    role: str | None = None  # hook/body/cta


class TemplateDefinition(BaseModel):
    name: str
    target_duration: float = 30.0
    output_width: int = 1080
    output_height: int = 1920
    slots: list[SlotDefinition] = Field(default_factory=list)
    title_template: str = "{keyword}"
    title_position: str = "top"  # top | upper | middle — fixed band, not random
    title_font_size: int = 96
    title_color: str = "#E10600"
    title_bar_color: str = "#111827"
    title_bar_opacity: float = 0.0  # no mask over footage
    title_bold: bool = True
    title_stroke_width: int = 6
    title_stroke_color: str = "#FFE600"
    title_layout: str = "dual_chip"  # dual_chip | stroke
    title_max_chars: int = 10
    title_max_lines: int = 2
    cooldown_days: int = 7
    min_assets_per_category: int = 3
    use_semantic: bool = True
    consistency_threshold: float = 0.35
    consistency_hard: bool = True  # below threshold → needs_review
    force_hook_title: bool = True
    reframe_mode: str = "smart"
    cover_count: int = 3
    # Q1 anti-repeat
    cliplet_cooldown_recent: int = 12  # exclude cliplets used in last N usages
    max_same_asset_per_plan: int = 1
    min_semantic_score: float = 0.08
    # Q8 job diversity
    title_cooldown_recent: int = 12
    hook_cooldown_recent: int = 10
    prefer_unused_assets_in_job: bool = True
    # Sprint B: drop dark/blurry cliplets (legacy unscored ~1.0 still pass)
    min_cliplet_quality: float = 0.28
    # G4: when True, slot durations come from TTS (see template_with_duration_budget)
    duration_from_tts: bool = False


def slots_from_duration_budget(
    durations: list[float],
    *,
    slack_sec: float = 0.12,
) -> list[SlotDefinition]:
    """One slot per narration segment; clip length must track TTS real duration."""
    if not durations:
        return [
            SlotDefinition(name="hook", min_duration=2.0, max_duration=4.0, role="hook"),
            SlotDefinition(name="body1", min_duration=3.0, max_duration=6.0, role="body"),
            SlotDefinition(name="cta", min_duration=2.0, max_duration=4.0, role="cta"),
        ]
    slots: list[SlotDefinition] = []
    n = len(durations)
    for i, raw in enumerate(durations):
        d = max(0.5, float(raw))
        if i == 0:
            role, name = "hook", "hook"
        elif i == n - 1 and n > 1:
            role, name = "cta", "cta"
        else:
            role, name = "body", f"body{i}"
        lo = max(0.5, round(d - slack_sec, 3))
        hi = max(lo, round(d + 0.05, 3))
        slots.append(
            SlotDefinition(
                name=name,
                min_duration=lo,
                max_duration=hi,
                role=role,
            )
        )
    return slots


def template_with_duration_budget(
    base: TemplateDefinition,
    durations: list[float],
    *,
    slack_sec: float = 0.12,
) -> TemplateDefinition:
    """Return a copy of ``base`` whose slots / target_duration follow TTS timings."""
    slots = slots_from_duration_budget(durations, slack_sec=slack_sec)
    data = base.model_dump()
    data["slots"] = [s.model_dump() for s in slots]
    data["target_duration"] = round(sum(max(0.5, float(d)) for d in durations), 3)
    data["name"] = f"{base.name}+tts-budget"
    data["duration_from_tts"] = True
    return TemplateDefinition(**data)


DEFAULT_TEMPLATE = TemplateDefinition(
    name="default-vertical",
    target_duration=30.0,
    slots=[
        SlotDefinition(name="hook", min_duration=2.0, max_duration=4.0, role="hook"),
        SlotDefinition(name="body1", min_duration=3.0, max_duration=6.0, role="body"),
        SlotDefinition(name="body2", min_duration=3.0, max_duration=6.0, role="body"),
        SlotDefinition(name="body3", min_duration=3.0, max_duration=6.0, role="body"),
        SlotDefinition(name="cta", min_duration=2.0, max_duration=4.0, role="cta"),
    ],
    title_template="{hook}\n{keyword}",
    title_position="top",
    title_font_size=96,
    title_color="#E10600",
    title_bar_color="#111827",
    title_bar_opacity=0.0,
    title_bold=True,
    title_stroke_width=6,
    title_stroke_color="#FFE600",
    title_layout="dual_chip",
    title_max_chars=6,
    title_max_lines=2,
    reframe_mode="smart",
    cover_count=3,
    consistency_threshold=0.35,
    consistency_hard=True,
    cliplet_cooldown_recent=12,
    max_same_asset_per_plan=1,
    title_cooldown_recent=12,
    hook_cooldown_recent=10,
    prefer_unused_assets_in_job=True,
    min_cliplet_quality=0.28,
)

# Sprint B · 快切发货：更多短镜、偏配送/装车节奏
FAST_SHIP_TEMPLATE = TemplateDefinition(
    name="fast-ship",
    target_duration=22.0,
    slots=[
        SlotDefinition(name="hook", min_duration=1.5, max_duration=2.5, role="hook"),
        SlotDefinition(name="body1", min_duration=2.0, max_duration=3.5, role="body"),
        SlotDefinition(name="body2", min_duration=2.0, max_duration=3.5, role="body"),
        SlotDefinition(name="body3", min_duration=2.0, max_duration=3.5, role="body"),
        SlotDefinition(name="body4", min_duration=2.0, max_duration=3.5, role="body"),
        SlotDefinition(name="cta", min_duration=1.5, max_duration=2.5, role="cta"),
    ],
    title_template="{hook}\n{keyword}",
    title_position="top",
    title_font_size=96,
    title_color="#E10600",
    title_bar_color="#111827",
    title_bar_opacity=0.0,
    title_bold=True,
    title_stroke_width=6,
    title_stroke_color="#FFE600",
    title_layout="dual_chip",
    title_max_chars=6,
    title_max_lines=2,
    reframe_mode="smart",
    cover_count=3,
    consistency_threshold=0.32,
    consistency_hard=True,
    cliplet_cooldown_recent=14,
    max_same_asset_per_plan=1,
    title_cooldown_recent=14,
    hook_cooldown_recent=12,
    prefer_unused_assets_in_job=True,
    min_cliplet_quality=0.28,
)

# Sprint B · 稳镜产品：更长特写、偏五金/产品
STABLE_PRODUCT_TEMPLATE = TemplateDefinition(
    name="stable-product",
    target_duration=32.0,
    slots=[
        SlotDefinition(name="hook", min_duration=2.5, max_duration=4.0, role="hook"),
        SlotDefinition(name="body1", min_duration=4.0, max_duration=7.0, role="body"),
        SlotDefinition(name="body2", min_duration=4.0, max_duration=7.0, role="body"),
        SlotDefinition(name="body3", min_duration=4.0, max_duration=7.0, role="body"),
        SlotDefinition(name="cta", min_duration=2.5, max_duration=4.0, role="cta"),
    ],
    title_template="{hook}\n{keyword}",
    title_position="top",
    title_font_size=96,
    title_color="#E10600",
    title_bar_color="#111827",
    title_bar_opacity=0.0,
    title_bold=True,
    title_stroke_width=6,
    title_stroke_color="#FFE600",
    title_layout="dual_chip",
    title_max_chars=6,
    title_max_lines=2,
    reframe_mode="smart",
    cover_count=3,
    consistency_threshold=0.35,
    consistency_hard=True,
    cliplet_cooldown_recent=12,
    max_same_asset_per_plan=1,
    title_cooldown_recent=12,
    hook_cooldown_recent=10,
    prefer_unused_assets_in_job=True,
    min_cliplet_quality=0.32,
)

BUILTIN_TEMPLATES: dict[str, TemplateDefinition] = {
    DEFAULT_TEMPLATE.name: DEFAULT_TEMPLATE,
    FAST_SHIP_TEMPLATE.name: FAST_SHIP_TEMPLATE,
    STABLE_PRODUCT_TEMPLATE.name: STABLE_PRODUCT_TEMPLATE,
}


def template_for_theme(theme: str, *, pack_id: str | None = None) -> str:
    """Bind calendar/job themes to rhythm templates via industry pack bindings."""
    from engine.catalog.industry_pack import template_bindings_for_pack

    t = (theme or "default").strip().lower()
    bindings = template_bindings_for_pack(pack_id)
    mapped = bindings.get(t)
    if mapped and mapped in BUILTIN_TEMPLATES:
        return mapped
    return DEFAULT_TEMPLATE.name


@dataclass
class ClipPlan:
    slot: str
    asset_uuid: str
    source_path: str
    start_sec: float
    duration_sec: float
    cliplet_id: int | None = None
    score: float | None = None
    description: str | None = None


@dataclass
class MontagePlan:
    seed: int
    template_name: str
    theme: str
    category: str
    title: str
    clips: list[ClipPlan]
    warnings: list[str]
    blocked: bool
    block_reasons: list[str]
    consistency_score: float = 0.0
    needs_review: bool = False
    title_style: dict[str, Any] = field(default_factory=dict)
    content_theme: str = "default"


def recent_cliplet_ids(session: Session, limit: int, *, customer_id: int | None = None) -> set[int]:
    if limit <= 0:
        return set()
    stmt = select(ClipletUsage.cliplet_id).order_by(ClipletUsage.id.desc()).limit(limit * 5)
    if customer_id is not None:
        stmt = stmt.where(ClipletUsage.customer_id == customer_id)
    rows = session.scalars(stmt).all()
    # unique preserve order-ish
    out: set[int] = set()
    for cid in rows:
        out.add(int(cid))
        if len(out) >= limit:
            break
    return out


def _pick_from_assets(
    rng: random.Random,
    assets: list[Asset],
    slot: SlotDefinition,
    used_assets: set[str],
    max_same: int,
) -> ClipPlan | None:
    if max_same <= 1:
        eligible = [a for a in assets if (a.duration_sec or 0) >= slot.min_duration and a.uuid not in used_assets]
    else:
        eligible = [a for a in assets if (a.duration_sec or 0) >= slot.min_duration]
    if not eligible:
        return None
    asset = rng.choice(eligible)
    duration = min(slot.max_duration, max(slot.min_duration, asset.duration_sec or slot.max_duration))
    max_start = max(0.0, (asset.duration_sec or duration) - duration)
    start = rng.uniform(0, max_start) if max_start > 0 else 0.0
    return ClipPlan(
        slot=slot.name,
        asset_uuid=asset.uuid,
        source_path=asset.storage_path,
        start_sec=round(start, 3),
        duration_sec=round(duration, 3),
    )


def recent_titles(session: Session, limit: int, *, customer_id: int | None = None) -> set[str]:
    if limit <= 0:
        return set()
    stmt = select(KeywordUsage.keyword).order_by(KeywordUsage.id.desc()).limit(limit * 3)
    if customer_id is not None:
        stmt = stmt.where(KeywordUsage.customer_id == customer_id)
    out: set[str] = set()
    for title in session.scalars(stmt).all():
        out.add(str(title))
        if len(out) >= limit:
            break
    return out


def recent_hooks_from_titles(titles: set[str]) -> set[str]:
    hooks: set[str] = set()
    for t in titles:
        if "\n" in t:
            hooks.add(t.split("\n", 1)[0].strip())
        elif "｜" in t:
            hooks.add(t.split("｜", 1)[0].strip())
        elif "|" in t:
            hooks.add(t.split("|", 1)[0].strip())
    return hooks


def _pick_from_cliplets(
    rng: random.Random,
    session: Session,
    slot: SlotDefinition,
    query: str,
    category: str,
    used_ids: set[int],
    used_assets: set[str],
    cooldown_ids: set[int],
    template: TemplateDefinition,
    *,
    customer_id: int | None = None,
    prefer_unused_assets: set[str] | None = None,
    content_theme: str | None = None,
) -> ClipPlan | None:
    prefer_theme = content_theme if content_theme and content_theme != "default" else None
    candidates: list[tuple[Cliplet, float]] = []
    if template.use_semantic:
        # Prefer theme-scoped search first; fall back to global semantic if thin.
        if prefer_theme:
            candidates = search_cliplets(
                session,
                query,
                category=category if category != "default" else None,
                theme=prefer_theme,
                top_k=40,
                min_duration=slot.min_duration,
                customer_id=customer_id,
            )
        if len(candidates) < 5:
            extra = search_cliplets(
                session,
                query,
                category=category if category != "default" else None,
                top_k=40,
                min_duration=slot.min_duration,
                customer_id=customer_id,
            )
            seen = {c.id for c, _ in candidates}
            for row, score in extra:
                if row.id not in seen:
                    candidates.append((row, score))
    if not candidates:
        stmt = select(Cliplet).where(Cliplet.duration_sec >= slot.min_duration)
        if category and category != "default":
            stmt = stmt.where(Cliplet.category == category)
        if prefer_theme:
            themed = stmt.where(Cliplet.theme == prefer_theme)
            if customer_id is not None:
                themed = themed.join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer_id)
            rows = list(session.scalars(themed).all())
            if len(rows) < 3:
                if customer_id is not None:
                    stmt = stmt.join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer_id)
                rows = list(session.scalars(stmt).all())
            candidates = [(r, 0.0) for r in rows]
        else:
            if customer_id is not None:
                stmt = stmt.join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer_id)
            rows = list(session.scalars(stmt).all())
            candidates = [(r, 0.0) for r in rows]

    def _ok(c: Cliplet, score: float) -> bool:
        if c.id in used_ids:
            return False
        if template.max_same_asset_per_plan <= 1 and c.asset_uuid in used_assets:
            return False
        if score < template.min_semantic_score and template.use_semantic and score > 0:
            return False
        # Sprint B visual quality (legacy 1.0 unscored still passes)
        q = float(c.score or 1.0)
        if q < template.min_cliplet_quality:
            return False
        return True

    filtered = [(c, s) for c, s in candidates if _ok(c, s) and c.id not in cooldown_ids]
    if not filtered:
        filtered = [(c, s) for c, s in candidates if _ok(c, s)]
    if not filtered:
        filtered = [
            (c, s)
            for c, s in candidates
            if c.id not in used_ids and (template.max_same_asset_per_plan > 1 or c.asset_uuid not in used_assets)
        ]
    if not filtered:
        return None

    if prefer_theme:
        themed = [(c, s) for c, s in filtered if (c.theme or "") == prefer_theme]
        if len(themed) >= 2:
            filtered = themed

    # Prefer higher semantic score; down-weight assets already used earlier in this job
    job_used = prefer_unused_assets or set()
    weights = []
    for c, s in filtered:
        w = max(0.02, (s + 0.05) ** 2)
        if c.asset_uuid in job_used:
            w *= 0.15
        if prefer_theme and (c.theme or "") == prefer_theme:
            w *= 4.0
        weights.append(w)
    pick_idx = rng.choices(range(len(filtered)), weights=weights, k=1)[0]
    cliplet, score = filtered[pick_idx]
    asset = session.scalar(select(Asset).where(Asset.uuid == cliplet.asset_uuid))
    if not asset:
        return None
    duration = min(slot.max_duration, cliplet.duration_sec)
    start = cliplet.start_sec
    if cliplet.duration_sec > duration:
        start = cliplet.start_sec + rng.uniform(0, cliplet.duration_sec - duration)
    return ClipPlan(
        slot=slot.name,
        asset_uuid=cliplet.asset_uuid,
        source_path=asset.storage_path,
        start_sec=round(start, 3),
        duration_sec=round(duration, 3),
        cliplet_id=cliplet.id,
        score=round(score, 4),
        description=cliplet.description,
    )


def _assemble_clips(
    rng: random.Random,
    session: Session,
    template: TemplateDefinition,
    *,
    assets: list[Asset],
    category: str,
    theme: str,
    keyword: str,
    hook: str,
    brand: str,
    semantic_query: str,
    cooldown_ids: set[int],
    warnings: list[str],
    customer_id: int | None = None,
    job_used_assets: set[str] | None = None,
    content_theme: str | None = None,
    exclude_cliplet_ids: set[int] | None = None,
    exclude_asset_uuids: set[str] | None = None,
) -> tuple[list[ClipPlan], str]:
    used_cliplet_ids: set[int] = set(exclude_cliplet_ids or ())
    used_assets: set[str] = set(exclude_asset_uuids or ())
    job_used = set(job_used_assets or set()) | set(exclude_asset_uuids or set())
    clips: list[ClipPlan] = []
    desc_blob = ""
    cliplet_count = session.scalar(select(Cliplet.id).limit(1))
    use_cliplets = cliplet_count is not None

    for slot in template.slots:
        clip = None
        if use_cliplets:
            q = query_for_role(
                slot.role,
                theme=theme,
                keyword=keyword,
                hook=hook,
                brand=brand,
                fallback=semantic_query,
            )
            clip = _pick_from_cliplets(
                rng,
                session,
                slot,
                q,
                category,
                used_cliplet_ids,
                used_assets,
                cooldown_ids,
                template,
                customer_id=customer_id,
                prefer_unused_assets=job_used if template.prefer_unused_assets_in_job else None,
                content_theme=content_theme,
            )
            if clip and clip.cliplet_id:
                used_cliplet_ids.add(clip.cliplet_id)
                used_assets.add(clip.asset_uuid)
        if not clip:
            slot_assets = assets
            if slot.category:
                slot_assets = [a for a in assets if a.category == slot.category]
            if template.prefer_unused_assets_in_job and job_used:
                fresh = [a for a in (slot_assets or assets) if a.uuid not in job_used]
                if fresh:
                    slot_assets = fresh
            clip = _pick_from_assets(rng, slot_assets or assets, slot, used_assets, template.max_same_asset_per_plan)
            if clip:
                used_assets.add(clip.asset_uuid)
                warnings.append(f"槽位 {slot.name} 回退到整片抽样")
        if clip:
            clips.append(clip)
            if clip.description:
                desc_blob += " " + clip.description
        else:
            warnings.append(f"槽位 {slot.name} 无可用素材")
    return clips, desc_blob


def build_plan(
    session: Session,
    template: TemplateDefinition,
    *,
    customer_name: str,
    theme: str,
    category: str,
    seed: int,
    job_used_assets: set[str] | None = None,
    exclude_cliplet_ids: set[int] | None = None,
    exclude_asset_uuids: set[str] | None = None,
    duration_budget: list[float] | None = None,
) -> MontagePlan:
    rng = random.Random(seed)
    warnings: list[str] = []
    block_reasons: list[str] = []

    if duration_budget:
        template = template_with_duration_budget(template, list(duration_budget))
        warnings.append(
            f"TTS时长预算驱动取片: {len(duration_budget)}段 / {template.target_duration:.1f}s"
        )

    customer = session.scalar(select(Customer).where(Customer.name == customer_name))
    customer_id = customer.id if customer else None
    from engine.catalog.industry_pack import pack_id_for_customer

    pack_id = pack_id_for_customer(
        customer_name,
        customer.profile_json if customer else None,
    )

    content_theme, pack_theme = resolve_job_themes(
        session, theme, customer_id=customer_id, rng=rng, min_clips=3, pack_id=pack_id
    )
    job_theme = (theme or "default").strip() or "default"
    if job_theme == "default":
        warnings.append(f"自动选题: 画面={content_theme} / 词条={pack_theme}")
    elif content_theme != "default" and pack_theme != job_theme:
        warnings.append(f"主题对齐: 画面={content_theme} / 词条={pack_theme}")
    elif content_theme != "default":
        warnings.append(f"按主题选片: {content_theme}")

    query = select(Asset).where(Asset.status == "ready")
    if customer_id is not None:
        query = query.where(Asset.customer_id == customer_id)
    if category and category != "default":
        query = query.where(Asset.category == category)
    assets = list(session.scalars(query).all())

    if len(assets) < template.min_assets_per_category:
        warnings.append(f"素材不足: 需要至少 {template.min_assets_per_category} 条，当前 {len(assets)} 条")

    pack = get_active_pack(session, customer_name)
    brand = "品牌"
    hooks = None
    if pack:
        brand = pack.data_json.get("company_info", {}).get("display_name_preferred", brand)
        hooks = pack.data_json.get("keyword_pool", {}).get("hooks")
        fallbacks = [t for t in content_to_pack_themes(content_theme, pack_id=pack_id) if t != pack_theme]
        keyword = pick_keyword(
            session,
            pack,
            pack_theme,
            rng,
            template.cooldown_days,
            customer_id=customer_id,
            theme_fallbacks=fallbacks,
        )
        compliance_hits = check_compliance(keyword, pack)
        if compliance_hits:
            block_reasons.extend([f"合规命中: {t}" for t in compliance_hits])
    else:
        keyword = pack_theme
        warnings.append("未导入词包，使用主题作为标题")

    recent = recent_titles(session, template.title_cooldown_recent, customer_id=customer_id)
    exclude_hooks = recent_hooks_from_titles(recent)
    hook = pick_hook(rng, hooks, exclude=exclude_hooks, pack_id=pack_id)
    if template.force_hook_title:
        title = template.title_template.format(hook=hook, keyword=keyword, brand=brand, theme=pack_theme)
    else:
        title = f"{keyword}"
    # If exact title recently used, reshuffle hook once more
    if title in recent:
        hook = pick_hook(rng, hooks, exclude=exclude_hooks | {hook}, pack_id=pack_id)
        title = template.title_template.format(hook=hook, keyword=keyword, brand=brand, theme=pack_theme)
        warnings.append("已避开近期重复标题")

    # Sprint A: clamp title length before style / consistency
    # Allow newline between hook/keyword without counting as content overflow.
    compact = title.replace("\n", "")
    max_title = int(template.title_max_chars) * int(template.title_max_lines)
    if len(compact) > max_title:
        # Prefer keeping two short lines
        parts = [p.strip() for p in title.split("\n") if p.strip()]
        if len(parts) >= 2:
            title = parts[0][: template.title_max_chars] + "\n" + parts[1][: template.title_max_chars]
        else:
            title = compact[: max_title - 1] + "…"
        warnings.append(f"标题已截断至每行≤{template.title_max_chars}字")
    theme_hint = {
        "配送": "装车 货车 配送 发货",
        "仓配": "仓库 货架 配货 库存",
        "门店": "门店 展厅 门头",
        "施工机械": "钢筋机械 切断机 弯曲机",
        "产品": "五金 工具 产品特写",
    }.get(content_theme, "工地五金 门店 仓库 配货 批发")
    semantic_query = f"{pack_theme} {keyword} {hook} {theme_hint}"
    cooldown_ids = recent_cliplet_ids(session, template.cliplet_cooldown_recent, customer_id=customer_id)
    if exclude_cliplet_ids:
        cooldown_ids = set(cooldown_ids) | set(exclude_cliplet_ids)
        warnings.append(f"重渲排除片段 {len(exclude_cliplet_ids)} 个")
    if cooldown_ids:
        warnings.append(f"已冷却近期片段 {len(cooldown_ids)} 个")

    used_assets_seed = set(job_used_assets or set()) | set(exclude_asset_uuids or set())
    if exclude_asset_uuids:
        warnings.append(f"重渲排除素材 {len(exclude_asset_uuids)} 条")

    best_clips: list[ClipPlan] = []
    best_score = -1.0
    best_warnings = list(warnings)
    desc_blob = ""

    for attempt in range(3):
        attempt_rng = random.Random(seed + attempt * 9973)
        attempt_warnings = list(warnings)
        clips, blob = _assemble_clips(
            attempt_rng,
            session,
            template,
            assets=assets,
            category=category,
            theme=pack_theme,
            keyword=keyword,
            hook=hook,
            brand=brand,
            semantic_query=semantic_query,
            cooldown_ids=cooldown_ids,
            warnings=attempt_warnings,
            customer_id=customer_id,
            job_used_assets=used_assets_seed,
            content_theme=content_theme,
            exclude_cliplet_ids=exclude_cliplet_ids,
            exclude_asset_uuids=exclude_asset_uuids,
        )
        score = 0.0
        if blob.strip():
            t_emb, _ = embed_text(title)
            d_emb, _ = embed_text(blob)
            score = cosine(t_emb, d_emb)
        if score > best_score and clips:
            best_score = score
            best_clips = clips
            best_warnings = attempt_warnings
            desc_blob = blob
        if score >= template.consistency_threshold and clips:
            break

    consistency = round(best_score if best_score >= 0 else 0.0, 4)
    needs_review = False
    if best_clips and consistency < template.consistency_threshold:
        msg = f"标题-画面一致性偏低: {consistency:.3f} < {template.consistency_threshold}"
        best_warnings.append(msg)
        if template.consistency_hard:
            needs_review = True
            best_warnings.append("已标记 needs_review（进 review 目录）")

    if not best_clips:
        block_reasons.append("无法组装任何片段")

    title_style = {
        "position": template.title_position,
        "font_size": template.title_font_size,
        "color": template.title_color,
        "bar_color": template.title_bar_color,
        "bar_opacity": template.title_bar_opacity,
        "bold": template.title_bold,
        "stroke_width": template.title_stroke_width,
        "stroke_color": template.title_stroke_color,
        "layout": template.title_layout,
        "max_chars": template.title_max_chars,
        "max_lines": template.title_max_lines,
    }

    return MontagePlan(
        seed=seed,
        template_name=template.name,
        theme=pack_theme,
        category=category,
        title=title,
        clips=best_clips,
        warnings=best_warnings,
        blocked=bool(block_reasons),
        block_reasons=block_reasons,
        consistency_score=consistency,
        needs_review=needs_review,
        title_style=title_style,
        content_theme=content_theme,
    )


def plan_to_dict(plan: MontagePlan) -> dict[str, Any]:
    return {
        "seed": plan.seed,
        "template_name": plan.template_name,
        "theme": plan.theme,
        "content_theme": plan.content_theme,
        "category": plan.category,
        "title": plan.title,
        "clips": [c.__dict__ for c in plan.clips],
        "warnings": plan.warnings,
        "blocked": plan.blocked,
        "block_reasons": plan.block_reasons,
        "consistency_score": plan.consistency_score,
        "needs_review": plan.needs_review,
        "title_style": plan.title_style,
    }


def record_cliplet_usage(
    session: Session,
    plan: MontagePlan,
    *,
    job_id: int | None,
    render_output_id: int | None,
    customer_id: int | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    for clip in plan.clips:
        if not clip.cliplet_id:
            continue
        session.add(
            ClipletUsage(
                cliplet_id=clip.cliplet_id,
                asset_uuid=clip.asset_uuid,
                job_id=job_id,
                render_output_id=render_output_id,
                used_at=now,
                customer_id=customer_id,
            )
        )
