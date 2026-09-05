from __future__ import annotations

from engine.reach.cdp_platforms.base import BasePlatformAdapter


class ChannelsAdapter(BasePlatformAdapter):
    def __init__(self) -> None:
        super().__init__(platform="channels")

    def wait_upload_ready(self, sess, timeout: float = 12):
        from engine.reach.cdp_publish import wait_upload_ready

        return wait_upload_ready(sess, timeout=timeout)
