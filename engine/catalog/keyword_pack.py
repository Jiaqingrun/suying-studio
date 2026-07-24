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
    customer = session.scalar(select(Customer).where(Customer.name == customer_name))
    if not customer:
        customer = Customer(name=customer_name, profile_json=data.get("company_info", {}))
        session.add(customer)
        session.flush()
    else:
        customer.profile_json = data.get("company_info", customer.profile_json)

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
