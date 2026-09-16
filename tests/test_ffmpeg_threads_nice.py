"""P2.1 — ffmpeg -threads cap + nice; never touch CRF/preset."""

from __future__ import annotations

import unittest
from unittest.mock import patch


class TestFfmpegThreadsNice(unittest.TestCase):
    def test_thread_cap_leaves_one_core(self) -> None:
        from engine.render.ffmpeg import ffmpeg_thread_cap

        with patch("engine.render.ffmpeg.os.cpu_count", return_value=8):
            self.assertEqual(ffmpeg_thread_cap(), 7)
        with patch("engine.render.ffmpeg.os.cpu_count", return_value=1):
            self.assertEqual(ffmpeg_thread_cap(), 1)
        with patch("engine.render.ffmpeg.os.cpu_count", return_value=None):
            self.assertEqual(ffmpeg_thread_cap(), 1)

    def test_argv_injects_threads_and_nice_without_crf_preset(self) -> None:
        from engine.render.ffmpeg import prepare_ffmpeg_argv

        raw = [
            "ffmpeg",
            "-y",
            "-i",
            "in.mp4",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "out.mp4",
        ]
        with patch("engine.render.ffmpeg.ffmpeg_thread_cap", return_value=5):
            with patch("engine.render.ffmpeg.shutil.which", return_value="/usr/bin/nice"):
                out = prepare_ffmpeg_argv(raw)
        self.assertEqual(out[0], "/usr/bin/nice")
        self.assertEqual(out[1:3], ["-n", "10"])
        self.assertEqual(out[3], "ffmpeg")
        self.assertEqual(out[4:6], ["-threads", "5"])
        self.assertIn("-preset", out)
        self.assertEqual(out[out.index("-preset") + 1], "fast")
        self.assertIn("-crf", out)
        self.assertEqual(out[out.index("-crf") + 1], "18")

    def test_does_not_duplicate_threads(self) -> None:
        from engine.render.ffmpeg import prepare_ffmpeg_argv

        raw = ["ffmpeg", "-threads", "2", "-y", "out.mp4"]
        with patch("engine.render.ffmpeg.shutil.which", return_value=None):
            out = prepare_ffmpeg_argv(raw)
        self.assertEqual(out.count("-threads"), 1)
        self.assertEqual(out[0], "ffmpeg")


if __name__ == "__main__":
    unittest.main()
