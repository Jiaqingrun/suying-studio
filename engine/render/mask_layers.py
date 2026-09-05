"""GRuleVisualLab · mask / sticker PNG helpers + overlay filter fragments."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def render_geom_mask_png(
    out: Path,
    *,
    kind: str,
    width: int,
    height: int,
) -> Path:
    """Render built-in geometric mask (RGBA)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if kind == "geom_vignette":
        # Soft corner darkening via concentric ellipses
        for i in range(12):
            alpha = int(18 + i * 10)
            inset = int(min(width, height) * (0.02 + i * 0.035))
            draw.ellipse(
                [inset, inset, width - inset, height - inset],
                outline=(0, 0, 0, alpha),
                width=max(8, inset // 4),
            )
        # Fill outside approx by dark border rectangles
        band = int(min(width, height) * 0.12)
        for a, box in (
            (110, [0, 0, width, band]),
            (110, [0, height - band, width, height]),
            (90, [0, 0, band, height]),
            (90, [width - band, 0, width, height]),
        ):
            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.rectangle(box, fill=(0, 0, 0, a))
            img = Image.alpha_composite(img, overlay)
    elif kind == "geom_bottom_gradient":
        for y in range(height // 2, height):
            t = (y - height // 2) / max(1, height // 2)
            alpha = int(200 * t * t)
            draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    elif kind == "geom_top_gradient":
        for y in range(0, height // 2):
            t = 1.0 - (y / max(1, height // 2))
            alpha = int(180 * t * t)
            draw.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
    elif kind == "geom_side_bar_left":
        w = max(24, width // 12)
        draw.rectangle([0, 0, w, height], fill=(0, 0, 0, 140))
    elif kind == "geom_side_bar_right":
        w = max(24, width // 12)
        draw.rectangle([width - w, 0, width, height], fill=(0, 0, 0, 140))
    else:
        # fallback faint vignette
        draw.rectangle([0, 0, width, height], fill=(0, 0, 0, 40))
    img.save(out)
    return out


def layer_overlay_xy(layer: dict[str, Any], *, width: int, height: int, img_w: int, img_h: int) -> tuple[str, str]:
    """Center of layer at x_pct/y_pct of frame → ffmpeg overlay x/y expressions."""
    x_pct = float(layer.get("x_pct") or 50.0) / 100.0
    y_pct = float(layer.get("y_pct") or 50.0) / 100.0
    cx = width * x_pct
    cy = height * y_pct
    x = int(cx - img_w / 2)
    y = int(cy - img_h / 2)
    return str(x), str(y)


def _apply_scale_rotate_opacity(
    img: Image.Image,
    layer: dict[str, Any],
    *,
    canvas_w: int,
    canvas_h: int,
    base_frac: float = 0.4,
) -> Image.Image:
    """
    scale=1 → width ≈ base_frac * canvas_w (aspect kept).
    Free range: tiny … larger than frame. Rotation expands canvas of the PNG.
    """
    try:
        scale = float(layer.get("scale") or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    scale = max(0.02, min(12.0, scale))
    try:
        rot = float(layer.get("rotation_deg") or 0.0)
    except (TypeError, ValueError):
        rot = 0.0
    try:
        opacity = float(layer.get("opacity") or 1.0)
    except (TypeError, ValueError):
        opacity = 1.0
    opacity = max(0.0, min(1.0, opacity))

    base_w = max(8, int(canvas_w * base_frac))
    tw = max(4, int(round(base_w * scale)))
    # Cap extreme pixel sizes for memory (12× of 4K still ok; soft cap ~8k)
    tw = min(tw, max(canvas_w, canvas_h) * 8)
    th = max(4, int(round(img.height * (tw / max(1, img.width)))))
    img = img.resize((tw, th), Image.Resampling.LANCZOS)

    if abs(rot) > 0.05:
        # PIL rotates counter-clockwise; UI clockwise-positive matches CSS rotate()
        img = img.rotate(-rot, expand=True, resample=Image.Resampling.BICUBIC)

    if opacity < 0.999:
        r, g, b, a = img.split()
        a = a.point(lambda p, o=opacity: int(p * o))
        img = Image.merge("RGBA", (r, g, b, a))
    return img


def prepare_mask_pngs(
    layers: list[dict[str, Any]],
    *,
    temp_dir: Path,
    width: int,
    height: int,
) -> list[tuple[Path, dict[str, Any]]]:
    """Materialize visible mask/sticker layers to PNG paths for ffmpeg inputs."""
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[tuple[Path, dict[str, Any]]] = []
    for i, layer in enumerate(layers or []):
        if not isinstance(layer, dict):
            continue
        if not layer.get("visible", True):
            continue
        source = str(layer.get("source") or "").strip()
        if not source:
            continue
        out = temp_dir / f"mask_layer_{i}.png"
        if source.startswith("geom_"):
            # Full-frame geom; optional uniform scale via resize of whole frame
            render_geom_mask_png(out, kind=source, width=width, height=height)
            try:
                scale = float(layer.get("scale") or 1.0)
            except (TypeError, ValueError):
                scale = 1.0
            if abs(scale - 1.0) > 0.02 or abs(float(layer.get("rotation_deg") or 0)) > 0.05:
                img = Image.open(out).convert("RGBA")
                img = _apply_scale_rotate_opacity(
                    img, layer, canvas_w=width, canvas_h=height, base_frac=1.0
                )
                img.save(out)
        elif source in {"twemoji", "emoji"} or source.startswith("twemoji:") or source.startswith("emoji:"):
            glyph = "✨"
            if ":" in source:
                glyph = source.split(":", 1)[1].strip() or glyph
            try:
                from engine.pack.emoji_stickers import resolve_emoji_png

                emoji_png = resolve_emoji_png(
                    glyph,
                    cache_dir=temp_dir / "twemoji_cache",
                    size=max(64, int(min(width, height) * 0.12)),
                    plate=False,
                )
            except Exception:
                emoji_png = None
            if emoji_png is None or not Path(emoji_png).is_file():
                continue
            img = Image.open(emoji_png).convert("RGBA")
            img = _apply_scale_rotate_opacity(
                img, layer, canvas_w=width, canvas_h=height, base_frac=0.12
            )
            img.save(out)
        elif source.startswith("user:"):
            src = Path(source[5:])
            if not src.is_file():
                continue
            img = Image.open(src).convert("RGBA")
            img = _apply_scale_rotate_opacity(
                img, layer, canvas_w=width, canvas_h=height, base_frac=0.4
            )
            img.save(out)
        elif Path(source).is_file():
            img = Image.open(source).convert("RGBA")
            img = _apply_scale_rotate_opacity(
                img, layer, canvas_w=width, canvas_h=height, base_frac=0.4
            )
            img.save(out)
        else:
            continue
        prepared.append((out, layer))
    return prepared
