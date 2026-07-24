"""Semi-auto open official creator portals (G5).

Human-in-the-loop only: we open a URL / optional local browser profile hint,
never automate login, never click publish, never bypass detection.
"""

from __future__ import annotations

import json
import os
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from engine.catalog.db import ReachQueueItem
from engine.reach.queue import _append_log

# Official / creator entry points (public sites). Not scraper targets.
OFFICIAL_ENTRY: dict[str, dict[str, str]] = {
    "douyin": {
        "label": "抖音创作者中心",
        "url": "https://creator.douyin.com/",
    },
    "channels": {
        "label": "微信视频号助手",
        "url": "https://channels.weixin.qq.com/",
    },
    "xhs": {
        "label": "小红书创作者服务平台",
        "url": "https://creator.xiaohongshu.com/",
    },
    "wechat_mp": {
        "label": "微信公众平台",
        "url": "https://mp.weixin.qq.com/",
    },
}


def entry_for_platform(platform: str) -> dict[str, str]:
    p = (platform or "").strip().lower()
    if p not in OFFICIAL_ENTRY:
        raise ValueError(f"不支持的平台: {platform}")
    return dict(OFFICIAL_ENTRY[p])


def browser_config_from_profile(profile: dict[str, Any] | None, platform: str) -> dict[str, Any]:
    """Per-customer browser hints from profile_json.reach.browsers[platform]."""
    profile = profile or {}
    reach = profile.get("reach") or {}
    browsers = reach.get("browsers") or {}
    cfg = browsers.get(platform) or browsers.get("default") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "browser_app": str(cfg.get("browser_app") or ""),  # e.g. "Google Chrome"
        "profile_dir": str(cfg.get("profile_dir") or ""),  # local user-data hint only
        "note": str(cfg.get("note") or ""),
    }


def write_paste_card(item: ReachQueueItem, dest: Path | None = None) -> Path:
    """Write a human paste card (title/body/video path) beside pack or cache."""
    if dest is None:
        if item.pack_dir:
            dest = Path(item.pack_dir) / f"reach_paste_{item.platform}.txt"
        else:
            dest = Path(item.video_path).parent / f"reach_paste_{item.id}_{item.platform}.txt"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    entry = entry_for_platform(item.platform)
    lines = [
        "速影 · 触达粘贴卡（人点发布，系统不会自动发）",
        f"平台: {entry['label']} ({item.platform})",
        f"入口: {entry['url']}",
        "",
        f"标题:\n{item.title}",
        "",
        f"正文:\n{item.body}",
        "",
        f"视频路径:\n{item.video_path}",
        "",
        "请用已登录的本人账号在官方页面手动上传并点击发布。",
        "本产品不提供绕检测、矩阵养号或自动点击发布。",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def open_official_entry(
    *,
    platform: str,
    profile: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Open the official URL in the default browser (or return plan if dry_run)."""
    entry = entry_for_platform(platform)
    bcfg = browser_config_from_profile(profile, platform)
    result: dict[str, Any] = {
        "platform": platform,
        "label": entry["label"],
        "url": entry["url"],
        "opened": False,
        "dry_run": dry_run,
        "browser": bcfg,
        "auto_publish": False,
        "human_in_loop": True,
        "method": "webbrowser",
    }
    if dry_run:
        return result

    url = entry["url"]
    app = (bcfg.get("browser_app") or "").strip()
    # Optional: macOS `open -a App url` when customer configured a browser name.
    if app and os.uname().sysname == "Darwin":
        cmd = ["open", "-a", app, url]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            result["opened"] = True
            result["method"] = f"open -a {app}"
            return result
        result["browser_open_error"] = (proc.stderr or proc.stdout or "").strip()

    ok = webbrowser.open(url, new=2)
    result["opened"] = bool(ok)
    result["method"] = "webbrowser.open"
    return result


def prepare_and_open(
    session,  # noqa: ANN001
    item: ReachQueueItem,
    *,
    profile: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write paste card, open official entry, log on the queue item."""
    from datetime import datetime, timezone

    card = write_paste_card(item)
    open_info = open_official_entry(platform=item.platform, profile=profile, dry_run=dry_run)
    _append_log(
        item,
        "open_official_entry",
        url=open_info.get("url"),
        opened=open_info.get("opened"),
        dry_run=dry_run,
        paste_card=str(card),
    )
    item.updated_at = datetime.now(timezone.utc)
    # Keep awaiting_human if already there; queued → awaiting_human
    if item.status == "queued":
        from engine.reach.queue import set_status

        set_status(session, item, "awaiting_human", note=item.note or "opened_official_entry")
    else:
        session.commit()
        session.refresh(item)

    return {
        "ok": True,
        "item_id": item.id,
        "paste_card": str(card),
        "open": open_info,
        "prefill": {
            "title": item.title,
            "body": item.body,
            "video_path": item.video_path,
            "pack_dir": item.pack_dir,
        },
        "auto_publish": False,
        "human_in_loop": True,
        "disclaimer": "仅打开官方入口并给出粘贴卡；发布须本人点击，不做自动发帖/反检测。",
    }
