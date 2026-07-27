"""Read-only message-page adapters for the seven supported official portals."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

from engine.reach.cdp_client import CdpError, CdpSession, find_tab_ws


class MessageAdapterError(RuntimeError):
    code = "adapter_error"


class LoginRequired(MessageAdapterError):
    code = "login_required"


class VerificationRequired(MessageAdapterError):
    code = "verification_required"


class AdapterChanged(MessageAdapterError):
    code = "adapter_changed"


@dataclass(frozen=True)
class PlatformMessageSpec:
    platform: str
    label: str
    host: str
    message_url: str


SPECS: dict[str, PlatformMessageSpec] = {
    "douyin": PlatformMessageSpec(
        "douyin", "抖音", "creator.douyin.com", "https://creator.douyin.com/creator-micro/message"
    ),
    "channels": PlatformMessageSpec(
        "channels", "视频号", "channels.weixin.qq.com", "https://channels.weixin.qq.com/platform/message"
    ),
    "xhs": PlatformMessageSpec(
        "xhs", "小红书", "creator.xiaohongshu.com", "https://creator.xiaohongshu.com/notification"
    ),
    "kuaishou": PlatformMessageSpec(
        "kuaishou", "快手", "cp.kuaishou.com", "https://cp.kuaishou.com/message"
    ),
    "baijiahao": PlatformMessageSpec(
        "baijiahao",
        "百家号",
        "baijiahao.baidu.com",
        "https://baijiahao.baidu.com/builder/rc/commentmanage/comment/all",
    ),
    "toutiao": PlatformMessageSpec(
        "toutiao",
        "头条号",
        "mp.toutiao.com",
        "https://mp.toutiao.com/profile_v4/personal/message",
    ),
    "zhihu": PlatformMessageSpec(
        "zhihu", "知乎", "www.zhihu.com", "https://www.zhihu.com/messages"
    ),
}


def spec_for(platform: str) -> PlatformMessageSpec:
    key = (platform or "").strip().lower()
    if key not in SPECS:
        raise ValueError(f"不支持的消息平台: {platform}")
    return SPECS[key]


def validate_official_url(platform: str, url: str) -> str:
    spec = spec_for(platform)
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or parsed.hostname != spec.host or parsed.username or parsed.password:
        raise ValueError(f"仅允许 {spec.label} 官方 HTTPS 域名 {spec.host}")
    return parsed.geturl()


class MessageAdapter(Protocol):
    spec: PlatformMessageSpec

    def scan(self, cdp_http: str) -> dict[str, Any]: ...


_EXTRACT_JS = r"""
(() => {
  const bodyText = (document.body?.innerText || '').slice(0, 10000);
  const lower = bodyText.toLowerCase();
  const login = /扫码登录|密码登录|手机登录|登录后|log\s*in|sign\s*in/.test(lower);
  const verify = /验证码|安全验证|滑块|captcha|verify you are human/.test(lower);
  const notFound = /页面不见了|页面不存在|404\\s*(not\\s*found)?|page\\s*not\\s*found/.test(lower);
  const candidates = [...document.querySelectorAll(
    '[data-unread="true"],[aria-label*="未读"],.unread,.is-unread,[class*="unread"],[class*="Unread"]'
  )];
  const rows = [];
  const seen = new Set();
  for (const marker of candidates.slice(0, 100)) {
    const node = marker.closest(
      'li,[role="listitem"],tr,a,article,[class*="ChatUserListItem"],[class*="conversation"],[class*="Conversation"],[class*="session"],[class*="Session"]'
    );
    if (!node) continue;
    const text = (node.innerText || '').replace(/\s+/g, ' ').trim();
    if (!text || text.length < 3 || /^(未读|\d+)$/.test(text)) continue;
    const link = node.matches('a[href]') ? node : node.querySelector('a[href]');
    const href = link?.href || location.href;
    const senderNode = node.querySelector(
      '[class*="name"],[class*="Name"],[class*="sender"],[class*="Sender"],[class*="author"],[class*="Author"],[data-testid*="name"]'
    );
    const summaryNode = node.querySelector(
      '[class*="snippet"],[class*="Snippet"],[class*="preview"],[class*="Preview"],[class*="summary"],[class*="Summary"]'
    );
    const sender = (senderNode?.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 128);
    const summary = ((summaryNode?.innerText || text)).replace(/\s+/g, ' ').trim().slice(0, 280);
    const key = `${href}|${sender}|${summary}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({sender, summary, reply_url: href});
  }
  return {
    url: location.href,
    title: document.title,
    ready_state: document.readyState,
    login,
    verify,
    not_found: notFound,
    rows,
    candidate_count: candidates.length,
    body_present: Boolean(document.body),
  };
})()
"""


class GenericDomMessageAdapter:
    """Conservative heuristic: only explicit unread DOM markers produce rows."""

    def __init__(self, platform: str):
        self.spec = spec_for(platform)

    def scan(self, cdp_http: str) -> dict[str, Any]:
        deadline = time.monotonic() + 12
        raw: dict[str, Any] | None = None
        page_url = ""
        settled_once = False
        while time.monotonic() < deadline:
            try:
                ws, current_url = find_tab_ws(url_substr=self.spec.host, cdp_http=cdp_http)
                validate_official_url(self.spec.platform, current_url)
                with CdpSession(ws, timeout=10) as session:
                    candidate = session.evaluate(_EXTRACT_JS)
            except (CdpError, OSError, ValueError):
                time.sleep(0.4)
                continue
            if not isinstance(candidate, dict):
                time.sleep(0.4)
                continue
            if candidate.get("verify"):
                raise VerificationRequired("官方页要求人工完成验证码/安全验证")
            if candidate.get("login"):
                raise LoginRequired("官方页尚未登录，请在该 Chrome profile 中人工登录")
            if candidate.get("not_found"):
                raise AdapterChanged("平台当前未提供该消息页，需重新校准官方入口")
            try:
                candidate_url = validate_official_url(
                    self.spec.platform, str(candidate.get("url") or current_url)
                )
            except ValueError:
                time.sleep(0.4)
                continue
            expected_path = urlparse(self.spec.message_url).path.rstrip("/")
            current_path = urlparse(candidate_url).path.rstrip("/")
            if expected_path and not current_path.startswith(expected_path):
                time.sleep(0.4)
                continue
            if not candidate.get("body_present") or candidate.get("ready_state") == "loading":
                time.sleep(0.4)
                continue
            raw = candidate
            page_url = candidate_url
            if settled_once:
                break
            settled_once = True
            time.sleep(1.0)
        if raw is None or not page_url:
            raise AdapterChanged("官方消息页未在时限内完成加载，请人工确认登录状态或页面改版")
        rows: list[dict[str, Any]] = []
        for item in raw.get("rows") or []:
            if not isinstance(item, dict):
                continue
            reply_url = validate_official_url(
                self.spec.platform, urljoin(page_url, str(item.get("reply_url") or page_url))
            )
            sender = " ".join(str(item.get("sender") or "").split())[:128]
            summary = " ".join(str(item.get("summary") or "").split())[:280]
            if not summary:
                continue
            key_material = json.dumps(
                [self.spec.platform, sender, summary, reply_url], ensure_ascii=False
            ).encode("utf-8")
            rows.append(
                {
                    "external_key": hashlib.sha256(key_material).hexdigest()[:40],
                    "sender": sender,
                    "summary": summary,
                    "reply_url": reply_url,
                    "source": "dom_unread_marker",
                    "confidence": 0.65,
                }
            )
        return {
            "platform": self.spec.platform,
            "page_url": page_url,
            "messages": rows,
            "source": "dom_unread_marker",
            "confidence": 0.65 if rows else 0.0,
            "explicit_unread_candidates": int(raw.get("candidate_count") or 0),
        }


def adapter_for(platform: str) -> MessageAdapter:
    return GenericDomMessageAdapter(platform)
