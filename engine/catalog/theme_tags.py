"""Cliplet content-theme tagging and keyword-pack mapping.

Rules load from industry pack JSON (see engine.catalog.industry_pack).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Asset, Cliplet
from engine.catalog.industry_pack import (
    content_to_pack_map,
    pack_to_content_map,
    theme_rules_for_pack,
)

# Neutral product default: blank pack. Customer profile.industry_pack overrides.
_DEFAULT_PACK = "_blank"



def content_themes(pack_id: str | None = None) -> tuple[str, ...]:
    from engine.catalog.industry_pack import load_industry_pack

    themes = load_industry_pack(pack_id or _DEFAULT_PACK).get("content_themes") or ["default"]
    return tuple(str(t) for t in themes)


CONTENT_THEMES = content_themes(_DEFAULT_PACK)


def classify_theme(
    description: str,
    *,
    asset_name: str = "",
    category: str = "",
    pack_id: str | None = None,
) -> tuple[str, float]:
    """Return (content_theme, confidence_score) from text cues."""
    text = f"{description or ''} {asset_name or ''} {category or ''}"
    text_l = text.lower()
    scores: dict[str, float] = {}
    for theme, keywords in theme_rules_for_pack(pack_id or _DEFAULT_PACK):
        hits = 0
        for kw in keywords:
            if kw.lower() in text_l or kw in text:
                hits += 1
        if hits:
            scores[theme] = float(hits)
    if not scores:
        return "default", 0.0
    best = max(scores, key=scores.get)
    conf = min(1.0, 0.25 + scores[best] * 0.25)
    return best, round(conf, 3)


def pack_theme_to_content(pack_theme: str, *, pack_id: str | None = None) -> str:
    if not pack_theme or pack_theme == "default":
        return "default"
    return pack_to_content_map(pack_id or _DEFAULT_PACK).get(pack_theme, "default")


def content_to_pack_themes(content_theme: str, *, pack_id: str | None = None) -> list[str]:
    return list(content_to_pack_map(pack_id or _DEFAULT_PACK).get(content_theme or "default", ["default"]))


def apply_theme_to_cliplet(
    cliplet: Cliplet,
    asset: Asset | None = None,
    *,
    pack_id: str | None = None,
) -> str:
    """Classify and write theme (+ score) onto a cliplet. Returns theme."""
    name = ""
    cat = cliplet.category or ""
    if asset is not None:
        from pathlib import Path

        name = Path(asset.source_path or "").stem
        cat = cat or (asset.category or "")
    theme, score = classify_theme(
        cliplet.description or "",
        asset_name=name,
        category=cat,
        pack_id=pack_id,
    )
    cliplet.theme = theme
    cliplet.theme_score = score
    return theme


def count_themes(session: Session, *, customer_id: int | None = None) -> Counter[str]:
    stmt = select(Cliplet.theme)
    if customer_id is not None:
        stmt = stmt.join(Asset, Cliplet.asset_id == Asset.id).where(Asset.customer_id == customer_id)
    rows = session.scalars(stmt).all()
    return Counter((t or "default") for t in rows)


def resolve_job_themes(
    session: Session,
    job_theme: str,
    *,
    customer_id: int | None,
    rng: Any,
    min_clips: int = 3,
    pack_id: str | None = None,
    avoid_themes: set[str] | None = None,
) -> tuple[str, str]:
    """Pick (content_theme, pack_theme) for clip selection and keyword pick.

    When job_theme is default, down-weight / skip themes in avoid_themes (GQual 同质冷却).
    """
    jt = (job_theme or "default").strip() or "default"
    if jt != "default":
        content = pack_theme_to_content(jt, pack_id=pack_id)
        if content == "default" and jt not in {"default", "brands", "scenario"}:
            content = "产品"
        return content, jt

    counts = count_themes(session, customer_id=customer_id)
    eligible = [(t, n) for t, n in counts.items() if t != "default" and n >= min_clips]
    if not eligible:
        eligible = [(t, n) for t, n in counts.items() if t != "default" and n > 0]
    if not eligible:
        return "default", "default"
    avoid = {a for a in (avoid_themes or set()) if a and a != "default"}
    preferred = [(t, n) for t, n in eligible if t not in avoid]
    pool = preferred if preferred else eligible
    themes = [t for t, _ in pool]
    weights = [float(n) for _, n in pool]
    content = rng.choices(themes, weights=weights, k=1)[0]
    pack = content_to_pack_themes(content, pack_id=pack_id)[0]
    return content, pack


def backfill_cliplet_themes(
    session: Session,
    *,
    customer_id: int | None = None,
    limit: int = 2000,
    force: bool = False,
    pack_id: str | None = None,
) -> dict[str, Any]:
    """Tag existing cliplets. Skip those already tagged unless force=True."""
    stmt = select(Cliplet).order_by(Cliplet.id.asc()).limit(limit)
    if customer_id is not None:
        stmt = (
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(Asset.customer_id == customer_id)
            .order_by(Cliplet.id.asc())
            .limit(limit)
        )
    rows = list(session.scalars(stmt).all())
    updated = 0
    skipped = 0
    by_theme: Counter[str] = Counter()
    for row in rows:
        theme_ok = (row.theme or "").strip() not in ("", "default")
        scene_ok = (row.scene or "").strip() not in ("", "default")
        if not force and theme_ok and scene_ok:
            by_theme[row.theme] += 1
            skipped += 1
            continue
        asset = session.get(Asset, row.asset_id)
        from engine.catalog.semantic_tags import annotate_cliplet

        meta = annotate_cliplet(row, asset, pack_id=pack_id)
        theme = str(meta.get("theme") or row.theme or "default")
        by_theme[theme] += 1
        updated += 1
    session.commit()
    return {
        "scanned": len(rows),
        "updated": updated,
        "skipped": skipped,
        "by_theme": dict(by_theme),
    }
