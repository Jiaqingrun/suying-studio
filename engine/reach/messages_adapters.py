"""Read-only bounded-history adapters for the eight supported official portals."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from engine.reach.cdp_client import CdpError, CdpSession, find_tab_ws


class MessageAdapterError(RuntimeError):
    code = "adapter_error"


class LoginRequired(MessageAdapterError):
    code = "login_required"


class VerificationRequired(MessageAdapterError):
    code = "verification_required"


class AdapterChanged(MessageAdapterError):
    code = "adapter_changed"


class PageLoadTimeout(MessageAdapterError):
    """Official host responded but the message route did not settle in time."""

    code = "page_load_timeout"


class IdentityUnstable(MessageAdapterError):
    """Visible rows do not expose a stable remote identity."""

    code = "identity_unstable"


@dataclass(frozen=True)
class PlatformMessageSpec:
    platform: str
    label: str
    host: str
    message_url: str
    adapter_version: str = "history.v2"
    kinds: tuple[str, ...] = ("dm", "comment", "notice")
    row_selector: str = (
        '[data-message-id],[data-comment-id],[data-conversation-id],'
        '[class*="conversation" i],[class*="chat-item" i],'
        '[class*="message-item" i],[class*="comment-item" i]'
    )
    allowed_hosts: tuple[str, ...] = ()
    readonly_verified: bool = False
    reply_supported: bool = True


# Official inbox entry points. SPAs may settle on sibling routes; path matching is soft.
SPECS: dict[str, PlatformMessageSpec] = {
    "douyin": PlatformMessageSpec(
        "douyin",
        "抖音",
        "creator.douyin.com",
        "https://creator.douyin.com/creator-micro/data/following/chat",
        allowed_hosts=("creator.douyin.com", "www.douyin.com"),
    ),
    "channels": PlatformMessageSpec(
        "channels",
        "视频号",
        "channels.weixin.qq.com",
        "https://channels.weixin.qq.com/platform/message",
        readonly_verified=True,
    ),
    "xhs": PlatformMessageSpec(
        "xhs",
        "小红书",
        "www.xiaohongshu.com",
        "https://www.xiaohongshu.com/chat",
        kinds=("dm",),
        row_selector=".xhs-im-conv-item,[data-conv-id]",
        allowed_hosts=("www.xiaohongshu.com", "creator.xiaohongshu.com"),
        readonly_verified=True,
    ),
    "kuaishou": PlatformMessageSpec(
        "kuaishou",
        "快手",
        "cp.kuaishou.com",
        "https://cp.kuaishou.com/notification/list",
        kinds=("notice",),
        row_selector=(
            ".notify-main .auto-load-list > .item,"
            '[data-message-id],[data-conversation-id],[class*="conversation" i]'
        ),
        allowed_hosts=("cp.kuaishou.com", "www.kuaishou.com"),
        readonly_verified=True,
        reply_supported=False,
    ),
    "baijiahao": PlatformMessageSpec(
        "baijiahao",
        "百家号",
        "baijiahao.baidu.com",
        "https://baijiahao.baidu.com/builder/rc/commentmanage/comment/all",
        kinds=("comment",),
        row_selector=(
            '[data-comment-id],tr,[class*="comment-item" i],[class*="commentItem"]'
        ),
        readonly_verified=True,
    ),
    "toutiao": PlatformMessageSpec(
        "toutiao",
        "头条号",
        "mp.toutiao.com",
        "https://mp.toutiao.com/profile_v4/personal/message",
        row_selector=(
            '[data-message-id],[data-comment-id],[class*="message-item" i],'
            '[class*="comment-item" i],[role="listitem"]'
        ),
        readonly_verified=True,
    ),
    "zhihu": PlatformMessageSpec(
        "zhihu",
        "知乎",
        "www.zhihu.com",
        "https://www.zhihu.com/messages",
        row_selector=(
            '[data-message-id],[data-notification-id],[class*="Notification"],'
            '[class*="Message"],[role="listitem"]'
        ),
        readonly_verified=True,
    ),
    "wechat_mp": PlatformMessageSpec(
        "wechat_mp",
        "微信公众号",
        "mp.weixin.qq.com",
        "https://mp.weixin.qq.com/",
        row_selector=(
            '[data-message-id],[data-comment-id],tr,[class*="message" i],'
            '[class*="comment" i]'
        ),
    ),
}

_INBOX_PATH_KEYWORDS = (
    "message",
    "msg",
    "comment",
    "notice",
    "notif",
    "inbox",
    "chat",
    "im",
    "会话",
    "消息",
    "评论",
    "私信",
    "通知",
    "互动",
)


def spec_for(platform: str) -> PlatformMessageSpec:
    key = (platform or "").strip().lower()
    if key not in SPECS:
        raise ValueError(f"不支持的消息平台: {platform}")
    return SPECS[key]


def validate_official_url(platform: str, url: str) -> str:
    spec = spec_for(platform)
    parsed = urlparse((url or "").strip())
    hosts = set(spec.allowed_hosts or (spec.host,))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"仅允许 {spec.label} 官方 HTTPS 域名 {' / '.join(sorted(hosts))}")
    return parsed.geturl()


def canonical_message_url(platform: str) -> str:
    return validate_official_url(platform, spec_for(platform).message_url)


def normalize_identity_url(platform: str, url: str) -> str:
    """Strip volatile query/fragment material before deriving a remote identity."""
    parsed = urlparse(validate_official_url(platform, url))
    stable_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower()
        not in {
            "token",
            "lang",
            "timestamp",
            "t",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "from",
            "enter_from",
        }
    ]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip("/") or "/",
            "",
            urlencode(stable_query),
            "",
        )
    )


def message_external_key(
    platform: str,
    adapter_version: str,
    identity_source: str,
    identity_value: str,
    kind: str,
) -> str:
    """Hash immutable remote identity; mutable summary/time never participate."""
    material = json.dumps(
        [platform, adapter_version, identity_source, identity_value, kind],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:40]


def platform_capabilities() -> list[dict[str, Any]]:
    """Public, auditable capability registry used by API/UI and tests."""
    return [
        {
            "platform": spec.platform,
            "label": spec.label,
            "message_url": spec.message_url,
            "adapter_version": spec.adapter_version,
            "kinds": list(spec.kinds),
            "readonly_verified": spec.readonly_verified,
            "reply_supported": spec.reply_supported,
        }
        for spec in SPECS.values()
    ]


def parse_event_time(raw: str, *, now: datetime | None = None) -> str | None:
    """Best-effort normalized UTC time; unknown labels remain non-authoritative."""
    text = " ".join((raw or "").split()).strip()
    if not text:
        return None
    current = now or datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=current.tzinfo)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    if text in {"刚刚", "现在", "just now"}:
        return current.isoformat()
    relative = re.fullmatch(r"(\d{1,3})\s*(分钟|小时|天)前", text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = (
            timedelta(minutes=amount)
            if unit == "分钟"
            else timedelta(hours=amount)
            if unit == "小时"
            else timedelta(days=amount)
        )
        return (current - delta).isoformat()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%m-%d %H:%M", "%m-%d", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        year = parsed.year if "%Y" in fmt else current.year
        month = parsed.month if "%m" in fmt else current.month
        day = parsed.day if "%d" in fmt else current.day
        hour = parsed.hour if "%H" in fmt else 0
        minute = parsed.minute if "%M" in fmt else 0
        candidate = datetime(year, month, day, hour, minute, tzinfo=current.tzinfo)
        if candidate > current + timedelta(days=1):
            candidate = candidate.replace(year=candidate.year - 1)
        return candidate.astimezone(timezone.utc).isoformat()
    return None


class MessageAdapter(Protocol):
    spec: PlatformMessageSpec

    def scan(self, cdp_http: str) -> dict[str, Any]: ...


_EXTRACT_JS = r"""
(() => {
  const bodyText = (document.body?.innerText || '').slice(0, 12000);
  const lower = bodyText.toLowerCase();
  const login = /扫码登录|密码登录|手机号登录|请先登录|立即登录|log\s*in\s*to|sign\s*in\s*to/.test(lower);
  const verify = /安全验证|滑块|图形验证码|请输入验证码|完成验证|captcha|verify you are human/.test(lower);
  const notFound = /页面不见了|页面不存在|page\s*not\s*found/.test(bodyText);
  const inboxHints = /消息中心|粉丝私信|私信管理|互动消息|对话列表|会话列表|message center|private message|all messages|粉丝消息/.test(bodyText);
  const rowSelector = __ROW_SELECTOR__;
  const markerSelector = [
    '[data-unread="true"]',
    '[aria-label*="未读"]',
    '.unread',
    '.is-unread',
    '[class*="unread" i]',
    '[class*="Unread"]',
    '[class*="badge" i]',
    '[class*="Badge"]',
    '[class*="red-dot" i]',
    '[class*="reddot" i]',
    '[class*="dot-num" i]',
    '[class*="notice-num" i]',
    '[class*="msg-count" i]',
  ].join(',');
  const candidates = [...document.querySelectorAll(markerSelector)];
  const rows = [];
  const seen = new Set();
  const pushRow = (node, forced = {}) => {
    if (!node) return;
    const vueItem = node.__vue__?.$props?.item || null;
    const text = (
      (vueItem && [vueItem.title, vueItem.desc, vueItem.context].filter(Boolean).join(' '))
      || node.innerText
      || ''
    ).replace(/\s+/g, ' ').trim();
    if (!text || text.length < 3) return;
    if (/^(未读|\d+)$/.test(text)) return;
    const link = node.matches('a[href]') ? node : node.querySelector('a[href]');
    let href = link?.href || location.href;
    if (vueItem?.url) {
      try {
        const candidate = new URL(String(vueItem.url), location.href);
        if (
          candidate.hostname === location.hostname
          || candidate.hostname.endsWith('.kuaishou.com')
        ) href = candidate.href;
      } catch (_) {}
    }
    const nativeId = String(
      forced.native_id
      || vueItem?.msgId
      || vueItem?.messageId
      || vueItem?.noticeId
      || node.dataset?.messageId
      || node.dataset?.commentId
      || node.dataset?.notificationId
      || node.dataset?.conversationId
      || node.dataset?.convId
      || node.dataset?.id
      || node.getAttribute?.('data-key')
      || node.getAttribute?.('data-row-key')
      || ''
    ).trim().slice(0, 240);
    const timeNode = node.querySelector('time,[datetime],[class*="time" i],[class*="date" i]');
    let eventTime = String(
      forced.event_time
      || vueItem?.sendTime
      || vueItem?.createTime
      || vueItem?.timestamp
      || timeNode?.getAttribute?.('datetime')
      || timeNode?.getAttribute?.('title')
      || timeNode?.innerText
      || ''
    ).replace(/\s+/g, ' ').trim().slice(0, 80);
    if (/^\d{12,13}$/.test(eventTime)) {
      eventTime = new Date(Number(eventTime)).toISOString();
    }
    const senderNode = node.querySelector(
      '[class*="name"],[class*="Name"],[class*="sender"],[class*="Sender"],[class*="author"],[class*="Author"],[class*="nickname"],[class*="Nickname"],[data-testid*="name"]'
    );
    const summaryNode = node.querySelector(
      '[class*="snippet"],[class*="Snippet"],[class*="preview"],[class*="Preview"],[class*="summary"],[class*="Summary"],[class*="content"],[class*="Content"],[class*="desc"],[class*="Desc"]'
    );
    const sender = (
      forced.sender || vueItem?.title || senderNode?.innerText || ''
    ).replace(/\s+/g, ' ').trim().slice(0, 128);
    let summary = (
      forced.summary || vueItem?.desc || vueItem?.context || summaryNode?.innerText || text
    ).replace(/\s+/g, ' ').trim().slice(0, 280);
    if (sender && summary.startsWith(sender)) {
      summary = summary.slice(sender.length).trim();
    }
    if (!summary) return;
    const blob = `${sender} ${summary} ${text}`.toLowerCase();
    let kind = forced.kind || 'other';
    if (/点赞|赞了|liked|likes\b|给了个赞|喜欢了你/.test(blob)) kind = 'like';
    else if (/私信|私聊|站内信|direct\s*message|\bdm\b|会话/.test(blob)) kind = 'dm';
    else if (/评论|回复了|留言|commented|replied/.test(blob)) kind = 'comment';
    const platformUnread = Boolean(
      forced.platform_unread
      || vueItem?.hasRead === false
      || node.matches?.('[data-unread="true"],.unread,.is-unread,[class*="unread" i]')
      || node.querySelector?.(markerSelector)
    );
    const key = `${nativeId}|${href}|${eventTime}|${kind}`;
    if (seen.has(key)) return;
    seen.add(key);
    rows.push({
      native_id: nativeId,
      event_time: eventTime,
      sender,
      summary,
      reply_url: href,
      kind,
      platform_unread: platformUnread,
    });
  };
  // Kuaishou's creator notification center exposes the unread count in the
  // top notification badge, while list rows intentionally have no unread
  // class. The list is newest-first, so only the first N rows are unread.
  const isKuaishouNotifications =
    location.hostname === 'cp.kuaishou.com' && location.pathname.startsWith('/notification/');
  let expectedUnread = 0;
  let platformReady = true;
  if (isKuaishouNotifications) {
    const badge = document
      .querySelector('.notifications')
      ?.closest('.el-badge')
      ?.querySelector('.el-badge__content');
    expectedUnread = Math.max(0, Math.min(99, Number.parseInt((badge?.innerText || '').trim(), 10) || 0));
    const notificationRows = [...document.querySelectorAll('.notify-main .auto-load-list > .item')];
    platformReady = Boolean(document.querySelector('.notify-main .auto-load-list'))
      || /暂无(?:通知|消息)/.test(bodyText);
    for (const node of notificationRows.slice(0, expectedUnread)) {
      const tag = (node.querySelector('.tag')?.innerText || '快手通知').trim();
      const title = (node.querySelector('.item-header .title')?.innerText || '').trim();
      const detail = (node.querySelector('.item-main')?.innerText || '').trim();
      pushRow(node, {
        sender: tag,
        summary: [title, detail].filter(Boolean).join('：'),
        kind: 'notice',
        platform_unread: true,
      });
    }
  }
  for (const marker of candidates.slice(0, 120)) {
    const markerText = (marker?.innerText || '').replace(/\s+/g, ' ').trim();
    const explicitUnread = marker?.dataset?.unread === 'true'
      || /未读|unread/i.test(marker?.getAttribute?.('aria-label') || '')
      || /unread/i.test(marker?.className || '');
    const countUnread = /^\d{1,3}$/.test(markerText) && Number(markerText) > 0;
    if (!explicitUnread && !countUnread) continue;
    const node = marker.closest(rowSelector);
    if (!node) continue;
    pushRow(node);
  }
  // History pass: collect visible rows without clicking conversations. Platform
  // selectors are deliberately narrow and must expose a stable id/link before
  // the Python normalizer accepts them.
  for (const node of [...document.querySelectorAll(rowSelector)].slice(0, 300)) {
    pushRow(node);
  }
  return {
    state: rows.length || inboxHints || (isKuaishouNotifications && platformReady)
      ? 'inbox'
      : 'other',
    url: location.href,
    title: document.title,
    ready_state: document.readyState,
    login,
    verify,
    not_found: notFound,
    inbox_hints: inboxHints,
    rows,
    candidate_count: candidates.length,
    expected_unread: expectedUnread,
    platform_ready: platformReady,
    body_present: Boolean(document.body),
  };
})()
"""

_SCROLL_JS = r"""
(() => {
  const candidates = [...document.querySelectorAll(
    '[role="list"],[class*="list" i],[class*="scroll" i],[class*="conversation" i]'
  )].filter((node) => node.scrollHeight > node.clientHeight + 20);
  const target = candidates.sort((a, b) => b.clientHeight - a.clientHeight)[0]
    || document.scrollingElement;
  if (!target) return {state: 'other', moved: false, top: 0, height: 0};
  const before = target.scrollTop;
  target.scrollTop = Math.min(target.scrollHeight, before + Math.max(target.clientHeight * 0.85, 360));
  const moved = target.scrollTop > before;
  return {
    state: moved ? 'scrolled' : 'other',
    moved,
    top: target.scrollTop,
    height: target.scrollHeight,
  };
})()
"""


class GenericDomMessageAdapter:
    """Conservative bounded-history DOM adapter; never opens a conversation."""

    def __init__(self, platform: str):
        self.spec = spec_for(platform)

    def _path_matches(self, candidate_url: str) -> bool:
        expected = urlparse(self.spec.message_url).path.rstrip("/")
        current = urlparse(candidate_url).path.rstrip("/")
        if not expected:
            return True
        if current.startswith(expected):
            return True
        # Soft match: last meaningful segment (SPA often strips a parent path).
        expected_tail = expected.rsplit("/", 1)[-1]
        current_tail = current.rsplit("/", 1)[-1]
        if expected_tail and expected_tail == current_tail:
            return True
        # SPAs for video/content portals often land on sibling inbox routes.
        blob = f"{current} {candidate_url}".lower()
        return any(k in blob for k in _INBOX_PATH_KEYWORDS)

    def _page_settled(self, candidate_url: str, candidate: dict[str, Any]) -> bool:
        if self._path_matches(candidate_url):
            return True
        return bool(candidate.get("inbox_hints"))

    def scan(self, cdp_http: str) -> dict[str, Any]:
        # Ensure we are on the official message URL; reused Chrome may still be
        # sitting on the creator home after 「打开登录」.
        try:
            from urllib.parse import urlparse as _up

            from engine.reach.chrome_runtime import _cdp_open_url

            port = int(_up(cdp_http).port or 0)
            if port:
                _cdp_open_url(port, self.spec.message_url)
        except Exception:
            pass

        deadline = time.monotonic() + 36
        raw: dict[str, Any] | None = None
        page_url = ""
        settled_once = False
        saw_official_host = False
        last_error_hint = ""
        renav_remaining = 1
        while time.monotonic() < deadline:
            try:
                ws, current_url = find_tab_ws(url_substr=self.spec.host, cdp_http=cdp_http)
                validate_official_url(self.spec.platform, current_url)
                saw_official_host = True
                with CdpSession(ws, timeout=10) as session:
                    candidate = session.evaluate_in_frames(
                        _EXTRACT_JS.replace(
                            "__ROW_SELECTOR__", json.dumps(self.spec.row_selector)
                        )
                    )
            except (CdpError, OSError, ValueError) as exc:
                last_error_hint = str(exc)[:160]
                time.sleep(0.5)
                continue
            if not isinstance(candidate, dict):
                time.sleep(0.5)
                continue
            if candidate.get("login"):
                raise LoginRequired("官方页尚未登录，请在该 Chrome profile 中人工登录")
            if candidate.get("verify"):
                raise VerificationRequired("官方页要求人工完成验证码/安全验证")
            if candidate.get("not_found"):
                raise AdapterChanged("平台当前未提供该消息页，需重新校准官方入口")
            try:
                candidate_url = validate_official_url(
                    self.spec.platform, str(candidate.get("url") or current_url)
                )
            except ValueError:
                time.sleep(0.5)
                continue
            if not self._page_settled(candidate_url, candidate):
                if renav_remaining > 0:
                    renav_remaining -= 1
                    try:
                        from urllib.parse import urlparse as _up

                        from engine.reach.chrome_runtime import _cdp_open_url

                        port = int(_up(cdp_http).port or 0)
                        if port:
                            _cdp_open_url(port, self.spec.message_url)
                    except Exception:
                        pass
                time.sleep(0.8)
                continue
            if not candidate.get("body_present") or candidate.get("ready_state") == "loading":
                time.sleep(0.5)
                continue
            if not candidate.get("platform_ready"):
                time.sleep(0.5)
                continue
            expected_unread = int(candidate.get("expected_unread") or 0)
            if expected_unread > len(candidate.get("rows") or []):
                time.sleep(0.5)
                continue
            raw = candidate
            page_url = candidate_url
            if settled_once:
                break
            settled_once = True
            time.sleep(1.2)
        if raw is None or not page_url:
            if saw_official_host:
                raise PageLoadTimeout(
                    "官方消息页未在时限内完成加载（已登录会话可能仍在跳转）；将自动重试，无需反复点确认"
                    + (f"：{last_error_hint}" if last_error_hint else "")
                )
            raise PageLoadTimeout(
                "未找到官方消息页标签，请确认该账号 Chrome 已打开且可访问创作者中心"
            )
        # Bounded history pass. Re-open the same tab for each scroll step so a
        # transient SPA rerender cannot leave a stale CDP object behind.
        collected: list[dict[str, Any]] = list(raw.get("rows") or [])
        boundary_reached = False
        raw_seen = {
            json.dumps(
                [
                    item.get("native_id"),
                    item.get("reply_url"),
                    item.get("event_time"),
                    item.get("kind"),
                ],
                ensure_ascii=False,
            )
            for item in collected
            if isinstance(item, dict)
        }
        for _ in range(8):
            if len(collected) >= 200:
                boundary_reached = True
                break
            try:
                ws, _current_url = find_tab_ws(
                    url_substr=self.spec.host, cdp_http=cdp_http
                )
                with CdpSession(ws, timeout=10) as session:
                    moved = session.evaluate_in_frames(_SCROLL_JS)
                    time.sleep(0.35)
                    page = session.evaluate_in_frames(
                        _EXTRACT_JS.replace(
                            "__ROW_SELECTOR__", json.dumps(self.spec.row_selector)
                        )
                    )
            except (CdpError, OSError, ValueError):
                break
            if isinstance(page, dict):
                for item in page.get("rows") or []:
                    if not isinstance(item, dict):
                        continue
                    raw_key = json.dumps(
                        [
                            item.get("native_id"),
                            item.get("reply_url"),
                            item.get("event_time"),
                            item.get("kind"),
                        ],
                        ensure_ascii=False,
                    )
                    if raw_key in raw_seen:
                        continue
                    raw_seen.add(raw_key)
                    collected.append(item)
            if not isinstance(moved, dict) or not moved.get("moved"):
                boundary_reached = True
                break
        raw["rows"] = collected[:200]
        rows: list[dict[str, Any]] = []
        like_count = 0
        unstable_count = 0
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
            kind = str(item.get("kind") or "other").strip().lower()
            if kind == "other" and self.spec.kinds == ("dm",):
                kind = "dm"
            if kind == "other" and self.spec.kinds == ("notice",):
                kind = "notice"
            if kind not in ("comment", "dm", "notice", "like", "other"):
                kind = "other"
            if kind == "like":
                like_count += 1
                continue  # likes: panel stats only, never inbox/notify
            normalized_url = normalize_identity_url(self.spec.platform, reply_url)
            native_id = str(item.get("native_id") or "").strip()[:240]
            event_time = str(item.get("event_time") or "").strip()[:80]
            event_at = parse_event_time(event_time)
            if native_id:
                identity = ["native_id", native_id, kind]
                identity_source = "native_id"
            elif normalized_url != normalize_identity_url(
                self.spec.platform, page_url
            ):
                identity = ["resource_url", normalized_url, kind]
                identity_source = "resource_url"
            else:
                unstable_count += 1
                continue
            rows.append(
                {
                    "external_key": message_external_key(
                        self.spec.platform,
                        self.spec.adapter_version,
                        identity_source,
                        str(identity[1]),
                        kind,
                    ),
                    "sender": sender,
                    "summary": summary,
                    "reply_url": reply_url,
                    "kind": kind,
                    "event_time_raw": event_time,
                    "event_at": event_at,
                    "identity_source": identity_source,
                    "platform_unread": bool(item.get("platform_unread")),
                    "source": "dom_bounded_history",
                    "confidence": 0.8 if native_id else 0.7,
                }
            )
        if not rows and unstable_count and raw.get("rows"):
            raise IdentityUnstable(
                "消息列表未暴露稳定消息/评论标识，已停止入库以避免已读项复活"
            )
        newest = rows[0] if rows else None
        return {
            "platform": self.spec.platform,
            "page_url": page_url,
            "messages": rows,
            "like_count": like_count,
            "source": "dom_bounded_history",
            "confidence": max((float(row["confidence"]) for row in rows), default=0.0),
            "explicit_unread_candidates": int(raw.get("candidate_count") or 0),
            "adapter_version": self.spec.adapter_version,
            "readonly_verified": self.spec.readonly_verified,
            "snapshot_complete": boundary_reached,
            "complete": boundary_reached,
            "boundary_reached": boundary_reached,
            "cursor": (
                json.dumps(
                    {
                        "adapter_version": self.spec.adapter_version,
                        "external_key": newest["external_key"],
                        "event_at": newest.get("event_at"),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if newest
                else None
            ),
            "unstable_count": unstable_count,
        }


def adapter_for(platform: str) -> MessageAdapter:
    return GenericDomMessageAdapter(platform)
