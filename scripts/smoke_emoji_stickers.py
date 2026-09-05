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
    inject_emojis_into_srt,
    resolve_emoji_png,
    sanitize_emoji_cues,
    strip_emoji_for_speech,
    text_has_emoji,
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


def test_inject_srt() -> None:
    srt = (
        "1\n00:00:00,000 --> 00:00:02,000\n此刻仓库货架整齐\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\n发货装车忙\n"
    )
    out = inject_emojis_into_srt(
        srt,
        [{"at_sec": 0.5, "emoji": "📦"}, {"at_sec": 3.0, "emoji": "🚚"}],
    )
    assert text_has_emoji(out), out
    assert "📦" in out and "🚚" in out
    print("inject_srt OK")


def test_subtitle_twemoji_styles() -> None:
    """Production rules use stroke_width=4 / colored fills — must still get Twemoji."""
    from engine.render.subtitles_burn import (
        _render_cue_png,
        cue_png_twemoji_chroma_count,
    )

    text = "此刻仓库货架整齐 📦"
    styles = [
        ("sw3_white", dict(color="#FFFFFF", stroke_color="#000000", stroke_width=3)),
        ("sw4_white", dict(color="#FFFFFF", stroke_color="#000000", stroke_width=4)),
        ("sw4_orange", dict(color="#FFAA00", stroke_color="#000000", stroke_width=4)),
        ("d950", dict(color="#D95000", stroke_color="#00A3D7", stroke_width=4)),
    ]
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for name, kwargs in styles:
            out = td_path / f"{name}.png"
            _render_cue_png(text, 1080, font_size=64, out=out, **kwargs)
            chromas = cue_png_twemoji_chroma_count(
                out,
                text_color=str(kwargs["color"]),
                stroke_color=str(kwargs["stroke_color"]),
            )
            assert chromas >= 60, (name, chromas, out.stat().st_size)
            print(f"  subtitle_twemoji {name}: chromas={chromas}")
    # no-emoji pure text path must not raise and need no chroma requirement
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "plain.png"
        _render_cue_png(
            "此刻仓库货架整齐",
            1080,
            font_size=64,
            out=out,
            color="#FFFFFF",
            stroke_color="#000000",
            stroke_width=4,
        )
        assert out.is_file() and out.stat().st_size > 200
    print("subtitle_twemoji_styles OK")


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


def test_burn_srt_with_inline_emoji() -> None:
    """End-to-end: SRT with emoji + production stroke=4 → burned frame gets color."""
    import subprocess

    from engine.render.subtitles_burn import burn_srt_into_video

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        vid = td / "src.mp4"
        out = td / "out.mp4"
        srt = td / "sub.srt"
        srt.write_text(
            "1\n00:00:00,000 --> 00:00:02,500\n此刻仓内货架 📦\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=gray:s=1080x1920:d=3",
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
        r = burn_srt_into_video(
            vid,
            srt,
            out,
            font_size=64,
            bottom_padding_px=420,
            color="#FFFFFF",
            stroke_color="#000000",
            stroke_width=4,
        )
        assert r.get("ok"), r
        frame = td / "f.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.8", "-i", str(out), "-frames:v", "1", str(frame)],
            capture_output=True,
            check=True,
        )
        from PIL import Image

        img = Image.open(frame).convert("RGB")
        # bottom band should contain non-grey chromatic pixels from Twemoji
        w, h = img.size
        band = img.crop((0, h - 520, w, h - 200))
        chromas = 0
        for r0, g0, b0 in band.getdata():
            if max(r0, g0, b0) - min(r0, g0, b0) >= 28 and max(r0, g0, b0) > 40:
                chromas += 1
        assert chromas >= 40, f"burned frame subtitle band chromas={chromas}"
        print("burn_srt_with_inline_emoji OK chromas=", chromas)


def main() -> int:
    test_strip_speech()
    test_resolve_png()
    test_inject_srt()
    test_subtitle_twemoji_styles()
    test_burn_overlay()
    test_burn_srt_with_inline_emoji()
    print("smoke_emoji_stickers PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
