export type Orientation = "portrait" | "landscape";

export type RuleRow = {
  id: number;
  name: string;
  content_category: string;
  source_text: string;
  requested_rules: Record<string, unknown>;
  effective_rules: Record<string, unknown>;
  status: string;
  revision: number;
  model?: string;
  parse_warnings?: string[];
  rejected?: string[];
  clamped?: Array<Record<string, string> | string>;
  activated_at?: string | null;
  rotation_enabled?: boolean;
};

export type OrientationDefaults = {
  title_font_size: number;
  title_font_size_min: number;
  title_font_size_max: number;
  title_stroke_width: number;
  title_stroke_width_min: number;
  title_stroke_width_max: number;
  title_glyph_top_px: number;
  title_glyph_top_px_min: number;
  title_glyph_top_px_max: number;
  subtitle_font_size: number;
  subtitle_font_size_min: number;
  subtitle_font_size_max: number;
  subtitle_stroke_width: number;
  subtitle_stroke_width_min: number;
  subtitle_stroke_width_max: number;
  subtitle_glyph_bottom_px: number;
  subtitle_glyph_bottom_px_min: number;
  subtitle_glyph_bottom_px_max: number;
};

export type RuleSchemaBundle = {
  empty_rules: Record<string, unknown>;
  orientation_defaults: Record<string, OrientationDefaults>;
  hard_locks: Array<{ id: string; label: string; detail: string }>;
  recommended_content_categories: string[];
  fx_assets?: {
    xfade_transitions?: string[];
    fonts?: Array<{
      id: string;
      label: string;
      use_hint?: string | null;
      available?: boolean;
      path?: string | null;
    }>;
    builtin_masks?: Array<{ id: string; label: string }>;
    mask_layer_max?: number;
    narration_text_effects?: string[];
    subtitle_layouts?: string[];
    sticker_sources?: Array<Record<string, unknown>>;
  };
};

/** Hard fallback only until schema loads (matches engine empty_rules portrait). */
export const FALLBACK_EMPTY_RULES: Record<string, unknown> = {
  theme: null,
  content_facet: null,
  category: null,
  template_preference: null,
  target_duration_sec: null,
  pace: "normal",
  narration_tone: "plain",
  prefer_semantic_labels: [],
  exclude_semantic_labels: [],
  exclude_people_faces: false,
  strict_semantic_v1: false,
  min_cliplet_quality: null,
  notes: "",
  item_label_enabled: false,
  item_label_side: "left",
  item_label_font_size: 56,
  item_label_safe_top: 360,
  item_label_safe_bottom: 1320,
  bgm_fade_out: "standard",
  intro_punch: "none",
  item_label_motion: "none",
  end_card: "none",
  color_lut: "off",
  plan_lang_reuse: "off",
  orientation: "portrait",
  title_font_size: 92,
  title_color: "#FFE600",
  title_stroke_color: "#000000",
  title_stroke_width: 6,
  title_stroke_enabled: true,
  title_glyph_top_px: 220,
  title_effect: "none",
  title_align: "center",
  title_x_pct: 50,
  title_font_family: "default",
  subtitle_font_size: 64,
  subtitle_color: "#FFFFFF",
  subtitle_stroke_color: "#000000",
  subtitle_stroke_width: 4,
  subtitle_stroke_enabled: true,
  subtitle_glyph_bottom_px: 420,
  subtitle_effect: "none",
  subtitle_align: "center",
  subtitle_x_pct: 50,
  subtitle_y_pct: 50,
  subtitle_font_family: "default",
  subtitle_layout: "horizontal",
  subtitle_vertical_side: "left",
  title_enabled: true,
  subtitle_enabled: true,
  narration_text_effect: "none",
  clip_transition: "none",
  clip_transition_duration_sec: 0.4,
  mask_layers: [],
  sticker_layers: [],
  voice_lang: "zh",
  subtitle_lang: "zh",
  dual_secondary_lang: "en",
  subtitle_burn: "burn_mono",
  tts_provider: "edge",
  tts_voice: "zh-CN-XiaoxiaoNeural",
  voice_pack: "aunt_slow",
  narration_rate: "-8%",
  narration_volume: "+12%",
  narration_pitch: "+35Hz",
  bgm_volume: 0.48,
};

export const FALLBACK_ORIENTATION_DEFAULTS: Record<Orientation, OrientationDefaults> = {
  portrait: {
    title_font_size: 92,
    title_font_size_min: 48,
    title_font_size_max: 128,
    title_stroke_width: 6,
    title_stroke_width_min: 0,
    title_stroke_width_max: 12,
    title_glyph_top_px: 220,
    title_glyph_top_px_min: 120,
    title_glyph_top_px_max: 420,
    subtitle_font_size: 64,
    subtitle_font_size_min: 32,
    subtitle_font_size_max: 96,
    subtitle_stroke_width: 4,
    subtitle_stroke_width_min: 0,
    subtitle_stroke_width_max: 12,
    subtitle_glyph_bottom_px: 420,
    subtitle_glyph_bottom_px_min: 240,
    subtitle_glyph_bottom_px_max: 620,
  },
  landscape: {
    title_font_size: 76,
    title_font_size_min: 48,
    title_font_size_max: 128,
    title_stroke_width: 5,
    title_stroke_width_min: 0,
    title_stroke_width_max: 12,
    title_glyph_top_px: 120,
    title_glyph_top_px_min: 60,
    title_glyph_top_px_max: 260,
    subtitle_font_size: 52,
    subtitle_font_size_min: 32,
    subtitle_font_size_max: 96,
    subtitle_stroke_width: 4,
    subtitle_stroke_width_min: 0,
    subtitle_stroke_width_max: 12,
    subtitle_glyph_bottom_px: 180,
    subtitle_glyph_bottom_px_min: 100,
    subtitle_glyph_bottom_px_max: 360,
  },
};
