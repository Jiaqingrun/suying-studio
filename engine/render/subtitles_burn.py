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


def _wrap_lines_for_width(lines: list[str], width: int, font_size: int) -> list[str]:
    """Split overlong foreign lines on spaces so they fit the 1080 canvas."""
    max_w = max(200, width - 48)
    # Approx advance for Thai/Latin at this size (CoreText is authoritative later)
    approx = max(18, int(font_size * 0.55))
    max_chars = max(12, max_w // approx)
    out: list[str] = []
    for ln in lines:
        if _script_kind(ln) == "cjk" or len(ln) <= max_chars:
            out.append(ln)
            continue
        # Prefer space breaks for Thai / Latin
        words = ln.split(" ")
        if len(words) == 1:
            # hard wrap
            buf = ln
            while len(buf) > max_chars:
                out.append(buf[:max_chars].strip())
                buf = buf[max_chars:].strip()
            if buf:
                out.append(buf)
            continue
        cur = ""
        for w in words:
            trial = f"{cur} {w}".strip() if cur else w
            if len(trial) <= max_chars:
                cur = trial
            else:
                if cur:
                    out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    return out[:6] or lines


def _render_cue_png(
    text: str,
    width: int,
    *,
    font_size: int,
    out: Path,
    bottom_padding_px: int = 400,
) -> None:
    """White text + black outline, no background mask; auto dual-line."""
    raw = (text or "").strip()
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
    lines = _wrap_lines_for_width(lines, width, font_size)

    needs_coretext = any(_script_kind(ln) in ("thai", "arabic", "mixed") for ln in lines)
    if needs_coretext and _render_cue_png_coretext(lines, width, font_size=font_size, out=out):
        _ = bottom_padding_px
        return

    # Per-line fonts so Thai+Chinese bilingual cues both render
    fonts = [_find_font(font_size, ln) for ln in lines]
    pad_x, pad_y = 8, 6
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
    box_w = min(width, max(text_w + pad_x * 2, 32))
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


def _burn_with_overlay(
    video: Path,
    cues: list[dict[str, Any]],
    out: Path,
    *,
    font_size: int,
    bottom_padding_px: int = 400,
) -> dict[str, Any]:
    if not cues:
        shutil.copy2(video, out)
        return {"ok": True, "out": str(out), "error": None, "method": "copy", "cues": 0}

    w, _h = _probe_size(video)
    margin_v = max(24, int(bottom_padding_px))
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
                f"BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV={margin_v}"
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
        video, cues, out, font_size=font_size, bottom_padding_px=margin_v
    )
