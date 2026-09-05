"""VO tempo-fit to planned picture duration (prefer over freeze pad)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.pack.tts import (
    NarrationSegment,
    fit_narration_to_picture_duration,
    probe_audio_duration,
)


def _sine_wav(path: Path, duration_sec: float) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=44100:duration={duration_sec:.3f}",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-500:])


class NarrationFitPictureTests(unittest.TestCase):
    def test_already_fits_no_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bed = Path(td) / "short.wav"
            _sine_wav(bed, 5.0)
            meta = fit_narration_to_picture_duration(
                bed, picture_duration_sec=10.0, max_speed=1.35
            )
            self.assertFalse(meta["applied"])
            self.assertEqual(meta["reason"], "already_fits")
            self.assertAlmostEqual(probe_audio_duration(bed), 5.0, delta=0.15)

    def test_compresses_when_longer_than_picture(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bed = Path(td) / "long.wav"
            _sine_wav(bed, 17.0)
            segs = [
                NarrationSegment(
                    index=0,
                    text="a",
                    lang="zh",
                    audio_path=str(bed),
                    duration_sec=17.0,
                    estimated_sec=17.0,
                    start_sec=0.0,
                    end_sec=17.0,
                )
            ]
            meta = fit_narration_to_picture_duration(
                bed,
                picture_duration_sec=14.9,
                segments=segs,
                max_speed=1.35,
                min_tail_sec=0.15,
            )
            self.assertTrue(meta["applied"], meta)
            after = probe_audio_duration(bed)
            # target ≈ 14.75; atempo may be a few 10ms off
            self.assertLess(after, 15.2)
            self.assertGreater(after, 13.5)
            self.assertLess(float(segs[0].end_sec or 0), 16.0)
            self.assertGreater(float(meta["speed"]), 1.05)


if __name__ == "__main__":
    unittest.main()
