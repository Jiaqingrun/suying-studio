export interface TopicIntentInput {
  mode: "single_product" | "same_category_products";
  cluster_ids: number[];
  official_evidence_ids: number[];
  similarity_threshold: number;
  requested_uses: string[];
}

export type Tab =
  | "overview"
  | "produce"
  | "rules"
  | "review"
  | "publish"
  | "messages"
  | "data"
  | "ops"
  | "settings";

/** Production page inner workspace */
export type ProduceWorkspace = "tasks" | "assets";

/** Publish page inner workspace */
export type PublishWorkspace = "pack" | "articles" | "desk" | "reach";
export type PublishDomain = "video" | "content";
export type VideoPublishPlatform = "douyin" | "channels" | "xhs" | "kuaishou";
export type PublishPackStatus = "pending" | "packing" | "ready" | "pack_failed";
export type PlatformAssetStatus = Record<
  VideoPublishPlatform,
  {
    status: "ready" | "blocked";
    error?: string;
    title?: boolean;
    body?: boolean;
    hashtags?: boolean;
    manifest?: string;
    compliance?: string;
  }
>;

export type RenderOutputPublishAssets = {
  pack_status: PublishPackStatus;
  pack_dir?: string | null;
  pack_error?: string;
  pack_version?: string;
  platform_asset_status?: Partial<PlatformAssetStatus>;
};

/** Ops page sections */
export type OpsSection =
  | "ai"
  | "services"
  | "backup"
  | "carrier"
  | "logs"
  | "health"
  | "advanced";

/** Settings page sections */
export type SettingsSection =
  | "customer"
  | "brand"
  | "accounts"
  | "defaults"
  | "notify"
  | "storage"
  | "paths"
  | "advanced";

export type LayoutDensityPref = "auto" | "comfort" | "compact";
export type LayoutDensity = "compact" | "standard" | "wide";

export type RuntimePauseState =
  | "ACTIVE"
  | "PAUSING"
  | "PAUSED"
  | "RESUMING"
  | "PAUSED_BLOCKED"
  | string;

export type VectorizationRunState =
  | "IDLE"
  | "RUNNING"
  | "PAUSE_REQUESTED"
  | "PAUSED"
  | "COMPLETED"
  | "BLOCKED"
  | "FAILED";

export type VectorizationGaps = {
  portrait_pending?: number;
  landscape_pending?: number;
  portrait_completed?: number;
  landscape_completed?: number;
  total_pending?: number;
  total_completed?: number;
};

export type VectorizationStatus = {
  status: VectorizationRunState;
  run_id: string | null;
  customer_id?: number | null;
  customer_name?: string;
  orientation?: "portrait" | "landscape";
  enabled: boolean;
  /** Settings master mode — always incremental when enabled. */
  vectorization_mode?: string;
  mode?: "count" | "all";
  requested_count?: number | null;
  cutoff_at?: string | null;
  current_asset_id: number | null;
  frozen_assets: number;
  frozen_cliplets?: number;
  /** Library-scoped fully vectorized assets for the selected orientation. */
  completed_assets: number;
  completed_cliplets: number;
  pending_assets: number;
  pending_cliplets: number;
  total_assets?: number;
  run_completed_assets?: number;
  run_completed_cliplets?: number;
  requested_at?: string | null;
  started_at?: string | null;
  paused_at?: string | null;
  finished_at?: string | null;
  pause_owner?: "manual" | "system" | "restart" | "schedule" | null;
  error?: string | null;
  /** Daily local window (Asia/Shanghai) edge schedule */
  schedule_enabled?: boolean;
  schedule_start?: string;
  schedule_end?: string;
  auto_stop_when_done?: boolean;
  in_window?: boolean;
  next_edge_hint?: string;
  schedule_timezone?: string;
  schedule_last_in_window?: boolean | null;
  gaps?: VectorizationGaps;
  message?: string;
};

export type FlashKind = "ok" | "err" | "info" | "warn";

export type InstallPlan = {
  schema_version: string;
  plan_id: string;
  created_at: string;
  app_version: string;
  host: Record<string, unknown> & {
    ram_gb?: number;
    chip?: string;
    arch?: string;
    tier?: string;
    disk_free_gb?: number;
  };
  profile: {
    id: string;
    label: string;
    reason: string;
    max_render_concurrency: number;
    semantic_mode: string;
    full_library_vision: boolean;
    vision_candidate_limit: number;
  };
  components: Array<{
    id: string;
    kind: string;
    name: string;
    required: boolean;
    selected: boolean;
    approx_gb: number;
  }>;
  selected_models: string[];
  estimated_download_gb: number;
  minimum_free_disk_gb: number;
  settings_patch: Record<string, unknown>;
  gates: Array<{ id: string; ok: boolean }>;
  human_steps: string[];
  warnings: string[];
  approved_at?: string | null;
  configured_at?: string | null;
  models_started_at?: string | null;
};

export type ReachMessageAccount = {
  id: number;
  customer_id: number;
  business_scope?: "video" | "content";
  platform: string;
  profile_name?: string;
  profile_role?: "message" | "shared_legacy" | "archived" | string | null;
  display_name: string;
  purpose?: "video" | "article" | "message" | "legacy";
  provisioning_status?: "explicit" | "legacy_unverified" | "deleted";
  explicit_created_at?: string | null;
  enabled: boolean;
  cooldown_sec: number;
  message_url: string;
  readonly_verified?: boolean;
  reply_supported?: boolean;
  supported_kinds?: string[];
  last_scanned_at?: string | null;
  last_status?: string | null;
  last_error?: string | null;
  /** From latest ReachMessageScan.error_code (login_required / chrome_busy / …). */
  last_error_code?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type MessageListQuery = {
  unread: boolean;
  history?: boolean;
  account_id?: number;
  limit: number;
};

export type ChromeProfile = {
  name: string;
  path: string;
  platform?: string | null;
  label?: string | null;
  profile_initialized?: boolean;
  cookie_store_present?: boolean;
  platform_cookie_count?: number | null;
  login_data_state?: "present" | "missing" | "not_initialized" | "unknown";
  login_status: "checking" | "verified_logged_in" | "logged_out" | "stale_unknown";
  login_checked_at?: string | null;
  login_status_source?: string;
  official_url?: string | null;
  custom_platform?: boolean;
  last_profile_exit_type?: string | null;
  business_scope?: "video" | "content";
  purpose?: "video" | "article" | "legacy";
  provisioning_status?: "explicit" | "legacy_unverified";
  explicit_created_at?: string | null;
};

export type PublishWindow = {
  start: string;
  end: string;
  weekdays?: number[];
  date?: string;
  count: number;
  min_gap_minutes: number;
};

export type PublishTrigger = {
  id: number;
  schedule_id: number;
  planned_at: string;
  status: string;
  occurrence_key?: string | null;
  local_date?: string | null;
  window_start?: string | null;
  window_end?: string | null;
  seed?: string | null;
  random_algorithm?: string;
  ordinal?: number;
  conflict_adjustment?: {
    reason?: string;
    attempts?: number;
    blocked_by_run?: string;
  };
  bypass_window_reason?: "quality_recovery" | null;
};

export type PublishSchedule = {
  id: number;
  name: string;
  enabled: boolean;
  timezone: string;
  chrome_profile: string;
  platform: string;
  content_source: "manual_ready" | "eligible_ready_random" | "generate_then_publish";
  source_config: {
    queue_ids?: number[];
    template_name?: string;
    theme?: string;
    category?: string;
  };
  times: Array<{ hour: number; minute: number; weekdays?: number[] }>;
  windows: PublishWindow[];
  random_algorithm: string;
  items_per_trigger: number;
  repeat_count: number;
  next_trigger_at?: string | null;
};

export type PublishRunStatus =
  | "queued"
  | "running"
  | "paused_human"
  | "outcome_unknown"
  | "stopping"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted_system";

export type PublishItemPhase =
  | "queued"
  | "switching_profile"
  | "waiting_login"
  | "uploading"
  | "filling_copy"
  | "setting_cover"
  | "submitting"
  | "verifying"
  | "paused_human"
  | "outcome_unknown"
  | "awaiting_confirmation"
  | "published"
  | "deferred"
  | "failed"
  | "skipped"
  | "cancelled";

export type PublishRunItem = {
  id: number;
  ordinal: number;
  queue_id?: number | null;
  output_id?: number | null;
  platform: string;
  chrome_profile: string;
  group_label?: string;
  phase: PublishItemPhase;
  outcome?: string | null;
  retry_count: number;
  retry_mode?: "manual" | "auto" | "none" | "";
  retry_after?: string | null;
  retry_of_item_id?: number | null;
  retry_run_id?: string | null;
  error?: string;
  note?: string;
  title?: string;
  started_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
  can_skip?: boolean;
  can_retry?: boolean;
  requires_outcome_confirmation?: boolean;
};

export type PublishRun = {
  ok: boolean;
  active?: boolean;
  run_id: string;
  customer_id: number;
  status: PublishRunStatus;
  phase: string;
  source: string;
  launch_mode: string;
  requested_total: number;
  current_item?: PublishRunItem | null;
  items: PublishRunItem[];
  counts: {
    total: number;
    published: number;
    failed: number;
    deferred: number;
    cancelled: number;
    remaining: number;
  };
  requested_action?: string;
  heartbeat_at?: string | null;
  error?: string;
  can_stop?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type HumanAlert = {
  id: number;
  kind: string;
  status: "open" | "acknowledged" | "resolved";
  summary: string;
  deep_link: string;
  escalation_step: number;
  next_escalation_at?: string | null;
  channels: Record<
    string,
    {
      at: string;
      app?: { ok: boolean; state?: string };
      macos?: { ok: boolean; detail?: string };
      ntfy?: { sent?: boolean; status?: string; detail?: string };
    }
  >;
};

export type ReachMessage = {
  id: number;
  account_id: number;
  customer_id: number;
  business_scope?: "video" | "content";
  platform: string;
  /** comment | dm | like | other — likes never notify */
  kind?: "comment" | "dm" | "like" | "other" | string;
  sender: string;
  summary: string;
  notification_sender: string;
  notification_summary: string;
  reply_url: string;
  unread: boolean;
  source: string;
  confidence: number;
  platform_event_at?: string | null;
  identity_source?: string;
  platform_unread?: boolean | null;
  redaction_version?: string;
  purged_at?: string | null;
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

/** Strict semantic coverage / on-demand verification status. */
export type SemanticBackfillStatus = {
  schema_version: string;
  eligible: number;
  processed: number;
  passed: number;
  rejected: number;
  remaining: number;
  last_cursor: number | null;
  claimed: number;
  mode?: "on_demand" | string;
  full_backfill_enabled?: boolean;
  full_backfill_env_override?: boolean;
  primary_model?: string;
  cascade?: boolean;
  worker?: {
    lab_enabled?: boolean;
    worker_running?: boolean;
    last?: Record<string, unknown>;
  };
};

export const TABS: Array<[Tab, string, string]> = [
  ["overview", "总览", "01"],
  ["produce", "生产", "02"],
  ["rules", "规则", "03"],
  ["review", "审片", "04"],
  ["publish", "发布", "05"],
  ["messages", "消息", "06"],
  ["data", "数据", "07"],
  ["ops", "运维", "08"],
  ["settings", "设置", "09"],
];

export const TAB_BLURB: Record<Tab, string> = {
  overview: "今日产线与下一步",
  produce: "素材、任务与内容日历",
  rules: "横竖画幅、标题、字幕与声音",
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
