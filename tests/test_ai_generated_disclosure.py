"""AI disclosure: default OFF (L14); optional append when enabled."""

from __future__ import annotations

import unittest
from unittest import mock

from engine.pack.publish import (
    AI_GENERATED_DISCLOSURE,
    build_platform_copy,
    ensure_ai_generated_disclosure,
    has_ai_generated_disclosure,
    strip_ai_generated_disclosure,
)


class AiGeneratedDisclosureTests(unittest.TestCase):
    def test_default_strips_aliases(self) -> None:
        with mock.patch(
            "engine.pack.publish.publish_ai_disclosure_enabled", return_value=False
        ):
            once = ensure_ai_generated_disclosure("仓配一体\n本地发货\n本作品由AI生成")
        self.assertNotIn(AI_GENERATED_DISCLOSURE, once)
        self.assertFalse(has_ai_generated_disclosure(once))
        self.assertEqual(once, "仓配一体\n本地发货")

        again = strip_ai_generated_disclosure(
            "本视频由AI生成\n正文在后\n本片由AI生成"
        )
        self.assertEqual(again, "正文在后")
        with mock.patch(
            "engine.pack.publish.publish_ai_disclosure_enabled", return_value=False
        ):
            self.assertEqual(ensure_ai_generated_disclosure(""), "")
            self.assertEqual(ensure_ai_generated_disclosure(AI_GENERATED_DISCLOSURE), "")

    def test_enabled_appends_canonical_line(self) -> None:
        with mock.patch(
            "engine.pack.publish.publish_ai_disclosure_enabled", return_value=True
        ):
            body = ensure_ai_generated_disclosure("仓配一体\n本地发货")
        self.assertTrue(body.endswith(AI_GENERATED_DISCLOSURE))
        self.assertTrue(has_ai_generated_disclosure(body))

        forced = ensure_ai_generated_disclosure("仅正文", enabled=True)
        self.assertEqual(forced, f"仅正文\n{AI_GENERATED_DISCLOSURE}")

        forced_off = ensure_ai_generated_disclosure(
            f"仅正文\n{AI_GENERATED_DISCLOSURE}", enabled=False
        )
        self.assertEqual(forced_off, "仅正文")

    def test_build_platform_copy_default_has_no_disclosure(self) -> None:
        with mock.patch(
            "engine.pack.publish.publish_ai_disclosure_enabled", return_value=False
        ):
            copy = build_platform_copy(
                title="仓配一体｜本地发货",
                brand="演示品牌",
                theme="产品",
                hashtags=["#仓配", "#五金"],
                music_credit="",
                description="仓库现货装车就走",
            )
        for plat, entry in (copy.get("platforms") or {}).items():
            body = str(entry.get("body") or "")
            self.assertFalse(
                has_ai_generated_disclosure(body),
                f"{plat} still has AI disclosure: {body!r}",
            )
            self.assertNotIn("本作品由AI生成", body)
            self.assertNotIn("本视频由AI生成", body)


if __name__ == "__main__":
    unittest.main()
