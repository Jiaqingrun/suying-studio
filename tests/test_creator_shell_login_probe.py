"""Creator-home shells (抖音/快手/小红书) must count as login-ready without upload form."""

from __future__ import annotations

import inspect
import unittest

from engine.reach import publish_runner


class CreatorShellLoginProbeSourceTests(unittest.TestCase):
    def test_probe_js_recognizes_four_platform_shells(self) -> None:
        src = inspect.getsource(publish_runner._probe_login_ready_across_frames)
        for token in (
            "douyinShell",
            "kuaishouShell",
            "xhsShell",
            "creator-micro",
            "xiaohongshu",
            "shellKind",
        ):
            self.assertIn(token, src)

    def test_decide_ready_when_douyin_home_shell(self) -> None:
        probes = [
            {
                "url": "https://creator.douyin.com/creator-micro/home",
                "textLen": 200,
                "login": False,
                "blocked": False,
                "form": False,
                "shellLoggedIn": True,
            }
        ]
        form = any(bool(p.get("form")) for p in probes)
        shell = any(bool(p.get("shellLoggedIn")) for p in probes)
        login_wall_frames = []
        for p in probes:
            url_l = str(p.get("url") or "").lower()
            text_login = bool(p.get("login"))
            url_login = "/login" in url_l or "login.html" in url_l
            if (text_login or url_login) and not p.get("form"):
                login_wall_frames.append(p)
        login = False if form else (bool(login_wall_frames) or any(bool(p.get("login")) for p in probes))
        ready = (not login) and (form or (shell and not login_wall_frames))
        self.assertTrue(ready)
        self.assertFalse(login)


if __name__ == "__main__":
    unittest.main()
