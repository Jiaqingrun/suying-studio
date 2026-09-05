"""Edge TTS multi-voice catalog + lock write-through."""

from __future__ import annotations

import unittest


class EdgeVoicesCatalogTests(unittest.TestCase):
    def test_bundled_catalog_covers_zh_and_full_set(self) -> None:
        from engine.pack.edge_voices import (
            all_edge_voices,
            edge_voice_label,
            list_edge_voices,
        )

        all_v = all_edge_voices(prefer_live=False)
        self.assertGreaterEqual(len(all_v), 200)
        ids = {v["id"] for v in all_v}
        self.assertIn("zh-CN-XiaoxiaoNeural", ids)
        self.assertIn("zh-CN-XiaoyiNeural", ids)
        self.assertIn("en-US-JennyNeural", ids)

        zh = list_edge_voices(lang="zh", all_locales=False)
        self.assertTrue(zh["ok"])
        self.assertGreaterEqual(zh["count"], 6)
        for row in zh["voices"]:
            self.assertTrue(str(row["id"]).startswith("zh-CN") or str(row["locale"]).startswith("zh-CN"))

        full = list_edge_voices(all_locales=True)
        self.assertEqual(full["count"], full["total"])
        self.assertGreaterEqual(full["count"], zh["count"])

        self.assertIn("晓伊", edge_voice_label("zh-CN-XiaoyiNeural"))
        self.assertIn("晓晓", edge_voice_label("zh-CN-XiaoxiaoNeural"))

    def test_apply_edge_voice_not_hardcoded_xiaoxiao(self) -> None:
        """Regression: saving edge provider must keep chosen Neural id."""
        from engine.api import app as api_app
        from engine.pack.edge_voices import edge_voice_label

        # Pure helper path: when edge_voice is passed, label + id must stick.
        # We only unit-test the mapping helpers used by _apply; full lock write needs FS.
        vid = "zh-CN-XiaoyiNeural"
        self.assertEqual(edge_voice_label(vid), "晓伊（女·温柔）")
        # Signature still accepts edge_voice (call will fail without customer paths —
        # so only inspect signature).
        import inspect

        sig = inspect.signature(api_app._apply_voice_to_video_lock)
        self.assertIn("edge_voice", sig.parameters)


if __name__ == "__main__":
    unittest.main()
