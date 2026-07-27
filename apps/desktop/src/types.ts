export type Tab =
  | "overview"
  | "produce"
  | "review"
  | "publish"
  | "messages"
  | "data"
  | "ops"
  | "settings";

/** Production page inner workspace */
export type ProduceWorkspace = "tasks" | "assets";

/** Publish page inner workspace */
export type PublishWorkspace = "pack" | "desk" | "reach";

/** Ops page sections */
export type OpsSection = "ai" | "services" | "carrier" | "health" | "logs";

/** Settings page sections */
export type SettingsSection =
  | "customer"
  | "brand"
  | "defaults"
  | "notify"
  | "paths"
  | "advanced";

export type LayoutDensityPref = "auto" | "comfort" | "compact";
export type LayoutDensity = "compact" | "standard" | "wide";

export type FlashKind = "ok" | "err" | "info" | "warn";

export type ReachMessageAccount = {
  id: number;
  customer_id: number;
  platform: string;
  profile_name: string;
  display_name: string;
  enabled: boolean;
  cooldown_sec: number;
  message_url: string;
  last_scanned_at?: string | null;
  last_status?: string | null;
  last_error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type ReachMessage = {
  id: number;
  account_id: number;
  customer_id: number;
  platform: string;
  sender: string;
  summary: string;
  notification_sender: string;
  notification_summary: string;
  reply_url: string;
  unread: boolean;
  source: string;
  confidence: number;
  read_at?: string | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
};

export type ReachMessageScanStatus = {
  active: boolean;
  phase: string;
  message?: string;
  error?: string | null;
  account_id?: number | null;
  platform?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  cancelled?: boolean;
};

export type ReachNtfyConfig = {
  enabled: boolean;
  server_url: string;
  topic: string;
  auth_mode: "none" | "token" | "basic";
  token_configured: boolean;
  username_configured: boolean;
  password_configured: boolean;
};

/** Strict semantic v1 backfill — GET /index/captions/status */
export type SemanticBackfillStatus = {
  schema_version: string;
  eligible: number;
  processed: number;
  passed: number;
  rejected: number;
  remaining: number;
  last_cursor: number | null;
  claimed: number;
};

export const TABS: Array<[Tab, string, string]> = [
  ["overview", "总览", "01"],
  ["produce", "生产", "02"],
  ["review", "审片", "03"],
  ["publish", "发布", "04"],
  ["messages", "消息", "05"],
  ["data", "数据", "06"],
  ["ops", "运维", "07"],
  ["settings", "设置", "08"],
];

export const TAB_BLURB: Record<Tab, string> = {
  overview: "今日产线与下一步",
  produce: "素材、任务与内容日历",
  review: "抽检成片、通过或重渲",
  publish: "物料、发布台与触达",
  messages: "平台消息摘要与官方回复",
  data: "产能、质量与发布统计",
  ops: "引擎、本地 AI、载体与同步",
  settings: "客户、表达与高级配置",
};

/** Main business pipeline order for next-step linking */
export const PIPELINE_TABS: Tab[] = ["overview", "produce", "review", "publish"];

export function nextPipelineTab(current: Tab): Tab | null {
  const i = PIPELINE_TABS.indexOf(current);
  if (i < 0 || i >= PIPELINE_TABS.length - 1) return null;
  return PIPELINE_TABS[i + 1] ?? null;
}
