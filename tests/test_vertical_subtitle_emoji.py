"""Vertical subtitle Twemoji regression (emoji must not tofu)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from engine.render.subtitle_fx import render_vertical_cue_png
from engine.render.subtitles_burn import cue_png_twemoji_chroma_count


class VerticalSubtitleEmojiTests(unittest.TestCase):
    def test_vertical_cue_with_emoji_has_twemoji_chroma(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "vert.png"
            render_vertical_cue_png(
                "仓配快✨",
                canvas_w=1080,
                canvas_h=1920,
                font_size=64,
                out=out,
                side="left",
                color="#FFFFFF",
                stroke_color="#000000",
                stroke_width=4,
            )
            self.assertTrue(out.is_file())
            img = Image.open(out)
            self.assertEqual(img.size, (1080, 1920))
            chroma = cue_png_twemoji_chroma_count(
                out, text_color="#FFFFFF", stroke_color="#000000"
            )
            self.assertGreaterEqual(chroma, 60, f"chroma={chroma}")

    def test_vertical_plain_text_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "plain.png"
            render_vertical_cue_png(
                "仓配快",
                canvas_w=1080,
                canvas_h=1920,
                font_size=64,
                out=out,
            )
            self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()
