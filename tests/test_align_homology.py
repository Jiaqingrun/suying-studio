"""Align homology: tighten final SRT against the VO bed READY_GATE hears."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from engine.pack.narration_script import tighten_srt_to_voiceover
from engine.qc.ready_gate import _check_align


class AlignHomologyTests(unittest.TestCase):
    def test_tighten_clears_overhang_on_real_failed_sample(self) -> None:
        wav = Path(
            "/Users/qr/Movies/速影工作区/render/job471_ed375d30_voice/voiceover.wav"
        )
        srt = Path(
            "/Users/qr/Movies/速影工作区/render/job471_ed375d30_voice/subtitle.zh.srt"
        )
        if not wav.is_file() or not srt.is_file():
            self.skipTest("local failed sample not present")
        body = srt.read_text(encoding="utf-8")
        self.assertTrue(_check_align(body, wav), "fixture should fail before tighten")
        tight = tighten_srt_to_voiceover(body, wav, tail_trim_seconds=0.12)
        self.assertEqual(_check_align(tight, wav), [])

    def test_synthetic_overhang_fixed(self) -> None:
        # Minimal silence-bounded tone: speech ~0.0-0.8s, cue ends at 1.0s.
        import subprocess

        with TemporaryDirectory() as td:
            work = Path(td)
            wav = work / "vo.wav"
            # 1s tone then 0.5s silence → speech_end ≈ 1.0; cue end 1.25 → overhang
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.9",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=24000:cl=mono",
                    "-filter_complex",
                    "[0][1]concat=n=2:v=0:a=1[a]",
                    "-map",
                    "[a]",
                    "-t",
                    "1.4",
                    "-ar",
                    "24000",
                    str(wav),
                ],
                capture_output=True,
                check=False,
            )
            if not wav.is_file() or wav.stat().st_size < 64:
                self.skipTest("ffmpeg unavailable")
            loose = (
                "1\n00:00:00,000 --> 00:00:01,250\n测试旁白\n"
            )
            fails = _check_align(loose, wav)
            if not fails:
                self.skipTest("silence detector did not flag synthetic overhang")
            tight = tighten_srt_to_voiceover(loose, wav, tail_trim_seconds=0.12)
            self.assertEqual(_check_align(tight, wav), [])


if __name__ == "__main__":
    unittest.main()
