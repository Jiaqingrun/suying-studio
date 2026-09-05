from __future__ import annotations

from engine.reach.cdp_platforms.base import BasePlatformAdapter


class DouyinAdapter(BasePlatformAdapter):
    def __init__(self) -> None:
        super().__init__(platform="douyin")

    # Implementation stays in cdp_publish; expose stable entry names as we peel.
    def wait_upload_ready(self, sess, timeout: float = 12):
        from engine.reach.cdp_publish import wait_upload_ready

        return wait_upload_ready(sess, timeout=timeout)
