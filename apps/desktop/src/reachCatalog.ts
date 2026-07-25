/** Built-in Reach platform + cover-slot catalog (mirrors engine PLATFORM_COVER_SPECS). */

export type CoverSlotSpec = {
  index: number;
  id: string;
  label: string;
  aspect: string;
  width: number;
  height: number;
  max_mb: number;
  role: string;
};

export type ReachPlatform = {
  id: string;
  label: string;
  url: string;
  short: string;
  cover_slots: number;
};

export const BUILTIN_COVER_SLOT_SPECS: Record<string, CoverSlotSpec[]> = {
  douyin: [
    {
      index: 0,
      id: "vertical",
      label: "竖封面",
      aspect: "9:16",
      width: 1080,
      height: 1920,
      max_mb: 5,
      role: "信息流/主页竖展示",
    },
    {
      index: 1,
      id: "horizontal",
      label: "横封面",
      aspect: "16:9",
      width: 1920,
      height: 1080,
      max_mb: 5,
      role: "横版推荐位与横视频",
    },
  ],
  kuaishou: [
    {
      index: 0,
      id: "vertical",
      label: "竖封面",
      aspect: "9:16",
      width: 1080,
      height: 1920,
      max_mb: 5,
      role: "信息流竖展示；横视频亦需竖封面",
    },
    {
      index: 1,
      id: "horizontal",
      label: "横封面",
      aspect: "16:9",
      width: 1920,
      height: 1080,
      max_mb: 5,
      role: "横版推荐位",
    },
  ],
  channels: [
    {
      index: 0,
      id: "vertical",
      label: "竖封面",
      aspect: "6:7",
      width: 1080,
      height: 1260,
      max_mb: 5,
      role: "视频号官方竖比例；朋友圈分享还会裁 1:1，核心居中",
    },
    {
      index: 1,
      id: "horizontal",
      label: "横封面",
      aspect: "16:9",
      width: 1920,
      height: 1080,
      max_mb: 5,
      role: "横版视频封面",
    },
  ],
  xhs: [
    {
      index: 0,
      id: "feed",
      label: "信息流封面",
      aspect: "3:4",
      width: 1080,
      height: 1440,
      max_mb: 5,
      role: "小红书信息流最优比例",
    },
  ],
  baijiahao: [
    {
      index: 0,
      id: "horizontal",
      label: "横封面",
      aspect: "16:9",
      width: 1280,
      height: 720,
      max_mb: 5,
      role: "百家号视频封面；建议 ≥720P，亦可 1920×1080",
    },
  ],
  toutiao: [
    {
      index: 0,
      id: "horizontal",
      label: "横封面",
      aspect: "16:9",
      width: 1920,
      height: 1080,
      max_mb: 5,
      role: "今日头条/头条号信息流横卡",
    },
  ],
  zhihu: [
    {
      index: 0,
      id: "vertical",
      label: "竖封面",
      aspect: "9:16",
      width: 1080,
      height: 1920,
      max_mb: 5,
      role: "知乎竖版视频",
    },
    {
      index: 1,
      id: "horizontal",
      label: "横封面",
      aspect: "16:9",
      width: 1920,
      height: 1080,
      max_mb: 5,
      role: "知乎横版视频",
    },
  ],
};

export const BUILTIN_REACH_PLATFORMS: ReachPlatform[] = [
  { id: "douyin", label: "抖音创作者中心", url: "https://creator.douyin.com/", short: "抖音", cover_slots: 2 },
  { id: "channels", label: "微信视频号助手", url: "https://channels.weixin.qq.com/", short: "视频号", cover_slots: 2 },
  { id: "xhs", label: "小红书创作者服务平台", url: "https://creator.xiaohongshu.com/", short: "小红书", cover_slots: 1 },
  { id: "kuaishou", label: "快手创作者服务平台", url: "https://cp.kuaishou.com/", short: "快手", cover_slots: 2 },
  { id: "baijiahao", label: "百家号", url: "https://baijiahao.baidu.com/", short: "百家号", cover_slots: 1 },
  { id: "toutiao", label: "头条号", url: "https://mp.toutiao.com/", short: "头条", cover_slots: 1 },
  { id: "zhihu", label: "知乎创作者中心", url: "https://www.zhihu.com/creator", short: "知乎", cover_slots: 2 },
];

export const BUILTIN_SLOT_COUNTS: Record<string, number> = Object.fromEntries(
  Object.entries(BUILTIN_COVER_SLOT_SPECS).map(([k, v]) => [k, v.length]),
);

/** Never shrink below product defaults when merging API/persisted counts. */
export function mergeSlotCounts(apiCounts?: Record<string, number> | null): Record<string, number> {
  const out: Record<string, number> = { ...BUILTIN_SLOT_COUNTS };
  if (!apiCounts) return out;
  for (const [plat, raw] of Object.entries(apiCounts)) {
    const id = String(plat || "").trim();
    if (!id) continue;
    const n = Number(raw);
    if (!Number.isFinite(n) || n < 1) continue;
    out[id] = Math.max(out[id] ?? 1, Math.floor(n), BUILTIN_SLOT_COUNTS[id] ?? 1);
  }
  return out;
}

/** Prefer API rows, but never drop built-in platforms; fill missing fields from catalog. */
export function mergeReachPlatforms(
  apiPlatforms?: Array<{ id: string; label: string; url: string; short?: string; cover_slots?: number }> | null,
): ReachPlatform[] {
  const byId = new Map(BUILTIN_REACH_PLATFORMS.map((p) => [p.id, { ...p }]));
  for (const p of apiPlatforms || []) {
    const id = String(p.id || "").trim();
    if (!id) continue;
    const base = byId.get(id);
    byId.set(id, {
      id,
      label: p.label || base?.label || id,
      url: p.url || base?.url || "",
      short: p.short || base?.short || id,
      cover_slots: Math.max(
        p.cover_slots ?? 0,
        base?.cover_slots ?? BUILTIN_SLOT_COUNTS[id] ?? 1,
        BUILTIN_SLOT_COUNTS[id] ?? 1,
      ),
    });
  }
  // Stable order: built-in first, then any extra API-only platforms
  const ordered: ReachPlatform[] = [];
  const seen = new Set<string>();
  for (const p of BUILTIN_REACH_PLATFORMS) {
    const row = byId.get(p.id);
    if (row) {
      ordered.push(row);
      seen.add(p.id);
    }
  }
  for (const [id, row] of byId) {
    if (!seen.has(id)) ordered.push(row);
  }
  return ordered;
}

export function mergeCoverSlotSpecs(
  apiSpecs?: Record<string, Array<Partial<CoverSlotSpec>>> | null,
): Record<string, CoverSlotSpec[]> {
  const out: Record<string, CoverSlotSpec[]> = {};
  for (const [plat, specs] of Object.entries(BUILTIN_COVER_SLOT_SPECS)) {
    out[plat] = specs.map((s) => ({ ...s }));
  }
  if (!apiSpecs) return out;
  for (const [plat, list] of Object.entries(apiSpecs)) {
    if (!Array.isArray(list) || !list.length) continue;
    const builtin = out[plat] || [];
    out[plat] = list.map((s, i) => {
      const b = builtin[i] || builtin[0];
      return {
        index: typeof s.index === "number" ? s.index : i,
        id: String(s.id || b?.id || `slot${i + 1}`),
        label: String(s.label || b?.label || `槽${i + 1}`),
        aspect: String(s.aspect || b?.aspect || ""),
        width: Number(s.width || b?.width || 0),
        height: Number(s.height || b?.height || 0),
        max_mb: Number(s.max_mb || b?.max_mb || 5),
        role: String(s.role || b?.role || ""),
      };
    });
    // Keep at least builtin length
    if (builtin.length > out[plat].length) {
      out[plat] = [...out[plat], ...builtin.slice(out[plat].length)];
    }
  }
  return out;
}
