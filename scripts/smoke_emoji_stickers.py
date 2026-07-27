#!/usr/bin/env python3
"""Smoke: emoji stickers visible + never spoken in VO script."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pack.emoji_stickers import (
    resolve_emoji_png,
    sanitize_emoji_cues,
    strip_emoji_for_speech,
    twemoji_codepoint,
)


def test_strip_speech() -> None:
    s = strip_emoji_for_speech("仓库发货快🚚批发方便👍让您省心")
    assert "🚚" not in s and "👍" not in s
    assert "仓库发货快" in s
    assert "表情包" not in strip_emoji_for_speech("贴个表情包看看")
    print("strip_speech OK", s)


def test_resolve_png() -> None:
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        cache = Path(td)
        for em in ("🚚", "📦", "👍", "🏠", "🔧"):
            code = twemoji_codepoint(em)
            assert code, em
            png = resolve_emoji_png(em, cache_dir=cache, size=200)
            assert png and png.is_file() and png.stat().st_size > 800, (em, png)
            img = Image.open(png).convert("RGBA")
            w, h = img.size
            # Four corners must be fully transparent (inside circular plate only)
            for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
                assert img.getpixel(xy)[3] == 0, (em, xy, img.getpixel(xy))
        print("resolve_png OK (circular clip)")


def test_burn_overlay() -> None:
    import subprocess

    from engine.render.subtitles_burn import burn_emoji_stickers_inplace

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        vid = td / "src.mp4"
        # 3s color bars
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=green:s=1080x1920:d=3",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(vid),
            ],
            capture_output=True,
            check=True,
        )
        cues = sanitize_emoji_cues(
            [{"at_sec": 0.5, "emoji": "🚚", "duration_sec": 1.5}, {"at_sec": 1.5, "emoji": "👍", "duration_sec": 1.2}]
        )
        work = td / "emoji"
        r = burn_emoji_stickers_inplace(vid, cues, work_dir=work, cache_dir=td / "cache", sticker_size=180)
        assert r.get("ok"), r
        assert int(r.get("count") or 0) >= 1
        pngs = list(work.glob("emoji_*.png"))
        assert pngs, "no sticker pngs"
        for p in pngs:
            assert p.stat().st_size > 800, p
        # sample frame must not be pure green in sticker corner — rough check via file size of jpg
        frame = td / "f.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.8", "-i", str(vid), "-frames:v", "1", str(frame)],
            capture_output=True,
            check=True,
        )
        assert frame.stat().st_size > 5000
        print("burn_overlay OK", r.get("count"), "png_bytes", [p.stat().st_size for p in pngs])


def main() -> int:
    test_strip_speech()
    test_resolve_png()
    test_burn_overlay()
    print("smoke_emoji_stickers PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
