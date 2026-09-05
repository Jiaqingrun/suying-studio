import type {
  ChromeProfile,
  HumanAlert,
  InstallPlan,
  PublishRun,
  PublishRunItem,
  PublishSchedule,
  PublishTrigger,
} from "../types";

export const API_BASE = "http://127.0.0.1:8766";

export type RuntimeLicenseStatus = {
  authorized: boolean;
  development_build: boolean;
  license_kind: "trial" | "perpetual" | "development" | "";
  locked_reason: string;
  expires_at?: string;
  trial_remaining_sec?: number;
};

/** In-app media URLs served by the engine (avoids broken file:// in Tauri/webview). */
export function outputVideoUrl(id: number, bust?: string | number | null): string {
  const q = bust != null && bust !== "" ? `?c=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}/outputs/${id}/video${q}`;
}

export function outputCoverUrl(id: number, index: number, bust?: string | number | null): string {
  const q = bust != null && bust !== "" ? `?c=${encodeURIComponent(String(bust))}` : "";
  return `${API_BASE}/outputs/${id}/cover/${index}${q}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      ...init,
    });
  } catch (e) {
    const detail = e instanceof Error && e.message ? e.message : String(e);
    const lower = detail.toLowerCase();
    // Transport-only failures; map common macOS / WebView copy to clear actions.
    let hint = "请确认引擎已启动";
    if (
      lower.includes("failed to fetch") ||
      lower.includes("load failed") ||
      lower.includes("networkerror")
    ) {
      hint = "可能是引擎刚重启或短暂不可达，请稍后重试";
    } else if (
      lower.includes("connection refused") ||
      lower.includes("econnrefused") ||
      detail.includes("Connection refused")
    ) {
      hint = "引擎端口未监听，请到运维页启动引擎后重试";
    } else if (lower.includes("abort") || lower.includes("timeout")) {
      hint = "请求超时，引擎可能正忙或数据库繁忙，请稍后重试";
    }
    // Keep Chinese banner stable; include transport detail for sticky-error clear + debug.
    throw new Error(`无法连接速影引擎，${hint}（${detail}）`);
  }
  if (!res.ok) {
    const text = await res.text();
    let message = text || res.statusText;
    try {
      const body = JSON.parse(text) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) message = body.detail;
      else if (body.detail && typeof body.detail === "object") {
        const d = body.detail as { message?: string; code?: string };
        if (d.message) message = d.message;
      }
    } catch {
      // Keep non-JSON response text.
    }
    const prefix =
      res.status === 409
        ? "当前路径或生产状态不可用"
        : res.status === 423
          ? "运行时已暂停"
          : `请求失败 (${res.status})`;
    throw new Error(`${prefix}：${message}`);
  }
  return res.json() as Promise<T>;
}

export type Health = {
  status: string;
  workspace_state?: string;
  workspace?: {
    state?: string;
    ok?: boolean;
    data_root?: string;
    reasons?: string[];
    workspace_id?: string | null;
    volume_uuid?: string | null;
  };
  product_name?: string;
  engine_version: string;
  active_customer?: string;
  active_customer_id?: number;
  paths: Record<string, unknown>;
  path_health: {
    ok: boolean;
    free_disk_gb: number;
    warnings: string[];
    errors: string[];
    library_mounted?: boolean;
    output_mounted?: boolean;
    writable?: boolean;
    write_checks?: Array<{
      path?: string;
      role?: string;
      ok?: boolean;
      detail?: string;
    }>;
  };
  disk?: Record<string, unknown>;
  auto_daily_enabled?: boolean;
  auto_daily_hour?: number;
  vectorization_enabled?: boolean;
  vectorization_enabled_at?: string | null;
  vectorization_mode?: string;
  /** L16: always true when engine enforces orientation hard audit. */
  orientation_hard_lock?: boolean;
  orientation_lock?: {
    lock?: string;
    lock_version?: number;
    hard?: boolean;
    integrity_ok?: boolean;
    ready_total?: number;
    ready_audited_ok?: number;
    ready_unverified?: number;
    rejected_orientation?: number;
    ready_by_orientation?: Record<string, number>;
    message?: string;
  };
  ollama_narration_enabled?: boolean;
  ollama_narration_model?: string;
  ollama_narration_burn_emoji?: boolean;
  onboarded?: boolean;
  setup_complete?: boolean;
  frames_root?: string;
  render_root?: string;
  vector_store?: { kind?: string; db?: string; table?: string; column?: string; note?: string };
  worker_running?: boolean;
  scheduler_running?: boolean;
  watcher_running?: boolean;
  runtime_state?: string;
  pause_reasons?: string[];
  system_event_generation?: number;
  resume_blockers?: string[];
  system_event_control?: Record<string, unknown>;
  ollama?: {
    reachable?: boolean;
    embed_ready?: boolean;
    vision_ready?: boolean;
    escalate_ready?: boolean | null;
    ready?: boolean;
    message?: string;
    embed_model?: string;
    vision_model?: string;
    escalate_model?: string | null;
    cascade?: boolean;
    vision_timeout_sec?: number;
    escalate_timeout_sec?: number;
    install_url?: string;
    host?: Record<string, unknown>;
    recommended?: {
      tier?: string;
      embed_model?: string;
      vision_model?: string;
      escalate_model?: string | null;
      cascade?: boolean;
      vision_label?: string;
      reason?: string;
    };
    setup_steps?: Array<{ id: string; title: string; ok: boolean; detail: string }>;
    pull?: Record<string, unknown>;
  };
};

export type Customer = {
  id: number;
  name: string;
  library_root: string | null;
  library_roots: string[];
  output_root: string | null;
  keyword_pack_path?: string | null;
  active: boolean;
  profile?: Record<string, unknown>;
};

export type ReportSummary = {
  generated_at: string;
  timezone: "Asia/Shanghai";
  business_date: string;
  source: "database";
  customer_id: number;
  assets: number;
  cliplets: number;
  cliplets_indexed: number;
  outputs: Record<string, number>;
  ready_available: number;
  production_passed: number;
  failed: number;
  quality_pass_rate: number | null;
  failure_rate: number | null;
  auto_approved: number;
  auto_rejected: number;
  uncertain_open: number;
  manual_decided: number;
  published: number;
  retired: number;
  theme_distribution: Record<string, number>;
  keyword_top: Array<{ keyword: string; count: number }>;
  reviews: Record<string, number>;
  reconciliation: {
    filesystem: {
      ready_files: number | null;
      published_files: number | null;
      ready_today_files: number | null;
      published_today_files: number | null;
    };
    differences: {
      ready_available_minus_files: number | null;
      retired_minus_files: number | null;
    };
    in_sync: boolean;
  };
};

export type ServicesStatus = {
  product_name?: string;
  watcher: boolean;
  worker: boolean;
  scheduler: boolean;
  auto_daily_enabled: boolean;
  auto_daily_hour: number;
  vectorization_enabled: boolean;
  vectorization_enabled_at?: string | null;
  onboarded?: boolean;
  scan?: Record<string, unknown>;
};

export const api = {
  health: () => request<Health>("/health"),
  licenseStatus: () => request<RuntimeLicenseStatus>("/license/status"),
  workspaceStatus: () =>
    request<{
      ok: boolean;
      state: string;
      workspace: Record<string, unknown>;
      note?: string;
    }>("/workspace/status"),
  workspaceProbe: () =>
    request<{ ok: boolean; state: string; workspace: Record<string, unknown> }>("/workspace/probe", {
      method: "POST",
      body: "{}",
    }),
  workspaceReconnect: () =>
    request<{
      ok: boolean;
      state: string;
      bound?: boolean;
      services_started?: boolean;
      workspace: Record<string, unknown>;
      hint?: string;
    }>("/workspace/reconnect", { method: "POST", body: "{}" }),
  ollamaHealth: () =>
    request<{
      reachable: boolean;
      install_url: string;
      embed_model: string;
      vision_model: string;
      embed_ready: boolean;
      vision_ready: boolean;
      models: string[];
      ready: boolean;
      message: string;
      pull_commands: string[];
      pull?: Record<string, unknown>;
      host?: Record<string, unknown>;
      recommended?: {
        tier?: string;
        embed_model?: string;
        vision_model?: string;
        vision_label?: string;
        reason?: string;
        pull_order?: string[];
      };
      setup_steps?: Array<{ id: string; title: string; ok: boolean; detail: string }>;
      inference_available?: boolean;
      circuit_open?: boolean;
      status_layers?: {
        service_reachable?: boolean;
        model_present?: boolean;
        inference_available?: boolean;
        circuit_open?: boolean;
      };
      circuit?: {
        state?: string;
        consecutive_failures?: number;
        open_remaining_sec?: number;
        last_error?: string;
        last_error_kind?: string;
      };
    }>("/health/ollama"),
  setupStatus: () =>
    request<{
      host: Record<string, unknown>;
      recommended: Record<string, unknown>;
      ollama: Record<string, unknown>;
      setup_steps: Array<{ id: string; title: string; ok: boolean; detail: string }>;
      ready_for_vectorization: boolean;
      install_url?: string;
    }>("/setup/status"),
  installPlan: (options?: { includeVision?: boolean; includeNarration?: boolean }) => {
    const params = new URLSearchParams();
    if (options?.includeVision != null) {
      params.set("include_vision", String(options.includeVision));
    }
    if (options?.includeNarration != null) {
      params.set("include_narration", String(options.includeNarration));
    }
    const query = params.toString();
    return request<InstallPlan>(`/setup/install-plan${query ? `?${query}` : ""}`);
  },
  approveInstallPlan: (body: {
    plan_id: string;
    include_vision?: boolean;
    include_narration?: boolean;
  }) =>
    request<InstallPlan>("/setup/install-plan", { method: "POST", body: JSON.stringify(body) }),
  applyInstallPlan: (body: { plan_id: string }) =>
    request<{
      ok: boolean;
      plan: InstallPlan;
      settings: { ok: boolean; applied: Record<string, unknown> };
      pull: { ok: boolean; message: string; models?: string[]; pull?: Record<string, unknown> };
    }>("/setup/install-plan/apply", { method: "POST", body: JSON.stringify(body) }),
  installState: () =>
    request<{ plan?: InstallPlan | null; receipt?: Record<string, unknown> | null }>(
      "/setup/install-state",
    ),
  ollamaPull: (model: string) =>
    request<{ ok: boolean; message: string; pull?: Record<string, unknown> }>(
      `/ollama/pull?model=${encodeURIComponent(model)}`,
      { method: "POST" },
    ),
  ollamaPullRecommended: () =>
    request<{
      ok: boolean;
      message: string;
      models?: string[];
      tier?: string;
      reason?: string;
      pull?: Record<string, unknown>;
    }>("/ollama/pull-recommended", { method: "POST" }),
  getSettings: () => request<Record<string, unknown>>("/settings"),
  updateSettings: (body: Record<string, unknown>) =>
    request("/settings", { method: "PUT", body: JSON.stringify(body) }),
  getTtsVoice: () =>
    request<{
      ok: boolean;
      customer: string;
      customer_id?: number;
      settings_provider: string;
      lock_provider: string | null;
      effective_provider: string;
      voice_pack: string;
      edge_voice?: string;
      tts_voice?: string;
      label: string;
      clone_speed: number;
      chars_per_sec_zh: number;
      clone_available: boolean;
      packs: Array<{
        id: string;
        label: string;
        chars_per_sec_zh: number;
        speed: number;
        engine: string;
        custom?: boolean;
        readonly?: boolean;
        confirmed?: boolean;
      }>;
      hint: string;
    }>("/voice/tts"),
  getEdgeVoices: (opts?: { lang?: string; locale?: string; all_locales?: boolean; refresh?: boolean }) => {
    const q = new URLSearchParams();
    if (opts?.lang) q.set("lang", opts.lang);
    if (opts?.locale) q.set("locale", opts.locale);
    if (opts?.all_locales) q.set("all_locales", "true");
    if (opts?.refresh) q.set("refresh", "true");
    const qs = q.toString();
    return request<{
      ok: boolean;
      count: number;
      total: number;
      all_locales: boolean;
      lang?: string | null;
      locale?: string | null;
      source: string;
      voices: Array<{
        id: string;
        locale: string;
        gender: string;
        label: string;
        friendly?: string;
      }>;
    }>(`/voice/edge-voices${qs ? `?${qs}` : ""}`);
  },
  updateTtsVoice: (body: {
    provider: string;
    voice_pack?: string;
    voice?: string;
    clone_speed?: number;
    update_video_lock?: boolean;
  }) =>
    request<Record<string, unknown>>("/voice/tts", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  importVoicePack: (body: {
    source_path: string;
    label: string;
    ref_text: string;
    pack_id?: string;
    speed?: number;
    confirm_authorized: boolean;
  }) =>
    request<{ ok: boolean; pack: Record<string, unknown>; packs: Array<Record<string, unknown>> }>(
      "/voice/packs/import",
      { method: "POST", body: JSON.stringify(body) },
    ),
  patchVoicePack: (
    packId: string,
    body: { label?: string; ref_text?: string; speed?: number },
  ) =>
    request<{ ok: boolean; pack: Record<string, unknown>; packs: Array<Record<string, unknown>> }>(
      `/voice/packs/${encodeURIComponent(packId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  deleteVoicePack: (packId: string) =>
    request<{ ok: boolean; packs: Array<Record<string, unknown>> }>(
      `/voice/packs/${encodeURIComponent(packId)}`,
      { method: "DELETE" },
    ),
  confirmVoicePack: (packId: string) =>
    request<{ ok: boolean; pack: Record<string, unknown>; packs: Array<Record<string, unknown>> }>(
      `/voice/packs/${encodeURIComponent(packId)}/confirm`,
      { method: "POST", body: "{}" },
    ),
  previewVoicePack: (body: { text?: string; voice_pack?: string; speed?: number }) =>
    request<{
      ok: boolean;
      audio_path?: string;
      pack?: string;
      duration_sec?: number;
      playing?: boolean;
    }>(
      "/voice/packs/preview",
      { method: "POST", body: JSON.stringify(body) },
    ),
  getSystemPauseState: () =>
    request<{
      state: string;
      generation: number;
      pause_reasons: string[];
      resume_blockers: string[];
      owned_units?: Record<string, unknown>;
      last_event_kind?: string | null;
      last_error?: string | null;
      is_paused?: boolean;
      accepts_new_work?: boolean;
      policy?: Record<string, unknown>;
    }>("/system/pause-state"),
  getSystemEventControl: () => request<Record<string, unknown>>("/system/event-control"),
  updateSystemEventControl: (body: Record<string, unknown>) =>
    request<Record<string, unknown>>("/system/event-control", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  manualSystemPause: (reason = "manual") =>
    request("/system/pause", { method: "POST", body: JSON.stringify({ reason }) }),
  manualSystemResume: (reasons?: string[]) =>
    request("/system/resume", {
      method: "POST",
      body: JSON.stringify(reasons ? { reasons } : {}),
    }),
  listSystemEvents: (limit = 50) =>
    request<{ events: Array<Record<string, unknown>> }>(`/system/events?limit=${limit}`),
  listAssets: () => request<Array<Record<string, unknown>>>("/assets"),
  scanAssets: (limit = 0) =>
    request<{ ingested?: number; status?: string }>(`/assets/scan?limit=${limit}&background=true`, {
      method: "POST",
    }),
  scanStatus: () => request<Record<string, unknown>>("/assets/scan/status"),
  reconcileAssets: (limit = 50, mode: "incremental" | "rebuild" = "incremental") =>
    request<Record<string, unknown>>(
      `/assets/reconcile?limit=${limit}&mode=${encodeURIComponent(mode)}`,
      { method: "POST" },
    ),
  vectorizationStatus: () => request<Record<string, unknown>>("/assets/vectorization-status"),
  getVectorizationStatus: (orientation: "portrait" | "landscape" = "portrait") =>
    request<import("../types").VectorizationStatus>(
      `/vectorization/status?orientation=${orientation}`,
    ),
  getOrientationLock: () =>
    request<{
      lock: string;
      lock_version: number;
      hard: boolean;
      integrity_ok: boolean;
      ready_total: number;
      ready_audited_ok: number;
      ready_unverified: number;
      rejected_orientation: number;
      ready_by_orientation: Record<string, number>;
      message: string;
      hard_ui?: boolean;
      customer_name?: string;
    }>("/vectorization/orientation-lock"),
  createVectorizationRun: (body: {
    mode: "count" | "all";
    count?: number;
    orientation?: "portrait" | "landscape";
  }) =>
    request<import("../types").VectorizationStatus>("/vectorization/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  pauseVectorization: () =>
    request<import("../types").VectorizationStatus>("/vectorization/pause", {
      method: "POST",
      body: "{}",
    }),
  resumeVectorization: () =>
    request<import("../types").VectorizationStatus>("/vectorization/resume", {
      method: "POST",
      body: "{}",
    }),
  disableVectorization: () =>
    request<import("../types").VectorizationStatus>("/vectorization/disable", {
      method: "POST",
      body: "{}",
    }),
  updateVectorizationSchedule: (body: {
    schedule_enabled?: boolean;
    schedule_start?: string;
    schedule_end?: string;
    auto_stop_when_done?: boolean;
  }) =>
    request<import("../types").VectorizationStatus>("/vectorization/schedule", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  captionsStatus: () =>
    request<import("../types").SemanticBackfillStatus>("/index/captions/status"),
  verifyCliplets: (clipletIds: number[]) =>
    request<Record<string, unknown>>("/index/captions/verify", {
      method: "POST",
      body: JSON.stringify({ cliplet_ids: clipletIds.slice(0, 10) }),
    }),
  enableVectorization: async (
    kickReconcile = true,
    limit = 50,
  ): Promise<{
    enabled: boolean;
    mode?: string;
    vectorization_mode?: string;
    message: string;
    gaps?: Record<string, unknown>;
    reconcile?: Record<string, unknown> | null;
    reconcile_started?: boolean;
  }> => {
    type EnableResult = {
      enabled: boolean;
      mode?: string;
      vectorization_mode?: string;
      message: string;
      gaps?: Record<string, unknown>;
      reconcile?: Record<string, unknown> | null;
      reconcile_started?: boolean;
    };
    try {
      return await request<EnableResult>(
        `/vectorization/enable?kick_reconcile=${kickReconcile ? "true" : "false"}&limit=${limit}`,
        { method: "POST" },
      );
    } catch (e) {
      const msg = String(e);
      // Old engine without /vectorization/enable — fall back to settings + incremental reconcile
      if (!/Not Found|404/i.test(msg)) throw e;
      try {
        return await request<EnableResult>(
          `/vectorization/enable-switch?kick_reconcile=${kickReconcile ? "true" : "false"}&limit=${limit}`,
          { method: "POST" },
        );
      } catch (e2) {
        if (!/Not Found|404/i.test(String(e2))) throw e2;
      }
      await request("/settings", {
        method: "PUT",
        body: JSON.stringify({
          vectorization_enabled: true,
          vectorization_mode: "incremental",
        }),
      });
      let gaps: Record<string, unknown> | undefined;
      let reconcile: Record<string, unknown> | null = null;
      try {
        gaps = await request("/assets/vectorization-status");
      } catch {
        gaps = undefined;
      }
      if (kickReconcile) {
        try {
          reconcile = await request(
            `/assets/reconcile?limit=${limit}&mode=incremental`,
            { method: "POST" },
          );
        } catch {
          // reconcile may also be gated until settings reload; ignore soft fail
        }
      }
      const legacy: EnableResult = {
        enabled: true,
        mode: "incremental",
        vectorization_mode: "incremental",
        message:
          "已开启增量向量化（兼容旧引擎接口）。已有向量保留，仅补齐缺口。",
        gaps,
        reconcile,
      };
      return legacy;
    }
  },
  setIncrementalVectorization: async (on: boolean, kickReconcile = true) => {
    if (on) {
      return api.enableVectorization(kickReconcile, 50);
    }
    return api.disableVectorization();
  },

  listJobs: (limit = 80) =>
    request<Array<Record<string, unknown>>>(`/jobs?limit=${Math.max(1, Math.min(limit, 200))}`),
  getJob: (id: number) => request<Record<string, unknown>>(`/jobs/${id}`),
  jobsPipeline: () =>
    request<{
      ok: boolean;
      running: Record<string, unknown> | null;
      queued_count: number;
      queued_ids: number[];
      recent_events: Array<Record<string, unknown>>;
      worker_running: boolean;
      worker_pid?: number;
      held_job_id?: number | null;
      resource_gate?: Record<string, unknown>;
      clone_runtime?: Record<string, unknown>;
      ollama_narration_model?: string;
      ollama_narration_enabled?: boolean;
      ollama_circuit?: Record<string, unknown>;
      chat_probe_ok?: boolean;
    }>("/jobs/pipeline"),
  runtimeHealth: () =>
    request<{
      ok: boolean;
      accepts_new_work?: boolean;
      pause?: Record<string, unknown>;
      health?: { health_hits_last_60s?: number; health_qps_approx?: number };
      resource_gate?: {
        pools?: Record<string, { used?: number; capacity?: number; holders?: string[] }>;
        pause_active?: boolean;
        pause_reasons?: string[];
      };
      worker_running?: boolean;
      scheduler_running?: boolean;
      watcher_running?: boolean;
    }>("/ops/runtime-health"),
  resourceGate: () =>
    request<{
      ok: boolean;
      pools?: Record<string, { used?: number; capacity?: number; holders?: string[] }>;
    }>("/ops/resource-gate"),
  reapStaleJobs: (staleAfterSec = 600) =>
    request<{ ok: boolean; reaped: Array<Record<string, unknown>>; count: number }>(
      `/jobs/reap-stale?stale_after_sec=${staleAfterSec}`,
      { method: "POST" },
    ),
  createJob: (body: Record<string, unknown>) =>
    request<{
      id: number;
      status: string;
      customer_id?: number;
      rush?: boolean;
      queued_ahead?: number;
      running_job_id?: number | null;
      message?: string;
    }>("/jobs", { method: "POST", body: JSON.stringify(body) }),
  pauseJob: (id: number) => request(`/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: number) => request(`/jobs/${id}/resume`, { method: "POST" }),
  dryRun: (body: Record<string, unknown>) =>
    request<Record<string, unknown>>("/dry-run", { method: "POST", body: JSON.stringify(body) }),
  listOutputs: (state?: string, opts?: { missing_voice?: boolean; missing_subtitle?: boolean; noncompliant_tts?: boolean }) => {
    const q = new URLSearchParams();
    if (state) q.set("state", state);
    if (opts?.missing_voice) q.set("missing_voice", "true");
    if (opts?.missing_subtitle) q.set("missing_subtitle", "true");
    if (opts?.noncompliant_tts) q.set("noncompliant_tts", "true");
    const qs = q.toString();
    return request<Array<Record<string, unknown>>>(qs ? `/outputs?${qs}` : "/outputs");
  },
  batchRerender: (body?: {
    output_ids?: number[];
    missing_voice?: boolean;
    missing_subtitle?: boolean;
    noncompliant_tts?: boolean;
    limit?: number;
    reason?: string;
  }) =>
    request<{
      ok: boolean;
      queued: Array<{ output_id: number; job_id: number; status: string }>;
      errors: Array<{ output_id: number; error: string }>;
      count: number;
      limit: number;
      reason?: string;
    }>("/outputs/batch-rerender", {
      method: "POST",
      body: JSON.stringify(body || { missing_voice: true, limit: 20 }),
    }),
  reportOps: () =>
    request<{
      ok: boolean;
      generated_at: string;
      timezone: "Asia/Shanghai";
      business_date: string;
      source: "database";
      customer_id: number;
      ready_available: number;
      production_passed: number;
      failed: number;
      quality_pass_rate: number | null;
      failure_rate: number | null;
      auto_approved: number;
      auto_rejected: number;
      uncertain_open: number;
      manual_decided: number;
      published: number;
      retired: number;
      production_passed_today: number;
      published_today: number;
      reconciliation: ReportSummary["reconciliation"];
      health_line: string;
      quota?: Record<string, unknown>;
      library_ok?: boolean;
      output_ok?: boolean;
      db_ok?: boolean;
      db_health?: Record<string, unknown>;
    }>("/reports/ops"),
  carrierStatus: () =>
    request<{
      ok: boolean;
      carrier_visible: boolean;
      primary_root?: string | null;
      latest?: { version?: string; force?: boolean; notes?: string } | null;
      backup_count?: number;
      bound_nas_ok?: boolean | null;
      whitelist?: string[];
    }>("/ops/carrier"),
  keywordStats: () =>
    request<{
      ok: boolean;
      total: number;
      themes: Record<string, number>;
      theme_count: number;
      cooldown_records: number;
      version?: number | string | null;
      empty: boolean;
      pack_path_exists?: boolean;
    }>("/ops/keyword-stats"),
  appUpdateCheck: () =>
    request<{
      ok: boolean;
      update_available: boolean;
      force?: boolean;
      current_version: string;
      remote_version?: string;
      notes?: string;
    }>("/ops/app-update"),
  appUpdateInstall: (apply = false) =>
    request<Record<string, unknown>>("/ops/app-update/install", {
      method: "POST",
      body: JSON.stringify({ apply }),
    }),
  carrierEnsure: () =>
    request<{ ok: boolean; root: string; seed?: Record<string, unknown> }>("/ops/carrier/ensure", {
      method: "POST",
    }),
  carrierRestore: (backup_path: string) =>
    request<{ ok: boolean; path?: string; error?: string; keys?: string[] }>("/ops/carrier/restore", {
      method: "POST",
      body: JSON.stringify({ backup_path }),
    }),
  carrierInstallUpdateAgent: () =>
    request<{ ok: boolean; stdout?: string; stderr?: string }>("/ops/carrier/install-update-agent", {
      method: "POST",
    }),
  ollamaNarrationPreview: (params?: { theme?: string; brand?: string; hint?: string }) => {
    const q = new URLSearchParams();
    if (params?.theme) q.set("theme", params.theme);
    if (params?.brand) q.set("brand", params.brand);
    if (params?.hint) q.set("hint", params.hint);
    const qs = q.toString();
    return request<{
      ok?: boolean;
      enabled?: boolean;
      model?: string;
      script?: string;
      emoji_cues?: Array<Record<string, unknown>>;
      error?: string;
    }>(`/ops/ollama-narration/preview${qs ? `?${qs}` : ""}`, { method: "POST" });
  },
  carrierBackup: () =>
    request<{ ok: boolean; path: string }>("/ops/carrier/backup", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  listEvents: () => request<Array<Record<string, unknown>>>("/logs/events"),
  operationLogs: (params?: {
    category?: string;
    level?: string;
    search?: string;
    page?: number;
    page_size?: number;
  }) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params || {})) {
      if (value != null && value !== "" && value !== "all") query.set(key, String(value));
    }
    return request<{
      ok: boolean;
      items: Array<Record<string, unknown>>;
      legacy_items: Array<Record<string, unknown>>;
      page: number;
      page_size: number;
      total: number;
    }>(`/operation-logs${query.size ? `?${query.toString()}` : ""}`);
  },
  reportSummary: () => request<ReportSummary>("/reports/summary"),
  reviewOutput: (id: number, status: string, note = "", opts?: { reason?: string; rerender?: boolean }) => {
    const q = new URLSearchParams({
      status,
      note,
      reason: opts?.reason || "",
      rerender: opts?.rerender ? "true" : "false",
    });
    return request(`/review/${id}?${q.toString()}`, { method: "POST" });
  },
  rerenderOutput: (id: number, reason = "") =>
    request<{ output_id: number; reason: string; job: { id: number; status: string } }>(
      `/review/${id}/rerender?reason=${encodeURIComponent(reason)}`,
      { method: "POST" },
    ),
  listReviewReasons: () =>
    request<{ reasons: Array<{ code: string; label: string }> }>("/review/reasons"),
  reviewAutoApproveBackfill: (limit = 50) =>
    request<{
      ok: boolean;
      approved?: number;
      uncertain?: number;
      rejected?: number;
      skipped?: number | string;
      items?: Array<{ output_id: number; review_id?: number }>;
      errors?: Array<{ output_id: number; error: string }>;
      limit?: number;
    }>(`/review/auto-approve/backfill?limit=${limit}`, { method: "POST" }),
  reviewReconcileGate: (opts?: {
    limit?: number;
    archiveMissing?: boolean;
    archiveGateFail?: boolean;
  }) => {
    const q = new URLSearchParams({
      limit: String(opts?.limit ?? 50),
      archive_missing: opts?.archiveMissing === false ? "false" : "true",
      archive_gate_fail: opts?.archiveGateFail ? "true" : "false",
    });
    return request<{
      ok: boolean;
      processed?: number;
      approved?: number;
      archived_missing?: number;
      archived_gate_fail?: number;
      gate_failed?: number;
      already_gate_ok?: number;
      skipped?: number;
      items?: Array<Record<string, unknown>>;
      errors?: Array<{ output_id: number; error: string }>;
      limit?: number;
    }>(`/review/reconcile-gate?${q.toString()}`, { method: "POST" });
  },
  reviewReconcileGateOne: (id: number) =>
    request<{
      ok: boolean;
      action?: string;
      decision?: string;
      ready_gate_ok?: boolean;
      ready_gate_fails?: string[];
      error?: string;
      note?: string;
    }>(`/review/${id}/reconcile-gate`, { method: "POST" }),
  reviewArchiveUnusable: (id: number, reason: "manual" | "missing" | "gate_fail" = "manual") =>
    request<{
      ok: boolean;
      action?: string;
      decision?: string;
      note?: string;
      reason?: string;
    }>(`/review/${id}/archive-unusable?reason=${encodeURIComponent(reason)}`, {
      method: "POST",
    }),
  reviewBatchApprove: (opts?: { skipKnownIssues?: boolean; limit?: number }) => {
    const q = new URLSearchParams({
      skip_known_issues: opts?.skipKnownIssues === false ? "false" : "true",
      limit: String(opts?.limit ?? 50),
    });
    return request<{
      ok: boolean;
      approved: number;
      skipped_known: number;
      skipped_gate: number;
      skipped_media: number;
      items: Array<{
        output_id: number;
        review_id?: number;
        decision?: string;
        pack_ok?: boolean;
        pack_error?: string;
      }>;
      errors: Array<{ output_id: number; error: string }>;
      limit: number;
      skip_known_issues: boolean;
    }>(`/review/batch-approve?${q.toString()}`, { method: "POST" });
  },
  listReviews: (scope: "history" | "open" | "current" = "history", limit = 100) =>
    request<Array<Record<string, unknown>>>(`/reviews?scope=${scope}&limit=${limit}`),
  listExpressionLanguages: () =>
    request<{
      languages: Array<{
        code: string;
        label_zh: string;
        label_native: string;
        region: string;
        rtl?: boolean;
        edge_voice?: string | null;
      }>;
      regions: Record<
        string,
        Array<{
          code: string;
          label_zh: string;
          label_native: string;
          region: string;
          rtl?: boolean;
          edge_voice?: string | null;
        }>
      >;
    }>("/expression/languages"),
  exportPublishPack: (
    id: number,
    opts?: {
      voice_lang?: string;
      subtitle_lang?: string;
      subtitle_burn?: string;
      dual_secondary_lang?: string;
    },
  ) => {
    const q = new URLSearchParams();
    if (opts?.voice_lang) q.set("voice_lang", opts.voice_lang);
    if (opts?.subtitle_lang) q.set("subtitle_lang", opts.subtitle_lang);
    if (opts?.subtitle_burn) q.set("subtitle_burn", opts.subtitle_burn);
    if (opts?.dual_secondary_lang) q.set("dual_secondary_lang", opts.dual_secondary_lang);
    const qs = q.toString();
    return request<{ ok: boolean; output_id: number; manifest: Record<string, unknown> }>(
      `/outputs/${id}/publish-pack${qs ? `?${qs}` : ""}`,
      { method: "POST" },
    );
  },
  previewNarration: (body: {
    script: string;
    lang?: string;
    provider?: string;
    voice?: string;
    template_name?: string;
  }) =>
    request<{
      ok: boolean;
      provider: string;
      total_duration_sec: number;
      duration_budget: number[];
      segments: Array<Record<string, unknown>>;
      template: Record<string, unknown>;
    }>("/pack/narration/preview", { method: "POST", body: JSON.stringify(body) }),
  outputPublishCard: (id: number) =>
    request<Record<string, unknown>>(`/outputs/${id}/publish-card`),
  reachList: (status?: string) =>
    request<{ ok: boolean; items: Array<Record<string, unknown>>; auto_publish: boolean }>(
      `/reach/queue${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  reachFromPack: (body: {
    pack_dir: string;
    platforms?: string[];
    locale?: string;
    mark_ready?: boolean;
  }) =>
    request<{ ok: boolean; count: number; items: Array<Record<string, unknown>>; auto_publish: boolean }>(
      "/reach/queue/from-pack",
      { method: "POST", body: JSON.stringify(body) },
    ),
  reachSetStatus: (id: number, status: string, note?: string) =>
    request<{ ok: boolean; item: Record<string, unknown> }>(`/reach/queue/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status, note }),
    }),
  reachOpen: (id: number, dryRun = false, chromeProfile?: string) =>
    request<{
      ok: boolean;
      paste_card?: string;
      open?: Record<string, unknown>;
      disclaimer?: string;
      auto_publish: boolean;
    }>(`/reach/queue/${id}/open`, {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun, chrome_profile: chromeProfile || null }),
    }),
  reachChromeProfiles: () =>
    request<{
      ok: boolean;
      root: string;
      selected: string | null;
      selected_platform?: string | null;
      profiles: ChromeProfile[];
      legacy_profiles?: ChromeProfile[];
      platforms?: Array<{ id: string; label: string; url: string; short?: string }>;
      chrome_installed?: boolean;
      note?: string;
    }>("/reach/chrome-profiles"),
  reachChromeCreate: (body: { platform: string; count?: number; name_prefix?: string }) =>
    request<{
      ok: boolean;
      platform: string;
      label?: string;
      count: number;
      selected: string | null;
      created: Array<{ name: string; platform: string; path: string }>;
      note?: string;
    }>("/reach/chrome-profiles/create", { method: "POST", body: JSON.stringify(body) }),
  reachChromeOpen: (name: string, platform?: string | null, dryRun = false) =>
    request<{
      ok: boolean;
      platform?: string;
      disclaimer?: string;
      open?: Record<string, unknown>;
      auto_publish: boolean;
    }>("/reach/chrome-profiles/open", {
      method: "POST",
      body: JSON.stringify({ name, platform: platform || null, dry_run: dryRun }),
    }),
  reachChromeSelect: (name: string, platform?: string | null) =>
    request<{
      ok: boolean;
      selected: string;
      platform?: string;
      profiles?: Array<{ name: string; path: string; platform?: string | null }>;
    }>("/reach/chrome-profiles/select", {
      method: "POST",
      body: JSON.stringify({ name, platform: platform || null }),
    }),
  reachChromeRename: (oldName: string, newName: string) =>
    request<{
      ok: boolean;
      old_name: string;
      name: string;
      path: string;
      platform?: string | null;
    }>("/reach/chrome-profiles/rename", {
      method: "POST",
      body: JSON.stringify({ old_name: oldName, new_name: newName }),
    }),
  reachChromeDelete: (names: string[]) =>
    request<{
      ok: boolean;
      deleted: string[];
      message_bindings_disabled: number;
      cleanup_pending?: string[];
    }>("/reach/chrome-profiles", {
      method: "DELETE",
      body: JSON.stringify({ names }),
    }),
  reachPublishImmediatePreview: (body: {
    accounts: Array<{ platform: string; chrome_profile: string }>;
    total_count: number;
    allocation_mode: "auto_even" | "manual";
    manual_counts: Record<string, number>;
    content_mode: "random_unique" | "selected_cycle" | "single";
    output_ids: number[];
    seed?: number;
    accept_risk?: boolean;
    allow_published?: boolean;
  }) =>
    request<{
      ok: boolean;
      seed: number;
      candidate_count: number;
      assignments: Array<Record<string, unknown>>;
      skipped_accounts: Array<{ platform: string; chrome_profile: string }>;
      skipped_occurrences: Array<Record<string, unknown>>;
      requested_count: number;
      actual_count: number;
      allow_published?: boolean;
    }>("/reach/publish/immediate/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  reachPublishImmediateStart: (body: {
    accounts: Array<{ platform: string; chrome_profile: string }>;
    total_count: number;
    allocation_mode: "auto_even" | "manual";
    manual_counts: Record<string, number>;
    content_mode: "random_unique" | "selected_cycle" | "single";
    output_ids: number[];
    seed?: number;
    accept_risk: true;
    allow_published?: boolean;
  }) =>
    request<Record<string, unknown>>("/reach/publish/immediate/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  reachPublishRunStatus: (runId: string) =>
    request<PublishRun>(`/reach/publish/runs/${encodeURIComponent(runId)}?compact=true`),
  reachPublishActiveStatus: () =>
    request<Partial<PublishRun> & { ok: boolean; active?: boolean }>(
      "/reach/publish/runs/active/status",
    ),
  reachPublishCancelRun: (runId: string) =>
    request<Record<string, unknown>>(`/reach/publish/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
    }),
  reachPublishConfirmOutcome: (
    runId: string,
    body: {
      item_id: number;
      outcome: "published" | "not_published" | "failed" | "skip";
      note?: string;
      retry_mode?: "manual" | "auto" | "none";
    },
  ) =>
    request<Record<string, unknown>>(
      `/reach/publish/runs/${encodeURIComponent(runId)}/confirm-outcome`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  reachPublishSkipCurrent: (
    runId: string,
    body: { item_id: number; retry_mode: "manual" | "auto" | "none" },
  ) =>
    request<Record<string, unknown>>(
      `/reach/publish/runs/${encodeURIComponent(runId)}/skip-current`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  reachPublishDeferred: () =>
    request<{ ok: boolean; items: PublishRunItem[] }>("/reach/publish/deferred"),
  reachPublishRetryDeferred: (itemIds: number[]) =>
    request<PublishRun>("/reach/publish/deferred/retry", {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds }),
    }),
  reachPublishMakeup: () =>
    request<{
      ok: boolean;
      deferred: PublishRunItem[];
      triggers: Array<{
        id: number;
        schedule_id: number | null;
        status: string;
        planned_at: string | null;
        local_date: string | null;
        task_name: string;
        chrome_profile: string;
        platform: string;
        note?: string | null;
      }>;
      counts: {
        deferred: number;
        triggers: number;
        missed_human_confirm: number;
        makeup_pending: number;
      };
    }>("/reach/publish/makeup"),
  reachPublishMakeupRetry: (itemIds: number[]) =>
    request<PublishRun & { ok?: boolean }>("/reach/publish/makeup/retry", {
      method: "POST",
      body: JSON.stringify({ item_ids: itemIds }),
    }),
  reachPublishTasks: () =>
    request<{
      ok: boolean;
      tasks: Array<{
        id: string;
        name: string;
        enabled: boolean;
        auto_production: boolean;
        targets: Array<{
          schedule_id: number;
          platform: string;
          chrome_profile: string;
          publish_count: number;
        }>;
      }>;
    }>("/reach/publish/tasks"),
  reachPublishTaskCreate: (body: {
    name: string;
    timezone: string;
    targets: Array<{
      platform: string;
      chrome_profile: string;
      publish_count: number;
    }>;
    windows: Array<{ start: string; end: string }>;
    weekdays: number[];
    auto_production: boolean;
    min_gap_minutes: number;
    accept_risk: true;
  }) =>
    request<Record<string, unknown>>("/reach/publish/tasks", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  reachPublishTaskUpdate: (
    taskId: string,
    body: { enabled?: boolean; name?: string },
  ) =>
    request<Record<string, unknown>>(
      `/reach/publish/tasks/${encodeURIComponent(taskId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  reachPublishTaskDelete: (taskId: string) =>
    request<Record<string, unknown>>(
      `/reach/publish/tasks/${encodeURIComponent(taskId)}`,
      { method: "DELETE" },
    ),
  reachPublishSchedulePreview: (scheduleId: number) =>
    request<{
      ok: boolean;
      schedule: PublishSchedule;
      next_trigger_at?: string | null;
      triggers: PublishTrigger[];
    }>(`/reach/publish/schedules/${scheduleId}/preview`),
  humanAlerts: () =>
    request<{
      ok: boolean;
      alerts: HumanAlert[];
      notification_preflight: {
        triple_channel_ready: boolean;
        app: { available: boolean };
        macos: { available: boolean; permission: string };
        ntfy: { available: boolean; configured: boolean; enabled: boolean };
      };
    }>("/reach/publish/human-alerts"),
  acknowledgeHumanAlert: (alertId: number) =>
    request<{ ok: boolean; alert: HumanAlert }>(
      `/reach/publish/human-alerts/${alertId}/acknowledge`,
      { method: "POST" },
    ),
  coverTemplatesList: () =>
    request<{
      ok: boolean;
      selected_id: string | null;
      slot_counts: Record<string, number>;
      slot_specs?: Record<
        string,
        Array<{
          index: number;
          id?: string;
          label?: string;
          aspect?: string;
          width?: number;
          height?: number;
          max_mb?: number;
          role?: string;
        }>
      >;
      templates: Array<Record<string, unknown>>;
      root?: string;
    }>("/reach/cover-templates"),
  coverTemplateCreate: (name: string) =>
    request<{ ok: boolean; template: Record<string, unknown> }>("/reach/cover-templates", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  coverTemplateDelete: (templateId: string) =>
    request<{ ok: boolean; templates?: Array<Record<string, unknown>> }>(
      `/reach/cover-templates/${encodeURIComponent(templateId)}`,
      { method: "DELETE" },
    ),
  coverTemplateSelect: (templateId: string) =>
    request<{
      ok: boolean;
      selected_id?: string | null;
      templates?: Array<Record<string, unknown>>;
    }>("/reach/cover-templates/select", {
      method: "POST",
      body: JSON.stringify({ template_id: templateId }),
    }),
  coverTemplateSetSlot: (templateId: string, platform: string, slotIndex: number, sourcePath: string) =>
    request<{ ok: boolean; template: Record<string, unknown> }>(
      `/reach/cover-templates/${encodeURIComponent(templateId)}/slot`,
      {
        method: "POST",
        body: JSON.stringify({ platform, slot_index: slotIndex, source_path: sourcePath }),
      },
    ),
  coverTemplateSeed: (templateId: string, packDir: string) =>
    request<{ ok: boolean; template: Record<string, unknown> }>(
      `/reach/cover-templates/${encodeURIComponent(templateId)}/seed`,
      { method: "POST", body: JSON.stringify({ pack_dir: packDir }) },
    ),
  coverTemplatesResolve: (platform: string, packDir?: string, templateId?: string) =>
    request<{
      ok: boolean;
      resolve: Record<string, unknown>;
      gate?: Record<string, unknown> | null;
    }>("/reach/cover-templates/resolve", {
      method: "POST",
      body: JSON.stringify({
        platform,
        pack_dir: packDir || null,
        template_id: templateId || null,
      }),
    }),
  reachInbox: () =>
    request<{
      ok: boolean;
      unread_count: number;
      notices: Array<Record<string, unknown>>;
      auto_reply: boolean;
    }>("/reach/inbox"),
  reachMessageAccounts: () =>
    request<{ ok: boolean; accounts: import("../types").ReachMessageAccount[] }>(
      "/reach/message-accounts",
    ),
  reachMessageAccountCreate: (body: {
    platform: string;
    profile_name?: string;
    display_name?: string;
    cooldown_sec?: number;
  }) =>
    request<{ ok: boolean; account: import("../types").ReachMessageAccount }>(
      "/reach/message-accounts",
      { method: "POST", body: JSON.stringify(body) },
    ),
  reachMessageAccountUpdate: (
    id: number,
    body: { enabled?: boolean; display_name?: string; cooldown_sec?: number },
  ) =>
    request<{ ok: boolean; account: import("../types").ReachMessageAccount }>(
      `/reach/message-accounts/${id}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  reachMessageAccountsDelete: (ids: number[]) =>
    request<{ ok: boolean; deleted: Array<Record<string, unknown>>; count: number }>(
      "/reach/message-accounts",
      { method: "DELETE", body: JSON.stringify({ ids }) },
    ),
  reachMessageAccountOpen: (account: import("../types").ReachMessageAccount) =>
    request<{ ok: boolean; platform?: string; open?: Record<string, unknown> }>(
      `/reach/message-accounts/${account.id}/open`,
      {
        method: "POST",
        body: JSON.stringify({ dry_run: false }),
      },
    ),
  reachMessages: (query: import("../types").MessageListQuery = { unread: true, limit: 100 }) => {
    const params = new URLSearchParams({
      unread: String(query.unread),
      history: String(Boolean(query.history)),
      limit: String(query.limit),
    });
    if (query.account_id != null) params.set("account_id", String(query.account_id));
    return (
    request<{
      ok: boolean;
      messages: import("../types").ReachMessage[];
      unread_count: number;
    }>(`/reach/messages?${params.toString()}`)
    );
  },
  reachMessageScanStart: (accountId?: number, dryRun = false) =>
    request<{ ok: boolean; task_id: string; queued: number; dry_run: boolean }>(
      "/reach/messages/scan",
      {
        method: "POST",
        body: JSON.stringify({
          account_id: accountId || null,
          accept_risk: true,
          dry_run: dryRun,
        }),
      },
    ),
  reachMessageScanStatus: () =>
    request<{
      ok: boolean;
      worker: import("../types").ReachMessageScanStatus;
      scans: Array<Record<string, unknown>>;
    }>(
      "/reach/messages/status",
    ),
  reachMessageScanCancel: () =>
    request<{ ok: boolean; cancel_requested: boolean; discarded: number }>(
      "/reach/messages/cancel",
      { method: "POST" },
    ),
  reachMessageRead: (id: number) =>
    request<{ ok: boolean; message: import("../types").ReachMessage }>(
      `/reach/messages/${id}/read`,
      { method: "POST" },
    ),
  reachMessagesReadAll: (accountId?: number) =>
    request<{ ok: boolean; updated: number; unread_count?: number }>("/reach/messages/read-all", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId ?? null }),
    }),
  reachMessageClaimNotifications: () =>
    request<{
      ok: boolean;
      messages: import("../types").ReachMessage[];
    }>("/reach/notifications/claim", { method: "POST" }),
  reachNtfyConfig: () =>
    request<{
      ok: boolean;
      config: import("../types").ReachNtfyConfig;
    }>("/reach/notifications/ntfy"),
  reachNtfySave: (body: {
    enabled: boolean;
    server_url: string;
    topic: string;
    auth_mode: "none" | "token" | "basic";
    token?: string;
    username?: string;
    password?: string;
  }) =>
    request<{ ok: boolean; config: import("../types").ReachNtfyConfig }>(
      "/reach/notifications/ntfy",
      { method: "PUT", body: JSON.stringify(body) },
    ),
  reachNtfyTest: () =>
    request<{ ok: boolean; sent: boolean; test: true }>(
      "/reach/notifications/ntfy/test",
      { method: "POST" },
    ),
  reachNotificationReport: (
    messageId: number,
    channel: "app" | "macos",
    status: "sent" | "failed" | "permission_denied" | "skipped_in_app_only",
    detail = "",
  ) =>
    request<{ ok: boolean }>("/reach/notifications/report", {
      method: "POST",
      body: JSON.stringify({
        message_id: messageId,
        channel,
        status,
        detail,
      }),
    }),
  reachMessageOpen: (id: number) =>
    request<{ ok: boolean; opened: Record<string, unknown>; auto_reply: false }>(
      `/reach/messages/${id}/open`,
      { method: "POST", body: JSON.stringify({ dry_run: false }) },
    ),
  reachPlatforms: () =>
    request<{
      ok: boolean;
      platforms: Array<{
        id: string;
        label: string;
        url: string;
        short?: string;
        cover_slots?: number;
      }>;
      slot_specs?: Record<
        string,
        Array<{
          index: number;
          id?: string;
          label?: string;
          aspect?: string;
          width?: number;
          height?: number;
          max_mb?: number;
          role?: string;
        }>
      >;
    }>("/reach/platforms"),
  listCalendar: (fromDay?: string, toDay?: string) => {
    const q = new URLSearchParams();
    if (fromDay) q.set("from_day", fromDay);
    if (toDay) q.set("to_day", toDay);
    const qs = q.toString();
    return request<Array<Record<string, unknown>>>(`/calendar${qs ? `?${qs}` : ""}`);
  },
  calendarToday: () => request<Record<string, unknown>>("/calendar/today"),
  upsertCalendar: (day: string, body: Record<string, unknown>) =>
    request(`/calendar/${day}`, { method: "PUT", body: JSON.stringify({ ...body, day }) }),
  deleteCalendar: (day: string) => request(`/calendar/${day}`, { method: "DELETE" }),
  createJobFromCalendar: (day?: string) =>
    request<{ id: number; status: string; calendar_day: string; theme: string; quota: number }>(
      `/jobs/from-calendar${day ? `?day=${encodeURIComponent(day)}` : ""}`,
      { method: "POST" },
    ),
  diskReport: () => request<Record<string, unknown>>("/ops/disk"),
  cleanCache: (olderThanHours = 24) =>
    request<{ removed_files: number; freed_mb: number }>(
      `/ops/cache/clean?older_than_hours=${olderThanHours}`,
      { method: "POST" },
    ),
  diskCleanupReport: () =>
    request<{
      ok: boolean;
      policy: Record<string, unknown>;
      categories: Record<string, Record<string, unknown>>;
      published_eligible: {
        count: number;
        bytes: number;
        items: Array<Record<string, unknown>>;
      };
      allowlist_roots: Record<string, string>;
      forbidden: string[];
    }>("/ops/disk-cleanup/report"),
  diskCleanupPolicy: () =>
    request<{ ok: boolean; policy: Record<string, unknown> }>(
      "/ops/disk-cleanup/policy",
    ),
  diskCleanupPolicyUpdate: (token: string, body: Record<string, unknown>) =>
    request<{ ok: boolean; policy: Record<string, unknown> }>(
      "/ops/disk-cleanup/policy",
      {
        method: "PUT",
        headers: { "X-Suying-Ops-Token": token },
        body: JSON.stringify(body),
      },
    ),
  diskCleanupRun: (
    token: string,
    body: { tiers: string[]; confirm: boolean },
  ) =>
    request<Record<string, unknown>>("/ops/disk-cleanup/run", {
      method: "POST",
      headers: { "X-Suying-Ops-Token": token },
      body: JSON.stringify(body),
    }),
  backupStatus: () =>
    request<Record<string, unknown> & { policy?: Record<string, unknown> }>(
      "/ops/backups/status",
    ),
  backupList: () =>
    request<{ ok: boolean; backups: Array<Record<string, unknown>> }>("/ops/backups"),
  backupStart: (token: string) =>
    request<Record<string, unknown>>("/ops/backups", {
      method: "POST",
      headers: { "X-Suying-Ops-Token": token },
    }),
  backupPolicyUpdate: (token: string, body: Record<string, unknown>) =>
    request<{ ok: boolean; policy: Record<string, unknown> }>("/ops/backups/policy", {
      method: "PUT",
      headers: { "X-Suying-Ops-Token": token },
      body: JSON.stringify(body),
    }),
  backupPrune: (
    token: string,
    body: { dry_run: boolean; retention_count?: number; confirm?: boolean },
  ) =>
    request<Record<string, unknown>>("/ops/backups/prune", {
      method: "POST",
      headers: { "X-Suying-Ops-Token": token },
      body: JSON.stringify(body),
    }),
  publishedCleanupPolicy: () =>
    request<{ ok: boolean; policy: Record<string, unknown> }>(
      "/ops/published-cleanup/policy",
    ),
  publishedCleanupPolicyUpdate: (token: string, body: Record<string, unknown>) =>
    request<{ ok: boolean; policy: Record<string, unknown> }>(
      "/ops/published-cleanup/policy",
      {
        method: "PUT",
        headers: { "X-Suying-Ops-Token": token },
        body: JSON.stringify(body),
      },
    ),
  publishedCleanupPreview: (olderThanDays?: number) =>
    request<{
      ok: boolean;
      count: number;
      bytes: number;
      items: Array<Record<string, unknown>>;
    }>(
      `/ops/published-cleanup/preview${
        olderThanDays ? `?older_than_days=${olderThanDays}` : ""
      }`,
    ),
  publishedCleanupRun: (
    token: string,
    body: { output_ids?: number[]; older_than_days?: number; confirm: boolean },
  ) =>
    request<{
      ok: boolean;
      count: number;
      bytes: number;
      items: Array<Record<string, unknown>>;
    }>("/ops/published-cleanup", {
      method: "POST",
      headers: { "X-Suying-Ops-Token": token },
      body: JSON.stringify(body),
    }),
  schedulerStatus: () => request<Record<string, unknown>>("/ops/scheduler"),
  schedulerRunNow: (force = false) =>
    request(`/ops/scheduler/run-now?force=${force}`, { method: "POST" }),
  servicesStatus: () => request<ServicesStatus>("/ops/services"),
  serviceControl: (name: string, action: "start" | "stop") =>
    request<{ name: string; action: string; running: boolean }>(`/ops/services/${name}/${action}`, {
      method: "POST",
    }),
  exportEvents: () => request<{ path: string }>("/export/events.csv", { method: "POST" }),
  exportRenders: () => request<{ path: string }>("/export/renders.csv", { method: "POST" }),
  reloadKeywordsFromPath: (customerId?: number) =>
    request<Record<string, unknown>>(
      `/keywords/reload-from-path${customerId != null ? `?customer_id=${customerId}` : ""}`,
      { method: "POST" },
    ),
  keywordsActiveSummary: (customerId?: number) =>
    request<{
      customer: string;
      keyword_pack_path?: string | null;
      loaded: boolean;
      pack_id?: number;
      version?: number;
      revision?: number;
      sha256?: string;
      schema?: string;
      validation_report?: Record<string, unknown>;
      title_pool_count: number;
      title_pool_sample: string[];
      title_pool_meta?: Record<string, unknown>;
      hooks_count: number;
      max_chars_per_line?: number;
    }>(`/keywords/active-summary${customerId != null ? `?customer_id=${customerId}` : ""}`),
  installKeywordsFromPath: (customerName: string, path: string) =>
    request<{
      id: number;
      revision: number;
      sha256: string;
      path: string;
      idempotent: boolean;
    }>(
      `/keywords/import?customer_name=${encodeURIComponent(customerName)}&path=${encodeURIComponent(path)}`,
      { method: "POST" },
    ),
  keywordHistory: (customerId?: number) =>
    request<Array<{
      id: number;
      revision: number;
      sha256: string;
      schema: string;
      status: string;
      created_at?: string | null;
    }>>(`/keywords/history${customerId != null ? `?customer_id=${customerId}` : ""}`),
  importKeywordsFile: async (customerName: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(
      `${API_BASE}/keywords/import-file?customer_name=${encodeURIComponent(customerName)}`,
      { method: "POST", body: form },
    );
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  listCustomers: () => request<Customer[]>("/customers"),
  createCustomer: (body: {
    name: string;
    library_root: string;
    output_root: string;
    library_roots?: string[];
    keyword_pack_path?: string;
  }) => request<Customer>("/customers", { method: "POST", body: JSON.stringify(body) }),
  updateCustomer: (id: number, body: Record<string, unknown>) =>
    request<Customer>(`/customers/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  activateCustomer: (name: string) =>
    request<{ active_customer: string; active_customer_id: number }>("/customers/activate", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  zspaceSyncStatus: () => request<Record<string, unknown>>("/ops/zspace-sync/status"),
  zspaceSyncInstall: () => request<Record<string, unknown>>("/ops/zspace-sync/install", { method: "POST" }),
  zspaceSyncAccounts: () => request<Record<string, unknown>>("/ops/zspace-sync/accounts"),
  zspaceSyncBind: (body: { username: string; nas_id: string; nas_name?: string }) =>
    request<Record<string, unknown>>("/ops/zspace-sync/bind", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  zspaceEnsureCustomer: (name: string, register = true) =>
    request<{
      ok: boolean;
      registered: boolean;
      paths: Record<string, string>;
      config_path: string;
    }>("/ops/zspace-sync/ensure-customer", {
      method: "POST",
      body: JSON.stringify({ name, register }),
    }),
  zspaceListMediaRemoteFolders: (remote_base: string = "手机相册备份") =>
    request<{ ok: boolean; remote_base: string; folders: string[] }>(
      `/ops/zspace-sync/media-remote-folders?remote_base=${encodeURIComponent(remote_base)}`,
    ),
  zspaceSetMediaSource: (body: { customer_name: string; remote_person: string; remote_base?: string }) =>
    request<Record<string, unknown>>("/ops/zspace-sync/media-set-source", {
      method: "POST",
      body: JSON.stringify({
        customer_name: body.customer_name,
        remote_person: body.remote_person,
        remote_base: body.remote_base || "手机相册备份",
      }),
    }),
  zspacePreviewMediaSource: (params: {
    customer_name: string;
    remote_person: string;
    remote_base?: string;
  }) => {
    const q = new URLSearchParams({
      customer_name: params.customer_name,
      remote_person: params.remote_person,
      remote_base: params.remote_base || "手机相册备份",
    });
    return request<Record<string, unknown>>(`/ops/zspace-sync/media-preview?${q}`);
  },
  zspaceSyncDryRun: (pullOnly = true) =>
    request<Record<string, unknown>>(`/ops/zspace-sync/dry-run?pull_only=${pullOnly ? "true" : "false"}`, {
      method: "POST",
    }),
  zspaceSyncReconcile: () => request<Record<string, unknown>>("/ops/zspace-sync/reconcile"),
  carrierSeeds: () =>
    request<{
      ok: boolean;
      seeds: Array<{
        id: string;
        name: string;
        description?: string;
        profile?: Record<string, unknown>;
        files?: Array<{ source: string; target: string }>;
      }>;
    }>("/ops/carrier/seeds"),
  importCarrierSeed: (seedId: string, overwrite = false) =>
    request<{
      ok: boolean;
      seed_id: string;
      copied: string[];
      skipped: string[];
      keyword_pack_path?: string;
    }>("/ops/carrier/seeds/import", {
      method: "POST",
      body: JSON.stringify({ seed_id: seedId, overwrite }),
    }),

  // GContent SEO/GEO
  contentCompleteness: () =>
    request<{
      ok: boolean;
      score: number;
      can_publish: boolean;
      missing: string[];
      checks: Record<string, boolean>;
      verified_fact_count: number;
      site_count: number;
    }>("/content/completeness"),
  contentPlatforms: (includeExperimental = true) =>
    request<{ ok: boolean; platforms: Array<Record<string, unknown>> }>(
      `/content/platforms?include_experimental=${includeExperimental ? "true" : "false"}`,
    ),
  contentSources: () =>
    request<{ ok: boolean; sources: Array<Record<string, unknown>> }>("/content/sources"),
  contentCreateSource: (body: {
    kind?: string;
    title: string;
    body: string;
    source_url?: string;
    verified?: boolean;
    verified_by?: string;
  }) =>
    request<{ ok: boolean; source: Record<string, unknown> }>("/content/sources", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  contentVerifySource: (id: number, verifiedBy = "operator") =>
    request<{ ok: boolean; source: Record<string, unknown> }>(
      `/content/sources/${id}/verify?verified_by=${encodeURIComponent(verifiedBy)}`,
      { method: "POST" },
    ),
  contentSites: () =>
    request<{ ok: boolean; sites: Array<Record<string, unknown>> }>("/content/sites"),
  contentCreateSite: (body: {
    domain: string;
    role?: string;
    cms_type?: string;
    publish_url?: string;
    canonical_group?: string;
    sitemap_url?: string;
    secret_ref?: string;
  }) =>
    request<{ ok: boolean; site: Record<string, unknown> }>("/content/sites", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  contentArticles: () =>
    request<{ ok: boolean; articles: Array<Record<string, unknown>> }>("/content/articles"),
  contentGenerateArticle: (body: {
    topic: string;
    campaign_id?: number | null;
    platforms?: string[];
    author?: string;
  }) =>
    request<{
      ok: boolean;
      article: Record<string, unknown>;
      variants: Array<Record<string, unknown>>;
    }>("/content/articles/generate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  contentGetArticle: (id: number) =>
    request<{
      ok: boolean;
      article: Record<string, unknown>;
      variants: Array<Record<string, unknown>>;
    }>(`/content/articles/${id}`),
  contentPatchArticle: (
    id: number,
    body: { title?: string; summary?: string; body_md?: string; faq?: unknown[] },
  ) =>
    request<{ ok: boolean; article: Record<string, unknown> }>(`/content/articles/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  contentApproveArticle: (id: number, approvedBy = "operator") =>
    request<{ ok: boolean; article: Record<string, unknown> }>(`/content/articles/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ approved_by: approvedBy }),
    }),
  contentJobs: () =>
    request<{ ok: boolean; jobs: Array<Record<string, unknown>> }>("/content/jobs"),
  contentEnqueueJob: (variantId: number, profileName = "") =>
    request<{ ok: boolean; job: Record<string, unknown> }>("/content/jobs", {
      method: "POST",
      body: JSON.stringify({ variant_id: variantId, profile_name: profileName }),
    }),
  contentRunJob: (jobId: number, dryRun = true, resume = false) =>
    request<{
      ok: boolean;
      job?: Record<string, unknown>;
      need_human?: boolean;
      validated?: boolean;
      dry_run?: boolean;
      live?: boolean;
      auto_publish?: boolean;
      result?: Record<string, unknown>;
    }>(
      `/content/jobs/${jobId}/run`,
      {
        method: "POST",
        body: JSON.stringify({ dry_run: dryRun, resume }),
      },
    ),
  contentSetJobStatus: (
    jobId: number,
    status: string,
    evidence?: { error?: string; published_url?: string; review_id?: string },
  ) => {
    const q = new URLSearchParams({ status });
    if (evidence?.error) q.set("error", evidence.error);
    if (evidence?.published_url) q.set("published_url", evidence.published_url);
    if (evidence?.review_id) q.set("review_id", evidence.review_id);
    return request<{ ok: boolean; job: Record<string, unknown> }>(
      `/content/jobs/${jobId}/status?${q.toString()}`,
      { method: "POST" },
    );
  },
  contentReplyDrafts: (messageId: number, extraContext = "", brand = "") =>
    request<{ ok: boolean; drafts: Array<Record<string, unknown>>; warning?: string }>(
      "/content/reply-drafts",
      {
        method: "POST",
        body: JSON.stringify({
          message_id: messageId,
          extra_context: extraContext,
          brand,
        }),
      },
    ),
  contentListReplyDrafts: (messageId?: number) => {
    const q = messageId != null ? `?message_id=${messageId}` : "";
    return request<{ ok: boolean; drafts: Array<Record<string, unknown>> }>(`/content/reply-drafts${q}`);
  },
  contentMarkReplyCopied: (draftId: number) =>
    request<{ ok: boolean; draft: Record<string, unknown> }>(`/content/reply-drafts/${draftId}/copied`, {
      method: "POST",
    }),
  reachReplyDrafts: (messageId: number, extraContext = "", brand = "") =>
    request<{ ok: boolean; drafts: Array<Record<string, unknown>>; warning?: string }>(
      "/reach/reply-drafts",
      {
        method: "POST",
        body: JSON.stringify({
          message_id: messageId,
          extra_context: extraContext,
          brand,
        }),
      },
    ),
  reachMarkReplyCopied: (draftId: number) =>
    request<{ ok: boolean; draft: Record<string, unknown> }>(`/reach/reply-drafts/${draftId}/copied`, {
      method: "POST",
    }),

  contentChromeProfiles: () =>
    request<{
      ok: boolean;
      root: string;
      selected: string | null;
      selected_platform?: string | null;
      profiles: ChromeProfile[];
      legacy_profiles?: ChromeProfile[];
      platforms?: Array<{ id: string; label: string; url: string; short?: string }>;
      unassigned?: Array<{ name: string; path: string }>;
      chrome_installed?: boolean;
      note?: string;
      business_scope?: string;
    }>("/content/chrome-profiles"),
  contentChromeConfirmLegacy: (name: string, platform: string) =>
    request<{ ok: boolean; name: string; platform: string }>(
      "/content/chrome-profiles/confirm-legacy",
      {
        method: "POST",
        body: JSON.stringify({ name, platform }),
      },
    ),
  contentChromeCreate: (body: {
    platform: string;
    count?: number;
    name_prefix?: string;
    custom_platform_name?: string;
    custom_platform_url?: string;
  }) =>
    request<{
      ok: boolean;
      platform: string;
      selected: string | null;
      created: Array<{ name: string; platform: string; path: string }>;
      root?: string;
    }>("/content/chrome-profiles/create", { method: "POST", body: JSON.stringify(body) }),
  contentChromeOpen: (name: string, platform?: string | null, dryRun = false) =>
    request<{ ok: boolean; platform?: string; open?: Record<string, unknown> }>(
      "/content/chrome-profiles/open",
      {
        method: "POST",
        body: JSON.stringify({ name, platform: platform || null, dry_run: dryRun }),
      },
    ),
  contentChromeSelect: (name: string, platform?: string | null) =>
    request<{ ok: boolean; selected: string; platform?: string; root?: string }>(
      "/content/chrome-profiles/select",
      { method: "POST", body: JSON.stringify({ name, platform: platform || null }) },
    ),
  contentChromeRename: (oldName: string, newName: string) =>
    request<{
      ok: boolean;
      old_name: string;
      name: string;
      path: string;
      platform?: string | null;
    }>("/content/chrome-profiles/rename", {
      method: "POST",
      body: JSON.stringify({ old_name: oldName, new_name: newName }),
    }),
  contentChromeUpdate: (body: {
    name: string;
    new_name?: string;
    platform?: string;
    custom_platform_name?: string;
    custom_platform_url?: string;
  }) =>
    request<{ ok: boolean; name: string; platform?: string; official_url?: string }>(
      "/content/chrome-profiles",
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  contentChromeDelete: (names: string[]) =>
    request<{
      ok: boolean;
      deleted: string[];
      message_bindings_disabled: number;
      cleanup_pending?: string[];
    }>("/content/chrome-profiles", {
      method: "DELETE",
      body: JSON.stringify({ names }),
    }),
  contentMessageAccounts: () =>
    request<{
      ok: boolean;
      accounts: import("../types").ReachMessageAccount[];
      empty_state?: { code: string; title: string; action: string } | null;
    }>("/content/message-accounts"),
  contentMessageAccountCreate: (body: {
    platform: string;
    profile_name?: string;
    display_name?: string;
    enabled?: boolean;
    message_url?: string;
  }) =>
    request<{ ok: boolean; account: import("../types").ReachMessageAccount }>("/content/message-accounts", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  contentMessageAccountUpdate: (
    id: number,
    body: { display_name?: string; enabled?: boolean; message_url?: string },
  ) =>
    request<{ ok: boolean; account: import("../types").ReachMessageAccount }>(`/content/message-accounts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  contentMessageAccountsDelete: (ids: number[]) =>
    request<{ ok: boolean; deleted: Array<Record<string, unknown>>; count: number }>(
      "/content/message-accounts",
      { method: "DELETE", body: JSON.stringify({ ids }) },
    ),
  contentMessageAccountOpen: (account: import("../types").ReachMessageAccount) =>
    request<{ ok: boolean; platform?: string; open?: Record<string, unknown> }>(
      `/content/message-accounts/${account.id}/open`,
      {
        method: "POST",
        body: JSON.stringify({ dry_run: false }),
      },
    ),
  contentMessages: (query: import("../types").MessageListQuery = { unread: true, limit: 100 }) => {
    const params = new URLSearchParams({
      unread: String(query.unread),
      history: String(Boolean(query.history)),
      limit: String(query.limit),
    });
    if (query.account_id != null) params.set("account_id", String(query.account_id));
    return (
    request<{
      ok: boolean;
      messages: import("../types").ReachMessage[];
      unread_count: number;
      empty_state?: { code: string; title: string; action?: string } | null;
    }>(
      `/content/messages?${params.toString()}`,
    )
    );
  },
  contentMessageScanStart: (accountId?: number, dryRun = false) =>
    request<{ ok: boolean; queued: number }>("/content/messages/scan", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId ?? null, dry_run: dryRun }),
    }),
  contentMessageScanStatus: () =>
    request<{
      ok: boolean;
      worker: import("../types").ReachMessageScanStatus;
      scans?: Array<Record<string, unknown>>;
    }>("/content/messages/status"),
  contentMessageScanCancel: () =>
    request<{ ok: boolean }>("/content/messages/cancel", { method: "POST" }),
  contentMessageRead: (id: number) =>
    request<{ ok: boolean; message: import("../types").ReachMessage }>(`/content/messages/${id}/read`, {
      method: "POST",
    }),
  contentMessagesReadAll: (accountId?: number) =>
    request<{ ok: boolean; updated: number; unread_count?: number }>("/content/messages/read-all", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId ?? null }),
    }),
  contentMessageOpen: (id: number) =>
    request<{ ok: boolean; opened?: Record<string, unknown> }>(`/content/messages/${id}/open`, {
      method: "POST",
      body: JSON.stringify({ dry_run: false }),
    }),

  // GVideoRules
  productionRulesSchema: () =>
    request<{
      ok: boolean;
      schema_version: string;
      metadata: Record<string, unknown>;
      empty_rules: Record<string, unknown>;
      orientation_defaults: Record<
        string,
        {
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
        }
      >;
      fields: Array<Record<string, unknown>>;
      hard_locks: Array<{ id: string; label: string; detail: string }>;
      recommended_content_categories?: string[];
    }>("/production-rules/schema"),
  productionRulesValidate: (rules: Record<string, unknown>, strictSemantic = false) =>
    request<{
      ok: boolean;
      requested_rules: Record<string, unknown>;
      effective_rules: Record<string, unknown>;
      rejected: string[];
      clamped: Array<Record<string, string>>;
      warnings: string[];
    }>("/production-rules/validate", {
      method: "POST",
      body: JSON.stringify({ rules, strict_semantic_v1: strictSemantic }),
    }),
  productionRulesParse: (sourceText: string, model?: string) =>
    request<{
      ok: boolean;
      model: string;
      confidence: number;
      requested_rules: Record<string, unknown>;
      effective_rules: Record<string, unknown>;
      rejected: string[];
      clamped: Array<Record<string, string>>;
      warnings: string[];
    }>("/production-rules/parse", {
      method: "POST",
      body: JSON.stringify({ source_text: sourceText, model: model || "" }),
    }),
  productionRulesContentFacets: () =>
    request<{
      ok: boolean;
      customer_id?: number;
      pack_id?: number | null;
      pack_revision?: number | null;
      facets: Array<{
        name: string;
        label: string;
        keyword_count: number;
        title_count: number;
        hook_count: number;
        title_categories: string[];
        titles_sample: string[];
        usable: boolean;
      }>;
    }>("/production-rules/content-facets"),
  productionRulesDraftsFromPack: (body?: {
    content_category?: string;
    orientation?: "portrait" | "landscape";
    facets?: string[];
    force?: boolean;
  }) =>
    request<{
      ok: boolean;
      created: Array<Record<string, unknown>>;
      created_count: number;
    }>("/production-rules/drafts-from-pack", {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),
  productionRulesList: (
    includeArchived = false,
    contentCategory?: string,
    orientation: "portrait" | "landscape" = "portrait",
  ) =>
    request<{
      ok: boolean;
      customer_id?: number;
      customer_name?: string;
      rules: Array<Record<string, unknown>>;
      active_id: number | null;
      active_by_category: Record<string, number | null>;
      categories: string[];
      rotation_policy?: boolean;
      rotation_pool?: Array<{
        id: number;
        name: string;
        revision: number;
        content_category: string;
      }>;
    }>(
      `/production-rules?include_archived=${includeArchived ? "true" : "false"}&orientation=${orientation}${contentCategory ? `&content_category=${encodeURIComponent(contentCategory)}` : ""}`,
    ),
  productionRulesActive: (
    contentCategory = "default",
    orientation: "portrait" | "landscape" = "portrait",
  ) =>
    request<{
      ok: boolean;
      customer_id?: number;
      customer_name?: string;
      rule: Record<string, unknown> | null;
    }>(
      `/production-rules/active?content_category=${encodeURIComponent(contentCategory)}&orientation=${orientation}`,
    ),
  productionRulesGet: (id: number) =>
    request<{ ok: boolean; rule: Record<string, unknown> }>(`/production-rules/${id}`),
  productionRulesCreate: (body: {
    name: string;
    content_category?: string;
    orientation?: "portrait" | "landscape";
    source_text?: string;
    rules: Record<string, unknown>;
    model?: string;
    parse_warnings?: string[];
  }) =>
    request<{ ok: boolean; rule: Record<string, unknown> }>("/production-rules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  productionRulesPatch: (
    id: number,
    body: { name?: string; source_text?: string; rules?: Record<string, unknown> },
  ) =>
    request<{ ok: boolean; rule: Record<string, unknown> }>(`/production-rules/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  productionRulesApprove: (id: number, approvedBy = "operator") =>
    request<{ ok: boolean; rule: Record<string, unknown> }>(`/production-rules/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ approved_by: approvedBy }),
    }),
  productionRulesActivate: (id: number) =>
    request<{ ok: boolean; rule: Record<string, unknown> }>(`/production-rules/${id}/activate`, {
      method: "POST",
    }),
  productionRulesArchive: (id: number) =>
    request<{ ok: boolean; rule: Record<string, unknown> }>(`/production-rules/${id}/archive`, {
      method: "POST",
    }),
  productionRulesCopy: (
    id: number,
    body?: { content_category?: string; name?: string },
  ) =>
    request<{ ok: boolean; rule: Record<string, unknown> }>(`/production-rules/${id}/copy`, {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),
  productionRulesSetRotation: (id: number, enabled: boolean) =>
    request<{ ok: boolean; rule: Record<string, unknown> }>(
      `/production-rules/${id}/rotation`,
      {
        method: "POST",
        body: JSON.stringify({ enabled }),
      },
    ),
  productionRulesSetRotationPolicy: (enabled: boolean) =>
    request<{ ok: boolean; rotation_policy: boolean }>("/production-rules/rotation-policy", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  productionRulesDelete: (id: number) =>
    request<{ ok: boolean; deleted_id: number }>(`/production-rules/${id}`, {
      method: "DELETE",
    }),
  semanticKeywordRevisionPreview: (body: {
    patch: Record<string, unknown>;
    semantic_candidate_ids: number[];
    official_evidence_ids?: number[];
    draft_key: string;
    auto_promote?: boolean;
  }) =>
    request<{ ok: boolean; revision: Record<string, unknown> }>(
      "/semantic-ops/keyword-revisions/preview",
      { method: "POST", body: JSON.stringify(body) },
    ),
  semanticKeywordRevisionApprove: (id: number, approvedBy = "operator") =>
    request<{ ok: boolean; revision: Record<string, unknown> }>(
      `/semantic-ops/keyword-revisions/${id}/approve`,
      { method: "POST", body: JSON.stringify({ approved_by: approvedBy }) },
    ),
  semanticKeywordRevisionPromote: (id: number, auto = false) =>
    request<{ ok: boolean; revision: Record<string, unknown> }>(
      `/semantic-ops/keyword-revisions/${id}/promote`,
      { method: "POST", body: JSON.stringify({ actor: "operator", auto }) },
    ),
  semanticKeywordRevisionRollback: (sourceRevision: number) =>
    request<{ ok: boolean; pack: Record<string, unknown> }>(
      "/semantic-ops/keyword-revisions/rollback",
      { method: "POST", body: JSON.stringify({ source_revision: sourceRevision }) },
    ),
  sceneTourLabels: () =>
    request<{
      ok: boolean;
      categories: Record<string, string>;
      ui: Record<string, string>;
      buckets: Record<string, string>;
    }>("/scene-tour/labels"),
  sceneTourBrief: () =>
    request<{ ok: boolean; brief: Record<string, unknown>; label_zh: string }>(
      "/scene-tour/brief",
    ),
  sceneTourCoverage: (orientation: "portrait" | "landscape" = "portrait") =>
    request<{
      ok: boolean;
      label_zh: string;
      reasons?: string[];
      buckets?: Array<Record<string, unknown>>;
      usable_total?: number;
    }>(`/scene-tour/coverage?orientation=${orientation}`),
  sceneTourDryRun: (orientation: "portrait" | "landscape" = "portrait", seed = 1) =>
    request<{
      ok: boolean;
      blocked: boolean;
      reasons: string[];
      warnings?: string[];
      title?: string;
      clip_count?: number;
      label_zh: string;
      plan?: Record<string, unknown>;
      rule?: { id: number; name: string };
    }>(`/scene-tour/dry-run?orientation=${orientation}&seed=${seed}`, {
      method: "POST",
    }),
  sceneTourBootstrapBuckets: (
    orientation: "portrait" | "landscape" = "portrait",
    force = false,
  ) =>
    request<{
      ok: boolean;
      updated: number;
      skipped: number;
      message: string;
    }>(
      `/scene-tour/bootstrap-buckets?orientation=${orientation}&force=${force ? "true" : "false"}`,
      { method: "POST" },
    ),
  sceneTourHotInbox: () =>
    request<{
      ok: boolean;
      enabled: boolean;
      label_zh: string;
      message: string;
      pending_count: number;
    }>("/scene-tour/hot-inbox"),
};
