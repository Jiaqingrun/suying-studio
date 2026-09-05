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
# HARD (2026-07-30 用户)：最终不透明字形外接框顶边 = 220px。
# legacy offset 仅为迁移输入，不再参与渲染验收。
LOCKED_TITLE_TOP_PX = 220
LOCKED_TITLE_OFFSET_Y_PX = 0
LOCKED_SUBTITLE_BOTTOM_PX = 420

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
        "glyph_top_px": LOCKED_TITLE_TOP_PX,
        "font_size_min": 72,
        "font_size_max": 92,
        "max_chars": 24,
        "max_lines": 2,
        "spoken": False,
        "forbid_in_subtitle": True,
        "auto_wrap_center": True,
        "theme_matched_rich": True,
        "prefer_full_dual": False,
        "random_single_or_dual": True,
        "min_chars_total": 6,
        "target_chars_per_line": 12,
    },
    "subtitle": {
        "font_size": 64,
        "color": "#FFFFFF",
        "outline": "#000000",
        "bottom_padding_px": LOCKED_SUBTITLE_BOTTOM_PX,
        "glyph_bottom_px": LOCKED_SUBTITLE_BOTTOM_PX,
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
    # PAPER_SLIP — 滚动避重写死
    "paper_slip": {
        "locked": True,
        "do_not_change_without_user_approval": True,
        "mode": "rolling_diversity",
        "rolling_cliplet_window": 20,
        "rolling_phrase_window": 15,
        "cliplet": True,
        "phrase": True,
        "count_on": "ready_only",
        "timezone": "Asia/Shanghai",
        "forbid_quota_circuit": True,
    },
}


def is_inside_packaged_app_runtime(path: Path | str) -> bool:
    """True when path sits under *.app/Contents/Resources/runtime (signed bundle).

    Writing here creates files outside RUNTIME_MANIFEST and permanently blocks
    App engine_start integrity checks after the next relaunch/reboot.
    """
    try:
        parts = Path(path).expanduser().resolve().parts
    except OSError:
        parts = Path(path).expanduser().parts
    for i, part in enumerate(parts):
        if not part.endswith(".app"):
            continue
        if i + 3 >= len(parts):
            continue
        if (
            parts[i + 1] == "Contents"
            and parts[i + 2] == "Resources"
            and parts[i + 3] == "runtime"
        ):
            return True
    return False


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


def writable_lock_paths_for_customer(
    customer_name: str, *, output_root: str | Path | None = None
) -> list[Path]:
    """Paths safe to mutate — never the signed App runtime tree."""
    return [
        path
        for path in lock_paths_for_customer(customer_name, output_root=output_root)
        if not is_inside_packaged_app_runtime(path)
    ]


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
    # Style is orientation-scoped and user-approved. Keep values inside safe
    # geometry; exact values are frozen into each Job and checked by READY_GATE.
    title = lock.get("title") if isinstance(lock.get("title"), dict) else {}
    canvas = lock.get("canvas") if isinstance(lock.get("canvas"), dict) else {}
    landscape = int(canvas.get("width") or 1080) > int(canvas.get("height") or 1920)
    top_default, top_low, top_high = (120, 60, 260) if landscape else (220, 120, 420)
    title["color"] = str(title.get("color") or LOCKED_TITLE_COLOR)
    title["stroke_color"] = str(
        title.get("stroke_color") or LOCKED_TITLE_STROKE_COLOR
    )
    title["stroke_width"] = max(0, min(12, int(title.get("stroke_width") or 0)))
    title["offset_y_px"] = LOCKED_TITLE_OFFSET_Y_PX
    title["glyph_top_px"] = max(
        top_low, min(top_high, int(title.get("glyph_top_px") or top_default))
    )
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
    bottom_default, bottom_low, bottom_high = (
        (180, 100, 360) if landscape else (420, 240, 620)
    )
    sub["bottom_padding_px"] = max(
        bottom_low,
        min(bottom_high, int(sub.get("bottom_padding_px") or bottom_default)),
    )
    sub["glyph_bottom_px"] = max(
        bottom_low,
        min(bottom_high, int(sub.get("glyph_bottom_px") or bottom_default)),
    )
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
    # PAPER_SLIP FROZEN: 滚动避重窗口写死；禁止恢复日/周满额
    from engine.catalog.paper_slip import (
        ROLLING_CLIPLET_WINDOW,
        ROLLING_PHRASE_WINDOW,
    )

    ps = lock.get("paper_slip") if isinstance(lock.get("paper_slip"), dict) else {}
    ps["locked"] = True
    ps["do_not_change_without_user_approval"] = True
    ps["cliplet"] = True
    ps["phrase"] = True
    ps["count_on"] = "ready_only"
    ps["timezone"] = "Asia/Shanghai"
    ps["mode"] = "rolling_diversity"
    ps["forbid_quota_circuit"] = True
    ps["rolling_cliplet_window"] = ROLLING_CLIPLET_WINDOW
    ps["rolling_phrase_window"] = ROLLING_PHRASE_WINDOW
    # Strip legacy hard-cap knobs so profile cannot reintroduce them.
    ps.pop("max_daily_uses", None)
    ps.pop("max_weekly_uses", None)
    lock["paper_slip"] = ps
    return lock


def apply_lock_to_title_style(base: dict[str, Any], lock: dict[str, Any]) -> dict[str, Any]:
    """Apply the customer-approved, safe title style."""
    t = lock.get("title") if isinstance(lock.get("title"), dict) else {}
    out = dict(base or {})
    out.update(
        {
            "position": t.get("position", out.get("position", "top")),
            "font_size": int(t.get("font_size", out.get("font_size", 92))),
            "color": str(t.get("color") or out.get("color") or LOCKED_TITLE_COLOR),
            "stroke_color": str(
                t.get("stroke_color")
                or out.get("stroke_color")
                or LOCKED_TITLE_STROKE_COLOR
            ),
            "stroke_width": max(
                0,
                min(
                    12,
                    int(
                        t.get(
                            "stroke_width",
                            out.get("stroke_width", LOCKED_TITLE_STROKE_WIDTH),
                        )
                    ),
                ),
            ),
            "offset_y_px": LOCKED_TITLE_OFFSET_Y_PX,
            "glyph_top_px": int(
                t.get("glyph_top_px")
                or out.get("glyph_top_px")
                or LOCKED_TITLE_TOP_PX
            ),
            "font_size_min": int(t.get("font_size_min") or 72),
            "font_size_max": int(t.get("font_size_max") or 92),
            "bar_opacity": float(t.get("bar_opacity", out.get("bar_opacity", 0.0))),
            "bold": bool(t.get("bold", out.get("bold", True))),
            "layout": t.get("layout", out.get("layout", "dual_chip")),
            "max_chars": int(t.get("max_chars") or out.get("max_chars") or 24),
            "max_lines": int(t.get("max_lines") or out.get("max_lines") or 2),
        }
    )
    return out


def overlay_production_rules_on_title_style(
    title_style: dict[str, Any] | None,
    production_rules: dict[str, Any] | None,
    *,
    orientation: str = "portrait",
) -> dict[str, Any]:
    """Job 冻结规则叠在 VIDEO_LOCK 之上（READY_GATE：锁 → 冻结值精确验收）。"""
    out = dict(title_style or {})
    er = production_rules if isinstance(production_rules, dict) else {}
    if not er:
        return out
    if er.get("title_font_size") is not None:
        out["font_size"] = int(er["title_font_size"])
    if er.get("title_color"):
        out["color"] = str(er["title_color"])
    if er.get("title_stroke_color"):
        out["stroke_color"] = str(er["title_stroke_color"])
    if er.get("title_stroke_width") is not None:
        out["stroke_width"] = int(er["title_stroke_width"])
    if er.get("title_stroke_enabled") is False:
        out["stroke_width"] = 0
    if er.get("title_glyph_top_px") is not None:
        out["glyph_top_px"] = int(er["title_glyph_top_px"])
    elif out.get("glyph_top_px") is None:
        out["glyph_top_px"] = 120 if str(orientation) == "landscape" else LOCKED_TITLE_TOP_PX
    out["effect"] = str(er.get("title_effect") or out.get("effect") or "none")
    out["align"] = str(er.get("title_align") or out.get("align") or "center")
    return out


def resolve_title_style(
    base: dict[str, Any] | None = None,
    *,
    customer_name: str,
    production_rules: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    output_root: str | Path | None = None,
    orientation: str = "portrait",
) -> dict[str, Any]:
    """规划/渲染共用：VIDEO_LOCK 上色后再叠 Job 冻结规则，避免跟镜黄字默认分叉。"""
    out = dict(base or {})
    lock = load_video_lock(
        str(customer_name or ""),
        output_root=output_root,
        profile=profile if isinstance(profile, dict) else None,
    )
    out = apply_lock_to_title_style(out, lock)
    return overlay_production_rules_on_title_style(
        out, production_rules, orientation=orientation
    )


def sync_video_lock_into_rules(
    rules: dict[str, Any] | None,
    *,
    customer_name: str,
    profile: dict[str, Any] | None = None,
    output_root: str | None = None,
) -> dict[str, Any]:
    """把客户 VIDEO_LOCK 标题/字幕/音色写入生产规则，避免 READY_GATE 与实渲分叉。

    跟镜等路径会按 VIDEO_LOCK 上色；若 Job 冻结规则仍是实验室默认黄字，
    会连续质检失败并质量熔断。
    """
    out = dict(rules or {})
    lock = load_video_lock(
        str(customer_name or ""),
        output_root=output_root,
        profile=profile if isinstance(profile, dict) else None,
    )
    title = lock.get("title") if isinstance(lock.get("title"), dict) else {}
    subtitle = lock.get("subtitle") if isinstance(lock.get("subtitle"), dict) else {}
    voice = lock.get("voice") if isinstance(lock.get("voice"), dict) else {}
    if title.get("color"):
        out["title_color"] = str(title.get("color"))
    if title.get("stroke_color"):
        out["title_stroke_color"] = str(title.get("stroke_color"))
    if title.get("stroke_width") is not None:
        out["title_stroke_width"] = int(title.get("stroke_width"))
    if title.get("glyph_top_px") is not None:
        out["title_glyph_top_px"] = int(title.get("glyph_top_px"))
    if title.get("font_size") is not None:
        out["title_font_size"] = int(title.get("font_size"))
    if subtitle.get("color"):
        out["subtitle_color"] = str(subtitle.get("color"))
    if subtitle.get("outline"):
        out["subtitle_stroke_color"] = str(subtitle.get("outline"))
    if subtitle.get("glyph_bottom_px") is not None:
        out["subtitle_glyph_bottom_px"] = int(subtitle.get("glyph_bottom_px"))
    if voice.get("provider"):
        out["tts_provider"] = str(voice.get("provider"))
    if voice.get("voice"):
        out["tts_voice"] = str(voice.get("voice"))
    if voice.get("rate"):
        out["narration_rate"] = str(voice.get("rate"))
    if voice.get("volume"):
        out["narration_volume"] = str(voice.get("volume"))
    if voice.get("pitch"):
        out["narration_pitch"] = str(voice.get("pitch"))
    return out


def is_title_style_lock_desync(gate_fails: list[str] | None) -> bool:
    """READY_GATE 失败是否仅为标题色/描边与冻结规则不一致（可自愈）。"""
    fails = [str(x) for x in (gate_fails or []) if str(x).strip()]
    if not fails:
        return False
    return all(
        f.startswith("title: color=")
        or f.startswith("title: stroke_color=")
        or f.startswith("title: stroke_width=")
        for f in fails
    )


def apply_lock_to_settings_patch(lock: dict[str, Any]) -> dict[str, Any]:
    from engine.pack.voice_clone import is_clone_provider

    v = lock.get("voice") if isinstance(lock.get("voice"), dict) else {}
    provider = str(v.get("provider") or "edge")
    patch: dict[str, Any] = {
        "tts_provider": provider,
        "tts_voice": str(v.get("voice") or "zh-CN-XiaoxiaoNeural"),
        "tts_rate": str(v.get("rate") or "-8%"),
        "tts_pitch": str(v.get("pitch") or "+35Hz"),
        "tts_chars_per_sec_zh": float(v.get("chars_per_sec_zh") or 3.8),
    }
    if is_clone_provider(provider):
        patch["tts_clone_pack"] = str(v.get("voice_pack") or v.get("voice") or "aunt_slow")
        patch["tts_clone_speed"] = float(v.get("clone_speed") or 1.0)
        patch["tts_chars_per_sec_zh"] = float(v.get("chars_per_sec_zh") or 3.3)
        if v.get("ref_wav"):
            patch["tts_clone_ref_wav"] = str(v.get("ref_wav"))
        if v.get("ref_text"):
            patch["tts_clone_ref_text"] = str(v.get("ref_text"))
    return patch


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
            "max_chars": int(title.get("max_chars") or 24),
            "max_lines": int(title.get("max_lines") or 2),
            "layout": title.get("layout") or "dual_chip",
            "bold": bool(title.get("bold", True)),
        }
    if sub:
        out["subtitle"] = {
            "font_size": int(sub.get("font_size_px") or sub.get("font_size") or 64),
            "bottom_padding_px": int(sub.get("bottom_padding_px") or LOCKED_SUBTITLE_BOTTOM_PX),
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
