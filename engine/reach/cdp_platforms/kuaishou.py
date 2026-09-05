from __future__ import annotations

from engine.reach.cdp_platforms.base import BasePlatformAdapter


class KuaishouAdapter(BasePlatformAdapter):
    def __init__(self) -> None:
        super().__init__(platform="kuaishou")

    def wait_upload_ready(self, sess, timeout: float = 12):
        from engine.reach.cdp_publish import wait_upload_ready

        return wait_upload_ready(sess, timeout=timeout)

    def fill_copy(self, sess, title: str, body: str):
        """Platform-owned fill; implementation remains in cdp_publish for CDP session types."""
        from engine.reach.cdp_publish import _fill_kuaishou_copy

        return _fill_kuaishou_copy(sess, title, body)
