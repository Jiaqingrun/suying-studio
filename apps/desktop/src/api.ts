export const API_BASE = "http://127.0.0.1:8766";

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
  listOutputs: (state?: string) =>
    request<Array<Record<string, unknown>>>(state ? `/outputs?state=${encodeURIComponent(state)}` : "/outputs"),
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
  exportPublishPack: (id: number) =>
    request<{ ok: boolean; output_id: number; manifest: Record<string, unknown> }>(
      `/outputs/${id}/publish-pack`,
      { method: "POST" },
    ),
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
  reachOpen: (id: number, dryRun = false) =>
    request<{
      ok: boolean;
      paste_card?: string;
      open?: Record<string, unknown>;
      disclaimer?: string;
      auto_publish: boolean;
    }>(`/reach/queue/${id}/open`, { method: "POST", body: JSON.stringify({ dry_run: dryRun }) }),
  reachQuota: () => request<Record<string, unknown>>("/reach/quota"),
  reachInbox: () =>
    request<{
      ok: boolean;
      unread_count: number;
      notices: Array<Record<string, unknown>>;
      auto_reply: boolean;
    }>("/reach/inbox"),
  reachPlatforms: () =>
    request<{ ok: boolean; platforms: Array<{ id: string; label: string; url: string }> }>(
      "/reach/platforms",
    ),
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
