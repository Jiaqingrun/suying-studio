from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet, ClipletUsage, Customer, KeywordUsage
from engine.catalog.keyword_pack import (
    check_compliance,
    get_active_pack,
    get_pack_by_id,
    normalize_title_layout,
    list_title_pool,
    pick_keyword,
    pick_title,
    resolve_rule_facet,
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
    title_max_chars: int = 24
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
    title_max_chars=24,
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
    title_max_chars=24,
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

# Sprint B · 稳镜：更长特写、偏静态细节；禁止仓配装车回混
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
    title_max_chars=24,
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

# When producing stable-product / product VO, never mix warehouse-loading shots.
PRODUCT_PREFER_SCENES: tuple[str, ...] = ("product_closeup", "product", "tabletop")
PRODUCT_EXCLUDE_SCENES: tuple[str, ...] = (
    "warehouse",
    "loading",
    "delivery",
    "shipping",
    "truck",
    "fleet",
    "people_activity",
)
PRODUCT_OPS_DESC_BANS: tuple[str, ...] = (
    "装车",
    "卸货",
    "发货",
    "出库",
    "上车",
    "车斗",
    "货车",
    "卡车",
    "仓库",
    "码放",
    "分拣",
    "仓配",
    # Outdoor yard / facility scale (mislabeled as product_closeup still reject for tabletop reels)
    "户外场景",
    "混凝土路面",
    "草地",
    "建筑物",
    "装卸",
    "工人忙碌",
    "车门敞开",
    # Large outdoor mixers / tanks mixed into “tabletop product” reels
    "五立方",
    "储料搅拌",
    "搅拌罐",
    "大型绿色工业",
    "大型红色金属",
    "圆柱形罐",
    "进料斗",
    "漏斗状",
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
    orientation: str = "portrait"
    consistency_score: float = 0.0
    needs_review: bool = False
    title_style: dict[str, Any] = field(default_factory=dict)
    content_theme: str = "default"
    content_fingerprint: dict[str, Any] = field(default_factory=dict)
    recipe_id: str = ""
    component_ids: list[str] = field(default_factory=list)
    copy_components: list[str] = field(default_factory=list)
    allowed_facts: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)


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
    exclude_scenes: list[str] | None = None,
    allowed_cliplet_ids: set[int] | None = None,
    hard_prefer_scenes: bool = False,
    reject_ops_description: bool = False,
    require_commercial_product: bool = False,
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
        # Unscoped extras reintroduce other styles (warehouse into 装车, tabletop into 店面).
        if not hard_prefer_scenes and len(candidates) < 5:
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
        if not hard_prefer_scenes and len(candidates) < 5:
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
    if not candidates and not (hard_prefer_scenes and prefer_scenes):
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

    if hard_prefer_scenes and prefer_scenes:
        wanted = {str(s).strip() for s in prefer_scenes if str(s).strip()}
        scene_pool = [(c, s) for c, s in candidates if (c.scene or "") in wanted]
        if len(scene_pool) < 10 and wanted:
            stmt = select(Cliplet).where(
                Cliplet.duration_sec >= float(slot.min_duration),
                Cliplet.scene.in_(list(wanted)),
            )
            if customer_id is not None:
                stmt = (
                    stmt.join(Asset, Cliplet.asset_id == Asset.id)
                    .where(Asset.customer_id == customer_id)
                    .where(Asset.status == "ready")
                )
            seen = {c.id for c, _ in scene_pool}
            for row in session.scalars(stmt.limit(120)).all():
                if row.id not in seen:
                    scene_pool.append((row, 0.0))
                    seen.add(row.id)
        candidates = scene_pool

    def _ok(c: Cliplet, score: float) -> bool:
        if allowed_cliplet_ids is not None and c.id not in allowed_cliplet_ids:
            return False
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
        if exclude_scenes:
            scene = str(c.scene or "").strip()
            if scene and scene in exclude_scenes:
                return False
            sem = c.semantic_json if isinstance(getattr(c, "semantic_json", None), dict) else {}
            scenes = sem.get("scenes") if isinstance(sem, dict) else None
            if isinstance(scenes, list):
                labels = {
                    str(x.get("label") or "").strip()
                    for x in scenes
                    if isinstance(x, dict)
                }
                if labels & set(exclude_scenes):
                    return False
            people_exclusions = {
                "people_activity",
                "person",
                "people",
                "portrait",
                "work_portrait",
                "team_image",
            }
            people = sem.get("people") if isinstance(sem, dict) else None
            if (
                set(exclude_scenes) & people_exclusions
                and isinstance(people, dict)
                and bool(people.get("present"))
            ):
                return False
        if reject_ops_description:
            blob = " ".join(
                [
                    str(c.description or ""),
                    str(c.scene or ""),
                    " ".join(str(x) for x in (c.objects_json or [])),
                    " ".join(str(x) for x in (c.actions_json or [])),
                ]
            )
            if any(k and k in blob for k in PRODUCT_OPS_DESC_BANS):
                return False
        if require_commercial_product:
            from engine.pack.narration_script import product_has_commercial_hook

            if not product_has_commercial_hook(str(c.description or "")):
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
        # stable-product: only tabletop product scenes — never fall through to yard/loading
        if hard_prefer_scenes:
            if not scene_hit:
                return None
            filtered = scene_hit
        elif len(scene_hit) >= 2:
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
    extra_prefer_scenes: list[str] | None = None,
    exclude_scenes: list[str] | None = None,
    allowed_cliplet_ids: set[int] | None = None,
    hard_prefer_scenes: bool = False,
    reject_ops_description: bool = False,
    forbid_asset_fallback: bool = False,
    require_commercial_product: bool = False,
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
        if extra_prefer_scenes:
            for s in extra_prefer_scenes:
                if s and s not in prefer_scenes:
                    prefer_scenes.append(s)
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
                exclude_scenes=exclude_scenes,
                allowed_cliplet_ids=allowed_cliplet_ids,
                hard_prefer_scenes=hard_prefer_scenes,
                reject_ops_description=reject_ops_description,
                require_commercial_product=require_commercial_product,
            )
            # Product reels: never whole-file fallback; if paper-slip emptied the pool,
            # retry once without roll-window block so we still fill tabletop slots.
            if (
                not clip
                and hard_prefer_scenes
                and paper_slip_blocked
            ):
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
                    continuity_soft=False,
                    paper_slip_blocked=None,
                    strict_semantic_v1=strict_semantic_v1,
                    exclude_scenes=exclude_scenes,
                    allowed_cliplet_ids=allowed_cliplet_ids,
                    hard_prefer_scenes=hard_prefer_scenes,
                    reject_ops_description=reject_ops_description,
                    require_commercial_product=require_commercial_product,
                )
                if clip:
                    warnings.append(f"槽位 {slot.name} 放宽滚动避重以补齐商品特写（仍禁装车/整片回退）")
            if clip and clip.cliplet_id:
                used_cliplet_ids.add(clip.cliplet_id)
                used_assets.add(clip.asset_uuid)
                used_object_kinds.update(clip.objects or [])
                prev_scene = clip.scene
                prev_objects = list(clip.objects or [])
        # Product/stable reels: never inject unlabelled whole-file Windows (loading/yard bleed)
        if not clip and not strict_semantic_v1 and not forbid_asset_fallback:
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
        elif not clip and strict_semantic_v1:
            warnings.append(f"槽位 {slot.name} 严格 semantic v1 候选不足，禁止整片回退")
        elif not clip and forbid_asset_fallback:
            warnings.append(f"槽位 {slot.name} 无商品特写候选，禁止整片回退（防装车混入）")
        if clip and reject_ops_description:
            # Second hard gate: even product_closeup mislabels (yard hoppers etc.)
            blob = " ".join(
                [
                    str(clip.description or ""),
                    str(clip.scene or ""),
                    " ".join(str(x) for x in (clip.objects or [])),
                    " ".join(str(x) for x in (clip.actions or [])),
                ]
            )
            if any(k and k in blob for k in PRODUCT_OPS_DESC_BANS):
                warnings.append(f"槽位 {slot.name} 跳过仓配/户外场地描述片段")
                clip = None
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
    production_rules: dict[str, Any] | None = None,
    keyword_pack_id: int | None = None,
    topic_intent: dict[str, Any] | None = None,
    paper_slip_reservation_key: str | None = None,
    customer_id: int | None = None,
    orientation: str = "portrait",
    asset_category: str | None = None,
) -> MontagePlan:
    rng = random.Random(seed)
    warnings: list[str] = []
    block_reasons: list[str] = []
    if topic_intent:
        strict_semantic_v1 = True
        warnings.append(f"专题模式: {topic_intent.get('mode')}")
        rejected_uses = list(topic_intent.get("rejected_uses") or [])
        if rejected_uses:
            block_reasons.append(
                "专题用途缺少“官方目录 + 当前画面语义”双证据："
                + "、".join(str(x) for x in rejected_uses)
            )

    if duration_budget:
        template = template_with_duration_budget(template, list(duration_budget))
        warnings.append(
            f"TTS时长预算驱动取片: {len(duration_budget)}段 / {template.target_duration:.1f}s"
        )

    # Prefer explicit customer_id (job binding) over name lookup so paper-slip
    # filtering cannot fall back to customer_id=0 when snap.customer_name is null.
    customer = None
    if customer_id is not None:
        customer = session.get(Customer, int(customer_id))
    if customer is None and customer_name:
        customer = session.scalar(select(Customer).where(Customer.name == customer_name))
    customer_id = customer.id if customer else None
    if customer is not None:
        customer_name = customer.name
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

    orientation = str(orientation or "portrait").lower()
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("orientation 须为 portrait 或 landscape")
    query = select(Asset).where(
        Asset.status == "ready",
        Asset.orientation == orientation,
    )
    if customer_id is not None:
        query = query.where(Asset.customer_id == customer_id)
    # Only Asset folder names filter here — never rule-slot names (premium/…).
    from engine.jobs.job_categories import is_rule_content_category

    folder = (asset_category or "").strip()
    if not folder and category and category != "default" and not is_rule_content_category(category):
        # Legacy callers that still pass a real folder via ``category``.
        folder = category.strip()
    if folder and not is_rule_content_category(folder):
        query = query.where(Asset.category == folder)
    assets = list(session.scalars(query).all())
    asset_ids = [int(asset.id) for asset in assets]
    orientation_cliplet_ids = (
        {
            int(value)
            for value in session.scalars(
                select(Cliplet.id).where(Cliplet.asset_id.in_(asset_ids))
            ).all()
        }
        if asset_ids
        else set()
    )

    if len(assets) < template.min_assets_per_category:
        warnings.append(f"素材不足: 需要至少 {template.min_assets_per_category} 条，当前 {len(assets)} 条")

    pack = (
        get_pack_by_id(session, keyword_pack_id, customer_id=customer_id)
        if keyword_pack_id
        else get_active_pack(session, customer_name)
    )
    if keyword_pack_id and not pack:
        block_reasons.append(f"任务冻结词池不存在或跨客户：pack_id={keyword_pack_id}")
    facet_meta: dict[str, Any] = {}
    try:
        facet_meta = resolve_rule_facet(pack, production_rules)
    except ValueError as exc:
        block_reasons.append(str(exc))
        facet_meta = {}
    facet = str(facet_meta.get("facet") or "").strip() or None
    if facet:
        pack_theme = facet
        warnings.append(f"规则内容面: {facet}（标题/词条钉死该面，不混默认池）")
    brand = "品牌"
    hooks = None

    # PAPER_SLIP：滚动避重（近窗优先，渐进放宽；禁止满额硬拒）
    from engine.catalog.paper_slip import (
        ROLLING_CLIPLET_STEPS,
        ROLLING_PHRASE_STEPS,
        phrase_is_blocked,
        recently_used_cliplet_ids,
        recently_used_phrase_keys,
    )

    clip_windows = list(ROLLING_CLIPLET_STEPS)
    phrase_windows = list(ROLLING_PHRASE_STEPS)
    # Pair steps by index; pad with 0
    max_steps = max(len(clip_windows), len(phrase_windows))
    while len(clip_windows) < max_steps:
        clip_windows.append(0)
    while len(phrase_windows) < max_steps:
        phrase_windows.append(0)

    paper_blocked_cliplets: set[int] = set()
    paper_blocked_phrases: set[str] = set()
    rolling_clip_limit = clip_windows[0]
    rolling_phrase_limit = phrase_windows[0]
    paper_blocked_cliplets = recently_used_cliplet_ids(
        session, customer_id=customer_id, limit=rolling_clip_limit
    )
    paper_blocked_phrases = recently_used_phrase_keys(
        session, customer_id=customer_id, limit=rolling_phrase_limit
    )
    if paper_blocked_cliplets:
        warnings.append(f"滚动避重：排除近 {rolling_clip_limit} 次用量切片 {len(paper_blocked_cliplets)} 个")
    if paper_blocked_phrases:
        warnings.append(f"滚动避重：排除近 {rolling_phrase_limit} 条短句 {len(paper_blocked_phrases)} 个")

    if pack:
        brand = pack.data_json.get("company_info", {}).get("display_name_preferred", brand)
        hooks = pack.data_json.get("keyword_pool", {}).get("hooks")
        facet_hooks = list(facet_meta.get("hooks") or []) if facet else []
        if facet_hooks:
            hooks = facet_hooks
        if hooks and paper_blocked_phrases:
            hooks = [h for h in hooks if not phrase_is_blocked(str(h), paper_blocked_phrases)]
        fallbacks = [t for t in content_to_pack_themes(content_theme, pack_id=pack_id) if t != pack_theme]
        if facet:
            fallbacks = []
        keyword = pick_keyword(
            session,
            pack,
            pack_theme,
            rng,
            template.cooldown_days,
            customer_id=customer_id,
            theme_fallbacks=fallbacks,
            exclude_phrases=paper_blocked_phrases,
            allow_default_fallback=not bool(facet),
        )
        compliance_hits = check_compliance(keyword, pack)
        if compliance_hits:
            block_reasons.extend([f"合规命中: {t}" for t in compliance_hits])
        if phrase_is_blocked(keyword, paper_blocked_phrases):
            # 放宽短句近窗后再试一次关键词；仍撞上则沿用结果并警告（不熔断）
            for pwin in phrase_windows[1:]:
                relaxed = recently_used_phrase_keys(session, customer_id=customer_id, limit=pwin)
                alt_kw = pick_keyword(
                    session,
                    pack,
                    pack_theme,
                    rng,
                    template.cooldown_days,
                    customer_id=customer_id,
                    theme_fallbacks=fallbacks,
                    exclude_phrases=relaxed,
                    allow_default_fallback=not bool(facet),
                )
                if not phrase_is_blocked(alt_kw, relaxed):
                    keyword = alt_kw
                    paper_blocked_phrases = relaxed
                    warnings.append(f"滚动避重：短句近窗放宽至 {pwin}")
                    break
            else:
                warnings.append("滚动避重：关键词近窗紧张，已尽力选用")
    else:
        keyword = pack_theme
        warnings.append("未导入词包，使用主题作为标题")

    recent = recent_titles(
        session,
        max(int(template.title_cooldown_recent), 50),
        customer_id=customer_id,
    )
    exclude_hooks = recent_hooks_from_titles(recent)
    for ph in paper_blocked_phrases:
        exclude_hooks.add(ph.replace("|", "\n"))
    hook = pick_hook(rng, hooks, exclude=exclude_hooks, pack_id=pack_id)
    if phrase_is_blocked(hook, paper_blocked_phrases):
        alt = pick_hook(rng, hooks, exclude=exclude_hooks | {hook}, pack_id=pack_id)
        if not phrase_is_blocked(alt, paper_blocked_phrases):
            hook = alt
        else:
            warnings.append("滚动避重：钩子近窗紧张，已尽力选用")

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

    product_reel = str(template.name or "") == "stable-product"
    title_theme = pack_theme or content_theme
    title_strict = bool(facet)
    title_excludes = set(paper_blocked_phrases or set())
    if product_reel:
        from engine.catalog.keyword_pack import prefer_product_title_theme
        from engine.pack.narration_script import SHIPPING_CLAIM_TERMS

        title_theme = prefer_product_title_theme(pack, pack_theme)
        title_strict = True
        title_excludes |= set(SHIPPING_CLAIM_TERMS) | {"仓配", "搬运", "配送", "仓库"}
    title_pool = (
        list_title_pool(
            pack,
            theme=title_theme,
            prefer_rich=True,
            strict_theme=title_strict,
        )
        if pack
        else []
    )
    if pack_id:
        from engine.catalog.industry_pack import title_candidates_for_pack

        extra_titles = title_candidates_for_pack(pack_id)
        if extra_titles:
            merged: list[str] = []
            seen_t: set[str] = set()
            for t in list(title_pool or []) + extra_titles:
                s = str(t or "").strip()
                if s and s not in seen_t:
                    seen_t.add(s)
                    merged.append(s)
            title_pool = merged
    if title_pool and title_excludes:
        filtered_pool = [t for t in title_pool if not phrase_is_blocked(t, title_excludes)]
        if filtered_pool:
            title_pool = filtered_pool
        else:
            warnings.append("滚动避重：标题池近窗紧张，回退未过滤语库")
    title_from_pool = False
    if title_pool:
        picked = pick_title(
            rng,
            title_pool,
            exclude=set(recent),
            pack=pack,
            max_chars_per_line=title_line_cap,
            theme=title_theme,
            exclude_phrases=title_excludes,
            strict_theme=title_strict,
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
            warnings.append("滚动避重：组合标题近窗紧张，已尽力选用")

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

    from engine.pack.copy_diversity_gate import check_title, load_recent_copy_records

    copy_recent = load_recent_copy_records(session, customer_id, limit=50)
    for _div_try in range(3):
        if not check_title(title, copy_recent):
            break
        if title_pool:
            extra_exclude = set(recent)
            extra_exclude.add(title)
            picked = pick_title(
                rng,
                title_pool,
                exclude=extra_exclude,
                pack=pack,
                max_chars_per_line=title_line_cap,
                theme=title_theme,
                exclude_phrases=title_excludes,
                strict_theme=title_strict,
            )
            if picked:
                title = normalize_title_layout(picked, max_chars_per_line=title_line_cap)
                title = strip_all_punctuation(title, keep_newlines=True) or title
                title = normalize_title_layout(title, max_chars_per_line=max_c, max_lines=max_lines)
                warnings.append("copy_diversity:标题近窗碰撞，已重选")
                continue
        if not title_from_pool and template.force_hook_title:
            hook = pick_hook(rng, hooks, exclude=exclude_hooks | {hook}, pack_id=pack_id)
            title = template.title_template.format(
                hook=hook, keyword=keyword, brand=brand, theme=pack_theme
            )
            title = strip_all_punctuation(title, keep_newlines=True) or title
            title = normalize_title_layout(title, max_chars_per_line=max_c, max_lines=max_lines)
            warnings.append("copy_diversity:组合标题近窗碰撞，已重选钩子")
            continue
        warnings.append("copy_diversity:标题近窗紧张，已尽力选用")
        break

    from engine.catalog.industry_pack import load_industry_pack

    industry_data = load_industry_pack(pack_id)
    theme_hint = ""
    for rule in industry_data.get("theme_rules") or []:
        if isinstance(rule, dict) and str(rule.get("theme") or "") == content_theme:
            theme_hint = " ".join(str(k) for k in (rule.get("keywords") or [])[:8])
            break
    semantic_query = f"{pack_theme} {keyword} {hook} {theme_hint}"
    rule_hints: dict[str, Any] = {}
    if production_rules:
        from engine.template.rule_schema import rules_semantic_hints

        rule_hints = rules_semantic_hints(production_rules)
        extra = str(rule_hints.get("semantic_query_extra") or "").strip()
        # stable-product must not retrieve warehouse/loading via rule label dump
        if str(template.name or "") == "stable-product" and extra:
            for bad in (
                "warehouse",
                "loading",
                "delivery",
                "stacking",
                "inventory_full",
                "storefront",
                "shipping",
                "truck",
                "fleet",
            ):
                extra = extra.replace(bad, " ")
            extra = " ".join(extra.split())
        if extra:
            semantic_query = f"{semantic_query} {extra}".strip()
            warnings.append("已应用客户生产规则语义偏好")
        if production_rules.get("pace"):
            warnings.append(f"节奏规则: {production_rules.get('pace')}")
        if production_rules.get("narration_tone"):
            warnings.append(f"旁白语气: {production_rules.get('narration_tone')}")
    cooldown_ids = recent_cliplet_ids(session, template.cliplet_cooldown_recent, customer_id=customer_id)
    if exclude_cliplet_ids:
        cooldown_ids = set(cooldown_ids) | set(exclude_cliplet_ids)
        warnings.append(f"重渲排除片段 {len(exclude_cliplet_ids)} 个")
    if cooldown_ids:
        warnings.append(f"已冷却近期片段 {len(cooldown_ids)} 个")

    used_assets_seed = set(job_used_assets or set()) | set(exclude_asset_uuids or set())
    if exclude_asset_uuids:
        warnings.append(f"重渲排除素材 {len(exclude_asset_uuids)} 条")

    # stable-product = commercial tabletop; hard-prefer product_closeup, ban warehouse/loading bleed
    prefer_scenes_job: list[str] = list(rule_hints.get("prefer_scenes") or [])
    exclude_scenes_job: list[str] = list(rule_hints.get("exclude_scenes") or [])
    if product_reel:
        # Do NOT merge customer prefer that still lists warehouse/loading/delivery —
        # that reinjects ops clips via semantics even when hard_prefer is set.
        prefer_scenes_job = list(PRODUCT_PREFER_SCENES)
        for s in PRODUCT_EXCLUDE_SCENES:
            if s not in exclude_scenes_job:
                exclude_scenes_job.append(s)
        # Drop ops labels if they leaked into exclude-only confusion
        prefer_scenes_job = [
            s for s in prefer_scenes_job if s not in set(PRODUCT_EXCLUDE_SCENES)
        ]
        warnings.append("商品稳镜：硬偏好 product_closeup，排除仓配/装车场景，禁止整片回退")
    elif prefer_scenes_job:
        warnings.append(
            "风格硬切：仅使用规则偏好场景 "
            + "/".join(str(s) for s in prefer_scenes_job[:6])
            + "，禁止回退到其它画面"
        )

    best_clips: list[ClipPlan] = []
    best_score = -1.0
    best_warnings = list(warnings)

    for clip_win in clip_windows:
        paper_blocked_cliplets = recently_used_cliplet_ids(
            session, customer_id=customer_id, limit=clip_win
        )
        if clip_win != rolling_clip_limit and paper_blocked_cliplets is not None:
            warnings.append(f"滚动避重：切片近窗放宽至 {clip_win}")
        step_best_clips: list[ClipPlan] = []
        step_best_score = -1.0
        step_best_warnings = list(warnings)
        for attempt in range(3):
            attempt_rng = random.Random(seed + attempt * 9973 + clip_win * 17)
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
                extra_prefer_scenes=prefer_scenes_job,
                exclude_scenes=exclude_scenes_job,
                allowed_cliplet_ids=(
                    orientation_cliplet_ids
                    & {int(x) for x in topic_intent.get("cliplet_ids", [])}
                    if isinstance(topic_intent, dict)
                    else orientation_cliplet_ids
                ),
                hard_prefer_scenes=bool(prefer_scenes_job) or product_reel,
                reject_ops_description=product_reel,
                forbid_asset_fallback=bool(prefer_scenes_job) or product_reel,
                require_commercial_product=product_reel,
            )
            score = 0.0
            if blob.strip():
                t_emb, _ = embed_text(title)
                d_emb, _ = embed_text(blob)
                score = cosine(t_emb, d_emb)
            if score > step_best_score and clips:
                step_best_score = score
                step_best_clips = clips
                step_best_warnings = attempt_warnings
            if score >= template.consistency_threshold and clips:
                break
        if step_best_clips:
            best_clips = step_best_clips
            best_score = step_best_score
            best_warnings = step_best_warnings
            rolling_clip_limit = clip_win
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

    content_fingerprint: dict[str, Any] = {}
    routed_copy: dict[str, Any] = {}
    if best_clips:
        from engine.content.content_fingerprint import (
            build_content_fingerprint,
            select_recipe_components,
        )

        selected_ids = [int(c.cliplet_id) for c in best_clips if c.cliplet_id]
        semantic_rows = list(
            session.scalars(select(Cliplet).where(Cliplet.id.in_(selected_ids))).all()
        ) if selected_ids else []
        content_fingerprint = build_content_fingerprint(
            semantic_rows,
            pack.data_json if pack else None,
        )
        routed_copy = select_recipe_components(
            content_fingerprint,
            pack.data_json if pack else None,
            seed=seed,
            exclude_phrases=paper_blocked_phrases,
        )
        if pack and pack.schema_version == "suying.customer.content-pack.v2":
            from engine.catalog.keyword_pack import (
                pick_routed_on_screen_title,
                prefer_product_title_theme,
            )
            from engine.pack.narration_script import SHIPPING_CLAIM_TERMS

            title_theme = pack_theme
            title_content = content_theme
            title_excludes = set(paper_blocked_phrases or set())
            title_strict = bool(facet)
            if product_reel:
                title_theme = prefer_product_title_theme(pack, pack_theme)
                title_content = None
                title_strict = True
                title_excludes |= set(SHIPPING_CLAIM_TERMS) | {
                    "仓配",
                    "搬运",
                    "配送",
                    "仓库",
                }
            routed_title = pick_routed_on_screen_title(
                rng,
                pack,
                theme=title_theme,
                content_theme=title_content,
                recent=set(recent),
                exclude_phrases=title_excludes,
                max_chars_per_line=title_line_cap,
                max_lines=max_lines,
                strict_theme=title_strict,
            )
            if routed_title:
                title = normalize_title_layout(
                    routed_title,
                    max_chars_per_line=max_c,
                    max_lines=max_lines,
                )
                best_warnings.append(
                    "标题已按词池选用（路由="
                    f"{content_fingerprint.get('primary_type')}）"
                )
            else:
                best_warnings.append(
                    "保留规划标题（路由="
                    f"{content_fingerprint.get('primary_type')}，词池无可用项）"
                )

    # Final title/copy can change after initial candidate selection. Recheck the
    # exact cliplets, full title, every title line and routed brand/copy phrase.
    from engine.catalog.paper_slip import paper_slip_block_reasons

    block_reasons.extend(
        reason
        for reason in paper_slip_block_reasons(
            session,
            cliplet_ids=[int(c.cliplet_id) for c in best_clips if c.cliplet_id],
            phrases=list(routed_copy.get("component_texts") or []),
            title=title,
            customer_id=customer_id,
            exclude_reservation_key=paper_slip_reservation_key,
        )
        if reason not in block_reasons
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
        from engine.pack.video_lock import apply_lock_to_title_style, load_video_lock

        _lock = load_video_lock(customer_name)
        title_style = apply_lock_to_title_style(title_style, _lock)
    except Exception:
        pass
    from engine.pack.video_lock import overlay_production_rules_on_title_style

    title_style = overlay_production_rules_on_title_style(
        title_style,
        production_rules if isinstance(production_rules, dict) else None,
        orientation=orientation,
    )

    # Product tabletop footage must not keep shipping titles / ultra-short holds.
    # User 2026-08-08: 文案不错但 fast-ship 过快、标题装车/发货 vs 商品镜头脱节。
    try:
        from engine.pack.narration_script import (
            SHIPPING_CLAIM_TERMS,
            script_has_shipping_claim,
            visual_context_flags,
        )

        desc_hints = [str(getattr(c, "description", "") or "") for c in best_clips]
        scene_tags = [str(getattr(c, "scene", "") or "") for c in best_clips]
        vflags = visual_context_flags(
            desc_hints,
            scene_tags=scene_tags,
            theme=content_theme or pack_theme,
        )
        if (vflags.get("product_display") or product_reel) and best_clips:
            ship_ex = set(SHIPPING_CLAIM_TERMS) | {
                "装车就走",
                "现场直出",
                "直出实拍",
                "发货现场",
                "批发装车",
                "仓配",
                "搬运",
                "配送",
                "仓库",
            }
            if script_has_shipping_claim(title) or any(p in (title or "") for p in ship_ex):
                from engine.catalog.keyword_pack import prefer_product_title_theme

                product_theme = prefer_product_title_theme(pack, "产品")
                product_pool = (
                    list_title_pool(
                        pack,
                        theme=product_theme,
                        prefer_rich=True,
                        strict_theme=True,
                    )
                    if pack
                    else []
                )
                exclude_set = set(recent) | set(paper_blocked_phrases or set()) | ship_ex
                alt = None
                if product_pool:
                    alt = pick_title(
                        rng,
                        product_pool,
                        exclude=exclude_set,
                        pack=pack,
                        max_chars_per_line=title_line_cap,
                        theme=product_theme,
                        exclude_phrases=exclude_set,
                        strict_theme=True,
                    )
                if alt and not script_has_shipping_claim(alt) and not any(
                    p in alt for p in ship_ex
                ):
                    title = normalize_title_layout(
                        alt, max_chars_per_line=max_c, max_lines=max_lines
                    )
                    from engine.pack.text_sanitize import strip_all_punctuation

                    title = (
                        strip_all_punctuation(title, keep_newlines=True) or title
                    )
                    best_warnings.append("商品画面：标题已去掉装车/发货表述")
                else:
                    brand_title = f"{brand}实用五金\n现货到店选配"
                    title = normalize_title_layout(
                        brand_title, max_chars_per_line=max_c, max_lines=max_lines
                    )
                    best_warnings.append("商品画面：标题回退为品类向表述")
            best_warnings.append("商品画面：口播对齐稳镜节奏（避免快切装车标题）")
    except Exception as exc:  # noqa: BLE001 — never break production plan
        best_warnings.append(f"商品稳镜护栏跳过: {exc}")

    # Duration align: only compress when over-length. Never upscale past the
    # natural pick — stretched beyond cliplet source freezes the last frame
    # (ffmpeg -t > media) and users report "最后阶段停住".
    try:
        target = float(getattr(template, "target_duration", 0) or 0)
        if best_clips and target >= 8.0:
            got = sum(float(getattr(c, "duration_sec", 0) or 0) for c in best_clips)
            if got > 0.5 and got > target * 1.08:
                scale = float(target) / got
                for c in best_clips:
                    c.duration_sec = round(max(0.8, float(c.duration_sec or 1.0) * scale), 3)
                best_warnings.append(
                    f"时长压缩：计划 {got:.1f}s → 目标 {target:.1f}s（{len(best_clips)} 镜）"
                )
            elif got > 0.5 and got < target * 0.92:
                best_warnings.append(
                    f"素材有效时长不足：计划 {got:.1f}s < 目标 {target:.1f}s，"
                    f"已保留真·时长（{len(best_clips)} 镜，避免末尾定格）"
                )
    except Exception as exc:  # noqa: BLE001
        best_warnings.append(f"时长对齐跳过: {exc}")

    return MontagePlan(
        seed=seed,
        template_name=template.name,
        theme=pack_theme,
        category=category,
        orientation=orientation,
        title=title,
        clips=best_clips,
        warnings=best_warnings,
        blocked=bool(block_reasons),
        block_reasons=block_reasons,
        consistency_score=consistency,
        needs_review=needs_review,
        title_style=title_style,
        content_theme=content_theme,
        content_fingerprint=content_fingerprint,
        recipe_id=str(routed_copy.get("recipe_id") or ""),
        component_ids=list(routed_copy.get("component_ids") or []),
        copy_components=list(routed_copy.get("component_texts") or []),
        allowed_facts=list(routed_copy.get("allowed_facts") or []),
        forbidden_claims=list(routed_copy.get("forbidden_claims") or []),
    )


def plan_to_dict(plan: MontagePlan) -> dict[str, Any]:
    return {
        "seed": plan.seed,
        "template_name": plan.template_name,
        "theme": plan.theme,
        "content_theme": plan.content_theme,
        "category": plan.category,
        "orientation": plan.orientation,
        "title": plan.title,
        "clips": [c.__dict__ for c in plan.clips],
        "warnings": plan.warnings,
        "blocked": plan.blocked,
        "block_reasons": plan.block_reasons,
        "consistency_score": plan.consistency_score,
        "needs_review": plan.needs_review,
        "title_style": plan.title_style,
        "content_fingerprint": plan.content_fingerprint,
        "recipe_id": plan.recipe_id,
        "component_ids": plan.component_ids,
        "allowed_facts": plan.allowed_facts,
        "forbidden_claims": plan.forbidden_claims,
        "copy_components": list(plan.copy_components or []),
    }


def plan_from_dict(data: dict[str, Any]) -> MontagePlan:
    """Rebuild a MontagePlan from sidecar / reuse_montage_plan (GVisualPack2 V5).

    Clips must already be resolved paths; this does not re-query the DB.
    Invalid or empty plan raises ValueError (fail-closed).
    """
    if not isinstance(data, dict):
        raise ValueError("reuse_montage_plan 必须是对象")
    raw_clips = data.get("clips")
    if not isinstance(raw_clips, list) or not raw_clips:
        raise ValueError("reuse_montage_plan.clips 为空")
    clips: list[ClipPlan] = []
    for raw in raw_clips:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("source_path") or "").strip()
        uuid = str(raw.get("asset_uuid") or "").strip()
        if not path or not uuid:
            continue
        try:
            start = float(raw.get("start_sec") or 0)
            duration = float(raw.get("duration_sec") or 0)
        except (TypeError, ValueError):
            continue
        if duration <= 0.05:
            continue
        clips.append(
            ClipPlan(
                slot=str(raw.get("slot") or "body"),
                asset_uuid=uuid,
                source_path=path,
                start_sec=round(start, 3),
                duration_sec=round(duration, 3),
                cliplet_id=(
                    int(raw["cliplet_id"])
                    if raw.get("cliplet_id") is not None
                    else None
                ),
                score=(
                    float(raw["score"])
                    if raw.get("score") is not None
                    else None
                ),
                description=(
                    str(raw.get("description"))
                    if raw.get("description") is not None
                    else None
                ),
                theme=str(raw.get("theme")) if raw.get("theme") is not None else None,
                scene=str(raw.get("scene")) if raw.get("scene") is not None else None,
                objects=list(raw.get("objects") or [])
                if isinstance(raw.get("objects"), list)
                else [],
                actions=list(raw.get("actions") or [])
                if isinstance(raw.get("actions"), list)
                else [],
            )
        )
    if not clips:
        raise ValueError("reuse_montage_plan 无有效 clips")
    return MontagePlan(
        seed=int(data.get("seed") or 0),
        template_name=str(data.get("template_name") or DEFAULT_TEMPLATE.name),
        theme=str(data.get("theme") or "default"),
        category=str(data.get("category") or "default"),
        title=str(data.get("title") or ""),
        clips=clips,
        warnings=list(data.get("warnings") or []) if isinstance(data.get("warnings"), list) else [],
        blocked=bool(data.get("blocked")),
        block_reasons=list(data.get("block_reasons") or [])
        if isinstance(data.get("block_reasons"), list)
        else [],
        orientation=str(data.get("orientation") or "portrait"),
        consistency_score=float(data.get("consistency_score") or 0.0),
        needs_review=bool(data.get("needs_review")),
        title_style=dict(data.get("title_style") or {})
        if isinstance(data.get("title_style"), dict)
        else {},
        content_theme=str(data.get("content_theme") or "default"),
        content_fingerprint=dict(data.get("content_fingerprint") or {})
        if isinstance(data.get("content_fingerprint"), dict)
        else {},
        recipe_id=str(data.get("recipe_id") or ""),
        component_ids=list(data.get("component_ids") or [])
        if isinstance(data.get("component_ids"), list)
        else [],
        copy_components=list(data.get("copy_components") or [])
        if isinstance(data.get("copy_components"), list)
        else [],
        allowed_facts=list(data.get("allowed_facts") or [])
        if isinstance(data.get("allowed_facts"), list)
        else [],
        forbidden_claims=list(data.get("forbidden_claims") or [])
        if isinstance(data.get("forbidden_claims"), list)
        else [],
    )


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
