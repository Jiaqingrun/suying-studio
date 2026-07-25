export const API_BASE = "http://127.0.0.1:8766";

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
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export type Health = {
  status: string;
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
  };
  disk?: Record<string, unknown>;
  auto_daily_enabled?: boolean;
  auto_daily_hour?: number;
  vectorization_enabled?: boolean;
  vectorization_enabled_at?: string | null;
  vectorization_mode?: string;
  onboarded?: boolean;
  setup_complete?: boolean;
  frames_root?: string;
  render_root?: string;
  vector_store?: { kind?: string; db?: string; table?: string; column?: string; note?: string };
  cursor_api_key_configured?: boolean;
  cursor_api_key_hint?: string;
  worker_running?: boolean;
  scheduler_running?: boolean;
  watcher_running?: boolean;
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
  assets: number;
  cliplets: number;
  cliplets_indexed: number;
  outputs: Record<string, number>;
  ready: number;
  failed: number;
  failure_rate: number;
  duplicate_asset_rate: number;
  theme_distribution: Record<string, number>;
  keyword_top: Array<{ keyword: string; count: number }>;
  reviews: Record<string, number>;
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
  getSettings: () => request<Record<string, unknown>>("/settings"),
  updateSettings: (body: Record<string, unknown>) =>
    request("/settings", { method: "PUT", body: JSON.stringify(body) }),
  verifyCursorKey: (cursor_api_key?: string) =>
    request<{
      ok: boolean;
      message: string;
      cursor_api_key_configured?: boolean;
      cursor_api_key_hint?: string;
      me?: Record<string, unknown>;
    }>("/cursor/verify", {
      method: "POST",
      body: JSON.stringify(
        cursor_api_key != null && cursor_api_key !== ""
          ? { cursor_api_key }
          : {},
      ),
    }),
  cursorChatGet: (session_id = "default") =>
    request<{
      session_id: string;
      agent_id: string;
      customer: string;
      message_count: number;
      messages: Array<{ role: string; text: string; at?: string }>;
      busy: boolean;
      updated_at: string;
    }>(`/cursor/chat?session_id=${encodeURIComponent(session_id)}`),
  cursorChat: (message: string, session_id = "default", customer?: string) =>
    request<{
      ok: boolean;
      session_id: string;
      agent_id: string;
      reply: string;
      elapsed_sec: number;
      message_count: number;
    }>("/cursor/chat", {
      method: "POST",
      body: JSON.stringify({ message, session_id, customer }),
    }),
  /** SSE stream; onEvent for each JSON frame. Returns final done payload. */
  cursorChatStream: async (
    message: string,
    onEvent: (ev: Record<string, unknown>) => void,
    session_id = "default",
    customer?: string,
  ) => {
    const res = await fetch(`${API_BASE}/cursor/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ message, session_id, customer }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }
    if (!res.body) throw new Error("引擎未返回流式响应体");
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let donePayload: Record<string, unknown> | null = null;
    let streamError: string | null = null;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part
          .split("\n")
          .map((l) => l.trim())
          .find((l) => l.startsWith("data:"));
        if (!line) continue;
        const raw = line.slice(5).trim();
        if (!raw || raw === "[DONE]") continue;
        try {
          const ev = JSON.parse(raw) as Record<string, unknown>;
          onEvent(ev);
          if (ev.type === "done") donePayload = ev;
          if (ev.type === "error") streamError = String(ev.message || "流式错误");
        } catch {
          /* ignore malformed chunk */
        }
      }
    }
    if (streamError) throw new Error(streamError);
    if (!donePayload) throw new Error("流式结束但未收到完成事件");
    return donePayload as {
      ok: boolean;
      session_id: string;
      agent_id: string;
      reply: string;
      elapsed_sec: number;
      message_count: number;
    };
  },
  cursorChatReset: (session_id = "default") =>
    request<{
      session_id: string;
      agent_id: string;
      messages: Array<{ role: string; text: string; at?: string }>;
    }>("/cursor/chat/reset", {
      method: "POST",
      body: JSON.stringify({ session_id }),
    }),
  cursorChatCancel: (session_id = "default") =>
    request<{
      session_id: string;
      agent_id: string;
      busy: boolean;
      messages: Array<{ role: string; text: string; at?: string }>;
    }>("/cursor/chat/cancel", {
      method: "POST",
      body: JSON.stringify({ session_id }),
    }),
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
  enableVectorization: async (kickReconcile = true, limit = 50) => {
    try {
      return await request<{
        enabled: boolean;
        mode: string;
        message: string;
        gaps?: Record<string, unknown>;
        reconcile?: Record<string, unknown> | null;
      }>(
        `/vectorization/enable?kick_reconcile=${kickReconcile ? "true" : "false"}&limit=${limit}`,
        { method: "POST" },
      );
    } catch (e) {
      const msg = String(e);
      // Old engine without /vectorization/enable — fall back to settings + incremental reconcile
      if (!/Not Found|404/i.test(msg)) throw e;
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
      return {
        enabled: true,
        mode: "incremental",
        message:
          "已开启增量向量化（兼容旧引擎接口）。已有向量保留，仅补齐缺口。",
        gaps,
        reconcile,
      };
    }
  },
  listJobs: () => request<Array<Record<string, unknown>>>("/jobs"),
  createJob: (body: Record<string, unknown>) =>
    request("/jobs", { method: "POST", body: JSON.stringify(body) }),
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
      ready: number;
      failed: number;
      ready_rate: number;
      failure_rate: number;
      voice_coverage: number;
      subtitle_coverage: number;
      missing_voice: number;
      published_ready: number;
      tts_noncompliant?: number;
      tts_say?: number;
      tts_edge_ok?: number;
      tts_lock_hard_fail?: number;
      health_line: string;
      quota?: Record<string, unknown>;
      library_ok?: boolean;
      output_ok?: boolean;
    }>("/reports/ops"),
  listEvents: () => request<Array<Record<string, unknown>>>("/logs/events"),
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
  listReviews: () => request<Array<Record<string, unknown>>>("/reviews"),
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
      profiles: Array<{ name: string; path: string; platform?: string | null; label?: string | null }>;
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
  reachAutoUploadStart: (body: {
    chrome_profile?: string;
    queue_id?: number;
    platform?: string;
    accept_risk: boolean;
    timeout_sec?: number;
    dry_run?: boolean;
  }) =>
    request<{
      ok: boolean;
      active?: boolean;
      phase?: string;
      message?: string;
      job_id?: string;
      need_human?: boolean;
      disclaimer?: string;
      item?: Record<string, unknown>;
    }>("/reach/auto-upload/start", { method: "POST", body: JSON.stringify(body) }),
  reachAutoUploadStatus: () =>
    request<{
      ok: boolean;
      active: boolean;
      phase: string;
      message?: string;
      need_human?: boolean;
      error?: string;
      queue_id?: number;
      chrome_profile?: string;
    }>("/reach/auto-upload/status"),
  reachAutoUploadCancel: () =>
    request<{ ok: boolean; cancelled?: boolean }>("/reach/auto-upload/cancel", { method: "POST" }),
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
  reachQuota: () => request<Record<string, unknown>>("/reach/quota"),
  updateReachQuota: (body: {
    daily_quota?: number;
    platform_quotas?: Record<string, number>;
    fail_threshold?: number;
  }) =>
    request<Record<string, unknown>>("/reach/quota", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  reachInbox: () =>
    request<{
      ok: boolean;
      unread_count: number;
      notices: Array<Record<string, unknown>>;
      auto_reply: boolean;
    }>("/reach/inbox"),
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
      title_pool_count: number;
      title_pool_sample: string[];
      title_pool_meta?: Record<string, unknown>;
      hooks_count: number;
      max_chars_per_line?: number;
    }>(`/keywords/active-summary${customerId != null ? `?customer_id=${customerId}` : ""}`),
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
};
