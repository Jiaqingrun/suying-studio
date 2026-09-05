"""AI disclosure removed from publish copy (user lock 2026-08-13)."""

from __future__ import annotations

import unittest

from engine.pack.publish import (
    AI_GENERATED_DISCLOSURE,
    build_platform_copy,
    ensure_ai_generated_disclosure,
    has_ai_generated_disclosure,
    strip_ai_generated_disclosure,
)


class AiGeneratedDisclosureRemovedTests(unittest.TestCase):
    def test_ensure_strips_aliases(self) -> None:
        once = ensure_ai_generated_disclosure("仓配一体\n本地发货\n本作品由AI生成")
        self.assertNotIn(AI_GENERATED_DISCLOSURE, once)
        self.assertFalse(has_ai_generated_disclosure(once))
        self.assertEqual(once, "仓配一体\n本地发货")

        again = strip_ai_generated_disclosure(
            "本视频由AI生成\n正文在后\n本片由AI生成"
        )
        self.assertEqual(again, "正文在后")
        self.assertEqual(ensure_ai_generated_disclosure(""), "")
        self.assertEqual(ensure_ai_generated_disclosure(AI_GENERATED_DISCLOSURE), "")

    def test_build_platform_copy_has_no_disclosure(self) -> None:
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
