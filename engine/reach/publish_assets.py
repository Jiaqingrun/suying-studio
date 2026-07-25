"""Resolve + gate publish assets: video, copy body, cover slots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.reach.cover_templates import resolve_covers


class PublishAssetsError(ValueError):
    """Raised when hard gate fails (missing copy or covers)."""


def _load_copy(pack_dir: Path, platform: str, locale: str = "zh") -> dict[str, str]:
    """Load title/body for platform from copy.{locale}.json or paste card."""
    plat = (platform or "").strip().lower()
    copy_path = pack_dir / f"copy.{locale}.json"
    title = ""
    body = ""
    if copy_path.is_file():
        try:
            data = json.loads(copy_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        platforms = data.get("platforms") or {}
        entry = platforms.get(plat) or {}
        title = str(entry.get("title") or data.get("source_title") or "").strip()
        body = str(entry.get("body") or "").strip()
        if not body:
            # some packs put caption under platforms
            body = str(entry.get("caption") or "").strip()
    if not title or not body:
        paste = pack_dir / f"reach_paste_{plat}.txt"
        if paste.is_file():
            text = paste.read_text(encoding="utf-8")
            mode = None
            buf: list[str] = []
            parsed: dict[str, str] = {}
            for line in text.splitlines():
                if line.startswith("标题:"):
                    if mode:
                        parsed[mode] = "\n".join(buf).strip()
                    mode, buf = "title", []
                    continue
                if line.startswith("正文:"):
                    if mode:
                        parsed[mode] = "\n".join(buf).strip()
                    mode, buf = "body", []
                    continue
                if line.startswith("视频路径:") or line.startswith("封面"):
                    if mode:
                        parsed[mode] = "\n".join(buf).strip()
                    mode = None
                    continue
                if mode and not line.startswith("请用") and not line.startswith("本产品"):
                    buf.append(line)
            if mode:
                parsed[mode] = "\n".join(buf).strip()
            title = title or parsed.get("title", "")
            body = body or parsed.get("body", "")
    return {"title": title.strip(), "body": body.strip()}


def require_publish_assets(
    *,
    platform: str,
    pack_dir: str | Path,
    data_root: Path,
    title: str | None = None,
    body: str | None = None,
    template_id: str | None = None,
    locale: str = "zh",
) -> dict[str, Any]:
    """Hard gate: video + non-empty body + all cover slots.

    Returns dict with video, covers, title, body, cover_meta.
    Raises PublishAssetsError on failure.
    """
    plat = (platform or "").strip().lower()
    pdir = Path(pack_dir)
    if not pdir.is_dir():
        raise PublishAssetsError(f"publish_pack 不存在: {pdir}")

    video = pdir / "video.mp4"
    if not video.is_file():
        raise PublishAssetsError(f"缺少 video.mp4: {pdir}")

    copy = _load_copy(pdir, plat, locale=locale)
    final_title = (title if title is not None else copy["title"]).strip()
    final_body = (body if body is not None else copy["body"]).strip()
    if not final_body:
        raise PublishAssetsError(f"缺文案：平台 {plat} 正文/描述为空，禁止发布")
    if plat == "channels" and not final_title:
        raise PublishAssetsError("缺文案：视频号短标题为空，禁止发布")

    cover_meta = resolve_covers(
        data_root,
        platform=plat,
        pack_dir=pdir,
        template_id=template_id,
    )
    # App selected template is mandatory when present — never silently fall back to pack covers.
    from engine.reach.cover_templates import load_index

    selected = (load_index(data_root).get("selected_id") or "").strip() or None
    effective_tid = (template_id or selected or "").strip() or None
    if effective_tid:
        if cover_meta.get("source") != "template" or cover_meta.get("template_id") != effective_tid:
            raise PublishAssetsError(
                f"必须使用 App 当前选用封面模板「{effective_tid}」的槽位图，"
                f"当前来源={cover_meta.get('source')} template={cover_meta.get('template_id')}；"
                "请在 App「封面设置」补齐该平台槽位后再发"
            )
        if not cover_meta.get("ok"):
            raise PublishAssetsError(
                cover_meta.get("error")
                or f"App 封面模板 {effective_tid} 槽位不齐（{plat}）"
            )
    if not cover_meta.get("ok"):
        raise PublishAssetsError(cover_meta.get("error") or f"封面槽位不齐（{plat}）")

    covers = [Path(p) for p in (cover_meta.get("covers") or [])]
    for i, c in enumerate(covers):
        if not c.is_file():
            raise PublishAssetsError(f"封面槽位 {i} 文件不存在: {c}")

    return {
        "ok": True,
        "platform": plat,
        "pack_dir": str(pdir),
        "video": str(video),
        "covers": [str(c) for c in covers],
        "title": final_title or final_body.split("\n", 1)[0][:30],
        "body": final_body,
        "cover_meta": cover_meta,
    }
