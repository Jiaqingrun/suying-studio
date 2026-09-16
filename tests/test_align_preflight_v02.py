"""V-02 / PL-13: tail cue past VO must not invent end<=start; preflight matches READY."""

from __future__ import annotations

import unittest
from pathlib import Path

from engine.pack.narration_script import tighten_srt_to_voiceover
from engine.qc.ready_gate import _check_align, preflight_align_gate


class AlignPreflightV02Tests(unittest.TestCase):
    def test_p1r_failed_sample_tightens_to_ready_align(self) -> None:
        base = Path(
            "/Users/qr/Movies/速影工作区/速影客户/北京始峰伟业/02-成片/failed/2026-09-16"
        )
        wav = base / "montage_540_900111.voice.wav"
        srt = base / "montage_540_900111.zh.srt"
        if not wav.is_file() or not srt.is_file():
            self.skipTest("P1r failed sample not on disk")
        body = srt.read_text(encoding="utf-8")
        self.assertTrue(_check_align(body, wav), "fixture must fail before tighten")
        tight = tighten_srt_to_voiceover(body, wav, tail_trim_seconds=0.12)
        self.assertEqual(_check_align(tight, wav), [])
        self.assertEqual(preflight_align_gate(tight, wav), [])
        self.assertNotIn("00:00:19,329 --> 00:00:19,088", tight)


if __name__ == "__main__":
    unittest.main()
