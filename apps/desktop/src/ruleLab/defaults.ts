import type { Orientation, OrientationDefaults, RuleSchemaBundle } from "./types";
import { FALLBACK_EMPTY_RULES, FALLBACK_ORIENTATION_DEFAULTS } from "./types";

export function resolveOrientationDefaults(
  schema: RuleSchemaBundle | null,
  orientation: Orientation,
): OrientationDefaults {
  const fromApi = schema?.orientation_defaults?.[orientation];
  if (fromApi && typeof fromApi.title_glyph_top_px === "number") {
    return fromApi as OrientationDefaults;
  }
  return FALLBACK_ORIENTATION_DEFAULTS[orientation];
}

/** Build draft defaults from schema empty_rules + orientation glyph metrics. */
export function buildEmptyRules(
  schema: RuleSchemaBundle | null,
  orientation: Orientation,
): Record<string, unknown> {
  const base = {
    ...(schema?.empty_rules || FALLBACK_EMPTY_RULES),
  };
  const od = resolveOrientationDefaults(schema, orientation);
  return {
    ...base,
    orientation,
    title_font_size: od.title_font_size,
    title_stroke_width: od.title_stroke_width,
    title_glyph_top_px: od.title_glyph_top_px,
    subtitle_font_size: od.subtitle_font_size,
    subtitle_stroke_width: od.subtitle_stroke_width,
    subtitle_glyph_bottom_px: od.subtitle_glyph_bottom_px,
  };
}

export function asList(v: unknown): string {
  if (Array.isArray(v)) return v.map(String).join(", ");
  return "";
}

export function parseList(s: string): string[] {
  return s
    .split(/[,，\s]+/)
    .map((x) => x.trim())
    .filter(Boolean)
    .slice(0, 12);
}

/** Normalize to #rrggbb for <input type="color">. */
export function asColorHex(value: unknown, fallback: string): string {
  const raw = String(value || fallback || "").trim();
  const match = raw.match(/^#?([0-9a-fA-F]{6})$/);
  if (match) return `#${match[1].toLowerCase()}`;
  const short = raw.match(/^#?([0-9a-fA-F]{3})$/);
  if (short) {
    const [r, g, b] = short[1].toLowerCase().split("");
    return `#${r}${r}${g}${g}${b}${b}`;
  }
  return fallback.startsWith("#") ? fallback.toLowerCase() : `#${fallback}`;
}

export function packagingOpenCount(draft: Record<string, unknown>): number {
  let n = 0;
  if (String(draft.intro_punch || "none") !== "none") n += 1;
  if (String(draft.item_label_motion || "none") !== "none") n += 1;
  if (String(draft.end_card || "none") !== "none") n += 1;
  if (String(draft.color_lut || "off") !== "off") n += 1;
  if (String(draft.plan_lang_reuse || "off") !== "off") n += 1;
  if (String(draft.clip_transition || "none") !== "none") n += 1;
  if (String(draft.narration_text_effect || "none") !== "none") n += 1;
  if (String(draft.subtitle_layout || "horizontal") === "vertical") n += 1;
  if (Array.isArray(draft.mask_layers) && draft.mask_layers.length) n += 1;
  if (Array.isArray(draft.sticker_layers) && draft.sticker_layers.length) n += 1;
  return n;
}

export function mergeRecommendedCategories(
  fromList: string[],
  recommended: string[],
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const c of [...recommended, ...fromList, "default", "premium", "scene_tour"]) {
    const key = (c || "").trim() || "default";
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  return out;
}
