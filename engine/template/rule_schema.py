"""GVideoRules · customer-adjustable production rules + hard-lock clamping.

Two layers:
  - adjustable: theme / category / pace / duration / tone / semantic prefs …
  - hard locks: quality / title / subtitle / narration / paper-slip / READY_GATE
    — may only tighten, never relax; forbidden keys are rejected.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = "suying.production_rules.v3"
LEGACY_SCHEMA_VERSIONS = ("suying.production_rules.v1", "suying.production_rules.v2")

SCHEMA_METADATA: dict[str, Any] = {
    "schema_id": "suying.production_rules",
    "version": 3,
    "customer_scoped": True,
    "content_category_scoped": True,
    "active_scope": ["customer_id", "content_category"],
    "legacy_read_versions": list(LEGACY_SCHEMA_VERSIONS),
    "lifecycle": ["draft", "approved", "archived"],
    "physical_delete": "unapproved_unactivated_unreferenced_draft_only",
    "job_snapshot": "immutable",
    # GRuleLabOpt: recommended category keys (free-form still allowed)
    "recommended_content_categories": ["default", "premium", "scene_tour"],
}

# Safe ranges + glyph defaults per orientation (must match normalize_requested_rules).
ORIENTATION_DEFAULTS: dict[str, dict[str, Any]] = {
    "portrait": {
        "title_font_size": 92,
        "title_font_size_min": 48,
        "title_font_size_max": 128,
        "title_stroke_width": 6,
        "title_stroke_width_min": 0,
        "title_stroke_width_max": 12,
        "title_glyph_top_px": 220,
        "title_glyph_top_px_min": 120,
        "title_glyph_top_px_max": 420,
        "subtitle_font_size": 64,
        "subtitle_font_size_min": 32,
        "subtitle_font_size_max": 96,
        "subtitle_stroke_width": 4,
        "subtitle_stroke_width_min": 0,
        "subtitle_stroke_width_max": 12,
        "subtitle_glyph_bottom_px": 420,
        "subtitle_glyph_bottom_px_min": 240,
        "subtitle_glyph_bottom_px_max": 620,
    },
    "landscape": {
        "title_font_size": 76,
        "title_font_size_min": 48,
        "title_font_size_max": 128,
        "title_stroke_width": 5,
        "title_stroke_width_min": 0,
        "title_stroke_width_max": 12,
        "title_glyph_top_px": 120,
        "title_glyph_top_px_min": 60,
        "title_glyph_top_px_max": 260,
        "subtitle_font_size": 52,
        "subtitle_font_size_min": 32,
        "subtitle_font_size_max": 96,
        "subtitle_stroke_width": 4,
        "subtitle_stroke_width_min": 0,
        "subtitle_stroke_width_max": 12,
        "subtitle_glyph_bottom_px": 180,
        "subtitle_glyph_bottom_px_min": 100,
        "subtitle_glyph_bottom_px_max": 360,
    },
}

TEMPLATE_CHOICES = ("default-vertical", "fast-ship", "stable-product")
PACE_CHOICES = ("slow", "normal", "fast")
TONE_CHOICES = ("plain", "warm", "energetic", "passionate", "professional")

# Keys customers / local AI may set.
ALLOWED_KEYS = frozenset(
    {
        "theme",
        "content_facet",
        "category",
        "template_preference",
        "target_duration_sec",
        "pace",
        "narration_tone",
        "prefer_semantic_labels",
        "exclude_semantic_labels",
        "exclude_people_faces",
        "strict_semantic_v1",
        "min_cliplet_quality",
        "notes",
        "item_label_enabled",
        "item_label_side",
        "item_label_font_size",
        "item_label_safe_top",
        "item_label_safe_bottom",
        # GCustomerUX: BGM end fade presets (off / short / standard / long)
        "bgm_fade_out",
        # GVisualPack stage 1 — packaging overlays; default none (no day-job side effects)
        "intro_punch",
        "item_label_motion",
        "end_card",
        # GVisualPack2 stage 2 — light LUT + plan multi-lang reuse; default off
        "color_lut",
        "plan_lang_reuse",
        "orientation",
        "title_font_size",
        "title_color",
        "title_stroke_color",
        "title_stroke_width",
        "title_stroke_enabled",
        "title_glyph_top_px",
        "title_effect",
        "title_align",
        "title_x_pct",
        "title_font_family",
        "subtitle_font_size",
        "subtitle_color",
        "subtitle_stroke_color",
        "subtitle_stroke_width",
        "subtitle_stroke_enabled",
        "subtitle_glyph_bottom_px",
        "subtitle_effect",
        "subtitle_align",
        "subtitle_x_pct",
        "subtitle_font_family",
        "subtitle_layout",
        "subtitle_vertical_side",
        "subtitle_y_pct",
        "title_enabled",
        "subtitle_enabled",
        "narration_text_effect",
        "clip_transition",
        "clip_transition_duration_sec",
        "mask_layers",
        "sticker_layers",
        "voice_lang",
        "subtitle_lang",
        "dual_secondary_lang",
        "subtitle_burn",
        "tts_provider",
        "tts_voice",
        "voice_pack",
        "narration_rate",
        "narration_volume",
        "narration_pitch",
        "bgm_volume",
    }
)

# Explicitly forbidden — attempts are rejected, not silently dropped without report.
FORBIDDEN_KEYS = frozenset(
    {
        "skip_ready_gate",
        "ready_gate",
        "bypass_ready_gate",
        "title_offset_y_px",
        "subtitle_margin",
        "mock_tts",
        "paper_slip_daily_cap",
        "laplacian_min",
        "quality_floor_relax",
        "allow_blur",
        "disable_subtitle_align",
        "item_label_x",
        "item_label_offset_x",
        # L15: 成片时长 > 旁白时长硬锁 — 禁止任何放松键
        "skip_duration_gate",
        "bypass_duration_gate",
        "relax_duration_lock",
        "allow_video_shorter_than_narration",
        "trim_to_narration",
        "allow_trim_to_narration",
        "video_duration_le_narration",
    }
)

HARD_LOCK_READONLY = [
    {
        "id": "quality",
        "label": "画质门禁",
        "detail": "Laplacian≥48、score≥0.35；虚焦不入库；规则只能加严 min_cliplet_quality",
    },
    {
        "id": "title",
        "label": "标题安全区",
        "detail": "颜色、描边和位置按画幅规则冻结；文字必须清晰、完整且不超出画面",
    },
    {
        "id": "subtitle_narration",
        "label": "旁白×字幕",
        "detail": "样式按画幅规则冻结；话说完字幕即灭且静音空屏；只烧一次",
    },
    {
        "id": "ready_gate",
        "label": "成品库门禁",
        "detail": "READY_GATE 全过才 ready；不可跳过",
    },
    {
        "id": "duration_vs_narration",
        "label": "成片×旁白时长",
        "detail": "有旁白时成片 duration 必须严格大于旁白（≥+0.05s）；禁止裁到等长/更短",
    },
    {
        "id": "paper_slip",
        "label": "纸片规则",
        "detail": "滚动避重：近窗优先排除 + 渐进放宽；禁止日/周满额熔断",
    },
    {
        "id": "strict_semantic",
        "label": "严格语义",
        "detail": "任务启用后禁止整片回退；规则可打开不可强行关闭已启用的严格模式",
    },
    {
        "id": "item_label_geometry",
        "label": "物品名称图层",
        "detail": "仅官方证据+当前 strict 画面共同确认时显示；中文单列竖排；左/右字形边缘距画布 80px；禁止自由横移及侵入标题/字幕安全区",
    },
    {
        "id": "evidence",
        "label": "证据门禁",
        "detail": "coarse/目录候选不得冒充 strict 画面事实；无共同证据时物品名必须隐藏",
    },
]

FIELD_SCHEMA: list[dict[str, Any]] = [
    {"key": "theme", "label": "主题", "type": "string", "default": None, "default_source": "任务/行业包", "editable": True},
    {
        "key": "content_facet",
        "label": "词池内容面",
        "type": "string",
        "default": None,
        "default_source": "不钉死（跟随任务/自动选题）",
        "editable": True,
        "detail": "对应客户词池 themes 的分类键；仅影响新建任务冻结的标题/词条，不改客户默认词池文件",
    },
    {"key": "category", "label": "规则内分类提示", "type": "string", "default": None, "default_source": "内容类别", "editable": True},
    {
        "key": "template_preference",
        "label": "模板倾向",
        "type": "enum",
        "enum": list(TEMPLATE_CHOICES),
        "default": None,
        "default_source": "系统模板",
        "editable": True,
    },
    {
        "key": "target_duration_sec",
        "label": "目标时长",
        "type": "number",
        "min": 8,
        "max": 90,
        "default": None,
        "default_source": "模板",
        "editable": True,
    },
    {
        "key": "pace",
        "label": "节奏",
        "type": "enum",
        "enum": list(PACE_CHOICES),
        "default": "normal",
        "default_source": "normal",
        "editable": True,
    },
    {
        "key": "narration_tone",
        "label": "旁白语气",
        "type": "enum",
        "enum": list(TONE_CHOICES),
        "default": "plain",
        "default_source": "plain",
        "editable": True,
    },
    {"key": "prefer_semantic_labels", "label": "偏好语义", "type": "string[]", "default": [], "default_source": "空", "editable": True},
    {"key": "exclude_semantic_labels", "label": "排除语义", "type": "string[]", "default": [], "default_source": "空", "editable": True},
    {"key": "exclude_people_faces", "label": "避开人物正脸", "type": "boolean", "default": False, "default_source": "false", "editable": True},
    {"key": "strict_semantic_v1", "label": "严格语义", "type": "boolean", "default": False, "default_source": "false", "editable": True},
    {
        "key": "min_cliplet_quality",
        "label": "画质加严",
        "type": "number",
        "min": 0.35,
        "max": 1.0,
        "default": None,
        "default_source": "硬锁 0.35",
        "editable": True,
    },
    {"key": "item_label_enabled", "label": "物品名称图层", "type": "boolean", "default": False, "default_source": "false", "editable": True},
    {
        "key": "item_label_side",
        "label": "物品名侧边",
        "type": "enum",
        "enum": ["left", "right"],
        "default": "left",
        "default_source": "left",
        "editable": True,
    },
    {
        "key": "item_label_font_size",
        "label": "物品名字号",
        "type": "number",
        "min": 36,
        "max": 88,
        "default": 56,
        "default_source": "56",
        "editable": True,
    },
    {
        "key": "item_label_safe_top",
        "label": "纵向安全区顶部",
        "type": "number",
        "min": 300,
        "max": 1100,
        "default": 360,
        "default_source": "360",
        "editable": True,
    },
    {
        "key": "item_label_safe_bottom",
        "label": "纵向安全区底部",
        "type": "number",
        "min": 700,
        "max": 1500,
        "default": 1320,
        "default_source": "1320",
        "editable": True,
    },
    {
        "key": "bgm_fade_out",
        "label": "配乐结尾淡出",
        "type": "enum",
        "enum": ["off", "short", "standard", "long"],
        "default": "standard",
        "default_source": "standard",
        "editable": True,
    },
    {
        "key": "intro_punch",
        "label": "开场安全闪入",
        "type": "enum",
        "enum": ["none", "soft"],
        "default": "none",
        "default_source": "none",
        "editable": True,
    },
    {
        "key": "item_label_motion",
        "label": "物品名微入场",
        "type": "enum",
        "enum": ["none", "fade"],
        "default": "none",
        "default_source": "none",
        "editable": True,
    },
    {
        "key": "end_card",
        "label": "片尾名片条",
        "type": "enum",
        "enum": ["none", "simple"],
        "default": "none",
        "default_source": "none",
        "editable": True,
    },
    {
        "key": "color_lut",
        "label": "轻调色 LUT",
        "type": "enum",
        "enum": ["off", "light"],
        "default": "off",
        "default_source": "off",
        "editable": True,
    },
    {
        "key": "plan_lang_reuse",
        "label": "同 Plan 多语言复用",
        "type": "enum",
        "enum": ["off", "on"],
        "default": "off",
        "default_source": "off",
        "editable": True,
    },
    {
        "key": "orientation",
        "label": "成片画幅",
        "type": "enum",
        "enum": ["portrait", "landscape"],
        "default": "portrait",
        "default_source": "portrait",
        "editable": True,
    },
    {"key": "title_color", "label": "标题颜色", "type": "color", "default": "#FFE600", "default_source": "#FFE600", "editable": True},
    {
        "key": "title_font_size",
        "label": "标题字号",
        "type": "number",
        "min": 48,
        "max": 128,
        "default_source": "画幅推荐",
        "editable": True,
    },
    {
        "key": "title_stroke_width",
        "label": "标题描边宽度",
        "type": "number",
        "min": 0,
        "max": 12,
        "default_source": "画幅推荐",
        "editable": True,
    },
    {
        "key": "title_glyph_top_px",
        "label": "标题距顶部",
        "type": "number",
        "default_source": "画幅推荐",
        "editable": True,
        "orientation_scoped": True,
    },
    {"key": "subtitle_color", "label": "字幕颜色", "type": "color", "default": "#FFFFFF", "default_source": "#FFFFFF", "editable": True},
    {
        "key": "subtitle_font_size",
        "label": "字幕字号",
        "type": "number",
        "min": 32,
        "max": 96,
        "default_source": "画幅推荐",
        "editable": True,
    },
    {
        "key": "subtitle_stroke_width",
        "label": "字幕描边宽度",
        "type": "number",
        "min": 0,
        "max": 12,
        "default": 4,
        "default_source": "4",
        "editable": True,
    },
    {
        "key": "subtitle_glyph_bottom_px",
        "label": "字幕距底部",
        "type": "number",
        "default_source": "画幅推荐",
        "editable": True,
        "orientation_scoped": True,
    },
    {
        "key": "bgm_volume",
        "label": "配乐音量",
        "type": "number",
        "min": 0.0,
        "max": 1.0,
        "default": 0.48,
        "default_source": "0.48",
        "editable": True,
    },
    {"key": "voice_lang", "label": "旁白语言", "type": "string", "default": "zh", "default_source": "zh", "editable": True},
    {"key": "subtitle_lang", "label": "字幕语言", "type": "string", "default": "zh", "default_source": "zh", "editable": True},
    {
        "key": "tts_provider",
        "label": "旁白音色来源",
        "type": "enum",
        "enum": ["edge", "clone"],
        "default": "edge",
        "default_source": "edge",
        "editable": True,
    },
    {"key": "notes", "label": "备注", "type": "text", "default": "", "default_source": "空", "editable": True},
]


def orientation_defaults() -> dict[str, dict[str, Any]]:
    """Portrait/landscape glyph defaults and UI-safe ranges (GRuleLabOpt)."""
    return deepcopy(ORIENTATION_DEFAULTS)


def rules_for_orientation(orientation: str = "portrait") -> dict[str, Any]:
    """empty_rules with orientation-specific title/subtitle sizes applied."""
    orient = orientation if orientation in {"portrait", "landscape"} else "portrait"
    base = empty_rules()
    od = ORIENTATION_DEFAULTS[orient]
    base["orientation"] = orient
    base["title_font_size"] = od["title_font_size"]
    base["title_stroke_width"] = od["title_stroke_width"]
    base["title_glyph_top_px"] = od["title_glyph_top_px"]
    base["subtitle_font_size"] = od["subtitle_font_size"]
    base["subtitle_stroke_width"] = od["subtitle_stroke_width"]
    base["subtitle_glyph_bottom_px"] = od["subtitle_glyph_bottom_px"]
    return base

BGM_FADE_PRESETS: dict[str, float] = {
    "off": 0.0,
    "short": 1.5,
    "standard": 3.0,
    "long": 5.0,
}


def bgm_fade_out_seconds(rules: dict[str, Any] | None) -> float:
    raw = (rules or {}).get("bgm_fade_out", "standard")
    key = str(raw or "standard").strip().lower()
    if key in BGM_FADE_PRESETS:
        return float(BGM_FADE_PRESETS[key])
    try:
        return max(0.0, min(8.0, float(key)))
    except (TypeError, ValueError):
        return float(BGM_FADE_PRESETS["standard"])



def empty_rules() -> dict[str, Any]:
    return {
        "theme": None,
        "content_facet": None,
        "category": None,
        "template_preference": None,
        "target_duration_sec": None,
        "pace": "normal",
        "narration_tone": "plain",
        "prefer_semantic_labels": [],
        "exclude_semantic_labels": [],
        "exclude_people_faces": False,
        "strict_semantic_v1": False,
        "min_cliplet_quality": None,
        "notes": "",
        "item_label_enabled": False,
        "item_label_side": "left",
        "item_label_font_size": 56,
        "item_label_safe_top": 360,
        "item_label_safe_bottom": 1320,
        "bgm_fade_out": "standard",
        "intro_punch": "none",
        "item_label_motion": "none",
        "end_card": "none",
        "color_lut": "off",
        "plan_lang_reuse": "off",
        "orientation": "portrait",
        "title_font_size": 92,
        "title_color": "#FFE600",
        "title_stroke_color": "#000000",
        "title_stroke_width": 6,
        "title_stroke_enabled": True,
        "title_glyph_top_px": 220,
        "title_effect": "none",
        "title_align": "center",
        "title_x_pct": 50.0,
        "title_font_family": "default",
        "subtitle_font_size": 64,
        "subtitle_color": "#FFFFFF",
        "subtitle_stroke_color": "#000000",
        "subtitle_stroke_width": 4,
        "subtitle_stroke_enabled": True,
        "subtitle_glyph_bottom_px": 420,
        "subtitle_effect": "none",
        "subtitle_align": "center",
        "subtitle_x_pct": 50.0,
        "subtitle_font_family": "default",
        "subtitle_layout": "horizontal",
        "subtitle_vertical_side": "left",
        "subtitle_y_pct": 50.0,
        "title_enabled": True,
        "subtitle_enabled": True,
        "narration_text_effect": "none",
        "clip_transition": "none",
        "clip_transition_duration_sec": 0.4,
        "mask_layers": [],
        "sticker_layers": [],
        "voice_lang": "zh",
        "subtitle_lang": "zh",
        "dual_secondary_lang": "en",
        "subtitle_burn": "burn_mono",
        "tts_provider": "edge",
        "tts_voice": "zh-CN-XiaoxiaoNeural",
        "voice_pack": "aunt_slow",
        "narration_rate": "-8%",
        "narration_volume": "+12%",
        "narration_pitch": "+35Hz",
        "bgm_volume": 0.48,
    }


def parse_json_schema(*, require_complete: bool = False) -> dict[str, Any]:
    """JSON Schema for Ollama format= — additionalProperties false."""
    nullable_string = {"type": ["string", "null"]}
    nullable_template: dict[str, Any] = {
        "anyOf": [
            {"type": "string", "enum": list(TEMPLATE_CHOICES)},
            {"type": "null"},
        ],
    }
    nullable_number: dict[str, Any] = {
        "type": ["number", "null"],
        "minimum": 8,
        "maximum": 90,
    }
    nullable_quality: dict[str, Any] = {
        "type": ["number", "null"],
        "minimum": 0.35,
        "maximum": 1.0,
    }
    if require_complete:
        nullable_string = {"type": "string", "minLength": 1}
        nullable_template = {"type": "string", "enum": list(TEMPLATE_CHOICES)}
        nullable_number = {"type": "number", "minimum": 8, "maximum": 90}
        nullable_quality = {"type": "number", "minimum": 0.35, "maximum": 1.0}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "theme",
            "category",
            "template_preference",
            "target_duration_sec",
            "pace",
            "narration_tone",
            "prefer_semantic_labels",
            "exclude_semantic_labels",
            "exclude_people_faces",
            "strict_semantic_v1",
            "min_cliplet_quality",
            "item_label_enabled",
            "item_label_side",
            "item_label_font_size",
            "item_label_safe_top",
            "item_label_safe_bottom",
            "notes",
            "confidence",
            "warnings",
        ],
        "properties": {
            "theme": nullable_string,
            "content_facet": nullable_string,
            "category": nullable_string,
            "template_preference": nullable_template,
            "target_duration_sec": nullable_number,
            "pace": {"type": "string", "enum": list(PACE_CHOICES)},
            "narration_tone": {"type": "string", "enum": list(TONE_CHOICES)},
            "orientation": {"type": "string", "enum": ["portrait", "landscape"]},
            "title_font_size": {"type": "integer", "minimum": 48, "maximum": 128},
            "title_color": {"type": "string"},
            "title_stroke_color": {"type": "string"},
            "title_stroke_width": {"type": "integer", "minimum": 0, "maximum": 12},
            "title_stroke_enabled": {"type": "boolean"},
            "title_glyph_top_px": {"type": "integer", "minimum": 60, "maximum": 420},
            "title_effect": {"type": "string", "enum": ["none", "fade"]},
            "title_align": {"type": "string", "enum": ["left", "center", "right"]},
            "title_x_pct": {"type": "number", "minimum": 8, "maximum": 92},
            "subtitle_font_size": {"type": "integer", "minimum": 32, "maximum": 96},
            "subtitle_color": {"type": "string"},
            "subtitle_stroke_color": {"type": "string"},
            "subtitle_stroke_width": {"type": "integer", "minimum": 0, "maximum": 12},
            "subtitle_stroke_enabled": {"type": "boolean"},
            "subtitle_glyph_bottom_px": {"type": "integer", "minimum": 100, "maximum": 620},
            "subtitle_effect": {"type": "string", "enum": ["none", "fade"]},
            "subtitle_align": {"type": "string", "enum": ["left", "center", "right"]},
            "subtitle_x_pct": {"type": "number", "minimum": 8, "maximum": 92},
            "voice_lang": {"type": "string"},
            "subtitle_lang": {"type": "string"},
            "dual_secondary_lang": {"type": "string"},
            "subtitle_burn": {"type": "string", "enum": ["external", "burn_mono", "burn_dual"]},
            "tts_provider": {"type": "string", "enum": ["edge", "clone"]},
            "tts_voice": {"type": "string"},
            "voice_pack": {"type": "string"},
            "narration_rate": {"type": "string"},
            "narration_volume": {"type": "string"},
            "narration_pitch": {"type": "string"},
            "bgm_volume": {"type": "number", "minimum": 0, "maximum": 1},
            "prefer_semantic_labels": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 12,
            },
            "exclude_semantic_labels": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 12,
            },
            "exclude_people_faces": {"type": "boolean"},
            "strict_semantic_v1": {"type": "boolean"},
            "min_cliplet_quality": nullable_quality,
            "item_label_enabled": {"type": "boolean"},
            "item_label_side": {"type": "string", "enum": ["left", "right"]},
            "item_label_font_size": {"type": "integer", "minimum": 36, "maximum": 88},
            "item_label_safe_top": {"type": "integer", "minimum": 300, "maximum": 1100},
            "item_label_safe_bottom": {"type": "integer", "minimum": 700, "maximum": 1500},
            "intro_punch": {"type": "string", "enum": ["none", "soft"]},
            "item_label_motion": {"type": "string", "enum": ["none", "fade"]},
            "end_card": {"type": "string", "enum": ["none", "simple"]},
            "color_lut": {"type": "string", "enum": ["off", "light"]},
            "plan_lang_reuse": {"type": "string", "enum": ["off", "on"]},
            "title_font_family": {"type": "string"},
            "subtitle_font_family": {"type": "string"},
            "subtitle_layout": {"type": "string", "enum": ["horizontal", "vertical"]},
            "subtitle_vertical_side": {"type": "string", "enum": ["left", "right"]},
            "subtitle_y_pct": {"type": "number", "minimum": 8, "maximum": 92},
            "title_enabled": {"type": "boolean"},
            "subtitle_enabled": {"type": "boolean"},
            "narration_text_effect": {
                "type": "string",
                "enum": ["none", "karaoke", "marquee"],
            },
            "clip_transition": {"type": "string"},
            "clip_transition_duration_sec": {
                "type": "number",
                "minimum": 0.05,
                "maximum": 0.8,
            },
            "mask_layers": {"type": "array", "maxItems": 8},
            "sticker_layers": {"type": "array", "maxItems": 8},
            "notes": {
                "type": "string",
                "maxLength": 500,
                **({"minLength": 1} if require_complete else {}),
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "warnings": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        },
    }


def _clean_str(value: Any, *, max_len: int = 128) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "default", "自动"}:
        return None
    return text[:max_len]


def content_facet_of(rules: dict[str, Any] | None) -> str | None:
    """Pinned keyword-pack theme, or None when the rule does not pin a facet."""
    if not isinstance(rules, dict):
        return None
    return _clean_str(rules.get("content_facet"))


def _clean_str_list(value: Any, *, max_items: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        s = _clean_str(item, max_len=64)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def _clamp_overlay_layers(
    value: Any,
    *,
    max_layers: int,
    kind: str,
    builtin_ids: frozenset[str],
    rejected: list[str],
) -> list[dict[str, Any]]:
    """Clamp mask/sticker layer lists. Coordinates are percent of frame (0–100 center)."""
    if value is None or value == "":
        return []
    if not isinstance(value, list):
        rejected.append(f"{kind}_layers 须为数组，已清空")
        return []
    if len(value) > max_layers:
        rejected.append(f"{kind}_layers 超过上限 {max_layers}，已截断")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(value[:max_layers]):
        if not isinstance(raw, dict):
            rejected.append(f"{kind}_layers[{i}] 非法，已跳过")
            continue
        source = str(raw.get("source") or raw.get("id") or "").strip()[:256]
        if not source:
            rejected.append(f"{kind}_layers[{i}] 缺 source，已跳过")
            continue
        # Allow builtin ids, user: paths, absolute paths, twemoji:/emoji: glyphs
        if (
            source not in builtin_ids
            and not source.startswith("user:")
            and not source.startswith("/")
            and not source.startswith("geom_")
            and not source.startswith("twemoji:")
            and not source.startswith("emoji:")
            and source not in {"twemoji", "noto_emoji", "user_png"}
        ):
            # still allow freeform path-like; geom_* builtins covered
            if kind == "mask" and source not in builtin_ids and not (
                source.startswith("user:") or "/" in source or source.endswith((".png", ".webp", ".jpg", ".jpeg"))
            ):
                rejected.append(f"{kind}_layers[{i}] source 未识别，已跳过")
                continue
            if kind == "sticker" and not (
                source.startswith("user:")
                or source.startswith("twemoji:")
                or source.startswith("emoji:")
                or "/" in source
                or source.endswith((".png", ".webp", ".jpg", ".jpeg"))
            ):
                rejected.append(f"{kind}_layers[{i}] source 未识别，已跳过")
                continue
        try:
            x_pct = float(raw.get("x_pct", 50.0))
            y_pct = float(raw.get("y_pct", 50.0))
            scale = float(raw.get("scale", 1.0))
            opacity = float(raw.get("opacity", 1.0))
            rotation = float(raw.get("rotation_deg", 0.0))
            z = int(raw.get("z", i))
            visible = bool(raw.get("visible", True))
        except (TypeError, ValueError):
            rejected.append(f"{kind}_layers[{i}] 数值无法解析，已跳过")
            continue
        # Free placement: soft bound only (center may sit well outside the frame).
        x_pct = max(-500.0, min(500.0, x_pct))
        y_pct = max(-500.0, min(500.0, y_pct))
        layer = {
            "id": str(raw.get("id") or f"{kind}-{i}")[:64],
            "source": source,
            "x_pct": round(x_pct, 3),
            "y_pct": round(y_pct, 3),
            "scale": round(max(0.02, min(12.0, scale)), 3),
            "opacity": round(max(0.0, min(1.0, opacity)), 3),
            "rotation_deg": round(max(-180.0, min(180.0, rotation)), 2),
            "z": max(0, min(64, z)),
            "visible": visible,
        }
        out.append(layer)
    out.sort(key=lambda row: int(row.get("z") or 0))
    return out


def _color(value: Any, default: str) -> str:
    text = str(value or "").strip().upper()
    return text if re.fullmatch(r"#[0-9A-F]{6}", text) else default


def _signed_setting(
    value: Any,
    *,
    suffix: str,
    default: str,
    low: int,
    high: int,
) -> str:
    match = re.fullmatch(r"\s*([+-]?\d+)\s*" + re.escape(suffix) + r"\s*", str(value or ""))
    if not match:
        return default
    number = max(low, min(high, int(match.group(1))))
    return f"{number:+d}{suffix}"


def normalize_requested_rules(raw: dict[str, Any] | None) -> tuple[dict[str, Any], list[str], list[str]]:
    """Whitelist + type normalize. Returns (requested, rejected, warnings)."""
    data = raw if isinstance(raw, dict) else {}
    rejected: list[str] = []
    warnings: list[str] = []
    requested = empty_rules()

    for key in data:
        if key in FORBIDDEN_KEYS:
            rejected.append(f"禁止字段 {key}：触碰系统硬锁，已拒绝")
        elif key not in ALLOWED_KEYS and key not in {"confidence", "warnings"}:
            rejected.append(f"未知字段 {key}：已忽略")

    # Explicit "default" is meaningful in an AI-completed form: it means
    # system auto-selection and must remain visible instead of becoming blank.
    theme_raw = data.get("theme")
    theme = str(theme_raw).strip()[:128] if theme_raw is not None else None
    if theme:
        requested["theme"] = theme
    requested["content_facet"] = _clean_str(data.get("content_facet"))
    category_raw = data.get("category")
    category = str(category_raw).strip()[:128] if category_raw is not None else None
    if category:
        requested["category"] = category

    tpl = _clean_str(data.get("template_preference"))
    if tpl:
        if tpl in TEMPLATE_CHOICES:
            requested["template_preference"] = tpl
        else:
            rejected.append(f"template_preference={tpl} 不在允许列表")

    dur = data.get("target_duration_sec")
    if dur is not None and str(dur).strip() != "":
        try:
            d = float(dur)
            if 8.0 <= d <= 90.0:
                requested["target_duration_sec"] = round(d, 1)
            else:
                rejected.append(f"target_duration_sec={d} 超出 8–90 秒")
        except (TypeError, ValueError):
            rejected.append("target_duration_sec 无法解析为数字")

    pace = _clean_str(data.get("pace")) or "normal"
    if pace in PACE_CHOICES:
        requested["pace"] = pace
    else:
        rejected.append(f"pace={pace} 无效，回退 normal")
        requested["pace"] = "normal"

    tone = _clean_str(data.get("narration_tone")) or "plain"
    if tone in TONE_CHOICES:
        requested["narration_tone"] = tone
    else:
        rejected.append(f"narration_tone={tone} 无效，回退 plain")
        requested["narration_tone"] = "plain"

    orientation = str(data.get("orientation") or "portrait").strip().lower()
    if orientation not in {"portrait", "landscape"}:
        rejected.append("成片画幅无效，已恢复为竖屏")
        orientation = "portrait"
    requested["orientation"] = orientation
    od = ORIENTATION_DEFAULTS[orientation]
    for key, default, low, high in (
        ("title_font_size", od["title_font_size"], od["title_font_size_min"], od["title_font_size_max"]),
        (
            "title_stroke_width",
            od["title_stroke_width"],
            od["title_stroke_width_min"],
            od["title_stroke_width_max"],
        ),
        (
            "title_glyph_top_px",
            od["title_glyph_top_px"],
            od["title_glyph_top_px_min"],
            od["title_glyph_top_px_max"],
        ),
        (
            "subtitle_font_size",
            od["subtitle_font_size"],
            od["subtitle_font_size_min"],
            od["subtitle_font_size_max"],
        ),
        (
            "subtitle_stroke_width",
            od["subtitle_stroke_width"],
            od["subtitle_stroke_width_min"],
            od["subtitle_stroke_width_max"],
        ),
        (
            "subtitle_glyph_bottom_px",
            od["subtitle_glyph_bottom_px"],
            od["subtitle_glyph_bottom_px_min"],
            od["subtitle_glyph_bottom_px_max"],
        ),
    ):
        try:
            requested[key] = max(low, min(high, int(data.get(key, default))))
        except (TypeError, ValueError):
            requested[key] = default
            rejected.append(f"{key} 无法识别，已恢复推荐值")
    requested["title_color"] = _color(data.get("title_color"), "#FFE600")
    requested["title_stroke_color"] = _color(
        data.get("title_stroke_color"), "#000000"
    )
    requested["subtitle_color"] = _color(data.get("subtitle_color"), "#FFFFFF")
    requested["subtitle_stroke_color"] = _color(
        data.get("subtitle_stroke_color"), "#000000"
    )
    te_stroke = data.get("title_stroke_enabled")
    requested["title_stroke_enabled"] = True if te_stroke is None else bool(te_stroke)
    se_stroke = data.get("subtitle_stroke_enabled")
    requested["subtitle_stroke_enabled"] = True if se_stroke is None else bool(se_stroke)
    if (
        requested["title_stroke_enabled"]
        and requested["title_stroke_width"] == 0
        and requested["title_color"] == requested["title_stroke_color"]
    ):
        requested["title_stroke_width"] = 2
        rejected.append("标题缺少可读边缘，已加入细描边")
    if (
        requested["subtitle_stroke_enabled"]
        and requested["subtitle_stroke_width"] == 0
        and requested["subtitle_color"] == requested["subtitle_stroke_color"]
    ):
        requested["subtitle_stroke_width"] = 2
        rejected.append("字幕缺少可读边缘，已加入细描边")
    for key in ("title_effect", "subtitle_effect"):
        value = str(data.get(key) or "none").strip().lower()
        requested[key] = value if value in {"none", "fade"} else "none"
    for key in ("title_align", "subtitle_align"):
        value = str(data.get(key) or "center").strip().lower()
        requested[key] = value if value in {"left", "center", "right"} else "center"
    for key, align_key in (("title_x_pct", "title_align"), ("subtitle_x_pct", "subtitle_align")):
        raw = data.get(key)
        if raw is None:
            # Derive from align when legacy rules omit continuous x.
            align = requested.get(align_key) or "center"
            requested[key] = 18.0 if align == "left" else 82.0 if align == "right" else 50.0
        else:
            try:
                xp = float(raw)
            except (TypeError, ValueError):
                xp = 50.0
            requested[key] = round(max(8.0, min(92.0, xp)), 2)
            # Keep discrete align in sync for older consumers.
            if requested[key] < 35.0:
                requested[align_key] = "left"
            elif requested[key] > 65.0:
                requested[align_key] = "right"
            else:
                requested[align_key] = "center"
    requested["voice_lang"] = str(data.get("voice_lang") or "zh").strip()[:16]
    requested["subtitle_lang"] = str(data.get("subtitle_lang") or "zh").strip()[:16]
    requested["dual_secondary_lang"] = str(
        data.get("dual_secondary_lang") or "en"
    ).strip()[:16]
    burn = str(data.get("subtitle_burn") or "burn_mono").strip()
    requested["subtitle_burn"] = (
        burn if burn in {"external", "burn_mono", "burn_dual"} else "burn_mono"
    )
    provider = str(data.get("tts_provider") or "edge").strip().lower()
    requested["tts_provider"] = provider if provider in {"edge", "clone"} else "edge"
    requested["tts_voice"] = str(
        data.get("tts_voice") or "zh-CN-XiaoxiaoNeural"
    ).strip()[:128]
    requested["voice_pack"] = str(data.get("voice_pack") or "aunt_slow").strip()[:128]
    requested["narration_rate"] = _signed_setting(
        data.get("narration_rate"),
        suffix="%",
        default="-8%",
        low=-50,
        high=30,
    )
    requested["narration_volume"] = _signed_setting(
        data.get("narration_volume"),
        suffix="%",
        default="+12%",
        low=-50,
        high=50,
    )
    requested["narration_pitch"] = _signed_setting(
        data.get("narration_pitch"),
        suffix="Hz",
        default="+35Hz",
        low=-50,
        high=50,
    )
    try:
        requested["bgm_volume"] = round(
            max(0.0, min(1.0, float(data.get("bgm_volume", 0.48)))), 3
        )
    except (TypeError, ValueError):
        requested["bgm_volume"] = 0.48

    requested["prefer_semantic_labels"] = _clean_str_list(data.get("prefer_semantic_labels"))
    requested["exclude_semantic_labels"] = _clean_str_list(data.get("exclude_semantic_labels"))
    requested["exclude_people_faces"] = bool(data.get("exclude_people_faces"))
    requested["strict_semantic_v1"] = bool(data.get("strict_semantic_v1"))
    requested["item_label_enabled"] = bool(data.get("item_label_enabled"))
    side = str(data.get("item_label_side") or "left").strip().lower()
    requested["item_label_side"] = side if side in {"left", "right"} else "left"
    if side not in {"left", "right"}:
        rejected.append("item_label_side 仅允许 left/right，已回退 left")
    for key, default, low, high in (
        ("item_label_font_size", 56, 36, 88),
        ("item_label_safe_top", 360, 300, 1100),
        ("item_label_safe_bottom", 1320, 700, 1500),
    ):
        try:
            requested[key] = max(low, min(high, int(data.get(key, default))))
        except (TypeError, ValueError):
            requested[key] = default
            rejected.append(f"{key} 无法解析，已回退 {default}")
    if requested["item_label_safe_bottom"] - requested["item_label_safe_top"] < 240:
        requested["item_label_safe_top"] = 360
        requested["item_label_safe_bottom"] = 1320
        rejected.append("物品名纵向安全区过窄或反向，已恢复 360–1320")

    # GVisualPack · default-off packaging enums
    intro = str(data.get("intro_punch") or "none").strip().lower()
    requested["intro_punch"] = intro if intro in {"none", "soft"} else "none"
    if intro not in {"none", "soft", ""}:
        rejected.append("intro_punch 仅允许 none/soft，已回退 none")
    motion = str(data.get("item_label_motion") or "none").strip().lower()
    requested["item_label_motion"] = motion if motion in {"none", "fade"} else "none"
    if motion not in {"none", "fade", ""}:
        rejected.append("item_label_motion 仅允许 none/fade，已回退 none")
    end_card = str(data.get("end_card") or "none").strip().lower()
    requested["end_card"] = end_card if end_card in {"none", "simple"} else "none"
    if end_card not in {"none", "simple", ""}:
        rejected.append("end_card 仅允许 none/simple，已回退 none")
    # GVisualPack2 · default-off stage 2 enums
    color_lut = str(data.get("color_lut") or "off").strip().lower()
    requested["color_lut"] = color_lut if color_lut in {"off", "light"} else "off"
    if color_lut not in {"off", "light", ""}:
        rejected.append("color_lut 仅允许 off/light，已回退 off")
    plan_reuse = str(data.get("plan_lang_reuse") or "off").strip().lower()
    requested["plan_lang_reuse"] = plan_reuse if plan_reuse in {"off", "on"} else "off"
    if plan_reuse not in {"off", "on", ""}:
        rejected.append("plan_lang_reuse 仅允许 off/on，已回退 off")

    # GRuleVisualLab · fonts / layout / transition / overlay layers (default-off)
    from engine.pack.fx_assets import (
        MASK_LAYER_MAX,
        STICKER_LAYER_MAX,
        TRANSITION_DURATION_DEFAULT,
        TRANSITION_DURATION_MAX,
        allowed_font_ids,
        builtin_mask_ids,
        xfade_names,
    )

    def _font_family(raw: Any, *, field: str) -> str:
        text = str(raw or "default").strip() or "default"
        allowed = allowed_font_ids()
        if text in allowed or text.startswith("system:"):
            return text[:160]
        rejected.append(f"{field} 不在白名单，已回退 default")
        return "default"

    requested["title_font_family"] = _font_family(data.get("title_font_family"), field="title_font_family")
    requested["subtitle_font_family"] = _font_family(
        data.get("subtitle_font_family"), field="subtitle_font_family"
    )
    layout = str(data.get("subtitle_layout") or "horizontal").strip().lower()
    requested["subtitle_layout"] = layout if layout in {"horizontal", "vertical"} else "horizontal"
    if layout not in {"horizontal", "vertical", ""}:
        rejected.append("subtitle_layout 仅允许 horizontal/vertical，已回退 horizontal")
    vside = str(data.get("subtitle_vertical_side") or "left").strip().lower()
    requested["subtitle_vertical_side"] = vside if vside in {"left", "right"} else "left"
    try:
        sy = float(data["subtitle_y_pct"]) if data.get("subtitle_y_pct") is not None else 50.0
    except (TypeError, ValueError):
        sy = 50.0
    requested["subtitle_y_pct"] = round(max(8.0, min(92.0, sy)), 2)
    te = data.get("title_enabled")
    requested["title_enabled"] = True if te is None else bool(te)
    se = data.get("subtitle_enabled")
    requested["subtitle_enabled"] = True if se is None else bool(se)
    # Keep side in sync with continuous x for vertical layout.
    if requested["subtitle_layout"] == "vertical" and data.get("subtitle_x_pct") is not None:
        try:
            sx = float(requested.get("subtitle_x_pct") or 50)
        except (TypeError, ValueError):
            sx = 50.0
        requested["subtitle_vertical_side"] = "left" if sx < 50.0 else "right"
    nfx = str(data.get("narration_text_effect") or "none").strip().lower()
    requested["narration_text_effect"] = (
        nfx if nfx in {"none", "karaoke", "marquee"} else "none"
    )
    if nfx not in {"none", "karaoke", "marquee", ""}:
        rejected.append("narration_text_effect 仅允许 none/karaoke/marquee，已回退 none")
    trans = str(data.get("clip_transition") or "none").strip().lower()
    if trans in {"", "none", "hard"}:
        requested["clip_transition"] = "none"
    elif trans in xfade_names():
        requested["clip_transition"] = trans
    else:
        requested["clip_transition"] = "none"
        rejected.append(f"clip_transition={trans} 不在 xfade 白名单，已回退 none")
    try:
        td = float(data.get("clip_transition_duration_sec", TRANSITION_DURATION_DEFAULT))
        requested["clip_transition_duration_sec"] = round(
            max(0.05, min(TRANSITION_DURATION_MAX, td)), 3
        )
    except (TypeError, ValueError):
        requested["clip_transition_duration_sec"] = TRANSITION_DURATION_DEFAULT

    requested["mask_layers"] = _clamp_overlay_layers(
        data.get("mask_layers"),
        max_layers=MASK_LAYER_MAX,
        kind="mask",
        builtin_ids=builtin_mask_ids(),
        rejected=rejected,
    )
    requested["sticker_layers"] = _clamp_overlay_layers(
        data.get("sticker_layers"),
        max_layers=STICKER_LAYER_MAX,
        kind="sticker",
        builtin_ids=frozenset({"twemoji", "noto_emoji", "user_png"}),
        rejected=rejected,
    )

    mq = data.get("min_cliplet_quality")
    if mq is not None and str(mq).strip() != "":
        try:
            q = float(mq)
            if q < 0.35:
                rejected.append(f"min_cliplet_quality={q} 低于硬锁 0.35，已拒绝放松")
            elif q > 1.0:
                rejected.append(f"min_cliplet_quality={q} 超出范围")
            else:
                requested["min_cliplet_quality"] = round(q, 3)
        except (TypeError, ValueError):
            rejected.append("min_cliplet_quality 无法解析")

    notes = data.get("notes")
    if notes is not None:
        requested["notes"] = str(notes).strip()[:500]

    if isinstance(data.get("warnings"), list):
        for w in data["warnings"][:20]:
            t = str(w).strip()
            if t:
                warnings.append(t[:200])

    return requested, rejected, warnings


def clamp_to_effective(
    requested: dict[str, Any],
    *,
    job_strict_semantic: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Produce effective_rules and clamp reports. Hard locks always win."""
    from engine.ingest.quality import MIN_QUALITY_SCORE

    effective = deepcopy(requested) if isinstance(requested, dict) else empty_rules()
    clamped: list[dict[str, str]] = []

    floor = float(MIN_QUALITY_SCORE)
    mq = effective.get("min_cliplet_quality")
    if mq is None:
        pass
    else:
        try:
            q = float(mq)
        except (TypeError, ValueError):
            effective["min_cliplet_quality"] = None
            clamped.append(
                {"field": "min_cliplet_quality", "reason": "无效数值，已清除", "action": "cleared"}
            )
        else:
            if q < floor:
                effective["min_cliplet_quality"] = floor
                clamped.append(
                    {
                        "field": "min_cliplet_quality",
                        "reason": f"不得低于硬锁 {floor}",
                        "action": "raised",
                    }
                )
            else:
                effective["min_cliplet_quality"] = round(q, 3)

    # Job already strict → rules cannot turn it off
    if job_strict_semantic and not bool(effective.get("strict_semantic_v1")):
        effective["strict_semantic_v1"] = True
        clamped.append(
            {
                "field": "strict_semantic_v1",
                "reason": "任务已启用严格语义，规则不得关闭",
                "action": "forced_true",
            }
        )

    dur = effective.get("target_duration_sec")
    if dur is not None:
        d = float(dur)
        if d < 8.0:
            effective["target_duration_sec"] = 8.0
            clamped.append(
                {"field": "target_duration_sec", "reason": "最短 8 秒", "action": "raised"}
            )
        elif d > 90.0:
            effective["target_duration_sec"] = 90.0
            clamped.append(
                {"field": "target_duration_sec", "reason": "最长 90 秒", "action": "lowered"}
            )

    # burn_mono: on-screen captions must match VO wording (NARRATION_SUBTITLE_LOCK).
    # Mismatched subtitle_lang previously pulled brand short-cycle templates → 字幕≠旁白.
    burn = str(effective.get("subtitle_burn") or "burn_mono").strip()
    voice_lang = str(effective.get("voice_lang") or "zh").strip() or "zh"
    sub_lang = str(effective.get("subtitle_lang") or "zh").strip() or "zh"
    if (
        burn == "burn_mono"
        and voice_lang not in ("none", "")
        and sub_lang not in ("none", "")
        and sub_lang != voice_lang
    ):
        effective["subtitle_lang"] = voice_lang
        clamped.append(
            {
                "field": "subtitle_lang",
                "reason": "burn_mono 字幕语言必须与旁白一致；双语请改 burn_dual",
                "action": "aligned_to_voice_lang",
            }
        )

    return effective, clamped


def validate_and_clamp(
    raw: dict[str, Any] | None,
    *,
    job_strict_semantic: bool = False,
) -> dict[str, Any]:
    requested, rejected, warnings = normalize_requested_rules(raw)
    effective, clamped = clamp_to_effective(requested, job_strict_semantic=job_strict_semantic)
    return {
        "schema_version": SCHEMA_VERSION,
        "requested_rules": requested,
        "effective_rules": effective,
        "rejected": rejected,
        "clamped": clamped,
        "warnings": warnings,
        "hard_locks": HARD_LOCK_READONLY,
    }


def apply_rules_to_template(template: Any, effective: dict[str, Any] | None) -> Any:
    """Return a copy of TemplateDefinition mutated by adjustable rules only."""
    from engine.template.engine import TemplateDefinition

    if not isinstance(effective, dict) or not effective:
        return template
    data = template.model_dump() if hasattr(template, "model_dump") else dict(template)
    pace = str(effective.get("pace") or "normal")
    dur = effective.get("target_duration_sec")
    if dur is not None:
        try:
            data["target_duration"] = float(dur)
        except (TypeError, ValueError):
            pass

    slots = list(data.get("slots") or [])
    scale = 1.0
    if pace == "fast":
        scale = 0.85
        data["cliplet_cooldown_recent"] = max(6, int(data.get("cliplet_cooldown_recent") or 12) - 3)
    elif pace == "slow":
        scale = 1.2
        data["cliplet_cooldown_recent"] = int(data.get("cliplet_cooldown_recent") or 12) + 4

    # Pace first (relative), then absolute duration: stretch/compress slot
    # windows so sum(max) ≈ target_duration_sec (clip-aligned product VO
    # pads each slot to that window — without this, 16s rules still ship 29s).
    if slots:
        new_slots = []
        for slot in slots:
            s = dict(slot)
            s["min_duration"] = round(max(0.5, float(s.get("min_duration") or 2.0) * scale), 3)
            s["max_duration"] = round(
                max(s["min_duration"], float(s.get("max_duration") or 5.0) * scale), 3
            )
            new_slots.append(s)
        slots = new_slots
        try:
            target = float(data.get("target_duration") or 0) or None
        except (TypeError, ValueError):
            target = None
        if target is not None and target >= 8.0:
            sum_max = sum(float(s.get("max_duration") or 0) for s in slots)
            if sum_max > 0.5:
                d_scale = float(target) / sum_max
                # Keep each slot usable for TTS pad (≥0.8s) but track target tightly.
                scaled = []
                for s in slots:
                    ns = dict(s)
                    mx = max(0.8, float(s.get("max_duration") or 1.0) * d_scale)
                    mn = max(0.6, min(mx, float(s.get("min_duration") or 0.8) * d_scale))
                    # Prefer max as the pad target; min stays close so short clips still qualify
                    ns["max_duration"] = round(mx, 3)
                    ns["min_duration"] = round(mn, 3)
                    scaled.append(ns)
                slots = scaled
                data["target_duration"] = round(float(target), 3)
                # Longer targets need more unique product shots — ease recent cooldown
                if float(target) >= 28.0:
                    data["cliplet_cooldown_recent"] = max(
                        4, int(data.get("cliplet_cooldown_recent") or 12) // 2
                    )
        data["slots"] = slots

    mq = effective.get("min_cliplet_quality")
    if mq is not None:
        try:
            from engine.ingest.quality import MIN_QUALITY_SCORE

            data["min_cliplet_quality"] = max(float(MIN_QUALITY_SCORE), float(mq))
        except (TypeError, ValueError):
            pass

    if effective.get("orientation") == "landscape":
        data["output_width"], data["output_height"] = 1920, 1080
    else:
        data["output_width"], data["output_height"] = 1080, 1920
    data["title_font_size"] = int(
        effective.get("title_font_size") or data.get("title_font_size") or 92
    )
    data["title_color"] = str(
        effective.get("title_color") or data.get("title_color") or "#FFE600"
    )
    data["title_stroke_color"] = str(
        effective.get("title_stroke_color")
        or data.get("title_stroke_color")
        or "#000000"
    )
    data["title_stroke_width"] = int(
        effective.get("title_stroke_width")
        if effective.get("title_stroke_width") is not None
        else data.get("title_stroke_width", 6)
    )

    return TemplateDefinition(**data)


def resolve_job_inputs(
    *,
    theme: str = "default",
    category: str = "default",
    template_name: str = "default-vertical",
    strict_semantic_v1: bool = False,
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge task overrides with effective customer rules."""
    report = validate_and_clamp(rules, job_strict_semantic=strict_semantic_v1)
    eff = report["effective_rules"]

    out_theme = (theme or "default").strip() or "default"
    out_category = (category or "default").strip() or "default"
    out_tpl = (template_name or "default-vertical").strip() or "default-vertical"
    sources: dict[str, str] = {
        "theme": "task",
        "category": "task",
        "template_name": "task",
        "strict_semantic_v1": "task",
    }

    if out_theme in {"", "default"} and eff.get("theme"):
        out_theme = str(eff["theme"])
        sources["theme"] = "rule"
    if out_category in {"", "default"} and eff.get("category"):
        out_category = str(eff["category"])
        sources["category"] = "rule"
    if out_tpl == "default-vertical" and eff.get("template_preference"):
        out_tpl = str(eff["template_preference"])
        sources["template_name"] = "rule"
    elif out_tpl == "default-vertical" and eff.get("pace") == "fast":
        out_tpl = "fast-ship"
        sources["template_name"] = "rule_pace"

    # Product-first semantic prefs + fast-ship is a known mismatch: VO can't keep up
    # with ~2s cuts and titles drift to 装车. Prefer stable-product holds.
    prefer = [str(x).lower() for x in (eff.get("prefer_semantic_labels") or []) if str(x).strip()]
    productish = sum(1 for p in prefer if "product" in p or p in {"closeup", "sku", "shelf"})
    loadingish = sum(
        1
        for p in prefer
        if any(k in p for k in ("load", "deliver", "warehouse", "truck", "shipping", "装", "仓", "配"))
    )
    if out_tpl == "fast-ship" and productish >= 1 and productish >= loadingish:
        out_tpl = "stable-product"
        sources["template_name"] = "rule_product_pace_guard"

    out_strict = bool(strict_semantic_v1) or bool(eff.get("strict_semantic_v1"))
    if out_strict and not strict_semantic_v1:
        sources["strict_semantic_v1"] = "rule"

    facet = content_facet_of(eff)
    if facet:
        sources["content_facet"] = "rule"

    return {
        **report,
        "theme": out_theme,
        "category": out_category,
        "template_name": out_tpl,
        "strict_semantic_v1": out_strict,
        "content_facet": facet,
        "sources": sources,
    }


def rules_semantic_hints(effective: dict[str, Any] | None) -> dict[str, Any]:
    """Hints consumed by build_plan / _assemble_clips."""
    eff = effective if isinstance(effective, dict) else {}
    prefer = list(eff.get("prefer_semantic_labels") or [])
    exclude = list(eff.get("exclude_semantic_labels") or [])
    if eff.get("exclude_people_faces"):
        # The planner currently consumes scene exclusions.  Include the v1
        # scene label plus legacy scene aliases so this switch changes actual
        # candidate filtering instead of remaining UI-only metadata.
        for label in ("people_activity", "person", "people", "portrait", "work_portrait", "team_image"):
            if label not in exclude:
                exclude.append(label)
    notes = str(eff.get("notes") or "").strip()
    tone = str(eff.get("narration_tone") or "").strip()
    extra_query_parts = []
    if prefer:
        extra_query_parts.append(" ".join(prefer[:8]))
    if eff.get("exclude_people_faces"):
        extra_query_parts.append("无人、无人物、无正脸的产品或环境镜头")
    if notes:
        extra_query_parts.append(notes[:120])
    if tone and tone != "plain":
        extra_query_parts.append(f"语气{tone}")
    return {
        "prefer_scenes": prefer,
        "exclude_scenes": exclude,
        "semantic_query_extra": " ".join(extra_query_parts).strip(),
        "narration_tone": tone or "plain",
    }
