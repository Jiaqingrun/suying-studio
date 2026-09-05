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


def _find_font(
    size: int,
    *,
    bold: bool = True,
    font_path: str | Path | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Prefer explicit font_path, then bold Chinese faces for title overlays."""
    if font_path:
        p = Path(font_path)
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size, index=0)
            except OSError:
                try:
                    return ImageFont.truetype(str(p), size=size)
                except OSError:
                    pass
    # (path, index) — PingFang/Heiti TTC: higher index ≈ heavier weight
    candidates: list[tuple[str, int]] = []
    # Tier A bundled fonts (OFL) when present
    for rel in (
        "configs/fx_assets/fonts/NotoSansSC-Regular.otf",
        "configs/fx_assets/fonts/NotoSansSC-Regular.ttf",
        "configs/fx_assets/fonts/LXGWWenKai-Regular.ttf",
        "configs/fx_assets/fonts/ZCOOLKuaiLe-Regular.ttf",
        "configs/fx_assets/fonts/Inter-Regular.ttf",
        "configs/fx_assets/fonts/Inter-Regular.otf",
    ):
        candidates.append((str(Path(__file__).resolve().parents[2] / rel), 0))
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


def clamp_title_text(title: str, max_chars: int = 24, max_lines: int = 2) -> str:
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


def _balanced_wrap_chars(text: str, *, max_lines: int = 2) -> list[str]:
    """Split a title into up to max_lines centered-friendly chunks near midpoints."""
    compact = (text or "").replace("\n", "").replace("\r", "").replace(" ", "").replace("　", "")
    if not compact:
        return []
    if max_lines <= 1 or len(compact) <= 1:
        return [compact]
    # Two-line balanced wrap (primary case for 24-char overflow).
    if max_lines == 2:
        mid = (len(compact) + 1) // 2
        # Prefer a nearby break that keeps both sides non-empty.
        break_at = mid
        for delta in range(0, max(1, len(compact) // 4) + 1):
            for at in (mid - delta, mid + delta):
                if 1 <= at < len(compact):
                    break_at = at
                    break
            else:
                continue
            break
        return [compact[:break_at], compact[break_at:]]
    # Generic equal-ish chunks
    size = (len(compact) + max_lines - 1) // max_lines
    return [compact[i : i + size] for i in range(0, len(compact), size)][:max_lines]


def _measure_title_fit(
    lines: list[str],
    *,
    width: int,
    font_size: int,
    min_font_size: int,
    bold: bool,
    stroke_width: int,
    font_path: str | Path | None = None,
) -> tuple[object, list[tuple[int, int, int, int]], int] | None:
    """Return (font, boxes, chosen_size) if every line fits within the safe width."""
    sw = max(8, int(stroke_width))
    available = width - 80
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    for size in range(int(font_size), int(min_font_size) - 1, -2):
        candidate = _find_font(size, bold=bold, font_path=font_path)
        boxes = [probe.textbbox((0, 0), line, font=candidate, stroke_width=sw) for line in lines]
        if all((box[2] - box[0]) <= available for box in boxes):
            return candidate, boxes, size
    return None


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
    max_chars: int = 24,
    max_lines: int = 2,
    bold: bool = True,
    stroke_width: int = 6,
    stroke_color: str = "#000000",
    layout: str = "dual_chip",
    offset_y_px: int = 0,
    glyph_top_px: int = 220,
    min_font_size: int = 72,
    align: str = "center",
    font_family: str | None = None,
    center_x_pct: float | None = None,
) -> Path:
    """Render on-screen title; ≤max_chars, auto-wrap+center when a line overflows width.

    Flow:
    1) Clamp total content to ``max_chars`` (default 24).
    2) Prefer existing newlines; try font scale down to ``min_font_size``.
    3) If still over-wide and ``max_lines≥2``, auto-wrap near midpoint and re-fit.
    4) Each line is horizontally centered. Glyph top stays locked at 220px (scaled).
    """
    _ = (position, bar_color, bar_opacity, layout, offset_y_px)
    max_chars = max(1, int(max_chars))
    max_lines = max(1, int(max_lines))
    resolved_font_path: Path | None = None
    try:
        from engine.pack.fx_assets import resolve_font_file

        resolved_font_path = resolve_font_file(font_family)
    except Exception:
        resolved_font_path = None

    raw_lines = [line.strip() for line in (title or "").replace("\r", "").split("\n") if line.strip()]
    compact = "".join(ln.replace(" ", "").replace("　", "") for ln in raw_lines)
    if len(compact) > max_chars:
        compact = compact[:max_chars]
    if not compact:
        raise ValueError("标题为空")

    # Prefer author newlines when they already respect the char budget.
    candidate_line_sets: list[list[str]] = []
    if raw_lines and len(raw_lines) <= max_lines:
        rebuilt: list[str] = []
        left = max_chars
        for ln in raw_lines:
            piece = ln.replace(" ", "").replace("　", "")[:left]
            if piece:
                rebuilt.append(piece)
                left -= len(piece)
            if left <= 0:
                break
        if rebuilt:
            candidate_line_sets.append(rebuilt)
    # Try one line first (scale down), then auto-wrap near midpoint if still over-wide.
    candidate_line_sets.append([compact])
    if max_lines >= 2 and len(compact) > 1:
        wrapped = _balanced_wrap_chars(compact, max_lines=max_lines)
        if wrapped not in candidate_line_sets:
            candidate_line_sets.append(wrapped)
        mid = len(compact) // 2
        for at in (mid - 1, mid + 1, mid - 2, mid + 2):
            if 1 <= at < len(compact):
                alt = [compact[:at], compact[at:]]
                if alt not in candidate_line_sets:
                    candidate_line_sets.append(alt)

    selected_font = None
    measured: list[tuple[int, int, int, int]] = []
    lines: list[str] = []
    for trial in candidate_line_sets:
        if not trial or len(trial) > max_lines:
            continue
        if any(not ln for ln in trial):
            continue
        fit = _measure_title_fit(
            trial,
            width=width,
            font_size=font_size,
            min_font_size=min_font_size,
            bold=bold,
            stroke_width=stroke_width,
            font_path=resolved_font_path,
        )
        if fit is not None:
            selected_font, measured, font_size = fit
            lines = trial
            break

    if selected_font is None or not lines:
        raise ValueError("标题在自动换行并缩至锁定字号后仍超宽，已阻断")

    sw = max(0, min(12, int(stroke_width)))
    fill = _hex_to_rgba(color, 1.0)
    stroke = _hex_to_rgba(stroke_color, 1.0)
    gap = max(20, int(font_size * 0.12))
    expected_glyph_top = max(0, min(height - 1, int(glyph_top_px)))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y = expected_glyph_top
    for line, box in zip(lines, measured):
        line_w, line_h = box[2] - box[0], box[3] - box[1]
        layer = Image.new("RGBA", (line_w, line_h), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.text((-box[0], -box[1]), line, font=selected_font, fill=fill, stroke_width=sw, stroke_fill=stroke)
        alpha_box = layer.getbbox()
        if not alpha_box:
            raise ValueError("标题没有可见字形")
        layer = layer.crop(alpha_box)
        if align == "left":
            x = max(48, int(width * 0.06))
        elif align == "right":
            x = max(0, width - layer.width - max(48, int(width * 0.06)))
        else:
            x = (width - layer.width) // 2
        if center_x_pct is not None:
            try:
                xp = float(center_x_pct)
            except (TypeError, ValueError):
                xp = 50.0
            xp = max(8.0, min(92.0, xp))
            margin = max(48, int(width * 0.06))
            cx = width * (xp / 100.0)
            x = int(round(cx - layer.width / 2))
            x = max(margin, min(width - layer.width - margin, x))
        img.paste(layer, (x, y), layer)
        y += layer.height + gap
    final_box = img.getbbox()
    if not final_box or final_box[1] != expected_glyph_top:
        raise ValueError(
            f"标题最终字形顶边未满足冻结规则 {expected_glyph_top}px"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def render_end_card_png(
    label: str,
    width: int,
    height: int,
    out_path: Path,
) -> Path:
    """GVisualPack V3: simple brand bar for post-VO pad only (full-frame transparent PNG)."""
    text = (label or "").strip()[:24] or "谢谢观看"
    font = _find_font(max(36, min(56, width // 22)), bold=True)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bar_h = max(72, min(120, height // 16))
    # Rest above title top zone and above typical subtitle pad (~420px bottom).
    y0 = height - max(480, int(height * 0.28)) - bar_h // 2
    y0 = max(int(height * 0.42), min(y0, height - bar_h - 80))
    margin = max(48, int(width * 0.08))
    draw.rounded_rectangle(
        (margin, y0, width - margin, y0 + bar_h),
        radius=bar_h // 3,
        fill=(32, 24, 18, 165),
    )
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = draw.textsize(text, font=font)  # type: ignore[attr-defined]
    tx = (width - tw) // 2
    ty = y0 + (bar_h - th) // 2 - 2
    draw.text((tx, ty), text, font=font, fill=(255, 248, 240, 255))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def render_vertical_item_label_png(
    text: str,
    width: int,
    height: int,
    out_path: Path,
    *,
    side: str,
    font_size: int,
    safe_top: int,
    safe_bottom: int,
) -> dict[str, Any]:
    """Render one Chinese vertical column with an exact 80px side glyph edge."""
    chars = [ch for ch in (text or "").strip() if "\u4e00" <= ch <= "\u9fff"]
    if not chars:
        raise ValueError("物品名称必须包含中文")
    if side not in {"left", "right"}:
        raise ValueError("物品名称仅允许 left/right")
    font = _find_font(max(36, min(88, int(font_size))), bold=True)
    sw = 4
    gap = max(4, int(font_size * 0.08))
    glyphs: list[Image.Image] = []
    for char in chars:
        probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        box = probe.textbbox((0, 0), char, font=font, stroke_width=sw)
        layer = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (-box[0], -box[1]), char, font=font, fill=(255, 255, 255, 255),
            stroke_width=sw, stroke_fill=(0, 0, 0, 255)
        )
        bbox = layer.getbbox()
        if bbox:
            glyphs.append(layer.crop(bbox))
    column_w = max(g.width for g in glyphs)
    column_h = sum(g.height for g in glyphs) + gap * (len(glyphs) - 1)
    top, bottom = int(safe_top), int(safe_bottom)
    if top < 300 or bottom > height - 420 or column_h > bottom - top:
        raise ValueError("物品名称侵入标题/字幕安全区或超出纵向安全区")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = 80 if side == "left" else width - 80 - column_w
    y = top + (bottom - top - column_h) // 2
    for glyph in glyphs:
        gx = x + (column_w - glyph.width) // 2
        canvas.paste(glyph, (gx, y), glyph)
        y += glyph.height + gap
    bbox = canvas.getbbox()
    if not bbox or (side == "left" and bbox[0] != 80) or (side == "right" and width - bbox[2] != 80):
        raise ValueError("物品名称最终字形侧边未满足 80px 硬锁")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return {"text": "".join(chars), "side": side, "bbox": list(bbox)}


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
            "-pix_fmt",
            "yuv420p",
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
            "-pix_fmt",
            "yuv420p",
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
    object_label_cues: list[dict[str, Any]] | None = None,
) -> bool:
    temp_dir = settings.paths.cache_root / "temp" / f"job_{plan.seed}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    segment_paths: list[Path] = []

    tpl_w = int(template_meta.get("output_width", 1080))
    tpl_h = int(template_meta.get("output_height", 1920))
    # Dual-orientation contract is exact: portrait 1080x1920 or landscape
    # 1920x1080. Source resolution never changes the frozen output canvas.
    width, height = tpl_w, tpl_h
    reframe_mode = str(template_meta.get("reframe_mode", "smart"))
    keep_source = bool(getattr(settings, "keep_source_audio", False))
    ambient_gain = float(getattr(settings, "ambient_gain", 0.0) or 0.0)
    if not keep_source:
        ambient_gain = 0.0
    bgm_gain = float(getattr(settings, "bgm_gain", 0.45) or 0.45)
    narration_gain = float(getattr(settings, "narration_gain", 1.0) or 1.0)
    bgm_bed_gain = float(getattr(settings, "bgm_bed_gain", 0.2) or 0.2)
    if render_meta is not None and render_meta.get("bgm_volume") is not None:
        bgm_bed_gain = max(0.0, min(1.0, float(render_meta["bgm_volume"])))
    target_lufs = float(getattr(settings, "audio_target_lufs", -14.0) or -14.0)
    narr = Path(narration_path) if narration_path else None
    if narr is not None and not narr.is_file():
        narr = None
    # Output canvas is frozen by orientation contract; never preserve source res.
    preserve_source_resolution = False
    crf = "18"
    final_crf = "22"

    if render_meta is not None:
        render_meta["output_width"] = width
        render_meta["output_height"] = height
        render_meta["preserve_source_resolution"] = preserve_source_resolution
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

    merged = temp_dir / "merged.mp4"
    # Prefer frozen production rules (Job) over template defaults.
    _pre_rules: dict[str, Any] = {}
    if render_meta and isinstance(render_meta.get("production_rules"), dict):
        pr0 = render_meta["production_rules"]
        if isinstance(pr0.get("effective_rules"), dict):
            _pre_rules = pr0["effective_rules"]
    clip_transition = str(
        _pre_rules.get("clip_transition")
        or template_meta.get("clip_transition")
        or "none"
    ).strip().lower()
    try:
        clip_transition_dur = float(
            _pre_rules.get("clip_transition_duration_sec")
            if _pre_rules.get("clip_transition_duration_sec") is not None
            else template_meta.get("clip_transition_duration_sec")
            or 0.4
        )
    except (TypeError, ValueError):
        clip_transition_dur = 0.4
    from engine.render.clip_transitions import join_segments

    if not join_segments(
        segment_paths,
        out_path=merged,
        transition=clip_transition,
        duration_sec=clip_transition_dur,
        crf=crf,
    ):
        return False
    if render_meta is not None:
        render_meta["clip_transition"] = clip_transition
        render_meta["clip_transition_duration_sec"] = clip_transition_dur

    title_png = temp_dir / "title.png"
    style = getattr(plan, "title_style", None) or {}
    max_chars = int(style.get("max_chars") or template_meta.get("title_max_chars", 24))
    max_lines = int(style.get("max_lines") or template_meta.get("title_max_lines", 2))
    title_on = True if _pre_rules.get("title_enabled") is None else bool(_pre_rules.get("title_enabled"))
    if title_on:
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
            stroke_width=int(
                style.get("stroke_width")
                if style.get("stroke_width") is not None
                else template_meta.get("title_stroke_width", 6)
            ),
            stroke_color=str(style.get("stroke_color") or template_meta.get("title_stroke_color", "#000000")),
            layout=str(style.get("layout") or template_meta.get("title_layout", "dual_chip")),
            offset_y_px=int(
                style.get("offset_y_px")
                if style.get("offset_y_px") is not None
                else template_meta.get("title_offset_y_px", 0)
            ),
            glyph_top_px=int(style.get("glyph_top_px") or 220),
            min_font_size=int(style.get("font_size_min") or 72),
            align=str(style.get("align") or "center"),
            font_family=str(
                style.get("font_family")
                or _pre_rules.get("title_font_family")
                or template_meta.get("title_font_family")
                or "default"
            ),
            center_x_pct=(
                float(style["center_x_pct"])
                if style.get("center_x_pct") is not None
                else float(_pre_rules["title_x_pct"])
                if _pre_rules.get("title_x_pct") is not None
                else float(template_meta["title_x_pct"])
                if template_meta.get("title_x_pct") is not None
                else None
            ),
        )
    else:
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(title_png)
    if render_meta is not None:
        render_meta["title_enabled"] = title_on
    if render_meta is not None:
        title_bbox = Image.open(title_png).convert("RGBA").getbbox()
        render_meta["title_bbox"] = list(title_bbox) if title_bbox else None
        render_meta["title_effect"] = str(style.get("effect") or "none")
        render_meta["title_style_effective"] = {
            "font_size": int(style.get("font_size") or template_meta.get("title_font_size", 92)),
            "color": str(style.get("color") or template_meta.get("title_color", "#FFE600")),
            "stroke_color": str(style.get("stroke_color") or template_meta.get("title_stroke_color", "#000000")),
            "stroke_width": int(style.get("stroke_width") or 0),
            "glyph_top_px": int(style.get("glyph_top_px") or 220),
            "align": str(style.get("align") or "center"),
        }
    item_overlays: list[tuple[Path, dict[str, Any]]] = []
    for index, cue in enumerate(object_label_cues or []):
        item_png = temp_dir / f"item_label_{index:03d}.png"
        geometry = render_vertical_item_label_png(
            str(cue.get("text") or ""), width, height, item_png,
            side=str(cue.get("side") or "left"),
            font_size=int(cue.get("font_size") or 56),
            safe_top=int(cue.get("safe_top") or 360),
            safe_bottom=int(cue.get("safe_bottom") or 1320),
        )
        geometry.update(
            {
                "start": float(cue.get("start") or 0),
                "end": float(cue.get("end") or 0),
                "cliplet_id": cue.get("cliplet_id"),
                "evidence": dict(cue.get("evidence") or {}),
            }
        )
        item_overlays.append((item_png, geometry))
    if render_meta is not None:
        render_meta["item_labels"] = [geometry for _, geometry in item_overlays]

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

    def _video_chain(
        logo_input_idx: int | None,
        item_inputs: list[tuple[int, dict[str, Any]]],
        *,
        freeze_pad_sec: float = 0.0,
        intro_punch: str = "none",
        item_label_motion: str = "none",
        end_card_idx: int | None = None,
        end_card_start: float = 0.0,
        end_card_end: float = 0.0,
        color_lut: str = "off",
        mask_inputs: list[tuple[int, dict[str, Any]]] | None = None,
    ) -> str:
        """Title overlay, optional packaging / logo; optional freeze-pad at end."""
        # Title PNG is a still; must be looped as input (see final_cmd) so fade/in
        # overlay has frames for the full cut. eof_action=repeat is belt-and-suspenders
        # if the still stream ever ends early.
        intro = str(intro_punch or "none").strip().lower()
        motion = str(item_label_motion or "none").strip().lower()
        lut = str(color_lut or "off").strip().lower()
        parts: list[str] = []
        # V4: light grade on picture only (before title/item overlays — text stays clean).
        # Weak eq only — never blur / unsharp that would fight soft-focus / Laplacian gates.
        if lut == "light":
            parts.append(
                "[0:v]eq=contrast=1.03:brightness=0.008:saturation=1.06:gamma=1.02[vpic]"
            )
            pic = "[vpic]"
        else:
            pic = "[0:v]"
        if intro == "soft":
            # V1: ≤0.4s soft fade-in on picture only (title/overlays still full opacity).
            parts.append(f"{pic}fade=t=in:st=0:d=0.35[v0]")
            base = "[v0]"
        else:
            base = pic
        title_effect = str(style.get("effect") or "none")
        if title_effect == "fade":
            parts.extend(
                [
                    "[1:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1[titlefx]",
                    f"{base}[titlefx]overlay=0:0:eof_action=repeat[vt0]",
                ]
            )
        else:
            parts.append(f"{base}[1:v]overlay=0:0:eof_action=repeat[vt0]")
        last = "[vt0]"
        for number, (input_idx, geometry) in enumerate(item_inputs):
            nxt = f"[vt{number + 1}]"
            st = float(geometry.get("start") or 0)
            en = float(geometry.get("end") or 0)
            enable = f"between(t\\,{st:.3f}\\,{en:.3f})"
            if motion == "fade":
                # V2: micro fade-in ≤0.25s on item PNG, timeline-absolute st.
                parts.append(
                    f"[{input_idx}:v]format=rgba,fade=t=in:st={st:.3f}:d=0.25:alpha=1[il{number}]"
                )
                parts.append(f"{last}[il{number}]overlay=0:0:enable='{enable}'{nxt}")
            else:
                parts.append(f"{last}[{input_idx}:v]overlay=0:0:enable='{enable}'{nxt}")
            last = nxt
        if (
            end_card_idx is not None
            and end_card_end > end_card_start + 0.05
        ):
            nxt = "[vtend]"
            enable = f"between(t\\,{end_card_start:.3f}\\,{end_card_end:.3f})"
            parts.append(
                f"[{end_card_idx}:v]format=rgba,"
                f"fade=t=in:st={end_card_start:.3f}:d=0.25:alpha=1[ec]"
            )
            parts.append(f"{last}[ec]overlay=0:0:enable='{enable}'{nxt}")
            last = nxt
        for mi, (input_idx, layer) in enumerate(mask_inputs or []):
            nxt = f"[vm{mi}]"
            opacity = float(layer.get("opacity") or 1.0)
            x = int(layer.get("_x") or 0)
            y = int(layer.get("_y") or 0)
            if opacity < 0.999:
                parts.append(
                    f"[{input_idx}:v]format=rgba,colorchannelmixer=aa={opacity:.3f}[msk{mi}]"
                )
                parts.append(f"{last}[msk{mi}]overlay={x}:{y}:eof_action=repeat{nxt}")
            else:
                parts.append(
                    f"{last}[{input_idx}:v]overlay={x}:{y}:eof_action=repeat{nxt}"
                )
            last = nxt
        out_label = "[v]"
        if freeze_pad_sec > 0.001:
            out_label = "[vbase]"
        if logo_input_idx is None or logo_opts is None:
            parts.append(f"{last}null{out_label}")
        else:
            mw = logo_opts["max_width"]
            xy = overlay_xy_expr(str(logo_opts["position"]), int(logo_opts["margin"]))
            li = logo_input_idx
            parts.append(f"[{li}:v]scale={mw}:-1:force_original_aspect_ratio=decrease,format=rgba[lg]")
            parts.append(f"{last}[lg]overlay={xy}{out_label}")
        if freeze_pad_sec > 0.001:
            # HARD LOCK: extend picture (never trim to narration length or shorter)
            parts.append(
                f"[vbase]tpad=stop_mode=clone:stop_duration={freeze_pad_sec:.3f}[v]"
            )
        return ";".join(parts)

    # Resolve GVisualPack packaging knobs (default off).
    _eff_rules: dict[str, Any] = {}
    if render_meta and isinstance(render_meta.get("production_rules"), dict):
        pr = render_meta["production_rules"]
        if isinstance(pr.get("effective_rules"), dict):
            _eff_rules = pr["effective_rules"]
    intro_punch = str(_eff_rules.get("intro_punch") or "none").strip().lower()
    if intro_punch not in {"none", "soft"}:
        intro_punch = "none"
    item_label_motion = str(_eff_rules.get("item_label_motion") or "none").strip().lower()
    if item_label_motion not in {"none", "fade"}:
        item_label_motion = "none"
    end_card_mode = str(_eff_rules.get("end_card") or "none").strip().lower()
    if end_card_mode not in {"none", "simple"}:
        end_card_mode = "none"
    color_lut = str(_eff_rules.get("color_lut") or "off").strip().lower()
    if color_lut not in {"off", "light"}:
        color_lut = "off"
    if render_meta is not None:
        render_meta["intro_punch"] = intro_punch
        render_meta["item_label_motion"] = item_label_motion
        render_meta["end_card"] = end_card_mode
        render_meta["color_lut"] = color_lut

    # Input layout: 0=merged 1=title [items…] [bgm?] [narr?] [logo?] [end_card?]
    # Loop title still so fade=t=in:d=0.4 can reach full opacity and stay visible.
    # Without -loop 1, image2 is ~1 frame and fade never completes → 「片上无标题」.
    final_cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-i",
        str(merged),
        "-loop",
        "1",
        "-framerate",
        "25",
        "-i",
        str(title_png),
    ]
    next_idx = 2
    item_input_meta: list[tuple[int, dict[str, Any]]] = []
    for png, geometry in item_overlays:
        # Loop stills so fade-in alpha animation has continuous frames on timeline.
        final_cmd.extend(["-loop", "1", "-framerate", "25", "-i", str(png)])
        item_input_meta.append((next_idx, geometry))
        next_idx += 1
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
    end_card_idx: int | None = None
    end_card_png: Path | None = None
    if end_card_mode == "simple":
        end_card_png = temp_dir / "end_card.png"
        brand_label = ""
        if isinstance(profile, dict):
            b = profile.get("brand") if isinstance(profile.get("brand"), dict) else {}
            brand_label = str((b or {}).get("display_name") or "").strip()
        if not brand_label:
            brand_label = str(customer_name or plan.title or "").strip()[:24]
        try:
            render_end_card_png(brand_label, width, height, end_card_png)
            final_cmd.extend(["-loop", "1", "-framerate", "25", "-i", str(end_card_png)])
            end_card_idx = next_idx
            next_idx += 1
        except Exception:
            end_card_png = None
            end_card_idx = None
            end_card_mode = "none"
            if render_meta is not None:
                render_meta["end_card"] = "none"
                render_meta["end_card_error"] = "render_failed_fail_closed"

    mask_input_meta: list[tuple[int, dict[str, Any]]] = []
    try:
        from engine.render.mask_layers import layer_overlay_xy, prepare_mask_pngs

        raw_masks = list(_eff_rules.get("mask_layers") or [])
        raw_stickers = list(_eff_rules.get("sticker_layers") or [])
        prepared = prepare_mask_pngs(
            raw_masks + raw_stickers,
            temp_dir=temp_dir,
            width=width,
            height=height,
        )
        for png, layer in prepared:
            with Image.open(png) as im:
                iw, ih = im.size
            x_s, y_s = layer_overlay_xy(layer, width=width, height=height, img_w=iw, img_h=ih)
            meta = dict(layer)
            meta["_x"] = int(x_s)
            meta["_y"] = int(y_s)
            final_cmd.extend(["-loop", "1", "-framerate", "25", "-i", str(png)])
            mask_input_meta.append((next_idx, meta))
            next_idx += 1
        if render_meta is not None:
            render_meta["mask_layer_count"] = len(mask_input_meta)
    except Exception as exc:  # noqa: BLE001
        if render_meta is not None:
            render_meta["mask_layers_error"] = str(exc)[:200]

    # HARD LOCK (L15): with narration, final video duration must strictly exceed
    # narration (≥ +0.05s). Never trim picture to narration length or shorter;
    # if vdur <= ndur, freeze-pad the last frame instead.
    # Preferred path (voice_subtitle): fit_narration_to_picture_duration compresses
    # VO so ndur ≲ vdur and freeze_pad stays ~0; freeze remains fail-closed residual.
    # Keep 0.05 in sync with engine.qc.ready_gate.DURATION_MIN_EXCEED_SEC
    _DUR_MIN_EXCEED = 0.05
    _NARRATION_TAIL = 0.12  # preferred tail after VO ends
    freeze_pad_sec = 0.0
    final_t: float | None = None
    ndur = 0.0
    vdur = 0.0
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
            # Last-chance: if VO still longer than picture, tempo-fit to *actual*
            # merged duration (closes gap when plan durations overshot source length).
            # Prefer this over freeze-pad so we never freeze while speech continues.
            residual = ndur - (vdur - 0.15)
            if residual > 0.35 and narr:
                try:
                    from engine.pack.tts import fit_narration_to_picture_duration

                    max_fit = 1.35
                    if render_meta is not None:
                        try:
                            max_fit = float(
                                (render_meta.get("narration_fit_max_speed") or max_fit)
                            )
                        except (TypeError, ValueError):
                            max_fit = 1.35
                    max_fit = max(1.05, min(1.6, max_fit))
                    fit_meta = fit_narration_to_picture_duration(
                        Path(narr),
                        picture_duration_sec=float(vdur),
                        max_speed=max_fit,
                        min_tail_sec=0.15,
                    )
                    if render_meta is not None:
                        render_meta["narration_fit_at_mux"] = fit_meta
                    if fit_meta.get("applied"):
                        try:
                            ndur = float(probe_audio_duration(Path(narr)))
                        except Exception:
                            pass
                except Exception as fit_exc:  # noqa: BLE001
                    if render_meta is not None:
                        render_meta["narration_fit_at_mux"] = {
                            "applied": False,
                            "error": str(fit_exc)[:200],
                        }
            min_t = ndur + max(_DUR_MIN_EXCEED, _NARRATION_TAIL)
            if ndur >= max(0.1, vdur - 1.5):
                # VO nearly fills picture — keep a short tail after speech
                final_t = max(min_t, ndur + _DUR_MIN_EXCEED)
            else:
                # Short VO: keep full picture (BGM continues); still never ≤ narration
                final_t = max(vdur, ndur + _DUR_MIN_EXCEED)
            # V3: end_card needs post-VO pad; extend only when ON (opt-in premium).
            if end_card_mode == "simple" and end_card_idx is not None:
                final_t = max(float(final_t), ndur + 0.45)
            freeze_pad_sec = max(0.0, float(final_t) - vdur)
        elif ndur > 0.1:
            final_t = ndur + max(_DUR_MIN_EXCEED, _NARRATION_TAIL)
            if end_card_mode == "simple" and end_card_idx is not None:
                final_t = max(float(final_t), ndur + 0.45)
            freeze_pad_sec = max(0.0, float(final_t) - max(vdur, 0.0))
        if render_meta is not None and freeze_pad_sec > 0.001:
            render_meta["freeze_pad_sec"] = round(float(freeze_pad_sec), 3)
            render_meta["freeze_pad_reason"] = "l15_residual_after_voice_or_unfitted"

    end_card_start = 0.0
    end_card_end = 0.0
    if (
        end_card_mode == "simple"
        and end_card_idx is not None
        and ndur > 0.1
        and final_t is not None
        and float(final_t) > ndur + 0.05
    ):
        end_card_start = float(ndur)
        end_card_end = float(final_t)
        if render_meta is not None:
            render_meta["end_card_window"] = {
                "start": end_card_start,
                "end": end_card_end,
            }
    else:
        end_card_idx = None

    # Audio graph: narration lead + BGM bed
    # duration=longest so short foreign VO does not silence-cut BGM before picture ends
    audio_parts: list[str] = []
    audio_out = "[a]" if freeze_pad_sec <= 0.001 else "[apre]"
    # GCustomerUX: optional BGM end fade from production rules (default standard=3s).
    bgm_fade_sec = 0.0
    if render_meta is not None:
        try:
            bgm_fade_sec = float(render_meta.get("bgm_fade_out_sec") or 0.0)
        except (TypeError, ValueError):
            bgm_fade_sec = 0.0
    if bgm_fade_sec < 0:
        bgm_fade_sec = 0.0
    if bgm_fade_sec > 8.0:
        bgm_fade_sec = 8.0
    fade_st = max(0.0, float(final_t or 0.0) - bgm_fade_sec) if bgm_fade_sec > 0.05 and final_t else 0.0
    bgm_afade = (
        f",afade=t=out:st={fade_st:.3f}:d={bgm_fade_sec:.3f}" if bgm_fade_sec > 0.05 and final_t else ""
    )
    if render_meta is not None and bgm_fade_sec > 0.05 and final_t:
        render_meta["bgm_fade_out_applied"] = {
            "seconds": bgm_fade_sec,
            "start": fade_st,
            "final_t": float(final_t),
        }

    if narr_idx is not None and bgm_idx is not None:
        # Narration ends with a short fade so VO does not cut dead mid-timeline.
        narr_fade_d = 0.0
        if ndur > 2.5:
            narr_fade_d = min(1.4, max(0.7, ndur * 0.08))
        narr_fade_st = max(0.0, float(ndur) - narr_fade_d) if narr_fade_d > 0.05 else 0.0
        nar_afade = (
            f",afade=t=out:st={narr_fade_st:.3f}:d={narr_fade_d:.3f}"
            if narr_fade_d > 0.05
            else ""
        )
        if render_meta is not None and narr_fade_d > 0.05:
            render_meta["narration_fade_out_applied"] = {
                "seconds": narr_fade_d,
                "start": narr_fade_st,
                "narration_duration": float(ndur),
            }
        audio_parts = [
            f"[{narr_idx}:a]volume={narration_gain:.3f},aformat=sample_rates=48000:channel_layouts=stereo{nar_afade}[nar]",
            f"[{bgm_idx}:a]volume={effective_bgm_gain:.3f},aformat=sample_rates=48000:channel_layouts=stereo{bgm_afade}[bg]",
            f"[nar][bg]amix=inputs=2:duration=longest:dropout_transition=2[mix]",
            f"[mix]{loudnorm}{audio_out}",
        ]
    elif narr_idx is not None:
        narr_fade_d = 0.0
        if ndur > 2.5:
            narr_fade_d = min(1.4, max(0.7, ndur * 0.08))
        narr_fade_st = max(0.0, float(ndur) - narr_fade_d) if narr_fade_d > 0.05 else 0.0
        nar_afade = (
            f",afade=t=out:st={narr_fade_st:.3f}:d={narr_fade_d:.3f}"
            if narr_fade_d > 0.05
            else ""
        )
        audio_parts = [
            f"[{narr_idx}:a]volume={narration_gain:.3f},aformat=sample_rates=48000:channel_layouts=stereo{nar_afade},{loudnorm}{audio_out}",
        ]
    elif bgm_idx is not None and ambient_gain > 0.001:
        audio_parts = [
            f"[{bgm_idx}:a]volume={effective_bgm_gain:.3f}{bgm_afade}[bg]",
            f"[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[mix]",
            f"[mix]{loudnorm}{audio_out}",
        ]
    elif bgm_idx is not None:
        audio_parts = [
            f"[{bgm_idx}:a]volume={effective_bgm_gain:.3f}{bgm_afade},{loudnorm}{audio_out}"
        ]
    else:
        audio_parts = [f"[0:a]{loudnorm}{audio_out}"]
    if freeze_pad_sec > 0.001:
        audio_parts.append(f"[apre]apad=pad_dur={freeze_pad_sec:.3f}[a]")

    filter_complex = (
        f"{_video_chain(logo_idx, item_input_meta, freeze_pad_sec=freeze_pad_sec, intro_punch=intro_punch, item_label_motion=item_label_motion, end_card_idx=end_card_idx, end_card_start=end_card_start, end_card_end=end_card_end, color_lut=color_lut, mask_inputs=mask_input_meta)};"
        + ";".join(audio_parts)
    )
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
            "-pix_fmt",
            "yuv420p",
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
    if final_t is not None and final_t > 0.1:
        final_cmd.extend(["-t", f"{final_t:.3f}"])
    elif narr_idx is None:
        final_cmd.append("-shortest")
    else:
        final_cmd.append("-shortest")
    final_cmd.append(str(output_path))

    # Production encode wall-clock: stall killing so a wedged ffmpeg cannot pin an item forever.
    RENDER_ENCODE_TIMEOUT_SEC = 120.0
    try:
        ok = (
            subprocess.run(
                final_cmd,
                capture_output=True,
                check=False,
                timeout=RENDER_ENCODE_TIMEOUT_SEC,
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        ok = False
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
