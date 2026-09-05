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
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_preferred_font_path: ContextVar[Path | None] = ContextVar(
    "suying_subtitle_preferred_font", default=None
)


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


def _find_font(
    size: int,
    text: str = "",
    *,
    font_path: str | Path | None = None,
) -> ImageFont.ImageFont:
    """Pick a font that can actually render the cue (Thai must not use SC-only fonts)."""
    preferred = font_path or _preferred_font_path.get()
    if preferred:
        p = Path(preferred)
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size, index=0)
            except OSError:
                try:
                    return ImageFont.truetype(str(p), size=size)
                except OSError:
                    pass
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
            (
                str(
                    Path(__file__).resolve().parents[2]
                    / "configs/fx_assets/fonts/NotoSansSC-Regular.ttf"
                ),
                0,
            ),
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
            (str(Path.home() / "Library/Fonts/NotoSansSC.ttf"), 0),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("/Library/Fonts/Arial Unicode.ttf", 0),
        ],
        "latin": [
            (
                str(
                    Path(__file__).resolve().parents[2]
                    / "configs/fx_assets/fonts/Inter-Regular.ttf"
                ),
                0,
            ),
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
    # HARD: multi-line cues must not visually stack — gap scales with font size
    line_gap = max(16, int(font_size * 0.28))
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
    Latin uses measured pixel width too (approx char count alone underestimates wide
    strings after stroke and clips when box_h is underestimated).
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
        # Prefer measured wrap for Latin (and empty-space Latin-like) when Pillow works.
        if kind == "latin":
            wrapped = _wrap_latin_line(ln, max_w, font_size, stroke_w=stroke_w)
            if wrapped:
                out.extend(wrapped)
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


def _wrap_latin_line(text: str, max_w: int, font_size: int, *, stroke_w: int = 3) -> list[str]:
    """Word-first wrap by measured width so English stroke stays inside the box."""
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
    words = t.split(" ")
    if len(words) == 1:
        # hard break long tokens
        out: list[str] = []
        buf = ""
        for ch in t:
            trial = buf + ch
            if buf and _w(trial) > max_w:
                out.append(buf)
                buf = ch
            else:
                buf = trial
        if buf:
            out.append(buf)
        return out or [t]
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip() if cur else w
        if cur and _w(trial) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [t]


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
    bottom_padding_px: int = 420,
    side_margin_px: int = 48,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 3,
) -> None:
    """White text + black outline, no background mask; auto dual-line; side margins.

    HARD: when cue contains emoji, composite Twemoji PNGs (fonts tofu □).
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("字幕 cue 文本为空，无法生成可见字形")
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
    lines = [ln for ln in lines if ln]
    if not lines:
        raise ValueError(f"字幕 cue 去空白后为空: {raw!r}")
    lines = _wrap_lines_for_width(
        lines,
        width,
        font_size,
        side_margin_px=margin,
        stroke_w=max(0, int(stroke_width)),
    )

    from engine.pack.emoji_stickers import text_has_emoji

    # HARD / FREEZE (E3+E8+E9 · 2026-08-07 accepted): any cue with emoji MUST composite
    # Twemoji PNGs — fonts tofu/grey.mono. Do NOT reintroduce default_style / stroke==3
    # gates; production rules freeze stroke_width=4. Upgrades must keep this path.
    if any(text_has_emoji(ln) for ln in lines):
        if _render_cue_png_with_twemoji(
            lines,
            width,
            font_size=font_size,
            out=out,
            side_margin_px=margin,
            color=color,
            stroke_color=stroke_color,
            stroke_width=stroke_width,
        ):
            _crop_to_alpha(out, source_text=raw)
            _ = bottom_padding_px
            return
        # Soft degrade when cache/CDN/composite fails under worker load: burn text only
        # rather than abort the whole montage (caller still prefers Twemoji first).
        from engine.pack.emoji_stickers import strip_emoji_for_speech

        lines = [strip_emoji_for_speech(ln).strip(" ，,") for ln in lines]
        lines = [ln for ln in lines if ln]
        if not lines:
            raise ValueError(
                f"字幕行内 emoji 未能渲染为彩色 Twemoji（资源或合成失败）: {raw!r}"
            )

    needs_coretext = any(_script_kind(ln) in ("thai", "arabic", "mixed") for ln in lines)
    default_style = (
        color.upper() == "#FFFFFF"
        and stroke_color.upper() == "#000000"
        and int(stroke_width) in (3, 4)
    )
    if default_style and needs_coretext and _render_cue_png_coretext(lines, width, font_size=font_size, out=out):
        _crop_to_alpha(out, source_text=raw)
        _ = bottom_padding_px
        return

    # Per-line fonts so Thai+Chinese bilingual cues both render
    fonts = [_find_font(font_size, ln) for ln in lines]
    pad_x, pad_y = 12, 10
    line_gap = max(16, int(font_size * 0.28))
    tmp = Image.new("RGBA", (max(width, 64), max(font_size * (len(lines) + 4), 64)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tmp)
    # Each entry: (ink_w, ink_h, draw_dx, draw_dy) — dx/dy cancel bbox origin so
    # stroke/ascenders/descenders are not clipped when origin is not (0,0).
    sizes: list[tuple[int, int, int, int]] = []
    stroke_w = max(0, min(12, int(stroke_width)))

    def _rgba(value: str, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        text = str(value or "").lstrip("#")
        if len(text) != 6:
            return fallback
        try:
            return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), 255)
        except ValueError:
            return fallback

    fill_rgba = _rgba(color, (255, 255, 255, 255))
    stroke_rgba = _rgba(stroke_color, (0, 0, 0, 255))
    for ln, font in zip(lines, fonts):
        bbox = draw.textbbox((0, 0), ln, font=font, stroke_width=stroke_w)
        # textbbox can return top/left < 0 for TrueType; height must use full span.
        ink_w = max(1, int(bbox[2] - bbox[0]))
        ink_h = max(1, int(bbox[3] - bbox[1]))
        # Never size the line box shorter than the nominal font (Latin baselines often
        # report a short bbox and then clip understrokes when drawn at y=pad).
        ink_h = max(ink_h, int(font_size * 1.05) + stroke_w * 2)
        ink_w = max(ink_w, 1)
        sizes.append((ink_w, ink_h, int(bbox[0]), int(bbox[1])))
    text_w = max((w for w, _, _, _ in sizes), default=0)
    text_h = sum(h for _, h, _, _ in sizes) + max(0, len(lines) - 1) * line_gap
    # Cap box inside side margins so overlay never kisses frame edges
    max_box = max(32, width - margin * 2)
    box_w = min(max_box, max(text_w + pad_x * 2, 32))
    box_h = max(text_h + pad_y * 2, int(font_size * 1.25) + pad_y * 2)
    # Fully transparent — no pill / bar / mask
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = pad_y
    for ln, font, (lw, lh, b0, b1) in zip(lines, fonts, sizes):
        x = (box_w - lw) // 2
        # Place so the full textbbox (incl. stroke) sits inside [x, x+lw] × [y, y+lh]
        draw.text(
            (x - b0, y - b1),
            ln,
            font=font,
            fill=fill_rgba,
            stroke_width=stroke_w,
            stroke_fill=stroke_rgba,
        )
        y += lh + line_gap
    img.save(out)
    _crop_to_alpha(out, source_text=raw, stroke_pad=max(2, stroke_w + 1))
    _ = bottom_padding_px


def _crop_to_alpha(
    path: Path,
    *,
    horizontal_pad: int = 4,
    stroke_pad: int = 0,
    source_text: str | None = None,
) -> tuple[int, int, int, int]:
    image = Image.open(path).convert("RGBA")
    bbox = image.getbbox()
    if not bbox:
        detail = f" text={source_text!r}" if source_text is not None else ""
        raise ValueError(f"字幕 PNG 没有可见字形{detail}")
    # Horizontal pad keeps stroke off PNG edge. Small vertical pad prevents
    # half-letter crops (Latin descenders / stroke) while still roughly
    # measuring glyph bottom for H-h-bottom_padding.
    pad_h = max(0, int(horizontal_pad))
    pad_v = max(0, int(stroke_pad))
    guarded = (
        max(0, bbox[0] - pad_h),
        max(0, bbox[1] - pad_v),
        min(image.width, bbox[2] + pad_h),
        min(image.height, bbox[3] + pad_v),
    )
    image.crop(guarded).save(path)
    return bbox


def _emoji_twemoji_cache_dir() -> Path:
    """Writable Twemoji cache: prefer settings.cache_root, then data_root, local, temp.

    Never put cache on customer output roots. External volume may be offline —
    always return a path that can be created/written so CDN/seed still works.
    """
    candidates: list[Path] = []
    try:
        from engine.config.settings import load_settings

        settings = load_settings()
        paths = getattr(settings, "paths", None)
        if paths is not None:
            cache_root = getattr(paths, "cache_root", None)
            if cache_root:
                candidates.append(Path(cache_root).expanduser() / "emoji_twemoji")
            data_root = getattr(paths, "data_root", None)
            if data_root:
                candidates.append(Path(data_root).expanduser() / "cache" / "emoji_twemoji")
    except Exception:
        pass
    candidates.append(Path.home() / "Suying" / "cache" / "emoji_twemoji")
    candidates.append(Path(tempfile.gettempdir()) / "suying_emoji_twemoji")
    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write_ok"
            probe.write_bytes(b"1")
            probe.unlink(missing_ok=True)
            return cand
        except OSError:
            continue
    # Last resort: cwd-relative (should be rare)
    fallback = Path.home() / "Suying" / "cache" / "emoji_twemoji"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _hex_to_rgba(value: str, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    text = str(value or "").lstrip("#")
    if len(text) != 6:
        return fallback
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), 255)
    except ValueError:
        return fallback


def cue_png_colorful_pixel_count(path: Path, *, chroma_min: int = 28) -> int:
    """Count non-grayscale opaque pixels (Twemoji are multi-hue; pure CJK stroke is not)."""
    try:
        img = Image.open(path).convert("RGBA")
        import numpy as np

        a = np.asarray(img, dtype=np.int16)
        if a.ndim != 3 or a.shape[2] < 4:
            return 0
        alpha = a[:, :, 3] >= 40
        if not alpha.any():
            return 0
        rgb = a[:, :, :3]
        chroma = rgb.max(axis=2) - rgb.min(axis=2)
        return int((alpha & (chroma >= chroma_min)).sum())
    except Exception:
        return 0


def cue_png_twemoji_chroma_count(
    path: Path,
    *,
    text_color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    chroma_min: int = 28,
    near: int = 36,
) -> int:
    """Count multi-hue pixels that are *not* near text/stroke fill (true Twemoji signal).

    Orange/red subtitle styles have high global chroma from CJK fill alone; ignoring
    near-fill/near-stroke pixels isolates pasted Twemoji.
    """
    try:
        import numpy as np

        img = Image.open(path).convert("RGBA")
        a = np.asarray(img, dtype=np.int16)
        if a.ndim != 3 or a.shape[2] < 4:
            return 0
        fill = np.array(_hex_to_rgba(text_color, (255, 255, 255, 255))[:3], dtype=np.int16)
        stroke = np.array(_hex_to_rgba(stroke_color, (0, 0, 0, 255))[:3], dtype=np.int16)
        rgb = a[:, :, :3]
        alpha = a[:, :, 3] >= 40
        chroma = rgb.max(axis=2) - rgb.min(axis=2)
        near_fill = np.abs(rgb - fill).sum(axis=2) <= near * 3
        near_stroke = np.abs(rgb - stroke).sum(axis=2) <= near * 3
        mask = alpha & (chroma >= chroma_min) & (~near_fill) & (~near_stroke)
        return int(mask.sum())
    except Exception:
        return 0


def _render_cue_png_with_twemoji(
    lines: list[str],
    width: int,
    *,
    font_size: int,
    out: Path,
    side_margin_px: int = 48,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 3,
) -> bool:
    """Draw CJK text + inline Twemoji (no title stickers; emoji live in subtitle).

    Text fill/stroke follow production-rule colors; emoji glyphs are always
    multi-color Twemoji rasters (``plate=False``).
    """
    try:
        from engine.pack.emoji_stickers import (
            iter_emoji_tokens,
            resolve_emoji_png,
            text_has_emoji,
        )
    except Exception:
        return False

    cache = _emoji_twemoji_cache_dir()
    emoji_px = max(28, int(font_size * 1.05))
    pad_x, pad_y = 10, 8
    line_gap = max(16, int(font_size * 0.28))
    stroke_w = max(0, min(12, int(stroke_width)))
    fill_rgba = _hex_to_rgba(color, (255, 255, 255, 255))
    stroke_rgba = _hex_to_rgba(stroke_color, (0, 0, 0, 255))
    margin = max(24, int(side_margin_px))
    max_box = max(32, width - margin * 2)

    # Build per-line runs: ("text", str) | ("emoji", Path)
    rendered_lines: list[list[tuple[str, Any]]] = []
    line_sizes: list[tuple[int, int]] = []
    emoji_assets = 0
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
                    emoji_assets += 1
                else:
                    # Prefer skip tofu over embedding raw codepoints as mono font.
                    runs.append(("text", " "))
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

    if emoji_assets < 1:
        # Caller asked for Twemoji because SRT has emoji glyphs; if assets failed,
        # return False so we do not claim success with empty/mono faces.
        return False

    text_w = max((w for w, _ in line_sizes), default=0)
    text_h = sum(h for _, h in line_sizes) + max(0, len(lines) - 1) * line_gap
    box_w = min(max_box, max(text_w + pad_x * 2, 32))
    box_h = text_h + pad_y * 2
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = pad_y
    pasted = 0
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
                    pasted += 1
                except Exception:
                    continue
            else:
                draw.text(
                    (x, y),
                    str(val),
                    font=font,
                    fill=fill_rgba,
                    stroke_width=stroke_w,
                    stroke_fill=stroke_rgba,
                )
                bbox = draw.textbbox((0, 0), str(val), font=font, stroke_width=stroke_w)
                x += int(bbox[2] - bbox[0])
        y += lh + line_gap
    img.save(out)
    # File size alone is not enough: a fully transparent canvas can exceed 200B
    # when emoji assets fail to paste. Only claim success with visible + color glyphs.
    try:
        visible = Image.open(out).convert("RGBA").getbbox()
    except Exception:
        visible = None
    if not visible or pasted < 1:
        return False
    # Twemoji must introduce chroma distinct from text/stroke fill.
    if cue_png_twemoji_chroma_count(out, text_color=color, stroke_color=stroke_color) < 60:
        return False
    return True


def _burn_with_overlay(
    video: Path,
    cues: list[dict[str, Any]],
    out: Path,
    *,
    font_size: int,
    bottom_padding_px: int = 420,
    side_margin_px: int = 48,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 3,
    align: str = "center",
    center_x_pct: float | None = None,
    center_y_pct: float | None = None,
    subtitle_layout: str = "horizontal",
    subtitle_vertical_side: str = "left",
    narration_text_effect: str = "none",
) -> dict[str, Any]:
    if not cues:
        shutil.copy2(video, out)
        return {"ok": True, "out": str(out), "error": None, "method": "copy", "cues": 0}

    w, h = _probe_size(video)
    margin_v = max(24, int(bottom_padding_px))
    side = max(24, int(side_margin_px))
    layout = str(subtitle_layout or "horizontal").strip().lower()
    vside = str(subtitle_vertical_side or "left").strip().lower()
    effect = str(narration_text_effect or "none").strip().lower()
    with tempfile.TemporaryDirectory(prefix="suying-burn-") as td:
        work = Path(td)
        v_local = work / "in.mp4"
        shutil.copy2(video, v_local)
        pngs: list[Path] = []
        active_cues: list[dict[str, Any]] = []
        for i, cue in enumerate(cues):
            text = str(cue.get("text") or "").strip()
            if not text:
                continue
            if effect == "marquee":
                from engine.render.subtitle_fx import marquee_slice

                text = marquee_slice(text, progress=0.35, window=10) or text
            png = work / f"cue_{len(active_cues):03d}.png"
            if layout == "vertical":
                from engine.render.subtitle_fx import (
                    karaoke_highlight_count,
                    render_vertical_cue_png,
                )

                hi = (
                    karaoke_highlight_count(text, progress=0.55)
                    if effect == "karaoke"
                    else 0
                )
                render_vertical_cue_png(
                    text,
                    canvas_w=w,
                    canvas_h=h,
                    font_size=font_size,
                    out=png,
                    side=vside if vside in {"left", "right"} else "left",
                    color=color,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    highlight_count=hi,
                    center_x_pct=center_x_pct,
                    center_y_pct=center_y_pct,
                )
            else:
                _render_cue_png(
                    text,
                    w,
                    font_size=font_size,
                    out=png,
                    bottom_padding_px=margin_v,
                    side_margin_px=side,
                    color="#FFE600" if effect == "karaoke" else color,
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                )
            pngs.append(png)
            active_cues.append(cue)
        if not active_cues:
            shutil.copy2(video, out)
            return {
                "ok": True,
                "out": str(out),
                "error": None,
                "method": "copy_empty_cues",
                "cues": 0,
            }

        cmd: list[str] = ["ffmpeg", "-y", "-i", "in.mp4"]
        for png in pngs:
            cmd.extend(["-i", png.name])

        # Center horizontally; PNG is alpha-cropped, therefore this is the
        # final visible-glyph bottom distance, not an ASS baseline approximation.
        filters: list[str] = []
        last = "[0:v]"
        for i, cue in enumerate(active_cues):
            enable = f"between(t\\,{cue['start']:.3f}\\,{cue['end']:.3f})"
            nxt = f"[v{i}]" if i < len(active_cues) - 1 else "[vout]"
            if layout == "vertical":
                # Full-frame PNG already placed; overlay at 0:0
                filters.append(
                    f"{last}[{i + 1}:v]overlay=0:0:enable='{enable}'{nxt}"
                )
            else:
                if center_x_pct is not None:
                    try:
                        xp = max(8.0, min(92.0, float(center_x_pct)))
                    except (TypeError, ValueError):
                        xp = 50.0
                    x_expr = f"max({side}\\,min(W-w-{side}\\,W*{xp}/100-w/2))"
                else:
                    x_expr = (
                        str(side)
                        if align == "left"
                        else f"W-w-{side}"
                        if align == "right"
                        else "(W-w)/2"
                    )
                filters.append(
                    f"{last}[{i + 1}:v]overlay={x_expr}:H-h-{margin_v}:enable='{enable}'{nxt}"
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
                "cues": len(active_cues),
            }
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        shutil.copy2(o_local, out)
    return {"ok": True, "out": str(out), "error": None, "method": "overlay", "cues": len(active_cues)}


def burn_srt_into_video(
    video: Path,
    srt: Path,
    out: Path,
    *,
    font_size: int = 64,
    bottom_padding_px: int = 420,
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 3,
    align: str = "center",
    subtitle_layout: str = "horizontal",
    subtitle_vertical_side: str = "left",
    narration_text_effect: str = "none",
    font_family: str | None = None,
    center_x_pct: float | None = None,
    center_y_pct: float | None = None,
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

    token = None
    try:
        from engine.pack.fx_assets import resolve_font_file

        resolved = resolve_font_file(font_family)
        if resolved is not None:
            token = _preferred_font_path.set(resolved)
    except Exception:
        token = None

    try:
        # HARD: Pillow+overlay is authoritative because ASS MarginV is a baseline
        # approximation and cannot prove the final alpha bbox bottom is exactly 420.
        return _burn_with_overlay(
            video,
            cues,
            out,
            font_size=font_size,
            bottom_padding_px=margin_v,
            side_margin_px=48,
            color=color,
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            align=align,
            center_x_pct=center_x_pct,
            center_y_pct=center_y_pct,
            subtitle_layout=subtitle_layout,
            subtitle_vertical_side=subtitle_vertical_side,
            narration_text_effect=narration_text_effect,
        )
    finally:
        if token is not None:
            _preferred_font_path.reset(token)


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
