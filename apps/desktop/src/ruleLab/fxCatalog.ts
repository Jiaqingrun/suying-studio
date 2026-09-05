/** Controlled clip_transition catalog labels (GRuleVisualLab). */
export const TRANSITION_LABELS: Record<string, string> = {
  none: "硬切",
  fade: "淡入淡出",
  fadeblack: "黑场淡入",
  fadewhite: "白场淡入",
  fadegrays: "灰场淡入",
  dissolve: "溶解",
  pixelize: "像素化",
  wipeleft: "左擦除",
  wiperight: "右擦除",
  wipeup: "上擦除",
  wipedown: "下擦除",
  slideleft: "左滑",
  slideright: "右滑",
  slideup: "上滑",
  slidedown: "下滑",
  circlecrop: "圆形开合",
  circleopen: "圆展开",
  circleclose: "圆收合",
  vertopen: "纵向开",
  vertclose: "纵向合",
  horzopen: "横向开",
  horzclose: "横向合",
  diagtl: "对角擦除",
  radial: "径向",
  hblur: "横向模糊",
  distance: "距离模糊",
  squeezeh: "水平挤压",
  squeezev: "垂直挤压",
  zoomin: "推近",
  revealleft: "左揭开",
  revealright: "右揭开",
  revealup: "上揭开",
  revealdown: "下揭开",
  coverleft: "左覆盖",
  coverright: "右覆盖",
  coverup: "上覆盖",
  coverdown: "下覆盖",
};

/** Featured chips shown above the full select. */
export const TRANSITION_FEATURED = [
  "none",
  "fade",
  "dissolve",
  "wipeleft",
  "wiperight",
  "slideup",
  "slidedown",
  "circleopen",
  "zoomin",
  "pixelize",
] as const;

export const STICKER_EMOJI_PRESETS = ["✨", "⭐", "🔥", "💡", "👍", "❤️", "🎉", "📌"] as const;

export function transitionLabel(id: string): string {
  const key = String(id || "none");
  return TRANSITION_LABELS[key] || key;
}

export function overlayBasename(source: string): string {
  const s = String(source || "");
  if (s.startsWith("geom_")) return s.replace(/^geom_/, "");
  if (s.startsWith("twemoji:") || s.startsWith("emoji:")) {
    return s.slice(s.indexOf(":") + 1) || "表情";
  }
  if (s.startsWith("user:")) {
    const path = s.slice(5);
    const base = path.split("/").pop() || path;
    return base.length > 18 ? `${base.slice(0, 16)}…` : base;
  }
  const base = s.split("/").pop() || s;
  return base.length > 18 ? `${base.slice(0, 16)}…` : base;
}
