"""Product video lock — frozen template floors applied to every render.

Product-neutral defaults (HARD_LOCKS). Per-customer reference cuts live only under
configs/customers/<name>/brand/VIDEO_LOCK.json — never hard-coded in engine paths.
Floor changes require explicit user approval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]

# HARD (2026-07-26 用户)：标题 = 黄字黑描边（禁止红字黄描边）
LOCKED_TITLE_COLOR = "#FFE600"
LOCKED_TITLE_STROKE_COLOR = "#000000"
LOCKED_TITLE_STROKE_WIDTH = 6
# HARD (2026-07-26 用户)：标题整体下移 120px
LOCKED_TITLE_OFFSET_Y_PX = 120

# Product-neutral hard defaults (no customer disk paths in engine code)
DEFAULT_LOCK: dict[str, Any] = {
    "locked": True,
    "locked_at": "2026-07-26",
    "do_not_change_without_user_approval": True,
    "reference_title": "",
    "reference_outputs": [],
    "strip_all_punctuation": True,
    "canvas": {"width": 1080, "height": 1920, "aspect": "9:16"},
    "title": {
        "font_size": 92,
        "color": LOCKED_TITLE_COLOR,
        "stroke_color": LOCKED_TITLE_STROKE_COLOR,
        "stroke_width": LOCKED_TITLE_STROKE_WIDTH,
        "bar_opacity": 0.0,
        "bold": True,
        "layout": "dual_chip",
        "position": "top",
        "offset_y_px": LOCKED_TITLE_OFFSET_Y_PX,
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
        "side_margin_px": 48,
        "no_background_mask": True,
        "auto_dual_line": True,
        "horizontal": "center",
        "forbid_edge_clip": True,
        # HARD: cue end = speech end − trim; silence between sentences = blank (不得拖字)
        "tail_trim_seconds": 0.12,
        "inter_sentence_gap_seconds": 0.15,
        "align_to_voice_mandatory": True,
        "clear_during_silence": True,
        "forbid_title_content": True,
        "force_burn_mono": True,
        "emoji_in_subtitle": True,
    },
    "voice": {
        "provider": "edge",
        "voice": "zh-CN-XiaoxiaoNeural",
        "label": "晓晓",
        "rate": "-8%",
        "pitch": "+35Hz",
        "volume": "+12%",
        "fill_video_duration": True,
        "forbid_theme_announce": True,
        "chars_per_sec_zh": 3.8,
        "max_chars_per_breath": 18,
        "emotion_boost": True,
    },
    "logo": {"enabled": False},
    "narration": {
        "rich_theme_script": True,
        "strip_punctuation": True,
        "max_chars_per_breath": 18,
        "require_sentence_breaks": True,
        "forbid_title_content": True,
        "emotional_delivery": True,
        "forbid_interjections": ["嗨", "呀", "哦", "嗯", "呐", "啦", "嘿", "哈", "哇"],
        "forbid_tech_jargon": ["参数", "规格", "立方", "型号参数", "技术指标"],
        "focus": ["真实内容", "服务效率", "客户体验"],
    },
    "emoji": {
        "locked": True,
        "in_subtitle": True,
        "forbid_title_stickers": True,
        "forbid_spoken_emoji": True,
        "require_burn_mono": True,
    },
    "compose": {
        "preferred_runtime": "ffmpeg",
        "keep_source_audio": False,
        "template_name": "default-vertical",
    },
    # QUALITY_LOCK — 虚焦模糊写死（2026-07-26 用户：全部写死）
    "quality": {
        "locked": True,
        "do_not_change_without_user_approval": True,
        "reject_blur": True,
        "forbid_blur_ingest": True,
        "forbid_blur_vectorize": True,
        "forbid_blur_in_plan": True,
        "forbid_unscored_vectorize": True,
        "min_quality_score": 0.35,
        "min_laplacian_var": 48.0,
        "asset_blur_fail_ratio": 0.6,
        "cliplet_reject_status": "rejected_blur",
        "asset_reject_status": "rejected_blur",
    },
    # PAPER_SLIP — 纸片规则写死（每日最多 2 次）
    "paper_slip": {
        "locked": True,
        "do_not_change_without_user_approval": True,
        "max_daily_uses": 2,
        "cliplet": True,
        "phrase": True,
        "count_on": "ready_only",
        "timezone": "Asia/Shanghai",
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
    lock["do_not_change_without_user_approval"] = True
    # 强制参考 HARD_LOCKS / READY_GATE：文件/profile 不得改回红字黄描边；标题下移写死
    title = lock.get("title") if isinstance(lock.get("title"), dict) else {}
    title["color"] = LOCKED_TITLE_COLOR
    title["stroke_color"] = LOCKED_TITLE_STROKE_COLOR
    title["stroke_width"] = max(int(title.get("stroke_width") or 0), LOCKED_TITLE_STROKE_WIDTH)
    title["offset_y_px"] = LOCKED_TITLE_OFFSET_Y_PX
    lock["title"] = title
    # QUALITY_LOCK FROZEN: file/profile cannot soften code floors
    from engine.ingest.quality import locked_quality_floors

    floors = locked_quality_floors()
    q = lock.get("quality") if isinstance(lock.get("quality"), dict) else {}
    q["locked"] = True
    q["do_not_change_without_user_approval"] = True
    q["reject_blur"] = True
    q["forbid_blur_ingest"] = True
    q["forbid_blur_vectorize"] = True
    q["forbid_blur_in_plan"] = True
    q["forbid_unscored_vectorize"] = True
    q["min_quality_score"] = max(float(q.get("min_quality_score") or 0), float(floors["min_quality_score"]))
    q["min_laplacian_var"] = max(float(q.get("min_laplacian_var") or 0), float(floors["min_laplacian_var"]))
    q["asset_blur_fail_ratio"] = min(
        float(q.get("asset_blur_fail_ratio") or 1.0), float(floors["asset_blur_fail_ratio"])
    )
    q["cliplet_reject_status"] = "rejected_blur"
    q["asset_reject_status"] = "rejected_blur"
    lock["quality"] = q
    # Subtitle / voice floors that were locked this sprint
    sub = lock.get("subtitle") if isinstance(lock.get("subtitle"), dict) else {}
    sub["side_margin_px"] = max(int(sub.get("side_margin_px") or 0), 48)
    sub["forbid_edge_clip"] = True
    sub["no_background_mask"] = True
    sub["align_to_voice_mandatory"] = True
    sub["clear_during_silence"] = True
    sub["tail_trim_seconds"] = max(float(sub.get("tail_trim_seconds") or 0), 0.12)
    sub["force_burn_mono"] = True
    sub["emoji_in_subtitle"] = True
    lock["subtitle"] = sub
    voice = lock.get("voice") if isinstance(lock.get("voice"), dict) else {}
    voice["rate"] = str(voice.get("rate") or "-8%")
    # Emotion floors: pitch/volume only strengthen, never soften below defaults
    voice["pitch"] = str(voice.get("pitch") or "+35Hz")
    voice["volume"] = str(voice.get("volume") or "+12%")
    voice["max_chars_per_breath"] = min(int(voice.get("max_chars_per_breath") or 18), 18)
    voice["emotion_boost"] = True
    lock["voice"] = voice
    narr = lock.get("narration") if isinstance(lock.get("narration"), dict) else {}
    narr["require_sentence_breaks"] = True
    narr["max_chars_per_breath"] = min(int(narr.get("max_chars_per_breath") or 18), 18)
    narr["emotional_delivery"] = True
    lock["narration"] = narr
    emoji = lock.get("emoji") if isinstance(lock.get("emoji"), dict) else {}
    emoji["locked"] = True
    emoji["in_subtitle"] = True
    emoji["forbid_title_stickers"] = True
    emoji["forbid_spoken_emoji"] = True
    emoji["require_burn_mono"] = True
    lock["emoji"] = emoji
    # PAPER_SLIP FROZEN: max_daily_uses 只可加严（≤2），不可放宽
    from engine.catalog.paper_slip import MAX_DAILY_USES

    ps = lock.get("paper_slip") if isinstance(lock.get("paper_slip"), dict) else {}
    ps["locked"] = True
    ps["do_not_change_without_user_approval"] = True
    ps["cliplet"] = True
    ps["phrase"] = True
    ps["count_on"] = "ready_only"
    ps["timezone"] = "Asia/Shanghai"
    try:
        cfg_max = int(ps.get("max_daily_uses") or MAX_DAILY_USES)
    except (TypeError, ValueError):
        cfg_max = MAX_DAILY_USES
    ps["max_daily_uses"] = min(cfg_max, MAX_DAILY_USES)
    lock["paper_slip"] = ps
    return lock


def apply_lock_to_title_style(base: dict[str, Any], lock: dict[str, Any]) -> dict[str, Any]:
    """Apply VIDEO_LOCK title style — 黄字黑描边 + 下移 offset_y_px（强制参考）."""
    t = lock.get("title") if isinstance(lock.get("title"), dict) else {}
    out = dict(base or {})
    out.update(
        {
            "position": t.get("position", out.get("position", "top")),
            "font_size": int(t.get("font_size", out.get("font_size", 92))),
            "color": LOCKED_TITLE_COLOR,
            "stroke_color": LOCKED_TITLE_STROKE_COLOR,
            "stroke_width": max(
                int(t.get("stroke_width", out.get("stroke_width", LOCKED_TITLE_STROKE_WIDTH))),
                LOCKED_TITLE_STROKE_WIDTH,
            ),
            "offset_y_px": LOCKED_TITLE_OFFSET_Y_PX,
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
        "tts_pitch": str(v.get("pitch") or "+35Hz"),
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
            "color": title.get("color") or LOCKED_TITLE_COLOR,
            "stroke_color": stroke.get("color") or LOCKED_TITLE_STROKE_COLOR,
            "stroke_width": int(stroke.get("width_px") or LOCKED_TITLE_STROKE_WIDTH),
            "offset_y_px": int(
                title.get("offset_y_px")
                or title.get("top_padding_px")
                or LOCKED_TITLE_OFFSET_Y_PX
            ),
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
