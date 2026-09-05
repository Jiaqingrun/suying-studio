"""Platform-facing facade over ``engine.reach.cdp_publish`` (split in progress).

New publish helpers should land here as thin adapters; the monolith
``cdp_publish.py`` remains the implementation home until further extraction.
"""

from __future__ import annotations

from typing import Protocol

from engine.reach.cdp_platforms.channels import ChannelsAdapter
from engine.reach.cdp_platforms.douyin import DouyinAdapter
from engine.reach.cdp_platforms.kuaishou import KuaishouAdapter
from engine.reach.cdp_platforms.xhs import XhsAdapter

VIDEO_PUBLISH_PLATFORMS = ("douyin", "channels", "xhs", "kuaishou")


class PlatformCdpAdapter(Protocol):
    platform: str


_REGISTRY: dict[str, PlatformCdpAdapter] = {
    "douyin": DouyinAdapter(),
    "channels": ChannelsAdapter(),
    "xhs": XhsAdapter(),
    "kuaishou": KuaishouAdapter(),
}


def get_platform_adapter(platform: str) -> PlatformCdpAdapter:
    key = str(platform or "").strip().lower()
    if key not in _REGISTRY:
        raise KeyError(f"unsupported_publish_platform:{platform}")
    return _REGISTRY[key]


def list_platform_ids() -> tuple[str, ...]:
    return VIDEO_PUBLISH_PLATFORMS
