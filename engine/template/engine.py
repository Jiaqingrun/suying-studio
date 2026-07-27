from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet, ClipletUsage, Customer, KeywordUsage
from engine.catalog.keyword_pack import (
    check_compliance,
    get_active_pack,
    normalize_title_layout,
    list_title_pool,
    pick_keyword,
    pick_title,
)
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
    title_font_size: int = 92
    title_color: str = "#FFE600"
    title_bar_color: str = "#111827"
    title_bar_opacity: float = 0.0  # no mask over footage
    title_bold: bool = True
    title_stroke_width: int = 6
    title_stroke_color: str = "#000000"
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
    theme_cooldown_recent: int = 5  # GQual: avoid repeating auto-picked themes
    prefer_unused_assets_in_job: bool = True
    # Sprint B + QUALITY_LOCK: drop dark/blurry cliplets (legacy unscored 1.0 NO LONGER passes)
    min_cliplet_quality: float = 0.35
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
    title_font_size=92,
    title_color="#FFE600",
    title_bar_color="#111827",
    title_bar_opacity=0.0,
    title_bold=True,
    title_stroke_width=6,
    title_stroke_color="#000000",
    title_layout="dual_chip",
    title_max_chars=10,
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
    min_cliplet_quality=0.35,
)

# Sprint B · 快切：更多短镜、偏动态过程节奏
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
    title_font_size=92,
    title_color="#FFE600",
    title_bar_color="#111827",
    title_bar_opacity=0.0,
    title_bold=True,
    title_stroke_width=6,
    title_stroke_color="#000000",
    title_layout="dual_chip",
    title_max_chars=10,
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
    min_cliplet_quality=0.35,
)

# Sprint B · 稳镜：更长特写、偏静态细节
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
    title_font_size=92,
    title_color="#FFE600",
    title_bar_color="#111827",
    title_bar_opacity=0.0,
    title_bold=True,
    title_stroke_width=6,
    title_stroke_color="#000000",
    title_layout="dual_chip",
    title_max_chars=10,
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
    min_cliplet_quality=0.35,
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
    theme: str | None = None
    scene: str | None = None
    objects: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)


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
    prefer_scenes: list[str] | None = None,
    prefer_objects: list[str] | None = None,
    prev_scene: str | None = None,
    prev_objects: list[str] | None = None,
    used_object_kinds: set[str] | None = None,
    max_object_kinds: int | None = None,
    continuity_soft: bool = True,
    paper_slip_blocked: set[int] | None = None,
    strict_semantic_v1: bool = False,
) -> ClipPlan | None:
    prefer_theme = content_theme if content_theme and content_theme != "default" else None
    prefer_scene = None
    if prefer_scenes:
        # hard-filter first preferred scene when pool is deep enough later
        prefer_scene = prefer_scenes[0]
    candidates: list[tuple[Cliplet, float]] = []
    if template.use_semantic:
        # Prefer theme/scene-scoped search first; fall back to global semantic if thin.
        if prefer_theme or prefer_scene:
            candidates = search_cliplets(
                session,
                query,
                category=category if category != "default" else None,
                theme=prefer_theme,
                scene=prefer_scene,
                top_k=40,
                min_duration=slot.min_duration,
                customer_id=customer_id,
                strict_semantic_v1=strict_semantic_v1,
            )
        if len(candidates) < 5:
            extra = search_cliplets(
                session,
                query,
                category=category if category != "default" else None,
                theme=prefer_theme,
                top_k=40,
                min_duration=slot.min_duration,
                customer_id=customer_id,
                strict_semantic_v1=strict_semantic_v1,
            )
            seen = {c.id for c, _ in candidates}
            for row, score in extra:
                if row.id not in seen:
                    candidates.append((row, score))
        if len(candidates) < 5:
            extra = search_cliplets(
                session,
                query,
                category=category if category != "default" else None,
                top_k=40,
                min_duration=slot.min_duration,
                customer_id=customer_id,
                strict_semantic_v1=strict_semantic_v1,
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
                themed = (
                    themed.join(Asset, Cliplet.asset_id == Asset.id)
                    .where(Asset.customer_id == customer_id)
                    .where(Asset.status == "ready")
                )
            rows = list(session.scalars(themed).all())
            if len(rows) < 3:
                if customer_id is not None:
                    stmt = (
                        stmt.join(Asset, Cliplet.asset_id == Asset.id)
                        .where(Asset.customer_id == customer_id)
                        .where(Asset.status == "ready")
                    )
                rows = list(session.scalars(stmt).all())
            candidates = [(r, 0.0) for r in rows]
        else:
            if customer_id is not None:
                stmt = (
                    stmt.join(Asset, Cliplet.asset_id == Asset.id)
                    .where(Asset.customer_id == customer_id)
                    .where(Asset.status == "ready")
                )
            rows = list(session.scalars(stmt).all())
            if strict_semantic_v1:
                from engine.ingest.semantic_gate import semantic_gate_passed

                rows = [row for row in rows if semantic_gate_passed(row)]
            candidates = [(r, 0.0) for r in rows]

    def _ok(c: Cliplet, score: float) -> bool:
        if c.id in used_ids:
            return False
        if strict_semantic_v1:
            from engine.ingest.semantic_gate import semantic_gate_passed

            if not semantic_gate_passed(c):
                return False
        # PAPER_SLIP HARD: 今日已达 2 次的 cliplet 永不入选（禁止静默超发）
        if paper_slip_blocked and c.id in paper_slip_blocked:
            return False
        if template.max_same_asset_per_plan <= 1 and c.asset_uuid in used_assets:
            return False
        if score < template.min_semantic_score and template.use_semantic and score > 0:
            return False
        # QUALITY_LOCK FROZEN: floor is code constant; template cannot soften
        from engine.ingest.quality import (
            CLIPLET_STATUS_REJECTED_BLUR,
            MIN_QUALITY_SCORE,
            is_usable_quality,
        )

        if (c.status or "") == CLIPLET_STATUS_REJECTED_BLUR:
            return False
        if not is_usable_quality(float(c.score or 0.0)):
            return False
        floor = max(float(template.min_cliplet_quality or 0.0), float(MIN_QUALITY_SCORE))
        if float(c.score or 0.0) < floor:
            return False
        if max_object_kinds and used_object_kinds is not None:
            objs = set(c.objects_json or [])
            new_kinds = used_object_kinds | objs
            if len(new_kinds) > max_object_kinds and not objs.issubset(used_object_kinds):
                return False
        return True

    filtered = [(c, s) for c, s in candidates if _ok(c, s) and c.id not in cooldown_ids]
    if not filtered:
        filtered = [(c, s) for c, s in candidates if _ok(c, s)]
    if not filtered:
        # Last-resort fallback may relax cooldown/semantic preferences only.
        # QUALITY_LOCK and PAPER_SLIP must remain non-negotiable.
        filtered = [(c, s) for c, s in candidates if _ok(c, s)]
    if not filtered:
        return None

    if prefer_theme:
        themed = [(c, s) for c, s in filtered if (c.theme or "") == prefer_theme]
        if len(themed) >= 2:
            filtered = themed

    if prefer_scenes:
        scene_hit = [(c, s) for c, s in filtered if (c.scene or "") in prefer_scenes]
        if len(scene_hit) >= 2:
            filtered = scene_hit

    if prefer_objects:
        obj_hit = [
            (c, s)
            for c, s in filtered
            if set(c.objects_json or []) & set(prefer_objects)
        ]
        if len(obj_hit) >= 2:
            filtered = obj_hit

    # Soft continuity: prefer same scene OR shared object with previous clip
    if continuity_soft and (prev_scene or prev_objects):
        cont = []
        prev_objs = set(prev_objects or [])
        for c, s in filtered:
            same_scene = bool(prev_scene and (c.scene or "") == prev_scene)
            shared_obj = bool(prev_objs and prev_objs & set(c.objects_json or []))
            if same_scene or shared_obj:
                cont.append((c, s))
        if len(cont) >= 2:
            filtered = cont

    # Prefer higher semantic score; down-weight assets already used earlier in this job
    job_used = prefer_unused_assets or set()
    weights = []
    for c, s in filtered:
        w = max(0.02, (s + 0.05) ** 2)
        if c.asset_uuid in job_used:
            w *= 0.15
        if prefer_theme and (c.theme or "") == prefer_theme:
            w *= 4.0
        if prefer_scenes and (c.scene or "") in prefer_scenes:
            w *= 3.0
        if prefer_objects and set(c.objects_json or []) & set(prefer_objects):
            w *= 2.5
        if prev_scene and (c.scene or "") == prev_scene:
            w *= 2.0
        if prev_objects and set(prev_objects) & set(c.objects_json or []):
            w *= 2.0
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
        theme=cliplet.theme,
        scene=cliplet.scene,
        objects=list(cliplet.objects_json or []),
        actions=list(cliplet.actions_json or []),
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
    pack_id: str | None = None,
    paper_slip_blocked: set[int] | None = None,
    strict_semantic_v1: bool = False,
) -> tuple[list[ClipPlan], str]:
    from engine.catalog.industry_pack import piece_type_rules_for_pack, resolve_piece_type

    used_cliplet_ids: set[int] = set(exclude_cliplet_ids or ())
    used_assets: set[str] = set(exclude_asset_uuids or ())
    job_used = set(job_used_assets or set()) | set(exclude_asset_uuids or set())
    clips: list[ClipPlan] = []
    desc_blob = ""
    cliplet_count = session.scalar(select(Cliplet.id).limit(1))
    use_cliplets = cliplet_count is not None

    piece_type = resolve_piece_type(content_theme, pack_id=pack_id)
    piece_rules = piece_type_rules_for_pack(pack_id).get(piece_type) or {}
    slot_prefs = piece_rules.get("slots") if isinstance(piece_rules.get("slots"), dict) else {}
    continuity = piece_rules.get("continuity") if isinstance(piece_rules.get("continuity"), dict) else {}
    max_object_kinds = continuity.get("max_object_kinds")
    try:
        max_object_kinds = int(max_object_kinds) if max_object_kinds is not None else None
    except (TypeError, ValueError):
        max_object_kinds = None
    continuity_soft = bool(continuity.get("require_scene_or_object", True))
    used_object_kinds: set[str] = set()
    prev_scene: str | None = None
    prev_objects: list[str] = []

    for slot in template.slots:
        clip = None
        # slot prefs: exact name → role → empty
        pref = {}
        if isinstance(slot_prefs, dict):
            pref = slot_prefs.get(slot.name) or slot_prefs.get(slot.role or "") or {}
        prefer_scenes = list(pref.get("prefer_scenes") or []) if isinstance(pref, dict) else []
        prefer_objects = list(pref.get("prefer_objects") or []) if isinstance(pref, dict) else []
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
                prefer_scenes=prefer_scenes or None,
                prefer_objects=prefer_objects or None,
                prev_scene=prev_scene,
                prev_objects=prev_objects or None,
                used_object_kinds=used_object_kinds,
                max_object_kinds=max_object_kinds,
                continuity_soft=continuity_soft and bool(clips),
                paper_slip_blocked=paper_slip_blocked,
                strict_semantic_v1=strict_semantic_v1,
            )
            if clip and clip.cliplet_id:
                used_cliplet_ids.add(clip.cliplet_id)
                used_assets.add(clip.asset_uuid)
                used_object_kinds.update(clip.objects or [])
                prev_scene = clip.scene
                prev_objects = list(clip.objects or [])
        if not clip and not strict_semantic_v1:
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
        elif not clip:
            warnings.append(f"槽位 {slot.name} 严格 semantic v1 候选不足，禁止整片回退")
        if clip:
            clips.append(clip)
            if clip.description:
                desc_blob += " " + clip.description
        else:
            warnings.append(f"槽位 {slot.name} 无可用素材")
    if piece_type:
        warnings.append(f"片型: {piece_type}")
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
    strict_semantic_v1: bool = False,
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

    avoid_themes: set[str] = set()
    cool_n = int(getattr(template, "theme_cooldown_recent", 5) or 0)
    if cool_n > 0 and customer_id is not None:
        from engine.catalog.db import Job as JobRow

        recent_job_themes = list(
            session.scalars(
                select(JobRow.theme)
                .where(JobRow.customer_id == customer_id)
                .order_by(JobRow.id.desc())
                .limit(cool_n)
            ).all()
        )
        avoid_themes = {str(t) for t in recent_job_themes if t and str(t) != "default"}

    content_theme, pack_theme = resolve_job_themes(
        session,
        theme,
        customer_id=customer_id,
        rng=rng,
        min_clips=3,
        pack_id=pack_id,
        avoid_themes=avoid_themes,
    )
    job_theme = (theme or "default").strip() or "default"
    if job_theme == "default":
        warnings.append(f"自动选题: 画面={content_theme} / 词条={pack_theme}")
        if avoid_themes:
            warnings.append(f"主题冷却跳过近 {len(avoid_themes)} 个: {', '.join(sorted(avoid_themes)[:5])}")
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

    # PAPER_SLIP HARD：今日已达 2 次的 cliplet / 词句禁止再选
    from engine.catalog.paper_slip import (
        cliplet_ids_at_daily_cap,
        phrase_is_blocked,
        phrases_at_daily_cap,
    )

    paper_blocked_cliplets = cliplet_ids_at_daily_cap(session, customer_id=customer_id)
    paper_blocked_phrases = phrases_at_daily_cap(session, customer_id=customer_id)
    if paper_blocked_cliplets:
        warnings.append(f"纸片规则：今日已满配额切片 {len(paper_blocked_cliplets)} 个")
    if paper_blocked_phrases:
        warnings.append(f"纸片规则：今日已满配额词句 {len(paper_blocked_phrases)} 条")

    if pack:
        brand = pack.data_json.get("company_info", {}).get("display_name_preferred", brand)
        hooks = pack.data_json.get("keyword_pool", {}).get("hooks")
        if hooks and paper_blocked_phrases:
            hooks = [h for h in hooks if not phrase_is_blocked(str(h), paper_blocked_phrases)]
        fallbacks = [t for t in content_to_pack_themes(content_theme, pack_id=pack_id) if t != pack_theme]
        keyword = pick_keyword(
            session,
            pack,
            pack_theme,
            rng,
            template.cooldown_days,
            customer_id=customer_id,
            theme_fallbacks=fallbacks,
            exclude_phrases=paper_blocked_phrases,
        )
        compliance_hits = check_compliance(keyword, pack)
        if compliance_hits:
            block_reasons.extend([f"合规命中: {t}" for t in compliance_hits])
        if phrase_is_blocked(keyword, paper_blocked_phrases):
            block_reasons.append("纸片规则：无可用关键词（今日配额已满）")
    else:
        keyword = pack_theme
        warnings.append("未导入词包，使用主题作为标题")

    recent = recent_titles(session, template.title_cooldown_recent, customer_id=customer_id)
    exclude_hooks = recent_hooks_from_titles(recent)
    # Also exclude paper-slip saturated hooks via normalize keys in pick_hook exclude set
    for ph in paper_blocked_phrases:
        # pick_hook matches exact hook strings; expand exclude with raw hooks already filtered above
        exclude_hooks.add(ph.replace("|", "\n"))
    hook = pick_hook(rng, hooks, exclude=exclude_hooks, pack_id=pack_id)
    if phrase_is_blocked(hook, paper_blocked_phrases):
        # last resort: try again with empty pool → pick_hook falls to 今日推荐; still check
        alt = pick_hook(rng, hooks, exclude=exclude_hooks | {hook}, pack_id=pack_id)
        if not phrase_is_blocked(alt, paper_blocked_phrases):
            hook = alt
        else:
            block_reasons.append("纸片规则：无可用钩子短句（今日配额已满）")

    # Prefer dedicated on-screen title_pool (spoken=false); fall back to hook/keyword template
    # Layout: random single or dual from pool (no force dual-fill)
    title_line_cap = int(template.title_max_chars)
    try:
        from engine.pack.video_lock import load_video_lock

        _tlock = load_video_lock(customer_name)
        _tt = _tlock.get("title") if isinstance(_tlock.get("title"), dict) else {}
        if _tt.get("max_chars"):
            title_line_cap = int(_tt["max_chars"])
    except Exception:
        pass

    title_pool = list_title_pool(pack, theme=pack_theme or content_theme, prefer_rich=True) if pack else []
    if title_pool and paper_blocked_phrases:
        title_pool = [t for t in title_pool if not phrase_is_blocked(t, paper_blocked_phrases)]
    title_from_pool = False
    if title_pool:
        picked = pick_title(
            rng,
            title_pool,
            exclude=set(recent),
            pack=pack,
            max_chars_per_line=title_line_cap,
            theme=pack_theme or content_theme,
            exclude_phrases=paper_blocked_phrases,
        )
        if picked:
            title = normalize_title_layout(picked, max_chars_per_line=title_line_cap)
            title_from_pool = True
            hook = title.split("\n")[0].strip() or hook
            warnings.append(f"标题语库选用（共{len(title_pool)}条）")
        else:
            title = None  # type: ignore[assignment]
    else:
        title = None  # type: ignore[assignment]

    if not title_from_pool:
        if template.force_hook_title:
            title = template.title_template.format(hook=hook, keyword=keyword, brand=brand, theme=pack_theme)
        else:
            title = f"{keyword}"
        # If exact title recently used, reshuffle hook once more
        if title in recent:
            hook = pick_hook(rng, hooks, exclude=exclude_hooks | {hook}, pack_id=pack_id)
            title = template.title_template.format(hook=hook, keyword=keyword, brand=brand, theme=pack_theme)
            warnings.append("已避开近期重复标题")
        if phrase_is_blocked(title, paper_blocked_phrases):
            block_reasons.append("纸片规则：组合标题词句今日配额已满")

    if pack:
        title_hits = check_compliance(title, pack)
        if title_hits:
            block_reasons.extend([f"标题合规命中: {t}" for t in title_hits])

    # Clamp lines ≤ max_chars (preserve single vs dual from pool)
    max_c = int(title_line_cap)
    max_lines = int(template.title_max_lines)
    title = normalize_title_layout(title, max_chars_per_line=max_c, max_lines=max_lines)
    parts = [p.strip() for p in title.split("\n") if p.strip()]
    clamped = [p[:max_c] for p in parts[:max_lines]]
    if clamped != parts[:max_lines] or len(parts) > max_lines:
        title = "\n".join(clamped)
        warnings.append(f"标题已截断至每行≤{max_c}字、最多{max_lines}行")
    else:
        title = "\n".join(clamped) if clamped else title

    # Locked rule: strip all punctuation from on-screen titles
    from engine.pack.text_sanitize import strip_all_punctuation

    title = strip_all_punctuation(title, keep_newlines=True) or title
    title = normalize_title_layout(title, max_chars_per_line=max_c, max_lines=max_lines)
    from engine.catalog.industry_pack import load_industry_pack

    industry_data = load_industry_pack(pack_id)
    theme_hint = ""
    for rule in industry_data.get("theme_rules") or []:
        if isinstance(rule, dict) and str(rule.get("theme") or "") == content_theme:
            theme_hint = " ".join(str(k) for k in (rule.get("keywords") or [])[:8])
            break
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
            pack_id=pack_id,
            paper_slip_blocked=paper_blocked_cliplets,
            strict_semantic_v1=strict_semantic_v1,
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
    if strict_semantic_v1 and len(best_clips) != len(template.slots):
        block_reasons.append(
            "严格 semantic v1 素材不足："
            f"需要 {len(template.slots)} 个合规切片，实际 {len(best_clips)} 个；禁止整片资产回退"
        )

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
        "max_chars": title_line_cap,
        "max_lines": template.title_max_lines,
    }
    # Apply frozen VIDEO_LOCK title style when present on pack/company
    try:
        # Apply frozen VIDEO_LOCK title style when present on pack/company
        # 强制参考 docs/HARD_LOCKS.md + VIDEO_LOCK：黄字黑描边
        from engine.pack.video_lock import apply_lock_to_title_style, load_video_lock

        _lock = load_video_lock(customer_name)
        title_style = apply_lock_to_title_style(title_style, _lock)
    except Exception:
        pass

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
