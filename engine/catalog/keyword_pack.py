from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.catalog.db import Customer, KeywordPack, KeywordUsage


def parse_keyword_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    # Extract JSON block from markdown if present
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(text)


def import_keyword_pack(session: Session, customer_name: str, data: dict[str, Any], pack_name: str = "default") -> KeywordPack:
    # App-owned keys must survive keyword reload (logo toggle, VO prefs, reach, etc.)
    _preserve = ("brand", "expression", "reach", "industry_pack")
    company = data.get("company_info") if isinstance(data.get("company_info"), dict) else {}
    customer = session.scalar(select(Customer).where(Customer.name == customer_name))
    if not customer:
        customer = Customer(name=customer_name, profile_json=dict(company))
        session.add(customer)
        session.flush()
    else:
        prev = dict(customer.profile_json) if isinstance(customer.profile_json, dict) else {}
        merged = {**company}
        for key in _preserve:
            if key in prev:
                merged[key] = prev[key]
        customer.profile_json = merged

    pack = KeywordPack(
        customer_id=customer.id,
        name=pack_name,
        data_json=data,
        version=int(data.get("meta", {}).get("version", 1)),
    )
    session.add(pack)
    session.commit()
    session.refresh(pack)
    return pack


def get_active_pack(session: Session, customer_name: str, pack_name: str = "default") -> KeywordPack | None:
    customer = session.scalar(select(Customer).where(Customer.name == customer_name))
    if not customer:
        return None
    return session.scalar(
        select(KeywordPack)
        .where(KeywordPack.customer_id == customer.id, KeywordPack.name == pack_name)
        .order_by(KeywordPack.id.desc())
    )


def list_keywords_by_theme(
    pack: KeywordPack,
    theme: str,
    *,
    fallback_default: bool = True,
) -> list[dict[str, Any]]:
    pool = pack.data_json.get("keyword_pool", {})
    themes = pool.get("themes", {})
    if theme in themes:
        return themes[theme].get("keywords", [])
    if fallback_default and "default" in themes:
        return themes["default"].get("keywords", [])
    core = pool.get("core_phrases", [])
    if core:
        return [{"text": k, "weight": 1} for k in core]
    flat = pool.get("flat_keywords", [])
    return [{"text": k, "weight": 1} for k in flat]


def pick_keyword(
    session: Session,
    pack: KeywordPack,
    theme: str,
    seed_rng,
    cooldown_days: int = 7,
    *,
    customer_id: int | None = None,
    theme_fallbacks: list[str] | None = None,
) -> str:
    themes_try = [theme, *(theme_fallbacks or [])]
    # de-dupe preserve order
    seen_t: set[str] = set()
    ordered: list[str] = []
    for t in themes_try:
        if t and t not in seen_t:
            seen_t.add(t)
            ordered.append(t)

    candidates: list[dict[str, Any]] = []
    used_theme = theme
    for t in ordered:
        rows = list_keywords_by_theme(pack, t, fallback_default=False)
        if rows:
            candidates = rows
            used_theme = t
            break
    if not candidates:
        candidates = list_keywords_by_theme(pack, "default", fallback_default=True)
        used_theme = "default"
    if not candidates:
        return pack.data_json.get("company_info", {}).get("positioning_one_liner", "品牌宣传")

    cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)
    stmt = select(KeywordUsage).where(KeywordUsage.used_at >= cutoff, KeywordUsage.theme == used_theme)
    if customer_id is not None:
        stmt = stmt.where(KeywordUsage.customer_id == customer_id)
    recent = {row.keyword for row in session.scalars(stmt).all()}

    weighted: list[tuple[str, float]] = []
    for item in candidates:
        text = item["text"] if isinstance(item, dict) else str(item)
        weight = float(item.get("weight", 1)) if isinstance(item, dict) else 1.0
        if text in recent:
            weight *= 0.1
        weighted.append((text, max(weight, 0.01)))

    total = sum(w for _, w in weighted)
    r = seed_rng.random() * total
    acc = 0.0
    for text, w in weighted:
        acc += w
        if r <= acc:
            return text
    return weighted[-1][0]


def record_keyword_usage(
    session: Session,
    keyword: str,
    theme: str,
    job_id: int | None,
    *,
    customer_id: int | None = None,
) -> None:
    session.add(KeywordUsage(keyword=keyword, theme=theme, job_id=job_id, customer_id=customer_id))
    session.commit()


def check_compliance(text: str, pack: KeywordPack) -> list[str]:
    hits: list[str] = []
    blocked = pack.data_json.get("compliance", {}).get("blocked_terms", [])
    for term in blocked:
        if term and term in text:
            hits.append(term)
    return hits


def list_title_pool(
    pack: KeywordPack | None,
    *,
    theme: str | None = None,
    prefer_rich: bool = True,
) -> list[str]:
    """On-screen title corpus from keyword_pool.title_pool (not spoken).

    When theme is set, prefer matching categories; prefer_rich boosts longer lines.
    """
    if pack is None:
        return []
    pool = (pack.data_json.get("keyword_pool") or {}).get("title_pool")
    if not pool:
        return []
    if isinstance(pool, list):
        items = [str(x).strip() for x in pool if str(x).strip()]
        return _rank_titles(items, prefer_rich=prefer_rich)

    if not isinstance(pool, dict):
        return []

    cats = pool.get("categories") if isinstance(pool.get("categories"), dict) else {}
    theme_key = (theme or "").strip()
    preferred_cat_names = _theme_title_categories(theme_key)

    themed: list[str] = []
    general: list[str] = []
    seen: set[str] = set()

    def _add(bucket: list[str], raw: Any) -> None:
        t = str(raw).strip()
        if t and t not in seen:
            seen.add(t)
            bucket.append(t)

    if cats and preferred_cat_names:
        for name in preferred_cat_names:
            arr = cats.get(name)
            if isinstance(arr, list):
                for x in arr:
                    _add(themed, x)
        for name, arr in cats.items():
            if name in preferred_cat_names or not isinstance(arr, list):
                continue
            for x in arr:
                _add(general, x)
    elif cats:
        for arr in cats.values():
            if isinstance(arr, list):
                for x in arr:
                    _add(general, x)

    flat = pool.get("items")
    if isinstance(flat, list) and not themed and not general:
        for x in flat:
            _add(general, x)

    ordered = _rank_titles(themed, prefer_rich=prefer_rich) + _rank_titles(general, prefer_rich=prefer_rich)
    out: list[str] = []
    seen2: set[str] = set()
    for t in ordered:
        if t not in seen2:
            seen2.add(t)
            out.append(t)
    return out


def _theme_title_categories(theme: str) -> list[str]:
    """Map job/pack theme → title_pool category names (prefer rich / on-theme)."""
    t = (theme or "").strip().lower()
    mapping: dict[str, list[str]] = {
        "仓配": ["配货仓配", "发货装车", "长句丰富", "双行标题", "双行占满", "现货提货", "时效温和"],
        "配送": ["发货装车", "配货仓配", "工地场景", "长句丰富", "双行标题", "双行占满", "时效温和"],
        "门店": ["品牌开场", "现货提货", "价值表达", "长句丰富", "双行标题", "双行占满"],
        "施工机械": ["品类轻提", "工地场景", "价值表达", "长句丰富", "双行占满"],
        "产品": ["品类轻提", "价值表达", "品牌开场", "长句丰富", "双行占满"],
        "服务": ["服务体验", "价值表达", "时效温和", "长句丰富", "双行占满"],
    }
    for key, cats in mapping.items():
        if key in t or t in key:
            return cats
    # Mix short + long + dual — layout chosen randomly at pick time
    return ["配货仓配", "发货装车", "工地场景", "价值表达", "长句丰富", "双行标题", "双行占满", "服务体验"]


def normalize_title_layout(
    title: str,
    *,
    max_chars_per_line: int = 10,
    max_lines: int = 2,
    force_dual: bool = False,
) -> str:
    """Clamp title lines. Keep single-line short titles as-is unless force_dual.

    - Existing newlines preserved (each line clamped).
    - Single line longer than max_chars_per_line → wrap to dual.
    - force_dual=False (default): short singles stay one line (random pool decides).
    """
    raw = (title or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return raw
    line_cap = max(4, int(max_chars_per_line))
    max_lines = max(1, int(max_lines))

    if "\n" in raw:
        parts = [p.strip() for p in raw.split("\n") if p.strip()][:max_lines]
        return "\n".join(p[:line_cap] for p in parts)

    compact = raw.replace(" ", "").replace("　", "")
    budget = line_cap * max_lines
    if len(compact) > budget:
        compact = compact[:budget]
    # Short / mid single line: keep one row unless forced
    if not force_dual and len(compact) <= line_cap:
        return compact
    if max_lines <= 1:
        return compact[:line_cap]
    if len(compact) <= line_cap:
        return compact
    # Overflow → balanced dual wrap
    if len(compact) >= line_cap + 4:
        break_at = line_cap
    else:
        break_at = max(1, (len(compact) + 1) // 2)
    if len(compact) - break_at == 1 and break_at > 1:
        break_at -= 1
    line1 = compact[:break_at][:line_cap]
    line2 = compact[break_at:][:line_cap]
    return "\n".join(ln for ln in (line1, line2) if ln)


# Back-compat alias
def ensure_full_dual_title(
    title: str,
    *,
    max_chars_per_line: int = 10,
    max_lines: int = 2,
) -> str:
    return normalize_title_layout(
        title,
        max_chars_per_line=max_chars_per_line,
        max_lines=max_lines,
        force_dual=False,
    )


def _rank_titles(titles: list[str], *, prefer_rich: bool) -> list[str]:
    if not prefer_rich or not titles:
        return list(titles)

    def score(t: str) -> tuple[int, int]:
        compact = t.replace("\n", "").replace(" ", "")
        n = len(compact)
        # Mild richness only — do not force dual-line dominance
        rich = 0
        if 6 <= n <= 20:
            rich += 2
        elif n <= 4:
            rich -= 1
        if "\n" in t:
            rich += 1
        return (rich, n)

    return sorted(titles, key=score, reverse=True)


def title_pool_meta(pack: KeywordPack | None) -> dict[str, Any]:
    if pack is None:
        return {}
    pool = (pack.data_json.get("keyword_pool") or {}).get("title_pool")
    if not isinstance(pool, dict):
        return {}
    meta = pool.get("meta") if isinstance(pool.get("meta"), dict) else {}
    cats = pool.get("categories") if isinstance(pool.get("categories"), dict) else {}
    return {
        "max_chars_per_line": int(meta.get("max_chars_per_line") or 12),
        "spoken": bool(meta.get("spoken", False)),
        "purpose": meta.get("purpose"),
        "category_counts": {k: len(v) if isinstance(v, list) else 0 for k, v in cats.items()},
    }


def pick_title(
    seed_rng,
    titles: list[str],
    *,
    exclude: set[str] | None = None,
    pack: KeywordPack | None = None,
    max_chars_per_line: int = 12,
    theme: str | None = None,
) -> str | None:
    """Pick a compliant on-screen title; prefer theme-matched + richer unused lines."""
    # Re-list with theme bias when pack available
    if pack is not None and theme:
        themed = list_title_pool(pack, theme=theme, prefer_rich=True)
        if themed:
            titles = themed
    pool = [t for t in titles if t]
    if exclude:
        filtered = [t for t in pool if t not in exclude]
        if filtered:
            pool = filtered
    if not pool:
        return None

    compliant: list[str] = []
    for t in pool:
        if pack and check_compliance(t, pack):
            continue
        lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
        if not lines or len(lines) > 2:
            continue
        # Allow auto-wrap later: single long line ok if total fits 2× max
        if len(lines) == 1 and len(lines[0]) > max_chars_per_line * 2:
            continue
        if len(lines) == 1 and len(lines[0]) > max_chars_per_line:
            # keep as single; ensure_full_dual_title / render will wrap to fill
            compliant.append(lines[0])
            continue
        if any(len(ln) > max_chars_per_line for ln in lines):
            continue
        compliant.append("\n".join(lines))
    if not compliant:
        t = seed_rng.choice(pool)
        return normalize_title_layout(t, max_chars_per_line=max_chars_per_line)

    # Random single vs dual when both exist; otherwise random from pool
    singles = [t for t in compliant if "\n" not in t]
    duals = [t for t in compliant if "\n" in t]
    if singles and duals:
        bucket = singles if seed_rng.random() < 0.5 else duals
    else:
        bucket = compliant
    picked = seed_rng.choice(bucket)
    return normalize_title_layout(picked, max_chars_per_line=max_chars_per_line)
