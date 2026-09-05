"""Channels login readiness must pierce wujie child frames."""

from __future__ import annotations

import unittest


def _decide(probes: list[dict]) -> dict:
    """Mirror publish_runner merge rules for unit testing without CDP."""
    form = any(bool(p.get("form")) for p in probes)
    shell = any(bool(p.get("shellLoggedIn")) for p in probes)
    login_wall_frames: list[dict] = []
    for p in probes:
        url_l = str(p.get("url") or "").lower()
        text_login = bool(p.get("login"))
        url_login = "/login" in url_l or "login.html" in url_l
        if (text_login or url_login) and not p.get("form"):
            login_wall_frames.append(p)
    if form:
        login = False
    else:
        login = bool(login_wall_frames) or any(bool(p.get("login")) for p in probes)
    blocked = any(bool(p.get("blocked")) for p in probes)
    text_len = max((int(p.get("textLen") or 0) for p in probes), default=0)
    ready = (not login) and (form or (shell and not login_wall_frames))
    return {
        "ready": ready,
        "login": login,
        "blocked": blocked and not form,
        "form": form,
        "shellLoggedIn": shell,
        "text_len": text_len,
        "login_wall_frames": len(login_wall_frames),
    }


class ChannelsLoginProbeTests(unittest.TestCase):
    def test_shell_only_body_with_logged_in_markers_is_ready(self) -> None:
        # Shell logged-in without form is ready for non-strict shell path,
        # but publish wait_login requires form for channels (tested elsewhere).
        probes = [
            {
                "url": "https://channels.weixin.qq.com/platform/post/create",
                "textLen": 10,
                "login": False,
                "blocked": False,
                "form": False,
                "shellLoggedIn": True,
                "avatar": True,
            }
        ]
        decided = _decide(probes)
        self.assertTrue(decided["ready"])
        self.assertFalse(decided["login"])

    def test_wujie_child_form_marks_ready(self) -> None:
        probes = [
            {
                "url": "https://channels.weixin.qq.com/platform/post/create",
                "textLen": 10,
                "login": False,
                "blocked": True,  # template string may linger in shell HTML
                "form": False,
                "shellLoggedIn": True,
            },
            {
                "url": "https://channels.weixin.qq.com/micro/content/post/create",
                "textLen": 251,
                "login": False,
                "blocked": False,
                "form": True,
                "shellLoggedIn": False,
            },
        ]
        decided = _decide(probes)
        self.assertTrue(decided["ready"])
        self.assertTrue(decided["form"])
        self.assertFalse(decided["blocked"])

    def test_real_login_wall_not_ready(self) -> None:
        probes = [
            {
                "url": "https://channels.weixin.qq.com/login",
                "textLen": 80,
                "login": True,
                "blocked": False,
                "form": False,
                "shellLoggedIn": False,
            }
        ]
        decided = _decide(probes)
        self.assertFalse(decided["ready"])
        self.assertTrue(decided["login"])

    def test_login_html_frame_beats_shell_noise(self) -> None:
        probes = [
            {
                "url": "https://channels.weixin.qq.com/login.html",
                "textLen": 120,
                "login": True,
                "blocked": False,
                "form": False,
                "shellLoggedIn": False,
            },
            {
                "url": "https://channels.weixin.qq.com/platform/",
                "textLen": 20,
                "login": False,
                "blocked": False,
                "form": False,
                "shellLoggedIn": True,
            },
        ]
        decided = _decide(probes)
        self.assertTrue(decided["login"])
        self.assertFalse(decided["ready"])
        self.assertGreaterEqual(decided["login_wall_frames"], 1)

    def test_form_wins_over_login_noise(self) -> None:
        probes = [
            {
                "url": "https://channels.weixin.qq.com/platform/post/create",
                "textLen": 40,
                "login": True,  # leftover template text should not block form
                "blocked": False,
                "form": True,
                "shellLoggedIn": False,
            }
        ]
        decided = _decide(probes)
        self.assertTrue(decided["ready"])
        self.assertFalse(decided["login"])

    def test_feature_blocked_without_form(self) -> None:
        probes = [
            {
                "url": "https://channels.weixin.qq.com/platform/post/create",
                "textLen": 40,
                "login": False,
                "blocked": True,
                "form": False,
                "shellLoggedIn": True,
            }
        ]
        decided = _decide(probes)
        self.assertTrue(decided["ready"])  # shell logged in
        self.assertTrue(decided["blocked"])


class SoftSkipRequeuePolicyTests(unittest.TestCase):
    def test_login_wall_kinds_are_not_requeueable(self) -> None:
        from engine.reach.publish_runner import LOGIN_WALL_KINDS, REQUEUEABLE_SOFT_KINDS

        for kind in ("true_login_wall", "login_required", "post_probe_false_pass"):
            self.assertIn(kind, LOGIN_WALL_KINDS)
            self.assertNotIn(kind, REQUEUEABLE_SOFT_KINDS)

    def test_form_not_ready_is_requeueable(self) -> None:
        from engine.reach.publish_runner import REQUEUEABLE_SOFT_KINDS

        self.assertIn("form_not_ready", REQUEUEABLE_SOFT_KINDS)

    def test_channels_restore_budget_covers_spa_mount(self) -> None:
        from engine.reach.publish_runner import CHANNELS_LOGIN_RESTORE_BUDGET_SEC

        # Field incident: form_not_ready after ~1.8s; must wait long enough for SPA.
        self.assertGreaterEqual(CHANNELS_LOGIN_RESTORE_BUDGET_SEC, 40.0)

    def test_wait_login_source_has_shell_pass_after_budget(self) -> None:
        import inspect

        from engine.reach import publish_runner as pr

        src = inspect.getsource(pr._wait_login_ready)
        self.assertIn("session_ready_shell", src)
        self.assertIn("Hand off to", src)


class PublishLeasePriorityTests(unittest.TestCase):
    def test_message_scan_yields_when_publish_active(self) -> None:
        from engine.reach.chrome_runtime import (
            PublishPriorityError,
            acquire_operation,
            clear_publish_active,
            is_publish_active,
            mark_publish_active,
        )

        owner = "publish_run:unit-test-priority"
        clear_publish_active()
        mark_publish_active(owner)
        try:
            self.assertTrue(is_publish_active())
            with self.assertRaises(PublishPriorityError) as ctx:
                acquire_operation("message_scan:99")
            self.assertEqual(ctx.exception.code, "publish_priority")
            self.assertNotIn("打开登录", str(ctx.exception))
        finally:
            clear_publish_active(owner)
            self.assertFalse(is_publish_active())


if __name__ == "__main__":
    unittest.main()
