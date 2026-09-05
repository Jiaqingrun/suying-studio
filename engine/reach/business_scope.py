"""Business-domain boundary for video Reach vs article Content.

Scopes own separate Chrome dirs, message accounts, queues and reply drafts.
"""

from __future__ import annotations

import re
from typing import Literal

BusinessScope = Literal["video", "content"]

SCOPE_VIDEO: BusinessScope = "video"
SCOPE_CONTENT: BusinessScope = "content"
SCOPES: frozenset[str] = frozenset({SCOPE_VIDEO, SCOPE_CONTENT})

# Message + publish Chrome platforms that belong to video Reach.
VIDEO_PLATFORMS: frozenset[str] = frozenset({"douyin", "channels", "xhs", "kuaishou"})

# Article / message platforms that belong to soft-article Content.
CONTENT_PLATFORMS: frozenset[str] = frozenset(
    {
        "baijiahao",
        "toutiao",
        "zhihu",
        "wechat_mp",
        "sohu",
        "netease",
        "penguin",
        "website",
        "csdn",
        "juejin",
        "cnblogs",
        "baike",
        "baidu_ziyuan",
        "so_360",
    }
)

# Platforms that may bind a message-scan Chrome account.
CONTENT_MESSAGE_PLATFORMS: frozenset[str] = frozenset(
    {"baijiahao", "toutiao", "zhihu", "wechat_mp"}
)
_CUSTOM_CONTENT_PLATFORM = re.compile(r"^custom_[a-f0-9]{12}$")


def normalize_scope(scope: str | None) -> BusinessScope:
    key = (scope or "").strip().lower()
    if key not in SCOPES:
        raise ValueError(f"非法业务域: {scope}（仅允许 video|content）")
    return key  # type: ignore[return-value]


def scope_for_platform(platform: str) -> BusinessScope | None:
    """Return scope for a known platform; None if unknown / ambiguous."""
    key = (platform or "").strip().lower()
    if key in VIDEO_PLATFORMS:
        return SCOPE_VIDEO
    if key in CONTENT_PLATFORMS or _CUSTOM_CONTENT_PLATFORM.fullmatch(key):
        return SCOPE_CONTENT
    return None


def require_scope_for_platform(platform: str) -> BusinessScope:
    scope = scope_for_platform(platform)
    if scope is None:
        raise ValueError(f"无法判定平台业务域: {platform}")
    return scope


def assert_platform_in_scope(platform: str, scope: str) -> str:
    """Validate platform belongs to scope; return normalized platform id."""
    plat = (platform or "").strip().lower()
    want = normalize_scope(scope)
    got = scope_for_platform(plat)
    if got is None:
        raise ValueError(f"未知平台: {platform}")
    if got != want:
        raise ValueError(f"平台 {plat} 属于 {got}，不能用于 {want} 业务域")
    return plat


def platforms_for_scope(scope: str) -> frozenset[str]:
    want = normalize_scope(scope)
    return VIDEO_PLATFORMS if want == SCOPE_VIDEO else CONTENT_PLATFORMS
