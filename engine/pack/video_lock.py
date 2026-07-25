"""Customer video lock — frozen template rules applied to every render.

Authority for 北京始峰伟业 / 始峰五金 short-form montage after user lock (2026-07-25).
Changes require explicit user approval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Hard defaults = reference cut montage_45 / 始峰五金-旁白贯穿.mp4
DEFAULT_LOCK: dict[str, Any] = {
    "locked": True,
    "locked_at": "2026-07-25",
    "do_not_change_without_user_approval": True,
    "reference_title": "少等半天少跑几趟路",
    "reference_outputs": [
        "/Users/qr/Desktop/始峰五金-旁白贯穿.mp4",
        "/Users/qr/QR-Volume/极空间团队文件同步/速影客户/北京始峰伟业/02-成片/ready/2026-07-25/montage_45_1116965198.mp4",
    ],
    "strip_all_punctuation": True,
    "canvas": {"width": 1080, "height": 1920, "aspect": "9:16"},
    "title": {
        "font_size": 92,
        "color": "#E10600",
        "stroke_color": "#FFE600",
        "stroke_width": 6,
        "bar_opacity": 0.0,
        "bold": True,
        "layout": "dual_chip",
        "position": "top",
        "max_chars": 10,
        "max_lines": 2,
        "spoken": False,
        "forbid_in_subtitle": True,
        "auto_wrap_center": True,
        "theme_matched_rich": True,
        "prefer_full_dual": False,
        "random_single_or_dual": True,
        "min_chars_total": 6,
        "target_chars_per_line": 8,
    },
    "subtitle": {
        "font_size": 64,
        "color": "#FFFFFF",
        "outline": "#000000",
        "bottom_padding_px": 400,
        "no_background_mask": True,
        "auto_dual_line": True,
        "horizontal": "center",
        "tail_trim_seconds": 0.04,
            "inter_sentence_gap_seconds": 0.15,
        "forbid_title_content": True,
    },
    "voice": {
        "provider": "edge",
        "voice": "zh-CN-XiaoxiaoNeural",
        "label": "晓晓",
        "rate": "-8%",
        "pitch": "+20Hz",
        "fill_video_duration": True,
        "forbid_theme_announce": True,
        "chars_per_sec_zh": 3.8,
    },
    "logo": {"enabled": False},
    "narration": {
        "rich_theme_script": True,
        "strip_punctuation": True,
        "forbid_title_content": True,
        "forbid_interjections": ["嗨", "呀", "哦", "嗯", "呐", "啦", "嘿", "哈", "哇"],
        "forbid_tech_jargon": ["参数", "规格", "立方", "型号参数", "技术指标"],
        "focus": ["配货效率", "发货效率", "配送服务"],
    },
    "compose": {
        "preferred_runtime": "ffmpeg",
        "keep_source_audio": False,
        "template_name": "default-vertical",
    },
}


def lock_paths_for_customer(customer_name: str, *, output_root: str | Path | None = None) -> list[Path]:
    name = (customer_name or "").strip()
    paths: list[Path] = []
    if name:
        paths.append(_REPO_ROOT / "configs" / "customers" / name / "brand" / "VIDEO_LOCK.json")
        paths.append(_REPO_ROOT / "configs" / "customers" / name / "brand" / "style_lock.json")
    if output_root:
        parent = Path(output_root).expanduser().resolve().parent
        paths.insert(0, parent / "05-品牌" / "VIDEO_LOCK.json")
        paths.insert(1, parent / "05-品牌" / "style_lock.json")
    return paths


def load_video_lock(
    customer_name: str | None = None,
    *,
    output_root: str | Path | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load frozen lock; merge DEFAULT_LOCK ← file ← profile.video_lock."""
    lock = json.loads(json.dumps(DEFAULT_LOCK))  # deep copy
    for path in lock_paths_for_customer(customer_name or "", output_root=output_root):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        # style_lock uses nested title.font_size_px — normalize if needed
        if path.name == "style_lock.json":
            data = _normalize_style_lock(data)
        _deep_merge(lock, data)
        break  # first existing authority file wins (customer disk first)
    if isinstance(profile, dict) and isinstance(profile.get("video_lock"), dict):
        _deep_merge(lock, profile["video_lock"])
    lock["locked"] = True
    return lock


def apply_lock_to_title_style(base: dict[str, Any], lock: dict[str, Any]) -> dict[str, Any]:
    t = lock.get("title") if isinstance(lock.get("title"), dict) else {}
    out = dict(base or {})
    out.update(
        {
            "position": t.get("position", out.get("position", "top")),
            "font_size": int(t.get("font_size", out.get("font_size", 92))),
            "color": t.get("color", out.get("color", "#E10600")),
            "stroke_color": t.get("stroke_color", out.get("stroke_color", "#FFE600")),
            "stroke_width": int(t.get("stroke_width", out.get("stroke_width", 6))),
            "bar_opacity": float(t.get("bar_opacity", out.get("bar_opacity", 0.0))),
            "bold": bool(t.get("bold", out.get("bold", True))),
            "layout": t.get("layout", out.get("layout", "dual_chip")),
            "max_chars": int(t.get("max_chars", out.get("max_chars", 12))),
            "max_lines": int(t.get("max_lines", out.get("max_lines", 2))),
        }
    )
    return out


def apply_lock_to_settings_patch(lock: dict[str, Any]) -> dict[str, Any]:
    v = lock.get("voice") if isinstance(lock.get("voice"), dict) else {}
    return {
        "tts_provider": str(v.get("provider") or "edge"),
        "tts_voice": str(v.get("voice") or "zh-CN-XiaoxiaoNeural"),
        "tts_rate": str(v.get("rate") or "-8%"),
        "tts_pitch": str(v.get("pitch") or "+20Hz"),
        "tts_chars_per_sec_zh": float(v.get("chars_per_sec_zh") or 3.8),
    }


def logo_enabled_from_lock(lock: dict[str, Any], profile: dict[str, Any] | None = None) -> bool:
    brand = {}
    if isinstance(profile, dict) and isinstance(profile.get("brand"), dict):
        brand = profile["brand"]
    if "logo_enabled" in brand:
        return bool(brand.get("logo_enabled"))
    logo = lock.get("logo") if isinstance(lock.get("logo"), dict) else {}
    return bool(logo.get("enabled", False))


def write_video_lock(path: Path, lock: dict[str, Any] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = lock or DEFAULT_LOCK
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _normalize_style_lock(data: dict[str, Any]) -> dict[str, Any]:
    """Map legacy style_lock fields into VIDEO_LOCK shape."""
    out: dict[str, Any] = {}
    title = data.get("title") if isinstance(data.get("title"), dict) else {}
    sub = data.get("subtitle") if isinstance(data.get("subtitle"), dict) else {}
    canvas = data.get("canvas") if isinstance(data.get("canvas"), dict) else {}
    if title:
        stroke = title.get("stroke") if isinstance(title.get("stroke"), dict) else {}
        out["title"] = {
            "font_size": int(title.get("font_size_px") or title.get("font_size") or 92),
            "color": title.get("color") or "#E10600",
            "stroke_color": stroke.get("color") or "#FFE600",
            "stroke_width": int(stroke.get("width_px") or 6),
            "max_chars": int(title.get("max_chars") or 12),
            "max_lines": int(title.get("max_lines") or 2),
            "layout": title.get("layout") or "dual_chip",
            "bold": bool(title.get("bold", True)),
        }
    if sub:
        out["subtitle"] = {
            "font_size": int(sub.get("font_size_px") or sub.get("font_size") or 64),
            "bottom_padding_px": int(sub.get("bottom_padding_px") or 400),
            "no_background_mask": bool(sub.get("no_background_mask", True)),
        }
    if canvas:
        out["canvas"] = canvas
    if data.get("locked") is not None:
        out["locked"] = bool(data["locked"])
    return out


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
