"""Managed Chrome account switching must hard-isolate login sessions."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.reach import chrome_runtime
from engine.reach.browser import chrome_launch_args, chrome_launchservices_command, chrome_profiles_root
from engine.reach.cdp_client import find_tab_ws


class _FakeChrome:
    def __init__(self, profile_name: str, url: str, **_kwargs: object) -> None:
        self.profile = Path("/tmp") / profile_name
        self.url = url
        self.headless = False
        self.port = 45678
        self.stopped = False

    def _owned_pid(self) -> int:
        return 12345

    def start(
        self,
        timeout_sec: float = 12.0,
        *,
        allow_other_managed: bool = False,
        allow_bootstrap: bool = False,
    ) -> dict[str, object]:
        del timeout_sec, allow_bootstrap
        return {
            "started": True,
            "pid": 12345,
            "port": self.port,
            "profile": str(self.profile),
            "allow_other_managed": allow_other_managed,
        }

    def stop(self, timeout_sec: float = 5.0) -> None:
        del timeout_sec
        self.stopped = True


class ChromeRuntimeSwitchingTests(unittest.TestCase):
    def setUp(self) -> None:
        chrome_runtime._managed_instances.clear()

    def tearDown(self) -> None:
        chrome_runtime._managed_instances.clear()

    def test_interactive_switch_always_stops_previous_profile(self) -> None:
        """Bottom line: even exclusive=False still closes the previous account."""
        previous = _FakeChrome("账号一", "https://creator.xiaohongshu.com/")
        chrome_runtime._managed_instances[(1, "video", "账号一")] = previous

        def _enforce(**kwargs: object) -> dict[str, int]:
            del kwargs
            chrome_runtime._stop_managed_instance(previous)
            return {"closed_managed": 1, "closed_orphan": 0}

        with (
            patch.object(chrome_runtime, "ManagedChrome", _FakeChrome),
            patch.object(chrome_runtime, "_discover_chrome_for_profile", return_value=None),
            patch.object(chrome_runtime, "_read_state", return_value=None),
            patch.object(chrome_runtime, "_reclaim_runtime_lock"),
            patch.object(chrome_runtime, "enforce_single_chrome_slot", side_effect=_enforce) as enforce,
        ):
            current, info = chrome_runtime.start_or_reuse_managed(
                "账号二",
                "https://creator.xiaohongshu.com/",
                customer_id=1,
                business_scope="video",
                exclusive=False,
            )

        enforce.assert_called_once()
        self.assertTrue(previous.stopped)
        self.assertTrue(info["single_slot"])
        self.assertFalse(info["allow_other_managed"])
        self.assertIs(chrome_runtime._managed_instances[(1, "video", "账号二")], current)

    def test_exclusive_switch_stops_previous_profile(self) -> None:
        previous = _FakeChrome("账号一", "https://creator.xiaohongshu.com/")
        chrome_runtime._managed_instances[(1, "video", "账号一")] = previous

        with (
            patch.object(chrome_runtime, "ManagedChrome", _FakeChrome),
            patch.object(chrome_runtime, "_discover_chrome_for_profile", return_value=None),
            patch.object(chrome_runtime, "_read_state", return_value=None),
            patch.object(chrome_runtime, "_reclaim_runtime_lock"),
            patch.object(
                chrome_runtime,
                "enforce_single_chrome_slot",
                side_effect=lambda **_kwargs: (
                    chrome_runtime._stop_managed_instance(previous),
                    {"closed_managed": 1, "closed_orphan": 0},
                )[1],
            ),
        ):
            chrome_runtime.start_or_reuse_managed(
                "账号二",
                "https://creator.xiaohongshu.com/",
                customer_id=1,
                business_scope="video",
                exclusive=True,
            )

        self.assertTrue(previous.stopped)

    def test_repeated_open_reuses_existing_creator_tab(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            [
                {
                    "id": "keep-me",
                    "type": "page",
                    "url": "https://creator.xiaohongshu.com/new/home",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1",
                },
                {
                    "id": "close-me",
                    "type": "page",
                    "url": "https://creator.xiaohongshu.com/publish/publish",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/2",
                },
            ]
        ).encode()
        session = MagicMock()
        close_calls: list[str] = []

        def fake_urlopen(req, timeout=2):  # noqa: ANN001
            url = req if isinstance(req, str) else getattr(req, "full_url", None) or str(req)
            if "/json/close/" in str(url):
                close_calls.append(str(url))
                return MagicMock(__enter__=MagicMock(return_value=MagicMock()), __exit__=MagicMock())
            return response

        with (
            patch.object(chrome_runtime.urllib.request, "urlopen", side_effect=fake_urlopen) as open_url,
            patch("engine.reach.cdp_client.CdpSession", return_value=session),
        ):
            ok = chrome_runtime._cdp_open_url(
                9222,
                "https://creator.xiaohongshu.com/publish/publish?target=video",
            )

        self.assertTrue(ok)
        self.assertGreaterEqual(open_url.call_count, 1)
        session.call.assert_called_once_with(
            "Page.navigate",
            {"url": "https://creator.xiaohongshu.com/publish/publish?target=video"},
        )
        self.assertTrue(any("/json/close/close-me" in u for u in close_calls))

    def test_publish_tab_selection_skips_matching_iframe_target(self) -> None:
        tabs = [
            {
                "type": "iframe",
                "url": "https://summon.bytedance.com/?from=https%3A%2F%2Fcreator.douyin.com",
                "webSocketDebuggerUrl": "ws://iframe",
            },
            {
                "type": "page",
                "url": "https://creator.douyin.com/creator-micro/content/upload",
                "webSocketDebuggerUrl": "ws://page",
            },
        ]
        with patch("engine.reach.cdp_client.list_tabs", return_value=tabs):
            ws, url = find_tab_ws(url_substr="creator.douyin.com")

        self.assertEqual(ws, "ws://page")
        self.assertIn("/content/upload", url)

    def test_command_owns_profile_rejects_prefix_collision(self) -> None:
        profile = Path("/tmp/chrome-profiles/customer-1/video/快手-01")
        command = (
            "12345 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--user-data-dir=/tmp/chrome-profiles/customer-1/video/快手-010 "
            "--no-first-run --remote-debugging-port=9222"
        )
        self.assertFalse(chrome_runtime._command_owns_profile(command, profile))

    def test_command_owns_profile_exact_match(self) -> None:
        profile = Path("/tmp/chrome-profiles/customer-1/video/快手-01")
        command = (
            "12345 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
            "--user-data-dir=/tmp/chrome-profiles/customer-1/video/快手-01 "
            "--no-first-run --remote-debugging-port=9222"
        )
        self.assertTrue(chrome_runtime._command_owns_profile(command, profile))

    def test_zombie_chrome_pid_is_not_treated_as_alive(self) -> None:
        process = MagicMock(stdout="ZN\n")
        with (
            patch.object(chrome_runtime.os, "kill"),
            patch.object(chrome_runtime.os, "uname", return_value=MagicMock(sysname="Darwin")),
            patch.object(chrome_runtime.subprocess, "run", return_value=process),
        ):
            self.assertFalse(chrome_runtime._pid_alive(66154))

    def test_launch_restores_session_cookies_after_clean_switch(self) -> None:
        with patch("engine.reach.browser.dismiss_popups_extension_dir", return_value=None):
            args = chrome_launch_args(
                Path("/tmp/chrome-profiles/customer-1/video/账号一"),
                "https://creator.xiaohongshu.com/",
                cdp_port=9222,
                headless=False,
            )

        self.assertIn("--restore-last-session", args)
        # Publish URL must not be on argv — that piles a new tab on every cold start.
        self.assertNotIn("https://creator.xiaohongshu.com/", args)

    def test_macos_launch_uses_launchservices_not_raw_binary(self) -> None:
        """Direct binary exec cannot persist cookies; open -na must be used."""
        with patch("engine.reach.browser.dismiss_popups_extension_dir", return_value=None):
            cmd = chrome_launchservices_command(
                Path("/tmp/chrome-profiles/customer-1/video/账号一"),
                "https://creator.xiaohongshu.com/",
                cdp_port=9222,
                headless=False,
            )

        self.assertEqual(cmd[0], "open")
        self.assertEqual(cmd[1], "-na")
        self.assertTrue(str(cmd[2]).endswith("Google Chrome.app"))
        self.assertEqual(cmd[3], "--args")
        self.assertNotIn("-g", cmd)
        self.assertNotIn("/Contents/MacOS/Google Chrome", " ".join(cmd))
        self.assertIn("--user-data-dir=/tmp/chrome-profiles/customer-1/video/账号一", cmd)
        self.assertIn("--restore-last-session", cmd)

    def test_macos_headless_launch_stays_in_background(self) -> None:
        """G7 headless patrol must not activate Chrome.app (focus steal)."""
        with patch("engine.reach.browser.dismiss_popups_extension_dir", return_value=None):
            cmd = chrome_launchservices_command(
                Path("/tmp/chrome-profiles/customer-1/video/抖音-01"),
                "https://creator.douyin.com/",
                cdp_port=60651,
                headless=True,
            )

        self.assertEqual(cmd[:4], ["open", "-na", str(cmd[2]), "-g"])
        self.assertTrue(str(cmd[2]).endswith("Google Chrome.app"))
        self.assertEqual(cmd[4], "--args")
        self.assertIn("--headless=new", cmd)

    def test_main_screen_bounds_parses_finder_desktop(self) -> None:
        completed = MagicMock(stdout="0, 0, 1728, 1117\n", returncode=0)
        with patch.object(chrome_runtime.subprocess, "run", return_value=completed):
            bounds = chrome_runtime._main_screen_bounds()
        self.assertEqual(bounds, {"left": 0, "top": 0, "width": 1728, "height": 1117})

    def test_reveal_moves_window_off_secondary_display(self) -> None:
        session = MagicMock()
        session.call.side_effect = [
            {"windowId": 7, "bounds": {"left": 1758, "top": 46, "width": 1200, "height": 950, "windowState": "normal"}},
            {},
            {},
        ]
        tabs = [
            {
                "id": "page-1",
                "type": "page",
                "url": "https://creator.xiaohongshu.com/",
                "webSocketDebuggerUrl": "ws://127.0.0.1:58093/devtools/page/1",
            }
        ]
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(tabs).encode()
        with (
            patch.object(chrome_runtime, "_main_screen_bounds", return_value={"left": 0, "top": 0, "width": 1728, "height": 1117}),
            patch.object(chrome_runtime.urllib.request, "urlopen", return_value=response),
            patch("engine.reach.cdp_client.CdpSession", return_value=session),
            patch.object(chrome_runtime.subprocess, "run") as osa,
            patch.object(chrome_runtime.os, "uname", return_value=type("U", (), {"sysname": "Darwin"})()),
        ):
            info = chrome_runtime._reveal_visible_chrome(58093)

        self.assertTrue(info["ok"])
        self.assertEqual(info["from"]["left"], 1758)
        self.assertEqual(info["to"]["left"], 48)
        self.assertEqual(info["to"]["windowState"], "normal")
        session.call.assert_any_call("Page.bringToFront")
        self.assertTrue(osa.called)

    def test_default_profile_root_is_local_application_support(self) -> None:
        with (
            patch.dict("os.environ", {"SUYING_CHROME_PROFILES": ""}),
            patch("engine.reach.browser.Path.home", return_value=Path("/Users/test")),
        ):
            root = chrome_profiles_root()

        self.assertEqual(
            root,
            Path("/Users/test/Library/Application Support/com.qr.suying/chrome-profiles"),
        )

if __name__ == "__main__":
    unittest.main()
