/** 跟镜精品 · 用户可见中文文案（与 engine/pack/scene_tour_copy.py 对齐） */

export const CONTENT_CATEGORY_LABELS: Record<string, string> = {
  default: "日常日更",
  premium: "精品样式",
  scene_tour: "跟镜精品",
  store_culture: "门店文化",
};

/** 词库 / 行业包内部 theme 键 → 中文 */
export const PACK_THEME_LABELS: Record<string, string> = {
  brands: "品牌专题",
  scenario: "场景专题",
  supply: "供应",
  service: "仓配服务",
  uncategorized: "未分类",
};

export const PIPELINE_PHASE_LABELS: Record<string, string> = {
  producing: "生产中",
  rendering: "渲染中",
  completed: "已完成",
  queued: "排队中",
  blocked_plan: "规划受阻",
  blocked_path_readonly: "路径只读",
  blocked_clone_runtime: "音色未就绪",
  paused_ollama_infra: "旁白基建暂停",
};

export const TEMPLATE_PREFERENCE_LABELS: Record<string, string> = {
  "default-vertical": "竖屏默认",
  "fast-ship": "快发配送",
  "stable-product": "稳态产品",
};

export const TOPIC_MODE_LABELS: Record<string, string> = {
  "": "普通生产",
  single_product: "单品专题",
  same_category_products: "同类产品专题",
};

export const SCENE_TOUR_UI = {
  generate: "生成跟镜精品",
  brief: "跟镜简报",
  coverage: "场景覆盖",
  validation: "成片检查报告",
  blockReasons: "未通过原因",
  shotAlign: "画面与旁白对照",
  badge: "跟镜",
  bootstrap: "一键补场景分类",
  dailySecondary: "日常日更 · 立即生产 1 条",
} as const;

function hasCjk(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(text);
}

function isInternalSlug(text: string): boolean {
  return /^[a-z][a-z0-9_-]*$/i.test(text);
}

/** 生产任务 / 日历 theme 字段 → 用户可见中文（禁止裸显 default / scene_tour 等内部键） */
export function productionThemeLabel(key: string | null | undefined): string {
  const k = (key ?? "").trim();
  if (!k || k === "—") return "—";
  const mapped = CONTENT_CATEGORY_LABELS[k] ?? PACK_THEME_LABELS[k];
  if (mapped) return mapped;
  if (hasCjk(k)) return k;
  if (isInternalSlug(k)) return "未命名主题";
  return k;
}

/** 生产任务 category 字段 → 用户可见中文 */
export function productionCategoryLabel(key: string | null | undefined): string {
  return productionThemeLabel(key);
}

/** 内容类别（规则实验室 / 轮换池）→ 中文 */
export function contentCategoryLabel(key: string | null | undefined): string {
  const k = (key || "default").trim() || "default";
  return CONTENT_CATEGORY_LABELS[k] ?? productionThemeLabel(k);
}

export function pipelinePhaseLabel(phase: string | null | undefined): string {
  const k = (phase ?? "").trim();
  if (!k) return "";
  if (PIPELINE_PHASE_LABELS[k]) return PIPELINE_PHASE_LABELS[k];
  if (hasCjk(k)) return k;
  if (isInternalSlug(k)) return "处理中";
  return k;
}

export function templatePreferenceLabel(key: string | null | undefined): string {
  const k = (key ?? "").trim();
  if (!k) return "（跟随任务/行业）";
  return TEMPLATE_PREFERENCE_LABELS[k] ?? productionThemeLabel(k);
}

export function topicModeLabel(mode: string | null | undefined): string {
  const k = (mode ?? "").trim();
  return TOPIC_MODE_LABELS[k] ?? (k ? productionThemeLabel(k) : TOPIC_MODE_LABELS[""]);
}

/** 素材库文件夹分类 → 中文 */
export function assetCategoryLabel(key: string | null | undefined): string {
  const k = (key ?? "").trim();
  if (!k) return "未分类";
  if (PACK_THEME_LABELS[k]) return PACK_THEME_LABELS[k];
  if (k === "uncategorized") return "未分类";
  if (hasCjk(k)) return k;
  if (isInternalSlug(k)) return k.replace(/_/g, " ");
  return k;
}

export function bucketLabel(key: string | null | undefined): string {
  const map: Record<string, string> = {
    entrance: "进店门头",
    honor_wall: "荣誉展示",
    culture_wall: "服务说明墙",
    corridor: "走廊过道",
    slippers: "换履区",
    vanity: "梳妆台",
    sterilize: "消毒准备",
    treatment_bed: "护理床位",
    treatment_room: "护理间",
    supply: "备料柜",
    treatment_action: "护理过程",
    tea: "茶点休憩",
    other: "其他景别",
    storefront: "门店门头",
    warehouse: "仓内货架",
    loading: "装车卸货",
    product_closeup: "产品特写",
    transport: "配送在途",
    inventory_full: "库存陈列",
  };
  const k = (key || "other").trim() || "other";
  return map[k] || k;
}

export function roleLabel(role: string | null | undefined): string {
  const r = (role || "").trim();
  if (r === "open") return "开场";
  if (r === "close") return "收束";
  if (r === "body") return "巡店";
  return r || "巡店";
}

export const PRODUCTION_CATEGORY_OPTIONS = Object.entries(CONTENT_CATEGORY_LABELS).map(
  ([value, label]) => ({ value, label }),
);

/** Common Asset.category folder names (not rule slots). */
export const ASSET_FOLDER_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "不限（全部 ready 素材）" },
  { value: "Camera", label: "Camera" },
  { value: "DJI Album", label: "DJI Album" },
  { value: "uncategorized", label: "未分类" },
];

export function isKnownProductionCategory(key: string | null | undefined): boolean {
  const k = (key ?? "").trim();
  return Boolean(k && k in CONTENT_CATEGORY_LABELS);
}
