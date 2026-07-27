from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from engine.config.settings import AppSettings
from engine.render.logo import (
    brand_from_profile,
    logo_overlay_opts,
    overlay_xy_expr,
    resolve_logo_path,
)
from engine.template.engine import MontagePlan


def _find_font(size: int, *, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Prefer bold Chinese faces for title overlays."""
    # (path, index) — PingFang/Heiti TTC: higher index ≈ heavier weight
    candidates: list[tuple[str, int]] = []
    if bold:
        candidates.extend(
            [
                ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
                ("/System/Library/Fonts/PingFang.ttc", 2),
                ("/System/Library/Fonts/PingFang.ttc", 1),
                ("/System/Library/Fonts/Hiragino Sans GB.ttc", 1),
                ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),
            ]
        )
    candidates.extend(
        [
            ("/System/Library/Fonts/PingFang.ttc", 0),
            ("/System/Library/Fonts/STHeiti Light.ttc", 0),
            ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
            ("/Library/Fonts/Arial Unicode.ttf", 0),
            (str(Path.home() / "Library/Fonts/NotoSansSC.ttf"), 0),
        ]
    )
    for path, index in candidates:
        if not Path(path).exists():
            continue
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


def clamp_title_text(title: str, max_chars: int = 10, max_lines: int = 2) -> str:
    """Keep titles short — max_lines × max_chars."""
    t = (title or "").strip().replace("\n", " ")
    budget = max_chars * max_lines
    if len(t) > budget:
        t = t[: budget - 1] + "…"
    return t


def _wrap_title(
    title: str,
    max_chars: int = 12,
    max_lines: int = 2,
    *,
    per_line_limit: int | None = None,
) -> str:
    """Auto line-break + center-ready lines.

    - Respect existing newlines (clamp each line).
    - Else split near midpoint when longer than one line budget.
    - per_line_limit: pixel-fit chars/line (large fonts); max_chars is content budget/line.
    """
    raw = (title or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for sep in ("｜", "|", "·", "—", "－"):
        raw = raw.replace(sep, "\n")
    line_cap = max(1, int(per_line_limit or max_chars))
    content_cap = max(line_cap, int(max_chars))

    if "\n" in raw:
        parts = [p.strip() for p in raw.split("\n") if p.strip()]
        lines = [p[:content_cap] for p in parts[:max_lines]]
        # If a single explicit line still overflows pixel budget, re-split it
        if len(lines) == 1 and len(lines[0]) > line_cap and max_lines >= 2:
            return _wrap_title(lines[0], max_chars=content_cap, max_lines=max_lines, per_line_limit=line_cap)
        return "\n".join(ln[:line_cap] if len(ln) > line_cap else ln for ln in lines)

    title = clamp_title_text(raw.replace("\n", " "), max_chars=content_cap, max_lines=max_lines)
    if max_lines <= 1 or len(title) <= line_cap:
        return title[:line_cap]
    # Balanced split near midpoint
    target = min(line_cap, max(1, (len(title) + 1) // 2))
    break_at = target
    for i in range(target, max(0, target - 3), -1):
        if i < len(title) and title[i - 1] in " 　":
            break_at = i
            break
    # Prefer not overflowing line 2 either
    if len(title) - break_at > line_cap:
        break_at = max(1, len(title) - line_cap)
    line1 = title[:break_at].strip()[:line_cap]
    line2 = title[break_at:].strip()[:line_cap]
    return "\n".join(ln for ln in (line1, line2) if ln)


def _hex_to_rgba(color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return (255, 255, 255, int(255 * alpha))
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (r, g, b, int(max(0, min(255, alpha * 255))))


def render_title_png(
    title: str,
    width: int,
    height: int,
    font_size: int,
    out_path: Path,
    *,
    position: str = "top",
    color: str = "#FFE600",
    bar_color: str = "#111827",
    bar_opacity: float = 0.0,
    max_chars: int = 10,
    max_lines: int = 2,
    bold: bool = True,
    stroke_width: int = 6,
    stroke_color: str = "#000000",
    layout: str = "dual_chip",
    offset_y_px: int = 120,
) -> Path:
    """Render title overlay.

    layout=dual_chip (default) / stroke:
      Auto wrap up to max_lines, each line centered.
      Yellow fill (#FFE600) + black stroke (#000000) — HARD LOCK.
      offset_y_px: 相对 band 基准整体下移（写死 120）。
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _find_font(font_size, bold=bold)
    # Large fonts: fit chars/line to canvas (glyph≈font_size + stroke)
    px_budget = max(200, int(width * 0.92) - int(stroke_width) * 2)
    chars_fit = max(4, min(int(max_chars), px_budget // max(int(font_size), 1)))
    text = _wrap_title(
        title,
        max_chars=max_chars,
        max_lines=max_lines,
        per_line_limit=chars_fit,
    )
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()] or [text]

    pos = (position or "top").lower()
    if pos == "middle":
        y_ratio = 0.42
    elif pos == "upper":
        y_ratio = 0.14
    else:
        y_ratio = 0.05

    dy = max(0, int(offset_y_px))

    # 黄字黑描边：双行居中自动换行（dual_chip 与 stroke 统一视觉）
    layout_key = (layout or "dual_chip").lower()
    if layout_key in {"dual_chip", "douyin_ref", "ref", "stroke", "red_yellow", "yellow_black"}:
        sw = max(8, int(stroke_width))
        gap = max(20, int(font_size * 0.12))
        fill = _hex_to_rgba(color, 1.0)
        stroke = _hex_to_rgba(stroke_color, 1.0)

        def _draw_stroked_line(ln: str, y0: int) -> int:
            bb = draw.textbbox((0, 0), ln, font=font)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            layer_w = tw + sw * 2 + 8
            layer_h = th + sw * 2 + 8
            layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            ox, oy = sw + 4, sw + 2
            for dx in range(-sw, sw + 1):
                for dyy in range(-sw, sw + 1):
                    if dx == 0 and dyy == 0:
                        continue
                    if dx * dx + dyy * dyy > sw * sw:
                        continue
                    ld.text((ox + dx, oy + dyy), ln, font=font, fill=stroke)
            ld.text((ox, oy), ln, font=font, fill=fill)
            bbox = layer.getbbox()
            if bbox:
                layer = layer.crop(bbox)
            lw, lh = layer.size
            x = (width - lw) // 2
            img.paste(layer, (x, y0), layer)
            return y0 + lh + gap

        approx_h = 0
        for ln in lines[:max_lines]:
            bb = draw.textbbox((0, 0), ln, font=font)
            approx_h += (bb[3] - bb[1]) + sw * 2 + gap
        y = int(height * y_ratio) + dy
        y = max(8, min(height - approx_h - 8, y))
        for ln in lines[:max_lines]:
            y = _draw_stroked_line(ln, y)
    else:
        # Fallback: multiline centered stroke
        spacing = max(14, int(font_size * 0.1))
        bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=spacing)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_y = 24
        bar_h = th + pad_y * 2
        bar_y = int(height * y_ratio) + dy
        bar_y = max(0, min(height - bar_h - 8, bar_y))
        if bar_opacity and bar_opacity > 0:
            draw.rectangle([(0, bar_y), (width, bar_y + bar_h)], fill=_hex_to_rgba(bar_color, bar_opacity))
        x = (width - tw) // 2
        y = bar_y + pad_y
        fill = _hex_to_rgba(color, 1.0)
        stroke = _hex_to_rgba(stroke_color, 1.0)
        sw = max(1, int(stroke_width))
        for dx in range(-sw, sw + 1):
            for dyy in range(-sw, sw + 1):
                if dx == 0 and dyy == 0:
                    continue
                if dx * dx + dyy * dyy > sw * sw:
                    continue
                draw.multiline_text((x + dx, y + dyy), text, font=font, fill=stroke, align="center", spacing=spacing)
        draw.multiline_text((x, y), text, font=font, fill=fill, align="center", spacing=spacing)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def _probe_size(path: str) -> tuple[int, int]:
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
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return 1920, 1080
    parts = (result.stdout or "1920,1080").strip().split(",")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 1920, 1080


def _smart_crop_x(source: str, at_sec: float, scaled_w: int, crop_w: int, sample_path: Path) -> int:
    """Pick horizontal crop offset by column energy (proxy for subject focus)."""
    max_x = max(0, scaled_w - crop_w)
    if max_x <= 0:
        return 0
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{at_sec:.3f}",
        "-i",
        source,
        "-frames:v",
        "1",
        "-vf",
        f"scale={scaled_w}:-1",
        str(sample_path),
    ]
    if subprocess.run(cmd, capture_output=True, check=False).returncode != 0 or not sample_path.exists():
        return max_x // 2
    try:
        img = Image.open(sample_path).convert("L")
    except OSError:
        return max_x // 2
    tw = min(160, img.width)
    th = max(1, int(img.height * tw / img.width))
    img = img.resize((tw, th))
    pixels = list(img.getdata())
    energies = [0.0] * tw
    for y in range(th):
        row = pixels[y * tw : (y + 1) * tw]
        for x in range(1, tw):
            energies[x] += abs(row[x] - row[x - 1])
    win = max(1, int(tw * crop_w / scaled_w))
    best_i, best_e = 0, -1.0
    for i in range(0, tw - win + 1):
        e = sum(energies[i : i + win])
        if e > best_e:
            best_e = e
            best_i = i
    x = int(best_i / tw * scaled_w)
    return max(0, min(max_x, x))


def build_reframe_vf(
    source: str,
    start_sec: float,
    duration_sec: float,
    width: int,
    height: int,
    mode: str,
    sample_path: Path,
) -> str:
    mode = (mode or "center").lower()
    src_w, src_h = _probe_size(source)
    scale = max(width / src_w, height / src_h)
    scaled_w = int(round(src_w * scale))
    scaled_h = int(round(src_h * scale))
    scaled_w += scaled_w % 2
    scaled_h += scaled_h % 2
    if mode == "smart":
        at = start_sec + max(0.05, duration_sec * 0.4)
        x = _smart_crop_x(source, at, scaled_w, width, sample_path)
        y = max(0, (scaled_h - height) // 2)
        return f"scale={scaled_w}:{scaled_h},crop={width}:{height}:{x}:{y},setsar=1"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def _source_has_audio(path: str) -> bool:
    probe = probe_output(Path(path))
    return any(s.get("codec_type") == "audio" for s in probe.get("streams", []))


def resolve_bgm_path(settings: AppSettings, seed: int) -> Path | None:
    """Pick a BGM file: customer 04-音乐 first, then global music_root.

    Prefer upbeat / pop / energetic tracks when available.
    """
    exts = {".mp3", ".m4a", ".wav", ".aac", ".flac"}
    dirs: list[Path] = []
    for root in settings.paths.all_library_roots():
        # …/01-片库 → sibling 04-音乐；或片库父级下的 04-音乐
        parent = root.parent
        candidate = parent / "04-音乐"
        if candidate.is_dir():
            dirs.append(candidate)
        if root.name not in {"01-片库", "片库"} and (root / "04-音乐").is_dir():
            dirs.append(root / "04-音乐")
    dirs.append(settings.paths.music_root)

    files: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in exts and p.is_file() and not p.name.startswith("."):
                key = str(p.resolve())
                if key not in seen:
                    seen.add(key)
                    files.append(p)
    # Prefer real library tracks over auto-generated placeholder pad
    real = [f for f in files if not f.name.startswith("placeholder-")]
    if real:
        files = real
    if not files:
        pad = _ensure_placeholder_bgm(settings.paths.music_root)
        return pad

    upbeat_keys = (
        "pop", "dance", "energy", "happy", "sunny", "summer", "hey", "groovy",
        "hiphop", "dubstep", "cute", "moose", "action", "buddy", "ukulele",
        "funk", "rock",
        # Soft-cheerful Kevin MacLeod batch (km-*) — curated for 日更/轻柔欢快
        "carefree", "easy-lemon", "wallpaper", "wholesome", "rainbows", "vivacity",
        "hyperfun", "upbeat-forever", "digital-lemonade", "whimsy", "fluffing",
        "merry-go", "porch-swing", "clear-waters", "beauty-flow", "eternal-hope",
        "almost-new", "beachfront", "cattails", "monkeys-spinning", "life-of-riley",
        "your-call", "feelin-good", "pinball", "run-amok", "faster-does",
        "thatched", "folk-round", "daily-beetle", "lobby-time", "mellowtron",
    )
    calm_keys = ("soul", "creative", "anewbeginning", "littleidea", "suspense", "gymnopedie")
    preferred = [
        f for f in files
        if any(k in f.name.lower() for k in upbeat_keys)
        and not any(k in f.name.lower() for k in calm_keys)
    ]
    pool = preferred or [
        f for f in files if not any(k in f.name.lower() for k in calm_keys)
    ] or files
    return pool[seed % len(pool)]


def _ensure_placeholder_bgm(music_root: Path) -> Path | None:
    """Soft pad so first-run machines without music still produce audible cuts."""
    music_root.mkdir(parents=True, exist_ok=True)
    out = music_root / "placeholder-soft-pad.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return out
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=196:duration=45",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=247:duration=45",
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2,volume=0.12,afade=t=in:st=0:d=1.5,afade=t=out:st=42:d=3",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(out),
    ]
    if subprocess.run(cmd, capture_output=True, check=False).returncode != 0:
        return None
    return out if out.exists() else None


def _render_segment(
    clip: Any,
    seg: Path,
    sample: Path,
    *,
    width: int,
    height: int,
    reframe_mode: str,
    ambient_gain: float,
    crf: str = "18",
) -> bool:
    vf = build_reframe_vf(
        clip.source_path,
        clip.start_sec,
        clip.duration_sec,
        width,
        height,
        reframe_mode,
        sample,
    )
    # Default: strip source audio. Only keep ambient when gain > 0.
    use_source_audio = ambient_gain > 0.001 and _source_has_audio(clip.source_path)
    if use_source_audio:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(clip.start_sec),
            "-i",
            clip.source_path,
            "-t",
            str(clip.duration_sec),
            "-vf",
            vf,
            "-af",
            f"volume={ambient_gain:.3f},aformat=sample_rates=48000:channel_layouts=stereo",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            crf,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(seg),
        ]
    else:
        # Silent stereo bed so concat always has an audio stream (BGM mixed later)
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(clip.start_sec),
            "-i",
            clip.source_path,
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            str(clip.duration_sec),
            "-vf",
            vf,
            "-r",
            "30",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            crf,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(seg),
        ]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0


def resolve_canvas_size(
    clips: list[Any],
    template_width: int,
    template_height: int,
    *,
    max_width: int = 2160,
    max_height: int = 3840,
) -> tuple[int, int]:
    """
    Pick output canvas so we never downscale any source below its native size.
    Portrait sources contribute their WxH; landscape sources contribute a 9:16 crop
    sized to their height. Result is at least the template size, capped for safety.
    """
    w = int(template_width)
    h = int(template_height)
    for clip in clips:
        src = getattr(clip, "source_path", None)
        if not src:
            continue
        sw, sh = _probe_size(str(src))
        if sw <= 0 or sh <= 0:
            continue
        if sh >= sw:
            # already portrait
            w = max(w, sw)
            h = max(h, sh)
        else:
            # landscape → vertical 9:16 crop uses full height
            crop_h = sh
            crop_w = int(round(sh * 9 / 16))
            crop_w += crop_w % 2
            w = max(w, crop_w)
            h = max(h, crop_h)
    w = min(w, max_width)
    h = min(h, max_height)
    w -= w % 2
    h -= h % 2
    return max(2, w), max(2, h)


def render_plan(
    settings: AppSettings,
    plan: MontagePlan,
    output_path: Path,
    template_meta: dict[str, Any],
    *,
    render_meta: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    customer_name: str | None = None,
    narration_path: Path | None = None,
) -> bool:
    temp_dir = settings.paths.cache_root / "temp" / f"job_{plan.seed}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    segment_paths: list[Path] = []

    tpl_w = int(template_meta.get("output_width", 1080))
    tpl_h = int(template_meta.get("output_height", 1920))
    preserve = bool(getattr(settings, "preserve_source_resolution", True))
    if preserve and plan.clips:
        width, height = resolve_canvas_size(plan.clips, tpl_w, tpl_h)
    else:
        width, height = tpl_w, tpl_h
    reframe_mode = str(template_meta.get("reframe_mode", "smart"))
    keep_source = bool(getattr(settings, "keep_source_audio", False))
    ambient_gain = float(getattr(settings, "ambient_gain", 0.0) or 0.0)
    if not keep_source:
        ambient_gain = 0.0
    bgm_gain = float(getattr(settings, "bgm_gain", 0.45) or 0.45)
    narration_gain = float(getattr(settings, "narration_gain", 1.0) or 1.0)
    bgm_bed_gain = float(getattr(settings, "bgm_bed_gain", 0.2) or 0.2)
    target_lufs = float(getattr(settings, "audio_target_lufs", -14.0) or -14.0)
    narr = Path(narration_path) if narration_path else None
    if narr is not None and not narr.is_file():
        narr = None
    # Higher quality encode when preserving resolution
    crf = "18" if preserve else "23"
    final_crf = "17" if preserve else "22"

    if render_meta is not None:
        render_meta["output_width"] = width
        render_meta["output_height"] = height
        render_meta["preserve_source_resolution"] = preserve
        render_meta["template_width"] = tpl_w
        render_meta["template_height"] = tpl_h

    for i, clip in enumerate(plan.clips):
        seg = temp_dir / f"seg_{i:03d}.mp4"
        sample = temp_dir / f"sample_{i:03d}.jpg"
        if not _render_segment(
            clip,
            seg,
            sample,
            width=width,
            height=height,
            reframe_mode=reframe_mode,
            ambient_gain=ambient_gain,
            crf=crf,
        ):
            return False
        segment_paths.append(seg)

    if not segment_paths:
        return False

    list_file = temp_dir / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in segment_paths),
        encoding="utf-8",
    )
    merged = temp_dir / "merged.mp4"
    # Re-encode concat so A/V timestamps stay aligned across segments
    concat_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        crf,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(merged),
    ]
    if subprocess.run(concat_cmd, capture_output=True, check=False).returncode != 0:
        return False

    title_png = temp_dir / "title.png"
    style = getattr(plan, "title_style", None) or {}
    max_chars = int(style.get("max_chars") or template_meta.get("title_max_chars", 10))
    max_lines = int(style.get("max_lines") or template_meta.get("title_max_lines", 2))
    render_title_png(
        plan.title,
        width,
        height,
        int(style.get("font_size") or template_meta.get("title_font_size", 92)),
        title_png,
        position=str(style.get("position") or template_meta.get("title_position", "top")),
        color=str(style.get("color") or template_meta.get("title_color", "#FFE600")),
        bar_color=str(style.get("bar_color") or template_meta.get("title_bar_color", "#111827")),
        bar_opacity=float(
            style.get("bar_opacity")
            if style.get("bar_opacity") is not None
            else template_meta.get("title_bar_opacity", 0.0)
        ),
        max_chars=max_chars,
        max_lines=max_lines,
        bold=bool(style.get("bold", template_meta.get("title_bold", True))),
        stroke_width=int(style.get("stroke_width") or template_meta.get("title_stroke_width", 6)),
        stroke_color=str(style.get("stroke_color") or template_meta.get("title_stroke_color", "#000000")),
        layout=str(style.get("layout") or template_meta.get("title_layout", "dual_chip")),
        offset_y_px=int(
            style.get("offset_y_px")
            if style.get("offset_y_px") is not None
            else template_meta.get("title_offset_y_px", 120)
        ),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bgm = resolve_bgm_path(settings, plan.seed)
    effective_bgm_gain = bgm_bed_gain if narr is not None else bgm_gain
    if render_meta is not None:
        render_meta["bgm_path"] = str(bgm) if bgm else None
        render_meta["bgm_name"] = bgm.name if bgm else None
        render_meta["keep_source_audio"] = keep_source and ambient_gain > 0
        render_meta["ambient_gain"] = ambient_gain
        render_meta["narration_path"] = str(narr) if narr else None
        render_meta["narration_gain"] = narration_gain if narr else None
        render_meta["bgm_gain_effective"] = effective_bgm_gain if bgm else None
        if bgm and "bensound" in bgm.name.lower():
            render_meta["music_credit"] = f"Music: {bgm.stem.replace('bensound-', '')} — Bensound.com"
        elif bgm and bgm.name.lower().startswith("km-"):
            title = bgm.stem[3:].replace("-", " ").strip().title()
            render_meta["music_credit"] = (
                f"Music: {title} by Kevin MacLeod (incompetech.com) · CC BY 3.0"
            )
        elif bgm and not bgm.name.startswith("placeholder-"):
            render_meta["music_credit"] = f"Music: {bgm.stem}"
        else:
            render_meta["music_credit"] = "Music: generated pad"
    loudnorm = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"

    brand = brand_from_profile(profile)
    logo_path = resolve_logo_path(settings, profile=profile, customer_name=customer_name)
    logo_opts = logo_overlay_opts(brand, width) if logo_path else None
    if render_meta is not None:
        render_meta["logo_path"] = str(logo_path) if logo_path else None
        render_meta["logo_applied"] = bool(logo_path)

    def _video_chain(logo_input_idx: int | None) -> str:
        """Title overlay, then optional logo corner."""
        if logo_input_idx is None or logo_opts is None:
            return "[0:v][1:v]overlay=0:0[v]"
        mw = logo_opts["max_width"]
        xy = overlay_xy_expr(str(logo_opts["position"]), int(logo_opts["margin"]))
        li = logo_input_idx
        return (
            f"[0:v][1:v]overlay=0:0[vt];"
            f"[{li}:v]scale={mw}:-1:force_original_aspect_ratio=decrease,format=rgba[lg];"
            f"[vt][lg]overlay={xy}[v]"
        )

    # Input layout: 0=merged 1=title [2=bgm?] [N=narr?] [L=logo?]
    final_cmd: list[str] = ["ffmpeg", "-y", "-i", str(merged), "-i", str(title_png)]
    next_idx = 2
    bgm_idx: int | None = None
    narr_idx: int | None = None
    if bgm and bgm.exists():
        final_cmd.extend(["-stream_loop", "-1", "-i", str(bgm)])
        bgm_idx = next_idx
        next_idx += 1
    if narr is not None:
        final_cmd.extend(["-i", str(narr)])
        narr_idx = next_idx
        next_idx += 1
    logo_idx: int | None = None
    if logo_path:
        final_cmd.extend(["-i", str(logo_path)])
        logo_idx = next_idx
        next_idx += 1

    # Audio graph: narration lead + BGM bed
    # duration=longest so short foreign VO does not silence-cut BGM before picture ends
    audio_parts: list[str] = []
    if narr_idx is not None and bgm_idx is not None:
        audio_parts = [
            f"[{narr_idx}:a]volume={narration_gain:.3f},aformat=sample_rates=48000:channel_layouts=stereo[nar]",
            f"[{bgm_idx}:a]volume={effective_bgm_gain:.3f},aformat=sample_rates=48000:channel_layouts=stereo[bg]",
            f"[nar][bg]amix=inputs=2:duration=longest:dropout_transition=2[mix]",
            f"[mix]{loudnorm}[a]",
        ]
    elif narr_idx is not None:
        audio_parts = [
            f"[{narr_idx}:a]volume={narration_gain:.3f},aformat=sample_rates=48000:channel_layouts=stereo,{loudnorm}[a]",
        ]
    elif bgm_idx is not None and ambient_gain > 0.001:
        audio_parts = [
            f"[{bgm_idx}:a]volume={effective_bgm_gain:.3f}[bg]",
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[mix]",
            f"[mix]{loudnorm}[a]",
        ]
    elif bgm_idx is not None:
        audio_parts = [f"[{bgm_idx}:a]volume={effective_bgm_gain:.3f},{loudnorm}[a]"]
    else:
        audio_parts = [f"[0:a]{loudnorm}[a]"]

    filter_complex = f"{_video_chain(logo_idx)};" + ";".join(audio_parts)
    final_cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            final_crf,
            "-c:a",
            "aac",
            "-b:a",
            "160k",
        ]
    )
    # With narration: keep full picture when VO is much shorter (foreign short templates).
    # Only trim to VO when narration nearly fills the cut (classic 晓晓 贯穿).
    if narr_idx is not None:
        from engine.pack.tts import probe_audio_duration

        merged_probe = probe_output(merged)
        try:
            vdur = float((merged_probe.get("format") or {}).get("duration") or 0)
        except (TypeError, ValueError):
            vdur = 0.0
        try:
            ndur = float(probe_audio_duration(Path(narr))) if narr else 0.0
        except Exception:
            ndur = 0.0
        if vdur > 0.1 and ndur > 0.1:
            if ndur >= max(0.1, vdur - 1.5):
                # VO fills picture — trim tiny tail so旁白贯穿
                final_t = min(vdur, ndur + 0.12)
            else:
                # Short VO: keep full picture; BGM continues after speech
                final_t = vdur
            final_cmd.extend(["-t", f"{final_t:.3f}"])
        elif ndur > 0.1:
            final_cmd.extend(["-t", f"{ndur + 0.12:.3f}"])
        else:
            final_cmd.append("-shortest")
    else:
        final_cmd.append("-shortest")
    final_cmd.append(str(output_path))

    ok = subprocess.run(final_cmd, capture_output=True, check=False).returncode == 0
    return ok and output_path.exists()


def probe_output(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {}
    return json.loads(result.stdout or "{}")
