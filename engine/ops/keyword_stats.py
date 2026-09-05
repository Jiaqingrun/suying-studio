"""Keyword pack resource stats for ops dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.catalog.db import KeywordPack, KeywordUsage


def _as_dict(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _theme_keyword_count(block: Any) -> int:
    """Count usable keywords for a theme block (list or v4 {keywords:[...]})."""
    if isinstance(block, list):
        return len(block)
    if isinstance(block, dict):
        kws = block.get("keywords")
        if isinstance(kws, list):
            return len(kws)
        # Non-list theme metadata still proves the theme exists.
        return 1 if block else 0
    if block is None:
        return 0
    return 1


def _count_themes(data: dict[str, Any]) -> tuple[dict[str, int], int]:
    """Return (theme->count, total) for legacy top-level and v4 keyword_pool shapes."""
    themes: dict[str, int] = {}
    total = 0

    pool = data.get("keyword_pool") if isinstance(data.get("keyword_pool"), dict) else {}
    candidates: list[Any] = [
        data.get("themes"),
        data.get("theme_keywords"),
        pool.get("themes") if isinstance(pool, dict) else None,
    ]
    for raw_themes in candidates:
        if not isinstance(raw_themes, dict) or not raw_themes:
            continue
        for key, block in raw_themes.items():
            n = _theme_keyword_count(block)
            themes[str(key)] = n
            total += n
        if total > 0 or themes:
            break

    if total == 0:
        for key in ("keywords", "hooks", "flat_keywords", "core_phrases"):
            bucket = data.get(key)
            if bucket is None and isinstance(pool, dict):
                bucket = pool.get(key)
            if isinstance(bucket, list) and bucket:
                total = len(bucket)
                themes["default"] = total
                break

    return themes, total


def keyword_stats(
    session: Session,
    *,
    customer_id: int,
    keyword_pack_path: str | None = None,
) -> dict[str, Any]:
    packs = list(
        session.scalars(select(KeywordPack).where(KeywordPack.customer_id == customer_id)).all()
    )
    active = next((p for p in reversed(packs) if getattr(p, "status", None) == "active"), None)
    if active is None and packs:
        active = packs[-1]

    themes: dict[str, int] = {}
    total = 0
    version = None
    if active and active.data_json:
        data = _as_dict(active.data_json)
        version = (data.get("meta") or {}).get("version") or active.version
        themes, total = _count_themes(data)

    path_exists = bool(keyword_pack_path and Path(str(keyword_pack_path)).is_file())
    if keyword_pack_path and Path(keyword_pack_path).is_file() and total == 0:
        try:
            data = _as_dict(Path(keyword_pack_path).read_text(encoding="utf-8"))
            version = (data.get("meta") or {}).get("version") or version
            themes, total = _count_themes(data)
            path_exists = True
        except Exception:
            pass

    cooled = 0
    try:
        cooled = int(
            session.scalar(
                select(func.count())
                .select_from(KeywordUsage)
                .where(KeywordUsage.customer_id == customer_id)
            )
            or 0
        )
    except Exception:
        cooled = 0

    return {
        "ok": True,
        "total": total,
        "themes": themes,
        "theme_count": len(themes),
        "cooldown_records": cooled,
        "version": version,
        "pack_path": keyword_pack_path,
        "pack_path_exists": path_exists,
        "db_packs": len(packs),
        "empty": total <= 0,
    }
