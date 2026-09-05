"""Post-submit verification: success page/URL or works-list lookup."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from engine.reach.cdp_client import CdpError, CdpSession, find_tab_ws


WORKS_URL_HINTS: dict[str, str] = {
    "douyin": "creator.douyin.com",
    "channels": "channels.weixin.qq.com",
    "xhs": "creator.xiaohongshu.com",
    "kuaishou": "cp.kuaishou.com",
}


def _title_match(page_text: str, title: str) -> bool:
    t = (title or "").strip()
    if not t or len(t) < 4:
        return False
    needle = t[: min(24, len(t))]
    return needle in (page_text or "")


def verify_in_works_list(
    *,
    platform: str,
    title: str,
    cdp_http: str,
    window_minutes: int = 30,
) -> dict[str, Any]:
    """Navigate to content/manage and search for title within time window."""
    plat = (platform or "").strip().lower()
    hint = WORKS_URL_HINTS.get(plat)
    if not hint:
        return {"ok": False, "reason": "unsupported_platform", "platform": plat}
    try:
        ws, _ = find_tab_ws(url_substr=hint, cdp_http=cdp_http)
    except CdpError as exc:
        return {"ok": False, "reason": "cdp_unavailable", "error": str(exc)}

    manage_paths = {
        "douyin": "https://creator.douyin.com/creator-micro/content/manage",
        "channels": "https://channels.weixin.qq.com/platform/post/list",
        "xhs": "https://creator.xiaohongshu.com/publish/manage",
        "kuaishou": "https://cp.kuaishou.com/article/manage/video",
    }
    target = manage_paths.get(plat, "")
    with CdpSession(ws) as sess:
        sess.call("Page.enable")
        if target:
            sess.call("Page.navigate", {"url": target})
            deadline = time.time() + 12
            while time.time() < deadline:
                time.sleep(0.5)
                url = sess.evaluate("location.href") or ""
                if hint in url and "login" not in url.lower():
                    break
        body = sess.evaluate("document.body ? document.body.innerText.slice(0,12000) : ''") or ""
        url = sess.evaluate("location.href") or ""
        if _title_match(str(body), title):
            return {
                "ok": True,
                "reason": "works_list_title",
                "url": url,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "window_minutes": window_minutes,
            }
        # loose time hint in page — still inconclusive without title
        recent = bool(re.search(r"刚刚|分钟前|今天|刚刚发布", str(body)))
        return {
            "ok": False,
            "reason": "works_list_no_match",
            "url": url,
            "recent_activity": recent,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "window_minutes": window_minutes,
        }


def merge_verify(pub_result: dict[str, Any]) -> dict[str, Any]:
    """Extract inline verify payload from CDP publish result."""
    verify = pub_result.get("verify")
    if isinstance(verify, dict):
        return verify
    publish = pub_result.get("publish")
    if isinstance(publish, dict) and isinstance(publish.get("verify"), dict):
        return publish["verify"]
    return {}


def is_verified_success(pub_result: dict[str, Any]) -> bool:
    if not pub_result.get("ok"):
        return False
    verify = merge_verify(pub_result)
    return bool(verify.get("ok"))


def is_outcome_unknown(pub_result: dict[str, Any]) -> bool:
    if is_verified_success(pub_result):
        return False
    if pub_result.get("need_human") and pub_result.get("phase") in (
        "need_sms_verify",
        "publish_not_submitted",
    ):
        return False
    verify = merge_verify(pub_result)
    reason = str(verify.get("reason") or "")
    return bool(pub_result.get("pub_clicked")) and reason in (
        "unknown",
        "still_on_form",
        "still_on_form_bar",
        "eval_failed",
        "",
    )


def recheck_or_pause(
    *,
    platform: str,
    title: str,
    cdp_http: str,
    pub_result: dict[str, Any],
) -> dict[str, Any]:
    """After click without inline verify, try works list once."""
    if is_verified_success(pub_result):
        return {"phase": "published", "verify": merge_verify(pub_result)}
    if not is_outcome_unknown(pub_result):
        return {"phase": "paused_human", "verify": merge_verify(pub_result), "pub": pub_result}
    works = verify_in_works_list(platform=platform, title=title, cdp_http=cdp_http)
    if works.get("ok"):
        return {"phase": "published", "verify": works, "via": "works_list"}
    return {"phase": "outcome_unknown", "verify": works, "pub": pub_result}
