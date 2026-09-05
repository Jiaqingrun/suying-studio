"""scene_tour timing: expand clips to cover narration duration."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from engine.pack.scene_tour_timing import expand_plan_clips_to_cover_narration


class SceneTourTimingTests(unittest.TestCase):
    def test_expand_when_sources_cover_narration(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            narr = root / "n.wav"
            narr.write_bytes(b"fake")
            src = root / "clip.mp4"
            src.write_bytes(b"fake")
            plan = SimpleNamespace(
                clips=[
                    SimpleNamespace(
                        source_path=str(src),
                        start_sec=0.0,
                        duration_sec=3.0,
                        description="仓库现货装车",
                    ),
                    SimpleNamespace(
                        source_path=str(src),
                        start_sec=0.0,
                        duration_sec=3.0,
                        description="本地发货更快",
                    ),
                ],
            )
            with (
                patch(
                    "engine.pack.scene_tour_timing._probe_media_duration",
                    side_effect=lambda p: 20.0 if Path(p) == src else 0.0,
                ),
                patch(
                    "engine.pack.tts.probe_audio_duration",
                    return_value=10.0,
                ),
            ):
                meta = expand_plan_clips_to_cover_narration(plan, narration_path=narr)
            self.assertTrue(meta["applied"])
            self.assertFalse(meta["need_fit"])
            after = sum(float(c.duration_sec) for c in plan.clips)
            self.assertGreaterEqual(after, 10.0)
            self.assertLessEqual(after, 10.3)

    def test_need_fit_when_sources_too_short(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            narr = root / "n.wav"
            narr.write_bytes(b"fake")
            src = root / "clip.mp4"
            src.write_bytes(b"fake")
            plan = SimpleNamespace(
                clips=[
                    SimpleNamespace(
                        source_path=str(src),
                        start_sec=0.0,
                        duration_sec=4.0,
                        description="短素材",
                    ),
                ],
            )
            with (
                patch(
                    "engine.pack.scene_tour_timing._probe_media_duration",
                    return_value=5.0,
                ),
                patch(
                    "engine.pack.tts.probe_audio_duration",
                    return_value=12.0,
                ),
            ):
                meta = expand_plan_clips_to_cover_narration(plan, narration_path=narr)
            self.assertTrue(meta["need_fit"])
            self.assertLessEqual(float(plan.clips[0].duration_sec), 5.0)


if __name__ == "__main__":
    unittest.main()
