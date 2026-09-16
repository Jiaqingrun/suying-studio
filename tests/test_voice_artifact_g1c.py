"""Unit tests for V-02 G1C paid voice artifact reuse."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.pack.voice_artifact import (
    clear_voice_artifact,
    compute_voice_stamp,
    save_voice_artifact,
    try_reuse_voice_artifact,
)


class VoiceArtifactG1CTests(unittest.TestCase):
    def test_stamp_stable_and_sensitive(self) -> None:
        a = compute_voice_stamp(
            script="你好世界",
            provider="edge",
            voice="zh-CN-XiaoxiaoNeural",
            rate="-8%",
            pitch="+35Hz",
            volume="+12%",
        )
        b = compute_voice_stamp(
            script="你好世界",
            provider="edge",
            voice="zh-CN-XiaoxiaoNeural",
            rate="-8%",
            pitch="+35Hz",
            volume="+12%",
        )
        c = compute_voice_stamp(
            script="你好世界。",
            provider="edge",
            voice="zh-CN-XiaoxiaoNeural",
            rate="-8%",
            pitch="+35Hz",
            volume="+12%",
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_save_reuse_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "attempt1"
            work.mkdir()
            narr = work / "voiceover.wav"
            narr.write_bytes(b"RIFF" + b"\x00" * 200)
            srt = work / "subtitle.zh.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
            stamp = compute_voice_stamp(
                script="你好",
                provider="edge",
                voice="zh-CN-XiaoxiaoNeural",
            )
            cache = root / "paid"
            save_voice_artifact(
                cache,
                stamp=stamp,
                narr_path=narr,
                srt_path=srt,
                meta={"provider": "edge"},
            )
            dest = root / "attempt2"
            hit = try_reuse_voice_artifact(cache, stamp=stamp, dest_work_dir=dest)
            self.assertIsNotNone(hit)
            assert hit is not None
            self.assertTrue(hit["reused"])
            self.assertTrue(Path(hit["narration_path"]).is_file())
            self.assertTrue(Path(hit["srt_path"]).is_file())
            miss = try_reuse_voice_artifact(
                cache,
                stamp=compute_voice_stamp(
                    script="另一句",
                    provider="edge",
                    voice="zh-CN-XiaoxiaoNeural",
                ),
                dest_work_dir=root / "attempt3",
            )
            self.assertIsNone(miss)
            clear_voice_artifact(cache)
            self.assertFalse(cache.exists())


if __name__ == "__main__":
    unittest.main()
