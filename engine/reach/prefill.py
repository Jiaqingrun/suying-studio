"""Prefill Reach queue items from a publish_pack directory (G5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from engine.pack.publish import ensure_ai_generated_disclosure, limit_platform_hashtags
from engine.reach.business_scope import VIDEO_PLATFORMS
from engine.reach.queue import PLATFORMS, ReachQueueItem, enqueue, mark_awaiting_human


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_pack_prefill(pack_dir: Path, *, locale: str = "zh") -> dict[str, Any]:
    """Read video path + per-platform copy from an exported publish_pack."""
    pack_dir = Path(pack_dir)
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"publish_pack 不存在: {pack_dir}")

    manifest = _load_json(pack_dir / "manifest.json")
    copy_name = "copy.zh.json" if locale.startswith("zh") else f"copy.{locale}.json"
    if not (pack_dir / copy_name).is_file() and locale != "zh":
        copy_name = "copy.zh.json"
    copy_doc = _load_json(pack_dir / copy_name)
    platforms = (copy_doc.get("platforms") or {}) if copy_doc else {}
    if isinstance(platforms, dict):
        normalized: dict[str, Any] = {}
        for plat, raw in platforms.items():
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            body = str(entry.get("body") or entry.get("caption") or "")
            tags = list(entry.get("hashtags") or [])
            if str(plat).strip().lower() == "kuaishou":
                body, tags = limit_platform_hashtags("kuaishou", body, tags)
            if str(plat).strip().lower() in VIDEO_PLATFORMS:
                body = ensure_ai_generated_disclosure(body)
            entry["body"] = body
            entry["hashtags"] = tags
            normalized[plat] = entry
        platforms = normalized

    video = pack_dir / "video.mp4"
    if not video.is_file():
        # fallback to manifest source
        src = manifest.get("source_output")
        video_path = str(src) if src else ""
    else:
        video_path = str(video.resolve())

    return {
        "pack_dir": str(pack_dir.resolve()),
        "video_path": video_path,
        "brand": copy_doc.get("brand") or manifest.get("brand") or "",
        "theme": copy_doc.get("theme") or manifest.get("theme") or "",
        "locale": copy_doc.get("locale") or locale,
        "platforms": platforms,
        "manifest": manifest,
    }


def enqueue_from_pack(
    session: Session,
    *,
    customer_id: int,
    pack_dir: Path,
    platforms: list[str] | None = None,
    locale: str = "zh",
    output_id: int | None = None,
    mark_ready: bool = True,
) -> list[ReachQueueItem]:
    """
    Create one queue row per platform from publish_pack copy.
    Items go to ``awaiting_human`` when mark_ready (prefill done; human clicks publish).
    """
    prefill = load_pack_prefill(Path(pack_dir), locale=locale)
    plat_map: dict[str, Any] = prefill["platforms"]
    wanted = [p.lower() for p in (platforms or list(PLATFORMS))]
    if output_id is None:
        raise ValueError("从物料包入队必须提供 output_id，禁止旧包脱离成片事实发布")
    frozen_platforms = [plat for plat in wanted if plat in PLATFORMS]
    from engine.reach.publication_lifecycle import freeze_publication_group, resolve_target

    group, _ = freeze_publication_group(
        session,
        customer_id=customer_id,
        output_id=output_id,
        targets=[{"platform": plat, "account_key": ""} for plat in frozen_platforms],
        source="publish_pack",
    )
    items: list[ReachQueueItem] = []
    for plat in wanted:
        if plat not in PLATFORMS:
            continue
        entry = plat_map.get(plat) or {}
        title = str(entry.get("title") or prefill.get("brand") or "速影成片")
        body = str(entry.get("body") or "")
        body, hashtags = limit_platform_hashtags(
            plat,
            body,
            list(entry.get("hashtags") or []),
        )
        body = ensure_ai_generated_disclosure(body)
        item = enqueue(
            session,
            customer_id=customer_id,
            platform=plat,
            title=title,
            body=body,
            video_path=prefill["video_path"],
            pack_dir=prefill["pack_dir"],
            output_id=output_id,
            copy_json={
                "locale": prefill["locale"],
                "platform": {**entry, "body": body, "hashtags": hashtags},
                "hashtags": hashtags,
            },
            note="prefilled_from_publish_pack",
            status="queued",
            publication_group_id=group.id,
            publication_target_id=resolve_target(
                session, group_id=group.id, platform=plat
            ).id,
        )
        if mark_ready:
            item = mark_awaiting_human(session, item)
        items.append(item)
    if not items:
        raise ValueError("未创建任何平台队列项（检查 platforms / pack copy）")
    return items
