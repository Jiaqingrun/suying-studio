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
    """Draw CJK characters in a vertical column; optional karaoke highlight prefix."""
    chars = [c for c in (text or "").replace("\n", "") if not c.isspace()]
    if not chars:
        raise ValueError("vertical cue empty")
    font = _font(font_size)
    gap = max(2, font_size // 8)
    # Measure
    cell = font_size + gap
    box_h = cell * len(chars) + font_size
    box_w = font_size + stroke_width * 4 + 16
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = box_w // 2
    y = stroke_width + 4
    for i, ch in enumerate(chars):
        fill = highlight_color if i < highlight_count else color
        # stroke
        for dx in range(-stroke_width, stroke_width + 1):
            for dy in range(-stroke_width, stroke_width + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), ch, font=font, fill=stroke_color, anchor="mt")
        draw.text((x, y), ch, font=font, fill=fill, anchor="mt")
        y += cell
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


def karaoke_highlight_count(text: str, *, progress: float) -> int:
    chars = [c for c in (text or "").replace("\n", "") if not c.isspace()]
    if not chars:
        return 0
    p = max(0.0, min(1.0, float(progress)))
    return int(round(p * len(chars)))


def marquee_slice(text: str, *, progress: float, window: int = 8) -> str:
    chars = [c for c in (text or "").replace("\n", "") if not c.isspace()]
    if not chars:
        return ""
    n = len(chars)
    if n <= window:
        return "".join(chars)
    start = int(progress * max(1, n - window + 1)) % max(1, n - window + 1)
    return "".join(chars[start : start + window])
