"""Regression coverage for Kuaishou's four-topic limit."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from engine.pack.publish import build_platform_copy, limit_platform_hashtags
from engine.reach.cdp_publish import _fill_kuaishou_copy
from engine.reach.prefill import load_pack_prefill


class KuaishouHashtagLimitTests(unittest.TestCase):
    def test_generated_copy_limits_only_kuaishou(self) -> None:
        copy = build_platform_copy(
            title="仓配一体｜本地发货",
            brand="演示品牌",
            theme="仓配",
            hashtags=["#一", "#二", "#三", "#四", "#五", "#六"],
            music_credit="Music: test",
        )

        kuaishou = copy["platforms"]["kuaishou"]
        self.assertEqual(kuaishou["hashtags"], ["#一", "#二", "#三", "#四"])
        self.assertEqual(kuaishou["body"].count("#"), 4)
        self.assertNotIn("#五", kuaishou["body"])
        self.assertEqual(len(copy["platforms"]["douyin"]["hashtags"]), 6)
        self.assertIn("#六", copy["platforms"]["xhs"]["body"])

    def test_inline_topics_are_limited_before_metadata(self) -> None:
        body, tags = limit_platform_hashtags(
            "kuaishou",
            "正文 #甲 #乙 #丙 #丁 #戊，再补充 #己",
            ["#数组一", "#数组二"],
        )

        self.assertEqual(tags, ["#甲", "#乙", "#丙", "#丁"])
        self.assertEqual(body.count("#"), 4)
        self.assertNotIn("#戊", body)
        self.assertNotIn("#己", body)

    def test_other_platform_body_is_unchanged(self) -> None:
        original = "正文 #一 #二 #三 #四 #五"
        body, tags = limit_platform_hashtags("douyin", original, ["#数组一"] * 5)
        self.assertEqual(body, original)
        self.assertEqual(tags, ["#数组一"] * 5)

    def test_cdp_fill_receives_limited_body(self) -> None:
        session = MagicMock()
        session.evaluate.return_value = {"titleOk": True, "bodyOk": True}

        _fill_kuaishou_copy(session, "标题", "正文 #一 #二 #三 #四 #五 #六")

        expression = session.evaluate.call_args.args[0]
        self.assertIn("#四", expression)
        self.assertNotIn("#五", expression)
        self.assertNotIn("#六", expression)

    def test_publish_pack_prefill_exposes_kuaishou_copy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pack_dir = Path(td)
            (pack_dir / "video.mp4").write_bytes(b"video")
            copy = {
                "platforms": {
                    "kuaishou": {
                        "title": "旧包",
                        "body": "正文 #一 #二 #三 #四 #五 #六",
                        "hashtags": ["#一", "#二", "#三", "#四", "#五", "#六"],
                    }
                }
            }
            (pack_dir / "copy.zh.json").write_text(
                json.dumps(copy, ensure_ascii=False),
                encoding="utf-8",
            )

            prefill = load_pack_prefill(pack_dir)
            entry = prefill["platforms"]["kuaishou"]
            self.assertEqual(len(entry["hashtags"]), 4)
            self.assertEqual(entry["body"].count("#"), 4)


if __name__ == "__main__":
    unittest.main()
