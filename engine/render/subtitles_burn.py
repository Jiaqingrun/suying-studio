"""Burn SRT into video via ffmpeg (SEMANTIC_PIPELINE P3).

This environment's ffmpeg often lacks ``subtitles`` / ``drawtext`` (no libass /
freetype). Prefer those when present; otherwise render cue PNGs with Pillow and
composite with the ``overlay`` filter (always available).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    ms = (ms + "000")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt_cues(srt_text: str, *, max_cues: int = 60) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    blocks = re.split(r"\n\s*\n", (srt_text or "").replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        idx = 1 if lines[0].strip().isdigit() else 0
        if idx >= len(lines):
            continue
        m = _TIME_RE.search(lines[idx])
        if not m:
            continue
        start = _ts_to_sec(*m.groups()[:4])
        end = _ts_to_sec(*m.groups()[4:])
        text = "\n".join(ln.strip() for ln in lines[idx + 1 :] if ln.strip())
        if not text:
            continue
        cues.append({"start": start, "end": end, "text": text})
        if len(cues) >= max_cues:
            break
    return cues


_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_CJK_RE = re.compile(r"[\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")


def _script_kind(text: str) -> str:
    """Rough script class for font picking: thai | arabic | cjk | latin | mixed."""
    raw = text or ""
    has_thai = bool(_THAI_RE.search(raw))
    has_arabic = bool(_ARABIC_RE.search(raw))
    has_cjk = bool(_CJK_RE.search(raw))
    # Latin brand names inside Thai must still use a Thai-capable font (Thonburi),
    # not Arial Unicode which often breaks Thai tone marks.
    if has_thai and not has_cjk and not has_arabic:
        return "thai"
    if has_arabic and not has_thai and not has_cjk:
        return "arabic"
    if has_cjk and not has_thai and not has_arabic:
        return "cjk"
    if has_thai or has_arabic or has_cjk:
        # True dual-script line (e.g. accidental CJK inside Thai) → Unicode fallback
        return "mixed"
    return "latin"


def _find_font(size: int, text: str = "") -> ImageFont.ImageFont:
    """Pick a font that can actually render the cue (Thai must not use SC-only fonts)."""
    kind = _script_kind(text)
    by_kind: dict[str, list[tuple[str, int]]] = {
        "thai": [
            ("/System/Library/Fonts/Supplemental/Thonburi.ttc", 0),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("/Library/Fonts/Arial Unicode.ttf", 0),
        ],
        "arabic": [
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("/Library/Fonts/Arial Unicode.ttf", 0),
        ],
        "mixed": [
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("/Library/Fonts/Arial Unicode.ttf", 0),
            ("/System/Library/Fonts/Supplemental/Thonburi.ttc", 0),
            ("/System/Library/Fonts/PingFang.ttc", 0),
        ],
        "cjk": [
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
            (str(Path.home() / "Library/Fonts/NotoSansSC.ttf"), 0),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("/Library/Fonts/Arial Unicode.ttf", 0),
        ],
        "latin": [
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("/Library/Fonts/Arial Unicode.ttf", 0),
            ("/System/Library/Fonts/PingFang.ttc", 0),
        ],
    }
    candidates = by_kind.get(kind, by_kind["latin"]) + by_kind["latin"]
    seen: set[str] = set()
    for path, index in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not Path(path).is_file():
            continue
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


def _ffmpeg_has_filter(name: str) -> bool:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(re.search(rf"\b{re.escape(name)}\b", proc.stdout or ""))


def _probe_size(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    parts = (result.stdout or "720,1280").strip().split(",")
    try:
        return max(16, int(parts[0])), max(16, int(parts[1]))
    except (ValueError, IndexError):
        return 720, 1280


def _ns_font_name_for_line(line: str) -> str:
    kind = _script_kind(line)
    if kind == "thai":
        return "Thonburi"
    if kind == "arabic":
        return "GeezaPro"
    if kind == "cjk":
        return "PingFangSC-Regular"
    return "ArialUnicodeMS"


def _render_cue_png_coretext(
    lines: list[str],
    width: int,
    *,
    font_size: int,
    out: Path,
) -> bool:
    """macOS CoreText shaping — required for correct Thai tone marks (Pillow lacks RAQM here)."""
    try:
        from AppKit import (  # type: ignore[import-not-found]
            NSBitmapImageFileTypePNG,
            NSBitmapImageRep,
            NSColor,
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSGraphicsContext,
            NSMutableAttributedString,
            NSRectFill,
            NSStrokeColorAttributeName,
            NSStrokeWidthAttributeName,
        )
        from Foundation import NSMakeRect, NSPoint  # type: ignore[import-not-found]
    except ImportError:
        return False

    pad_x, pad_y = 8, 6
    line_gap = 10
    attrs_list: list[Any] = []
    sizes: list[tuple[float, float]] = []
    for ln in lines:
        font = NSFont.fontWithName_size_(_ns_font_name_for_line(ln), float(font_size))
        if font is None:
            font = NSFont.systemFontOfSize_(float(font_size))
        attrs = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: NSColor.whiteColor(),
            NSStrokeColorAttributeName: NSColor.blackColor(),
            NSStrokeWidthAttributeName: -3.0,
        }
        s = NSMutableAttributedString.alloc().initWithString_attributes_(ln, attrs)
        sz = s.size()
        attrs_list.append(s)
        sizes.append((float(sz.width), float(sz.height)))

    text_w = max((w for w, _ in sizes), default=0.0)
    text_h = sum(h for _, h in sizes) + max(0, len(lines) - 1) * line_gap
    box_w = min(width, max(int(text_w) + pad_x * 2, 32))
    box_h = max(int(text_h) + pad_y * 2, font_size + pad_y * 2)

    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, box_w, box_h, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0
    )
    NSGraphicsContext.saveGraphicsState()
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.setCurrentContext_(ctx)
    NSColor.clearColor().setFill()
    NSRectFill(NSMakeRect(0, 0, box_w, box_h))
    # NSBitmapImageRep origin is bottom-left — place first SRT line at the top
    y = float(box_h - pad_y)
    for s, (lw, lh) in zip(attrs_list, sizes):
        y -= lh
        x = (box_w - lw) / 2.0
        s.drawAtPoint_(NSPoint(x, y))
        y -= line_gap
    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, None)
    if data is None:
        return False
    return bool(data.writeToFile_atomically_(str(out), True))


def _wrap_lines_for_width(
    lines: list[str],
    width: int,
    font_size: int,
    *,
    side_margin_px: int = 48,
    stroke_w: int = 3,
) -> list[str]:
    """Fit lines inside canvas with side margins — never clip against frame edges.

    HARD (Job87): CJK must wrap by measured pixel width; full-width flush text
    looks like a hard 「边框」cut at left/right.
    """
    margin = max(24, int(side_margin_px))
    max_w = max(200, width - margin * 2 - int(stroke_w) * 2)
    # Approx advance for Thai/Latin at this size (CoreText is authoritative later)
    approx = max(18, int(font_size * 0.55))
    max_chars_latin = max(12, max_w // approx)
    out: list[str] = []
    for ln in lines:
        kind = _script_kind(ln)
        if kind == "cjk":
            out.extend(_wrap_cjk_line(ln, max_w, font_size, stroke_w=stroke_w))
            continue
        if len(ln) <= max_chars_latin:
            out.append(ln)
            continue
        # Prefer space breaks for Thai / Latin
        words = ln.split(" ")
        if len(words) == 1:
            # hard wrap
            buf = ln
            while len(buf) > max_chars_latin:
                out.append(buf[:max_chars_latin].strip())
                buf = buf[max_chars_latin:].strip()
            if buf:
                out.append(buf)
            continue
        cur = ""
        for w in words:
            trial = f"{cur} {w}".strip() if cur else w
            if len(trial) <= max_chars_latin:
                cur = trial
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return out[:8] or lines


def _wrap_cjk_line(text: str, max_w: int, font_size: int, *, stroke_w: int = 3) -> list[str]:
    """Greedy wrap CJK by font metrics so stroke stays inside max_w."""
    t = (text or "").strip()
    if not t:
        return []
    font = _find_font(font_size, t)
    probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)

    def _w(s: str) -> int:
        bbox = draw.textbbox((0, 0), s, font=font, stroke_width=stroke_w)
        return int(bbox[2] - bbox[0])

    if _w(t) <= max_w:
        return [t]
    lines: list[str] = []
    buf = ""
    for ch in t:
        trial = buf + ch
        if buf and _w(trial) > max_w:
            lines.append(buf)
            buf = ch
        else:
            buf = trial
    if buf:
        lines.append(buf)
    return lines or [t]


def _render_cue_png(
    text: str,
    width: int,
    *,
    font_size: int,
    out: Path,
    bottom_padding_px: int = 400,
    side_margin_px: int = 48,
) -> None:
    """White text + black outline, no background mask; auto dual-line; side margins.

    HARD: when cue contains emoji, composite Twemoji PNGs (fonts tofu □).
    """
    raw = (text or "").strip()
    margin = max(24, int(side_margin_px))
    # Prefer existing newlines; else auto dual-line near midpoint
    if "\n" in raw:
        lines = [ln.strip(" ，,、；;") for ln in raw.split("\n") if ln.strip()][:4]
    else:
        t = raw.replace("\n", "")
        limit = 28 if _script_kind(t) in ("thai", "latin", "arabic", "mixed") else 14
        if len(t) > limit:
            mid = (len(t) + 1) // 2
            break_at = mid
            for i in range(mid, max(2, mid - 12), -1):
                if t[i - 1] in " ，,、；; ":
                    break_at = i
                    break
            lines = [t[:break_at].strip(" ，,、；;"), t[break_at:].strip(" ，,、；;")]
        else:
            lines = [t]
    lines = [ln for ln in lines if ln] or [raw or " "]
    lines = _wrap_lines_for_width(
        lines, width, font_size, side_margin_px=margin, stroke_w=3
    )

    from engine.pack.emoji_stickers import text_has_emoji

    if any(text_has_emoji(ln) for ln in lines):
        if _render_cue_png_with_twemoji(
            lines, width, font_size=font_size, out=out, side_margin_px=margin
        ):
            _ = bottom_padding_px
            return

    needs_coretext = any(_script_kind(ln) in ("thai", "arabic", "mixed") for ln in lines)
    if needs_coretext and _render_cue_png_coretext(lines, width, font_size=font_size, out=out):
        _ = bottom_padding_px
        return

    # Per-line fonts so Thai+Chinese bilingual cues both render
    fonts = [_find_font(font_size, ln) for ln in lines]
    pad_x, pad_y = 10, 8
    line_gap = 10
    tmp = Image.new("RGBA", (width, font_size * (len(lines) + 3)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)
    sizes: list[tuple[int, int]] = []
    stroke_w = 3
    for ln, font in zip(lines, fonts):
        bbox = draw.textbbox((0, 0), ln, font=font, stroke_width=stroke_w)
        sizes.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))
    text_w = max((w for w, _ in sizes), default=0)
    text_h = sum(h for _, h in sizes) + max(0, len(lines) - 1) * line_gap
    # Cap box inside side margins so overlay never kisses frame edges
    max_box = max(32, width - margin * 2)
    box_w = min(max_box, max(text_w + pad_x * 2, 32))
    box_h = text_h + pad_y * 2
    # Fully transparent — no pill / bar / mask
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = pad_y
    for ln, font, (lw, lh) in zip(lines, fonts, sizes):
        x = (box_w - lw) // 2
        draw.text(
            (x, y),
            ln,
            font=font,
            fill=(255, 255, 255, 255),
            stroke_width=stroke_w,
            stroke_fill=(0, 0, 0, 255),
        )
        y += lh + line_gap
    img.save(out)
    _ = bottom_padding_px


def _emoji_twemoji_cache_dir() -> Path:
    """Resolve Twemoji cache under settings.cache_root (portable; never customer disk)."""
    try:
        from engine.config.settings import load_settings

        root = Path(load_settings().paths.cache_root).expanduser()
        return root / "emoji_twemoji"
    except Exception:
        return Path.home() / "Suying" / "cache" / "emoji_twemoji"


def _render_cue_png_with_twemoji(
    lines: list[str],
    width: int,
    *,
    font_size: int,
    out: Path,
    side_margin_px: int = 48,
) -> bool:
    """Draw CJK text + inline Twemoji (no title stickers; emoji live in subtitle)."""
    try:
        from engine.pack.emoji_stickers import (
            iter_emoji_tokens,
            resolve_emoji_png,
            text_has_emoji,
        )
    except Exception:
        return False

    # Portable cache: settings.cache_root → ~/Suying/cache (never hardcode customer disk)
    cache = _emoji_twemoji_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    emoji_px = max(28, int(font_size * 1.05))
    pad_x, pad_y = 10, 8
    line_gap = 10
    stroke_w = 3
    margin = max(24, int(side_margin_px))
    max_box = max(32, width - margin * 2)

    # Build per-line runs: ("text", str) | ("emoji", Path)
    rendered_lines: list[list[tuple[str, Any]]] = []
    line_sizes: list[tuple[int, int]] = []
    for ln in lines:
        runs: list[tuple[str, Any]] = []
        if not text_has_emoji(ln):
            runs.append(("text", ln))
        else:
            pos = 0
            for m in iter_emoji_tokens(ln):
                # find next occurrence from pos
                i = ln.find(m, pos)
                if i < 0:
                    continue
                if i > pos:
                    runs.append(("text", ln[pos:i]))
                png = resolve_emoji_png(m, cache_dir=cache, size=emoji_px, plate=False)
                if png and png.is_file():
                    runs.append(("emoji", png))
                else:
                    runs.append(("text", m))
                pos = i + len(m)
            if pos < len(ln):
                runs.append(("text", ln[pos:]))
        if not runs:
            runs = [("text", ln or " ")]
        rendered_lines.append(runs)

        # measure
        probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        draw = ImageDraw.Draw(probe)
        font = _find_font(font_size, ln)
        tw, th = 0, font_size
        for kind, val in runs:
            if kind == "emoji":
                tw += emoji_px + 2
                th = max(th, emoji_px)
            else:
                bbox = draw.textbbox((0, 0), str(val), font=font, stroke_width=stroke_w)
                tw += int(bbox[2] - bbox[0])
                th = max(th, int(bbox[3] - bbox[1]))
        line_sizes.append((tw, th))

    text_w = max((w for w, _ in line_sizes), default=0)
    text_h = sum(h for _, h in line_sizes) + max(0, len(lines) - 1) * line_gap
    box_w = min(max_box, max(text_w + pad_x * 2, 32))
    box_h = text_h + pad_y * 2
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = pad_y
    for runs, (lw, lh) in zip(rendered_lines, line_sizes):
        x = max(0, (box_w - lw) // 2)
        font = _find_font(font_size, "".join(str(v) for k, v in runs if k == "text") or "中")
        for kind, val in runs:
            if kind == "emoji":
                try:
                    em = Image.open(val).convert("RGBA")
                    em = em.resize((emoji_px, emoji_px), Image.Resampling.LANCZOS)
                    # center vertically on line
                    ey = y + max(0, (lh - emoji_px) // 2)
                    img.paste(em, (x, ey), em)
                    x += emoji_px + 2
                except Exception:
                    continue
            else:
                draw.text(
                    (x, y),
                    str(val),
                    font=font,
                    fill=(255, 255, 255, 255),
                    stroke_width=stroke_w,
                    stroke_fill=(0, 0, 0, 255),
                )
                bbox = draw.textbbox((0, 0), str(val), font=font, stroke_width=stroke_w)
                x += int(bbox[2] - bbox[0])
        y += lh + line_gap
    img.save(out)
    return out.is_file() and out.stat().st_size > 200


def _burn_with_overlay(
    video: Path,
    cues: list[dict[str, Any]],
    out: Path,
    *,
    font_size: int,
    bottom_padding_px: int = 400,
    side_margin_px: int = 48,
) -> dict[str, Any]:
    if not cues:
        shutil.copy2(video, out)
        return {"ok": True, "out": str(out), "error": None, "method": "copy", "cues": 0}

    w, _h = _probe_size(video)
    margin_v = max(24, int(bottom_padding_px))
    side = max(24, int(side_margin_px))
    with tempfile.TemporaryDirectory(prefix="suying-burn-") as td:
        work = Path(td)
        v_local = work / "in.mp4"
        shutil.copy2(video, v_local)
        pngs: list[Path] = []
        for i, cue in enumerate(cues):
            png = work / f"cue_{i:03d}.png"
            _render_cue_png(
                str(cue["text"]),
                w,
                font_size=font_size,
                out=png,
                bottom_padding_px=margin_v,
                side_margin_px=side,
            )
            pngs.append(png)

        cmd: list[str] = ["ffmpeg", "-y", "-i", "in.mp4"]
        for png in pngs:
            cmd.extend(["-i", png.name])

        # Center horizontally; bottom_padding_px from frame bottom (locked rule: 400).
        filters: list[str] = []
        last = "[0:v]"
        for i, cue in enumerate(cues):
            enable = f"between(t\\,{cue['start']:.3f}\\,{cue['end']:.3f})"
            nxt = f"[v{i}]" if i < len(cues) - 1 else "[vout]"
            filters.append(
                f"{last}[{i + 1}:v]overlay=(W-w)/2:H-h-{margin_v}:enable='{enable}'{nxt}"
            )
            last = f"[v{i}]"
        fc = ";".join(filters)
        cmd.extend(
            [
                "-filter_complex",
                fc,
                "-map",
                "[vout]",
                "-map",
                "0:a?",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                "out.mp4",
            ]
        )
        proc = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, check=False)
        o_local = work / "out.mp4"
        if proc.returncode != 0 or not o_local.is_file() or o_local.stat().st_size < 64:
            return {
                "ok": False,
                "out": str(out),
                "error": (proc.stderr or proc.stdout or "overlay burn failed")[-1200:],
                "method": "overlay",
                "cues": len(cues),
            }
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        shutil.copy2(o_local, out)
    return {"ok": True, "out": str(out), "error": None, "method": "overlay", "cues": len(cues)}


def burn_srt_into_video(
    video: Path,
    srt: Path,
    out: Path,
    *,
    font_size: int = 64,
    bottom_padding_px: int = 400,
) -> dict[str, Any]:
    video = Path(video)
    srt = Path(srt)
    out = Path(out)
    if not video.is_file():
        return {"ok": False, "out": str(out), "error": f"video missing: {video}"}
    if not srt.is_file():
        return {"ok": False, "out": str(out), "error": f"srt missing: {srt}"}
    if not shutil.which("ffmpeg"):
        return {"ok": False, "out": str(out), "error": "ffmpeg not found"}

    out.parent.mkdir(parents=True, exist_ok=True)
    cues = parse_srt_cues(srt.read_text(encoding="utf-8", errors="replace"))
    margin_v = max(24, int(bottom_padding_px))

    # Prefer native filters when present — no BackColour / BorderStyle box.
    if _ffmpeg_has_filter("subtitles"):
        with tempfile.TemporaryDirectory(prefix="suying-burn-") as td:
            work = Path(td)
            shutil.copy2(video, work / "in.mp4")
            shutil.copy2(srt, work / "in.srt")
            # Alignment=2 bottom-center; MarginV = distance from bottom; Outline only, no opaque box
            style = (
                f"Fontsize={max(28, font_size)},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                f"BorderStyle=1,Outline=3,Shadow=0,Alignment=2,"
                f"MarginV={margin_v},MarginL=48,MarginR=48"
            )
            vf = f"subtitles=in.srt:force_style='{style}'"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                "in.mp4",
                "-vf",
                vf,
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                "out.mp4",
            ]
            proc = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True, check=False)
            o_local = work / "out.mp4"
            if proc.returncode == 0 and o_local.is_file() and o_local.stat().st_size >= 64:
                shutil.copy2(o_local, out)
                return {"ok": True, "out": str(out), "error": None, "method": "subtitles", "cues": len(cues)}

    if _ffmpeg_has_filter("drawtext"):
        pass

    return _burn_with_overlay(
        video,
        cues,
        out,
        font_size=font_size,
        bottom_padding_px=margin_v,
        side_margin_px=48,
    )


def burn_emoji_stickers_inplace(
    video: Path,
    emoji_cues: list[dict[str, Any]],
    *,
    work_dir: Path | None = None,
    cache_dir: Path | None = None,
    sticker_size: int = 200,
) -> dict[str, Any]:
    """Burn theme emoji stickers (upper-right) via Twemoji PNG + ffmpeg overlay.

    HARD: never render tofu □ — use Twemoji/fallback plates, not CJK fonts.
    """
    video = Path(video)
    if not video.is_file():
        return {"ok": False, "error": f"video missing: {video}", "count": 0}
    if not emoji_cues:
        return {"ok": True, "error": None, "count": 0, "skipped": True}
    if not shutil.which("ffmpeg"):
        return {"ok": False, "error": "ffmpeg not found", "count": 0}

    from engine.pack.emoji_stickers import resolve_emoji_png, sanitize_emoji_cues

    # Probe size + duration
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        import json as _json

        info = _json.loads(probe.stdout or "{}")
        stream = (info.get("streams") or [{}])[0]
        width = int(stream.get("width") or 1080)
        height = int(stream.get("height") or 1920)
        vdur = float((info.get("format") or {}).get("duration") or 0) or None
    except Exception:
        width, height, vdur = 1080, 1920, None

    cues = sanitize_emoji_cues(emoji_cues, video_duration_sec=vdur)
    if not cues:
        return {"ok": True, "skipped": True, "count": 0, "error": "no valid emoji cues"}

    td_ctx = tempfile.TemporaryDirectory(prefix="suying-emoji-") if work_dir is None else None
    work = Path(work_dir) if work_dir else Path(td_ctx.name)  # type: ignore[union-attr]
    work.mkdir(parents=True, exist_ok=True)
    cache = Path(cache_dir) if cache_dir else (work / "_twemoji_cache")

    stickers: list[tuple[Path, float, float, int, int]] = []
    size = max(120, int(sticker_size))
    for i, cue in enumerate(cues[:6]):
        emoji = str(cue.get("emoji") or "").strip()
        png = resolve_emoji_png(emoji, cache_dir=cache, size=size)
        if png is None or not png.is_file():
            continue
        # Keep a stable copy in work_dir for QA
        local = work / f"emoji_{i}.png"
        try:
            shutil.copy2(png, local)
        except OSError:
            local = png
        start = float(cue.get("at_sec") or 0.0)
        dur = float(cue.get("duration_sec") or 1.8)
        x = max(24, width - size - 40)
        # Below dual title band (~ top 260px) so stickers don't cover 标题
        y = 300 + (i % 3) * (size // 4)
        stickers.append((local, max(0.0, start), max(0.8, dur), x, y))

    if not stickers:
        if td_ctx:
            td_ctx.cleanup()
        return {"ok": False, "error": "emoji PNG resolve failed (all cues)", "count": 0}

    # Build filter_complex chain
    inputs = ["-i", str(video)]
    for png, _, _, _, _ in stickers:
        inputs.extend(["-i", str(png)])
    parts: list[str] = []
    last = "[0:v]"
    for i, (_, start, dur, x, y) in enumerate(stickers):
        end = start + dur
        out_label = f"[v{i}]"
        parts.append(
            f"{last}[{i + 1}:v]overlay={x}:{y}:enable='between(t,{start:.3f},{end:.3f})'{out_label}"
        )
        last = out_label
    filt = ";".join(parts)
    out = work / "emoji_burned.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filt,
        "-map",
        last,
        "-map",
        "0:a?",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 64:
        err = (proc.stderr or proc.stdout or "emoji burn failed")[-1200:]
        if td_ctx:
            td_ctx.cleanup()
        return {"ok": False, "error": err, "count": 0}

    try:
        video.unlink(missing_ok=True)
        out.replace(video)
    except OSError as e:
        if td_ctx:
            td_ctx.cleanup()
        return {"ok": False, "error": str(e), "count": 0}
    if td_ctx:
        td_ctx.cleanup()
    return {"ok": True, "count": len(stickers), "out": str(video)}
