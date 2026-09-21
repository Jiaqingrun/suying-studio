"""GRuleVisualLab · vertical / karaoke / marquee subtitle helpers (Pillow)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    roots = Path(__file__).resolve().parents[2]
    for path in (
        str(roots / "configs/fx_assets/fonts/NotoSansSC-Regular.ttf"),
        str(roots / "configs/fx_assets/fonts/Inter-Regular.ttf"),
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ):
        try:
            if not Path(path).is_file():
                continue
            return ImageFont.truetype(path, size=size, index=0)
        except OSError:
            continue
    return ImageFont.load_default()


def _vertical_units(text: str) -> list[str]:
    """Split cue into vertical cells: emoji clusters stay whole; else one char."""
    from engine.pack.emoji_stickers import iter_emoji_tokens, text_has_emoji

    raw = (text or "").replace("\n", "")
    if not raw:
        return []
    if not text_has_emoji(raw):
        return [c for c in raw if not c.isspace()]
    units: list[str] = []
    pos = 0
    for em in iter_emoji_tokens(raw):
        idx = raw.find(em, pos)
        if idx < 0:
            continue
        for c in raw[pos:idx]:
            if not c.isspace():
                units.append(c)
        units.append(em)
        pos = idx + len(em)
    for c in raw[pos:]:
        if not c.isspace():
            units.append(c)
    return units


def render_vertical_cue_png(
    text: str,
    *,
    canvas_w: int,
    canvas_h: int,
    font_size: int,
    out: Path,
    side: str = "left",
    color: str = "#FFFFFF",
    stroke_color: str = "#000000",
    stroke_width: int = 4,
    highlight_count: int = 0,
    highlight_color: str = "#FFE600",
    center_x_pct: float | None = None,
    center_y_pct: float | None = None,
) -> None:
    """Draw CJK characters in a vertical column; optional karaoke highlight prefix.

    HARD (E3/E8): emoji cells MUST composite Twemoji rasters — fonts tofu □ on
    vertical layout the same way horizontal cues used to before Twemoji burn.
    """
    from engine.pack.emoji_stickers import resolve_emoji_png, text_has_emoji
    from engine.render.subtitles_burn import (
        _emoji_twemoji_cache_dir,
        cue_png_twemoji_chroma_count,
    )

    units = _vertical_units(text)
    if not units:
        raise ValueError("vertical cue empty")
    font = _font(font_size)
    gap = max(2, font_size // 8)
    emoji_px = max(28, int(font_size * 1.05))
    cell = max(font_size, emoji_px) + gap
    box_h = cell * len(units) + font_size
    box_w = max(font_size, emoji_px) + stroke_width * 4 + 16
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = box_w // 2
    y = stroke_width + 4
    cache = _emoji_twemoji_cache_dir()
    emoji_pasted = 0
    wants_emoji = text_has_emoji(text or "")

    for i, unit in enumerate(units):
        fill = highlight_color if i < highlight_count else color
        if text_has_emoji(unit):
            png = resolve_emoji_png(unit, cache_dir=cache, size=emoji_px, plate=False)
            if png is None or not Path(png).is_file():
                raise RuntimeError(
                    f"竖排字幕行内 emoji 未能解析为 Twemoji: {unit!r}"
                )
            em = Image.open(png).convert("RGBA")
            if em.size != (emoji_px, emoji_px):
                em = em.resize((emoji_px, emoji_px), Image.Resampling.LANCZOS)
            ex = max(0, (box_w - emoji_px) // 2)
            img.alpha_composite(em, (ex, y))
            emoji_pasted += 1
            y += cell
            continue
        for dx in range(-stroke_width, stroke_width + 1):
            for dy in range(-stroke_width, stroke_width + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), unit, font=font, fill=stroke_color, anchor="mt")
        draw.text((x, y), unit, font=font, fill=fill, anchor="mt")
        y += cell

    if wants_emoji and emoji_pasted < 1:
        raise RuntimeError(f"竖排字幕含 emoji 但未贴入 Twemoji: {text!r}")

    # Place on full-frame transparent canvas for overlay at side
    frame = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    margin = max(24, canvas_w // 24)
    margin_y = max(24, canvas_h // 24)
    if center_x_pct is not None:
        try:
            xp = max(8.0, min(92.0, float(center_x_pct)))
        except (TypeError, ValueError):
            xp = 18.0 if side != "right" else 82.0
        paste_x = int(round(canvas_w * (xp / 100.0) - box_w / 2))
        paste_x = max(margin, min(canvas_w - box_w - margin, paste_x))
    else:
        paste_x = margin if side == "left" else canvas_w - box_w - margin
    if center_y_pct is not None:
        try:
            yp = max(8.0, min(92.0, float(center_y_pct)))
        except (TypeError, ValueError):
            yp = 50.0
        paste_y = int(round(canvas_h * (yp / 100.0) - box_h / 2))
        paste_y = max(margin_y, min(canvas_h - box_h - margin_y, paste_y))
    else:
        paste_y = max(0, (canvas_h - box_h) // 2)
    frame.alpha_composite(img, (paste_x, paste_y))
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.save(out)

    if wants_emoji:
        chroma = cue_png_twemoji_chroma_count(
            out, text_color=color, stroke_color=stroke_color
        )
        if chroma < 60:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(
                f"竖排字幕 Twemoji 彩色验收失败 chroma={chroma}: {text!r}"
            )


def karaoke_highlight_count(text: str, *, progress: float) -> int:
    units = _vertical_units(text)
    if not units:
        return 0
    p = max(0.0, min(1.0, float(progress)))
    return int(round(p * len(units)))


def marquee_slice(text: str, *, progress: float, window: int = 8) -> str:
    chars = [c for c in (text or "").replace("\n", "") if not c.isspace()]
    if not chars:
        return ""
    n = len(chars)
    if n <= window:
        return "".join(chars)
    start = int(progress * max(1, n - window + 1)) % max(1, n - window + 1)
    return "".join(chars[start : start + window])
