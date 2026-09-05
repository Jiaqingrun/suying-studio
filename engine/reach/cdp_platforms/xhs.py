from __future__ import annotations

from engine.reach.cdp_platforms.base import BasePlatformAdapter


class XhsAdapter(BasePlatformAdapter):
    def __init__(self) -> None:
        super().__init__(platform="xhs")

    def wait_upload_ready(self, sess, timeout: float = 12):
        from engine.reach.cdp_publish import wait_upload_ready

        return wait_upload_ready(sess, timeout=timeout)

    def fill_copy(self, sess, title: str, body: str):
        from engine.reach.cdp_publish import _fill_xhs_copy

        return _fill_xhs_copy(sess, title, body)
