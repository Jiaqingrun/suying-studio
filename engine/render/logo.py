"""Customer logo corner overlay — paths from profile / 05-品牌 convention."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.config.settings import AppSettings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOGO_NAMES = ("logo.png", "logo.webp", "logo.jpg", "logo.jpeg")


def brand_from_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return {}
    brand = profile.get("brand")
    return brand if isinstance(brand, dict) else {}


def resolve_logo_path(
    settings: AppSettings,
    *,
    profile: dict[str, Any] | None = None,
    customer_name: str | None = None,
) -> Path | None:
    """Resolve logo file. Missing/disabled → None (render continues without overlay)."""
    brand = brand_from_profile(profile)
    if brand.get("logo_enabled") is False:
        return None

    raw = str(brand.get("logo_path") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            # Relative to customer workspace brand dir, then repo brand/, then cwd
            candidates = [
                Path(settings.paths.output_root).parent / "05-品牌" / raw,
                Path(settings.paths.output_root) / raw,
            ]
            if customer_name:
                candidates.insert(
                    0, _REPO_ROOT / "configs" / "customers" / customer_name / "brand" / raw
                )
            for c in candidates:
                if c.is_file():
                    return c
            p = Path.cwd() / raw
        if p.is_file():
            return p

    brand_dir = Path(settings.paths.output_root).parent / "05-品牌"
    for name in _LOGO_NAMES:
        cand = brand_dir / name
        if cand.is_file():
            return cand

    if customer_name:
        cfg_brand = _REPO_ROOT / "configs" / "customers" / customer_name / "brand"
        for name in _LOGO_NAMES:
            cand = cfg_brand / name
            if cand.is_file():
                return cand

    return None


def logo_overlay_opts(brand: dict[str, Any], canvas_w: int) -> dict[str, Any]:
    """Sizing / placement for ffmpeg overlay."""
    max_w = int(brand.get("logo_max_width") or max(120, canvas_w // 7))
    margin = int(brand.get("logo_margin") or max(24, canvas_w // 40))
    position = str(brand.get("logo_position") or "bottom_right")
    return {"max_width": max_w, "margin": margin, "position": position}


def overlay_xy_expr(position: str, margin: int) -> str:
    m = max(0, margin)
    return {
        "bottom_right": f"W-w-{m}:H-h-{m}",
        "bottom_left": f"{m}:H-h-{m}",
        "top_right": f"W-w-{m}:{m}",
        "top_left": f"{m}:{m}",
    }.get(position, f"W-w-{m}:H-h-{m}")
