"""Keyword pack resource stats for ops dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.catalog.db import KeywordPack, KeywordUsage


def keyword_stats(
    session: Session,
    *,
    customer_id: int,
    keyword_pack_path: str | None = None,
) -> dict[str, Any]:
    packs = list(
        session.scalars(select(KeywordPack).where(KeywordPack.customer_id == customer_id)).all()
    )
    active = packs[-1] if packs else None
    themes: dict[str, int] = {}
    total = 0
    version = None
    if active and active.data_json:
        if isinstance(active.data_json, dict):
            data = active.data_json
        else:
            try:
                data = json.loads(active.data_json)
            except (TypeError, json.JSONDecodeError):
                data = {}
        version = (data.get("meta") or {}).get("version") or active.version
        # common shapes: themes map or flat keywords list
        raw_themes = data.get("themes") or data.get("theme_keywords") or {}
        if isinstance(raw_themes, dict):
            for k, v in raw_themes.items():
                n = len(v) if isinstance(v, list) else 1
                themes[str(k)] = n
                total += n
        kws = data.get("keywords") or data.get("hooks") or []
        if isinstance(kws, list) and total == 0:
            total = len(kws)
            themes["default"] = total
    path = keyword_pack_path or (active.name if active else None)
    path_exists = bool(path and Path(str(keyword_pack_path or "")).is_file()) if keyword_pack_path else False
    if keyword_pack_path and Path(keyword_pack_path).is_file() and total == 0:
        try:
            data = json.loads(Path(keyword_pack_path).read_text(encoding="utf-8"))
            version = (data.get("meta") or {}).get("version") or version
            raw_themes = data.get("themes") or {}
            if isinstance(raw_themes, dict):
                for k, v in raw_themes.items():
                    n = len(v) if isinstance(v, list) else 1
                    themes[str(k)] = n
                    total += n
            elif isinstance(data.get("keywords"), list):
                total = len(data["keywords"])
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
