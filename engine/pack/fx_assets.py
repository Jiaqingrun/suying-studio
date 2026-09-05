"""GRuleVisualLab · load Tier A fx asset manifest + helpers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "configs" / "fx_assets" / "manifest.json"
_FONTS_DIR = _REPO_ROOT / "configs" / "fx_assets" / "fonts"

MASK_LAYER_MAX = 8
STICKER_LAYER_MAX = 8
TRANSITION_DURATION_MAX = 0.8
TRANSITION_DURATION_DEFAULT = 0.4
_SYSTEM_FONT_SCAN_CAP = 64


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    if not _MANIFEST.is_file():
        return {
            "transitions": {
                "xfade": ["fade"],
                "default_duration_sec": 0.4,
                "max_duration_sec": 0.8,
            },
            "fonts": [{"id": "default", "label": "系统默认"}],
            "masks": {"max_layers": 8, "builtin": []},
            "stickers": [],
            "narration_text_effects": ["none", "karaoke", "marquee"],
            "subtitle_layouts": ["horizontal", "vertical"],
        }
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def xfade_names() -> frozenset[str]:
    data = load_manifest().get("transitions") or {}
    names = data.get("xfade") or []
    return frozenset(str(x).strip().lower() for x in names if str(x).strip())


def allowed_font_ids() -> frozenset[str]:
    fonts = load_manifest().get("fonts") or []
    ids = {str(f.get("id") or "").strip() for f in fonts if isinstance(f, dict)}
    ids.add("default")
    ids.add("system")
    return frozenset(x for x in ids if x)


def builtin_mask_ids() -> frozenset[str]:
    masks = (load_manifest().get("masks") or {}).get("builtin") or []
    return frozenset(
        str(m.get("id") or "").strip()
        for m in masks
        if isinstance(m, dict) and m.get("id")
    )


def resolve_bundled_font_path(font_id: str) -> Path | None:
    fonts = load_manifest().get("fonts") or []
    for row in fonts:
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "") != font_id:
            continue
        rel = str(row.get("bundled_relpath") or "").strip()
        if not rel:
            return None
        path = (_REPO_ROOT / "configs" / "fx_assets" / rel).resolve()
        return path if path.is_file() else None
    return None


def resolve_font_file(font_id: str | None) -> Path | None:
    """Resolve rule font id → filesystem path (bundled or system:Stem)."""
    fid = str(font_id or "default").strip() or "default"
    if fid in {"default", "system", ""}:
        return None
    if fid.startswith("system:"):
        stem = fid[7:]
        for folder in (
            Path.home() / "Library" / "Fonts",
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/System/Library/Fonts"),
        ):
            if not folder.is_dir():
                continue
            for ext in (".otf", ".ttf", ".ttc"):
                candidate = folder / f"{stem}{ext}"
                if candidate.is_file():
                    return candidate
            # fuzzy: any file whose stem matches
            for path in folder.glob(f"{stem}.*"):
                if path.suffix.lower() in {".otf", ".ttf", ".ttc"} and path.is_file():
                    return path
        return None
    return resolve_bundled_font_path(fid)


def list_bundled_and_system_fonts(*, include_system: bool = True) -> list[dict[str, Any]]:
    """Catalog for API / rule lab. System scan is capped to avoid schema bloat."""
    out: list[dict[str, Any]] = []
    for row in load_manifest().get("fonts") or []:
        if not isinstance(row, dict):
            continue
        fid = str(row.get("id") or "").strip()
        if not fid:
            continue
        bundled = resolve_bundled_font_path(fid)
        out.append(
            {
                "id": fid,
                "label": str(row.get("label") or fid),
                "use_hint": str(row.get("use_hint") or "").strip() or None,
                "spdx": row.get("spdx"),
                "source": "bundled" if bundled else "manifest",
                "path": str(bundled) if bundled else None,
                "available": bool(bundled) or fid in {"default", "system"},
            }
        )
    if not include_system:
        return out

    scan_dirs = [
        Path.home() / "Library" / "Fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts/Supplemental"),
    ]
    seen = {str(x.get("path") or "") for x in out}
    prefer_tokens = (
        "noto",
        "sourcehan",
        "source han",
        "pingfang",
        "heiti",
        "songti",
        "kaiti",
        "stheit",
        "hiragino",
        "wenkai",
        "zcool",
        "inter",
        "sc-",
        "cn-",
    )
    ranked: list[tuple[int, Path]] = []
    for folder in scan_dirs:
        if not folder.is_dir():
            continue
        for path in folder.glob("*.*"):
            if path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            key = str(path)
            if key in seen:
                continue
            name = path.stem.lower()
            rank = 0
            for i, tok in enumerate(prefer_tokens):
                if tok in name:
                    rank = 100 - i
                    break
            ranked.append((rank, path))
    ranked.sort(key=lambda row: (-row[0], row[1].name.lower()))
    for _, path in ranked[:_SYSTEM_FONT_SCAN_CAP]:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        stem = path.stem
        out.append(
            {
                "id": f"system:{stem}",
                "label": f"{stem}（本机）",
                "use_hint": "本机已装",
                "spdx": None,
                "source": "system",
                "path": key,
                "available": True,
            }
        )
    return out


def schema_public_bundle() -> dict[str, Any]:
    """Slice of manifest safe to expose via GET /production-rules/schema extras."""
    m = load_manifest()
    return {
        "xfade_transitions": sorted(xfade_names()),
        "fonts": list_bundled_and_system_fonts(include_system=True),
        "builtin_masks": (m.get("masks") or {}).get("builtin") or [],
        "mask_layer_max": int((m.get("masks") or {}).get("max_layers") or MASK_LAYER_MAX),
        "narration_text_effects": m.get("narration_text_effects")
        or ["none", "karaoke", "marquee"],
        "subtitle_layouts": m.get("subtitle_layouts") or ["horizontal", "vertical"],
        "sticker_sources": [
            s
            for s in (m.get("stickers") or [])
            if isinstance(s, dict) and s.get("tier") == "A"
        ],
    }
