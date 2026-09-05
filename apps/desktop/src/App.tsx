import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api, Customer, Health, ReportSummary, ServicesStatus } from "./api";
import { getEngineStatus, isTauri, offlineClassLabel, startEngine, stopEngine, type EngineStatus } from "./engineControl";
import {
  BUILTIN_REACH_PLATFORMS,
  BUILTIN_SLOT_COUNTS,
  mergeCoverSlotSpecs,
  mergeReachPlatforms,
  mergeSlotCounts,
  type CoverSlotSpec,
  type ReachPlatform,
} from "./reachCatalog";
import { brandFromProfile, type BrandLogoState } from "./BrandLogoPanel";
import { CommandPalette, type CmdItem } from "./shell/CommandPalette";
import { AppDialog } from "./shell/AppDialog";
import { AppShell } from "./shell/AppShell";
import { CarrierInstallWizard } from "./CarrierInstallWizard";
import {
  ensureNotificationPermission,
  getNotificationPermission,
  playReachMessageSound,
  type NotificationPermissionState,
} from "./notifications";
import {
  AppNotifyHub,
  useNotifyHub,
  type NotifyOptions,
  type NotifyTarget,
} from "./AppNotifyHub";
import { listenSystemState } from "./systemEvents";
import {
  DEFAULT_PREFS as WORKSPACE_DEFAULT_PREFS,
  getWorkspaceSnapshot,
  getWorkspaceSyncPrefs,
  listenWorkspaceState,
  reconnectWorkspaceNow,
  setWorkspaceSyncPrefs,
  type WorkspaceProbeView,
  type WorkspaceStateSnapshot,
  type WorkspaceSyncPrefs,
} from "./workspaceSync";
import { POLL_BUDGET_MS, POLL_TICK } from "./pollBudget";
import { remainingProductionCount as countRemainingProduction } from "./taskMetrics";
import {
  TAB_BLURB,
  TABS,
  type FlashKind,
  type ChromeProfile,
  type OpsSection,
  type ProduceWorkspace,
  type PublishWorkspace,
  type MessageListQuery,
  type ReachMessage,
  type ReachMessageAccount,
  type ReachMessageScanStatus,
  type ReachNtfyConfig,
  type SemanticBackfillStatus,
  type SettingsSection,
  type Tab,
} from "./types";
import { useLayoutDensity } from "./hooks/useLayoutDensity";
import {
  OverviewPage,
  ProductionPage,
  ReviewPage,
  PublishPage,
  MessagesPage,
  DataCenterPage,
  OpsPage,
  SettingsPage,
} from "./pages";
import type { ActivityItem } from "./shell/ActivityTicker";
import { VideoRuleWorkbench } from "./VideoRuleWorkbench";
import {
  settingsPasswordStatus,
  settingsPasswordVerify,
  settingsPasswordLock,
  type SettingsPasswordStatus,
} from "./settingsLock";
import {
  LayoutDashboard,
  Clapperboard,
  ScanSearch,
  Send,
  MessageCircle,
  BarChart3,
  Wrench,
  Settings2,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import "./App.css";

const CONTENT_CHROME_SYNC_EVENT = "suying:content-chrome-profiles";

function broadcastContentChromeProfiles(snapshot: unknown) {
  if (typeof window === "undefined" || !snapshot) return;
  window.dispatchEvent(new CustomEvent(CONTENT_CHROME_SYNC_EVENT, { detail: snapshot }));
}

function notifyTargetFromDeepLink(value: unknown): NotifyTarget {
  const raw = String(value || "").trim();
  if (!raw) return { tab: "publish", section: "desk", guideTarget: "publish-next-step" };
  try {
    const url = new URL(raw);
    const area = url.hostname || url.pathname.replace(/^\/+/, "");
    if (area === "messages") {
      return { tab: "messages" };
    }
    if (area === "publish") {
      const run = url.searchParams.get("run");
      return {
        tab: "publish",
        section: "desk",
        guideTarget: run ? `publish-run-${run}` : "publish-next-step",
      };
    }
  } catch {
    // Unknown legacy links safely fall back to the publishing workspace.
  }
  return { tab: "publish", section: "desk", guideTarget: "publish-next-step" };
}

const TAB_ICONS: Record<Tab, LucideIcon> = {
  overview: LayoutDashboard,
  produce: Clapperboard,
  rules: SlidersHorizontal,
  review: ScanSearch,
  publish: Send,
  messages: MessageCircle,
  data: BarChart3,
  ops: Wrench,
  settings: Settings2,
};

function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [mountedTabs, setMountedTabs] = useState<Tab[]>(["overview"]);
  const [produceWorkspace, setProduceWorkspace] = useState<ProduceWorkspace>("tasks");
  const [publishWorkspace, setPublishWorkspace] = useState<PublishWorkspace>("desk");
  const [opsSection, setOpsSection] = useState<OpsSection>("ai");
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("paths");
  const [passwordStatus, setPasswordStatus] = useState<SettingsPasswordStatus>({
    configured: false,
    unlocked: false,
    unlocked_remaining_sec: 0,
    fail_cooldown_sec: 0,
  });
  const layout = useLayoutDensity();

  const goTab = useCallback((t: Tab, opts?: { produce?: ProduceWorkspace; publish?: PublishWorkspace; settings?: SettingsSection; ops?: OpsSection; section?: string; guideTarget?: string }) => {
    startTransition(() => {
      setMountedTabs((tabs) => (tabs.includes(t) ? tabs : [...tabs, t]));
      setTab(t);
      if (opts?.produce) setProduceWorkspace(opts.produce);
      if (opts?.publish) setPublishWorkspace(opts.publish);
      if (opts?.settings) setSettingsSection(opts.settings);
      if (opts?.ops) setOpsSection(opts.ops);
      if (opts?.section && t === "produce" && ["tasks", "assets"].includes(opts.section)) {
        setProduceWorkspace(opts.section as ProduceWorkspace);
      }
      if (opts?.section && t === "publish" && ["pack", "articles", "desk", "reach"].includes(opts.section)) {
        setPublishWorkspace(opts.section as PublishWorkspace);
      }
      if (opts?.section && t === "settings") setSettingsSection(opts.section as SettingsSection);
      if (
        opts?.section &&
        t === "ops" &&
        ["ai", "services", "backup", "carrier", "logs", "health", "advanced"].includes(opts.section)
      ) {
        setOpsSection(opts.section as OpsSection);
      }
      if (typeof window !== "undefined") {
        const query = new URLSearchParams({ tab: t });
        if (opts?.section) query.set("section", opts.section);
        window.history.replaceState(null, "", `#${query.toString()}`);
      }
      if (opts?.guideTarget) {
        const spotlightWhenMounted = (attemptsLeft: number) => {
          const el = document.querySelector<HTMLElement>(`[data-guide="${opts.guideTarget}"]`);
          if (!el) {
            if (attemptsLeft > 0) {
              window.setTimeout(() => spotlightWhenMounted(attemptsLeft - 1), 50);
            }
            return;
          }
          el.classList.add("guide-spotlight");
          el.scrollIntoView({ block: "center", behavior: "smooth" });
          window.setTimeout(() => el.classList.remove("guide-spotlight"), 4500);
        };
        window.setTimeout(() => spotlightWhenMounted(20), 50);
      }
    });
  }, []);

  useEffect(() => {
    const applyHash = () => {
      const hash = window.location.hash.replace(/^#/, "");
      const query = new URLSearchParams(hash);
      const initialTab = query.get("tab") as Tab | null;
      if (query.get("tab") === "logs") {
        goTab("ops", { section: "logs", guideTarget: "operation-logs" });
        return;
      }
      if (query.get("tab") === "settings" && query.get("section") === "advanced") {
        goTab("ops", { section: "advanced", guideTarget: "ops-advanced" });
        return;
      }
      if (initialTab && TABS.some(([candidate]) => candidate === initialTab)) {
        goTab(initialTab, { section: query.get("section") || undefined });
      }
    };
    applyHash();
    window.addEventListener("hashchange", applyHash);
    return () => window.removeEventListener("hashchange", applyHash);
  }, [goTab]);

  const [health, setHealth] = useState<Health | null>(null);
  const [services, setServices] = useState<ServicesStatus | null>(null);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [workspacePrefs, setWorkspacePrefs] = useState<WorkspaceSyncPrefs>({ ...WORKSPACE_DEFAULT_PREFS });
  const [workspacePhase, setWorkspacePhase] = useState("idle");
  const [workspaceMessage, setWorkspaceMessage] = useState<string | null>(null);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [workspaceProbe, setWorkspaceProbe] = useState<WorkspaceProbeView | null>(null);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const workspaceSyncingRef = useRef(false);
  const notifyHub = useNotifyHub();
  const {
    toasts,
    celebration,
    banner,
    notify: hubNotify,
    dismissToast,
    dismissCelebration,
    dismissBanner,
  } = notifyHub;
  const [jobsFlash, setJobsFlash] = useState(false);
  const prevRunningJobsRef = useRef<number | null>(null);
  const activeJobIdsRef = useRef<Set<number>>(new Set());
  const notifiedOutputIdsRef = useRef<Set<number>>(new Set());
  const jobsFlashTimerRef = useRef(0);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [reviewBusyId, setReviewBusyId] = useState<number | null>(null);
  const [assets, setAssets] = useState<Array<Record<string, unknown>>>([]);
  const [jobs, setJobs] = useState<Array<Record<string, unknown>>>([]);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [outputs, setOutputs] = useState<Array<Record<string, unknown>>>([]);
  const [report, setReport] = useState<ReportSummary | null>(null);
  const [opsReport, setOpsReport] = useState<Awaited<ReturnType<typeof api.reportOps>> | null>(null);
  const [reviewFilter, setReviewFilter] = useState<"all" | "missing_voice" | "missing_sub" | "tts_bad">(() => {
    try {
      const v = localStorage.getItem("suying.reviewFilter.v1");
      if (v === "all" || v === "missing_voice" || v === "missing_sub" || v === "tts_bad") return v;
    } catch {
      /* ignore */
    }
    return "all";
  });
  const [batchBusy, setBatchBusy] = useState(false);
  const [calendar, setCalendar] = useState<Array<Record<string, unknown>>>([]);
  const [todayPlan, setTodayPlan] = useState<Record<string, unknown> | null>(null);
  const [calDay, setCalDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [calTheme, setCalTheme] = useState("default");
  const [calQuota, setCalQuota] = useState(5);
  const [calNote, setCalNote] = useState("");
  const [dryResult, setDryResult] = useState<Record<string, unknown> | null>(null);
  const [libraryRoot, setLibraryRoot] = useState("");
  const [libraryRootsText, setLibraryRootsText] = useState("");
  const [outputRoot, setOutputRoot] = useState("");
  const [keywordPackPath, setKeywordPackPath] = useState("");
  const [titlePoolSummary, setTitlePoolSummary] = useState<{
    loaded: boolean;
    title_pool_count: number;
    title_pool_sample: string[];
    hooks_count: number;
    max_chars_per_line?: number;
    version?: number;
    revision?: number;
    sha256?: string;
    schema?: string;
  } | null>(null);
  const [cacheRoot, setCacheRoot] = useState("");
  const [renderRoot, setRenderRoot] = useState("");
  const [dataRoot, setDataRoot] = useState("");
  const [vectorDbPath, setVectorDbPath] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [activeCustomerId, setActiveCustomerId] = useState<number | null>(null);
  const [jobRuleProfileId, setJobRuleProfileId] = useState<number | null>(null);
  const [activeRuleSummary, setActiveRuleSummary] = useState("");
  const [ruleRotation, setRuleRotation] = useState(true);
  const [rotationPoolCount, setRotationPoolCount] = useState(0);
  const [ruleOptions, setRuleOptions] = useState<
    Array<{ id: number; label: string; contentCategory: string }>
  >([]);
  /** Bump on customer switch so <video>/<img> remount and bypass poisoned media cache. */
  const [mediaEpoch, setMediaEpoch] = useState(0);
  const [brandLogo, setBrandLogo] = useState<BrandLogoState>(() => brandFromProfile(null));
  const [brandBusy, setBrandBusy] = useState(false);
  const [theme, setTheme] = useState("default");
  const [category, setCategory] = useState("default");
  const [assetCategory, setAssetCategory] = useState("");
  const [topicMode, setTopicMode] = useState("");
  const [topicClusterIds, setTopicClusterIds] = useState("");
  const [topicEvidenceIds, setTopicEvidenceIds] = useState("");
  const [targetCount, setTargetCount] = useState(5);
  const [productionOrientation, setProductionOrientation] = useState<
    "portrait" | "landscape"
  >("portrait");
  const [reviewNote, setReviewNote] = useState("");
  const [reviewReason, setReviewReason] = useState("other");
  const [reviewReasons, setReviewReasons] = useState<Array<{ code: string; label: string }>>([
    { code: "other", label: "其他" },
  ]);
  const [packBusyId, setPackBusyId] = useState<number | null>(null);
  const [packLast, setPackLast] = useState<string>("");
  const [langCatalog, setLangCatalog] = useState<
    Array<{
      code: string;
      label_zh: string;
      label_native: string;
      region: string;
    }>
  >([]);
  const [reachItems, setReachItems] = useState<Array<Record<string, unknown>>>([]);
  const [reachInbox, setReachInbox] = useState<{
    unread_count: number;
    notices: Array<Record<string, unknown>>;
  } | null>(null);
  const [reachMessageAccounts, setReachMessageAccounts] = useState<ReachMessageAccount[]>([]);
  const [reachMessages, setReachMessages] = useState<ReachMessage[]>([]);
  const [reachMessageUnread, setReachMessageUnread] = useState(0);
  const [reachMessageStatus, setReachMessageStatus] = useState<ReachMessageScanStatus | null>(null);
  const [reachMessageBusy, setReachMessageBusy] = useState(false);
  const reachMessageQueryRef = useRef<MessageListQuery>({ unread: true, limit: 100 });
  const [reachNtfyConfig, setReachNtfyConfig] = useState<ReachNtfyConfig | null>(null);
  const [replyDrafts, setReplyDrafts] = useState<Array<Record<string, unknown>>>([]);
  const [replyDraftMessageId, setReplyDraftMessageId] = useState<number | null>(null);
  const [contentMessageAccounts, setContentMessageAccounts] = useState<ReachMessageAccount[]>([]);
  const [contentMessages, setContentMessages] = useState<ReachMessage[]>([]);
  const [contentMessageUnread, setContentMessageUnread] = useState(0);
  const [contentMessageStatus, setContentMessageStatus] = useState<ReachMessageScanStatus | null>(null);
  const [contentMessageBusy, setContentMessageBusy] = useState(false);
  const contentMessageQueryRef = useRef<MessageListQuery>({ unread: true, limit: 100 });
  const [contentReplyDrafts, setContentReplyDrafts] = useState<Array<Record<string, unknown>>>([]);
  const [contentReplyDraftMessageId, setContentReplyDraftMessageId] = useState<number | null>(null);
  const [notificationPermission, setNotificationPermission] =
    useState<NotificationPermissionState>("unavailable");
  const [reachPackDir, setReachPackDir] = useState("");
  const [chromeBusy, setChromeBusy] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [queueBusy, setQueueBusy] = useState(false);
  const [reachMsg, setReachMsg] = useState("");
  const [chromeProfiles, setChromeProfiles] = useState<ChromeProfile[]>([]);
  const [chromeSelected, setChromeSelected] = useState("");
  const [chromeCreatePlatform, setChromeCreatePlatform] = useState("douyin");
  const [chromeRoot, setChromeRoot] = useState("");
  const [chromeInstalled, setChromeInstalled] = useState(false);
  const [coverTemplates, setCoverTemplates] = useState<Array<Record<string, unknown>>>([]);
  const [coverSelectedId, setCoverSelectedId] = useState<string | null>(null);
  const [coverSlotCounts, setCoverSlotCounts] = useState<Record<string, number>>(() => ({
    ...BUILTIN_SLOT_COUNTS,
  }));
  const [coverSlotSpecs, setCoverSlotSpecs] = useState<Record<string, CoverSlotSpec[]>>(() =>
    mergeCoverSlotSpecs(null),
  );
  const [reachPlatforms, setReachPlatforms] = useState<ReachPlatform[]>(() =>
    mergeReachPlatforms(BUILTIN_REACH_PLATFORMS),
  );
  const [coverEditId, setCoverEditId] = useState("");
  const [coverNewName, setCoverNewName] = useState("");
  const [coverPreviewPlat, setCoverPreviewPlat] = useState("douyin");
  const [coverPreviewMsg, setCoverPreviewMsg] = useState("");
  const [autoDaily, setAutoDaily] = useState(false);
  const [autoHour, setAutoHour] = useState(9);
  const [semanticStatus, setSemanticStatus] = useState<SemanticBackfillStatus | null>(null);
  const [semanticMode, setSemanticMode] = useState<"off" | "on_demand">("on_demand");
  const [semanticToggleEnabled, setSemanticToggleEnabled] = useState(true);
  const [semanticFullBackfill, setSemanticFullBackfill] = useState(false);
  const [vectorPending, setVectorPending] = useState(0);
  const semanticPollFastRef = useRef(false);
  const [ollamaNarration, setOllamaNarration] = useState(false);
  const [ollamaNarrationEmoji, setOllamaNarrationEmoji] = useState(true);
  const [ollamaNarrationModel, setOllamaNarrationModel] = useState("");
  const [ollamaInfo, setOllamaInfo] = useState<{
    reachable?: boolean;
    embed_ready?: boolean;
    vision_ready?: boolean;
    escalate_ready?: boolean | null;
    ready?: boolean;
    message?: string;
    install_url?: string;
    embed_model?: string;
    vision_model?: string;
    escalate_model?: string | null;
    cascade?: boolean;
    vision_timeout_sec?: number;
    escalate_timeout_sec?: number;
    pull?: Record<string, unknown>;
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
  } | null>(null);
  const [showWizard, setShowWizard] = useState(false);
  const [wizName, setWizName] = useState("");
  const [wizLib, setWizLib] = useState("");
  const [wizOut, setWizOut] = useState("");
  const [wizKw, setWizKw] = useState("");
  const [engineBusy, setEngineBusy] = useState(false);
  const [syncStatus, setSyncStatus] = useState<Record<string, unknown> | null>(null);
  const [newCustName, setNewCustName] = useState("");
  const [remoteFolderOptions, setRemoteFolderOptions] = useState<string[]>([]);
  const [remoteFolder, setRemoteFolder] = useState<string>("");
  const [remoteFoldersBusy, setRemoteFoldersBusy] = useState(false);
  const [editSourceCustomer, setEditSourceCustomer] = useState("");
  const [editRemoteFolder, setEditRemoteFolder] = useState("");
  const [syncDryRunBusy, setSyncDryRunBusy] = useState(false);
  const [syncDryRunHint, setSyncDryRunHint] = useState("");
  /** Session-only dismiss so 5s health poll won't reopen the first-run wizard. */
  const wizardDismissedRef = useRef(false);
  /** When true, refreshHealth must not overwrite local editable form fields. */
  const formSyncPausedRef = useRef(false);
  /** 5s health poll — heavy customer/keyword sync only every ~30s. */
  const healthTickRef = useRef(0);
  /** Throttle health-failure toasts so a down engine does not spam every 5s. */
  const healthNotifyRef = useRef({ lastAt: 0, lastText: "" });
  /** Bounded heal when control plane drops; no permanent abandon (agent KeepAlive is backstop). */
  const engineHealRef = useRef({ lastAttemptMs: 0, failCount: 0, busy: false });
  const pathsBaselineRef = useRef({ lib: "", libs: "", out: "", kw: "", cache: "", render: "", data: "" });
  const scheduleBaselineRef = useRef({ autoDaily: false, autoHour: 9 });

  const [cmdOpen, setCmdOpen] = useState(false);
  const density = layout.density === "compact" ? "compact" : "command";
  const [cinemaMode, setCinemaMode] = useState(false);
  const [reviewFocusId, setReviewFocusId] = useState<number | null>(null);
  const [dialog, setDialog] = useState<
    | {
        mode: "confirm";
        title: string;
        body: string;
        confirmLabel?: string;
        cancelLabel?: string;
        danger?: boolean;
        resolve: (v: boolean) => void;
      }
    | {
        mode: "prompt";
        title: string;
        body: string;
        placeholder?: string;
        defaultValue?: string;
        confirmLabel?: string;
        cancelLabel?: string;
        resolve: (v: string | null) => void;
      }
    | null
  >(null);

  const askConfirm = useCallback(
    (opts: {
      title: string;
      body: string;
      confirmLabel?: string;
      cancelLabel?: string;
      danger?: boolean;
    }) =>
      new Promise<boolean>((resolve) => {
        setDialog({ mode: "confirm", ...opts, resolve });
      }),
    [],
  );

  const askPrompt = useCallback(
    (opts: {
      title: string;
      body: string;
      placeholder?: string;
      defaultValue?: string;
      confirmLabel?: string;
    }) =>
      new Promise<string | null>((resolve) => {
        setDialog({ mode: "prompt", ...opts, resolve });
      }),
    [],
  );

  const pickDir = useCallback(async (): Promise<string | null> => {
    if (!isTauri()) {
      return askPrompt({
        title: "文件夹路径",
        body: "请输入文件夹绝对路径",
        placeholder: "/Users/…",
        confirmLabel: "使用此路径",
      });
    }
    const selected = await open({ directory: true, multiple: false });
    return typeof selected === "string" ? selected : null;
  }, [askPrompt]);

  const pickFile = useCallback(async (): Promise<string | null> => {
    if (!isTauri()) {
      return askPrompt({
        title: "本地文件",
        body: "请输入文件绝对路径",
        placeholder: "/path/to/file",
        confirmLabel: "使用此路径",
      });
    }
    const selected = await open({
      multiple: false,
      filters: [
        {
          name: "可用文件",
          extensions: ["json", "md", "txt", "wav", "m4a", "mp3", "aac", "flac"],
        },
      ],
    });
    return typeof selected === "string" ? selected : null;
  }, [askPrompt]);

  const pickImageFile = useCallback(async (): Promise<string | null> => {
    if (!isTauri()) {
      return askPrompt({
        title: "封面图片",
        body: "请输入封面图片路径 (.jpg/.png/.webp)",
        placeholder: "/path/to/cover.jpg",
        confirmLabel: "使用此路径",
      });
    }
    const selected = await open({
      multiple: false,
      filters: [{ name: "封面图", extensions: ["jpg", "jpeg", "png", "webp"] }],
    });
    return typeof selected === "string" ? selected : null;
  }, [askPrompt]);

  const pathsDirty = useMemo(() => {
    const b = pathsBaselineRef.current;
    return (
      libraryRoot !== b.lib ||
      libraryRootsText !== b.libs ||
      outputRoot !== b.out ||
      keywordPackPath !== b.kw ||
      cacheRoot !== b.cache ||
      renderRoot !== b.render ||
      dataRoot !== b.data
    );
  }, [libraryRoot, libraryRootsText, outputRoot, keywordPackPath, cacheRoot, renderRoot, dataRoot]);

  const scheduleDirty = useMemo(() => {
    const b = scheduleBaselineRef.current;
    return autoDaily !== b.autoDaily || autoHour !== b.autoHour;
  }, [autoDaily, autoHour]);

  useEffect(() => {
    formSyncPausedRef.current = pathsDirty || scheduleDirty;
  }, [pathsDirty, scheduleDirty]);

  useEffect(() => {
    void settingsPasswordStatus()
      .then(setPasswordStatus)
      .catch(() => undefined);
  }, [tab, customerName]);


  useEffect(() => {
    try {
      localStorage.setItem("suying.density.v1", density);
    } catch {
      /* ignore */
    }
  }, [density]);

  useEffect(() => {
    try {
      localStorage.setItem("suying.reviewFilter.v1", reviewFilter);
    } catch {
      /* ignore */
    }
  }, [reviewFilter]);

  useEffect(() => {
    const el = document.querySelector(".workspace");
    if (el instanceof HTMLElement) el.scrollTop = 0;
  }, [tab]);

  const notify = useCallback(
    (text: string, kind: FlashKind = "info", opts?: NotifyOptions) => {
      hubNotify(text, kind, opts);
    },
    [hubNotify],
  );

  const refreshEngine = useCallback(async () => {
    if (!isTauri()) {
      try {
        await api.health();
        setEngine({ running: true, healthy: true, pid: null, repo: "" });
      } catch {
        setEngine({ running: false, healthy: false, pid: null, repo: "" });
      }
      return;
    }
    setEngine(await getEngineStatus());
  }, []);

  const refreshHealth = useCallback(async (opts?: { syncCustomer?: boolean }) => {
    const syncCustomer = opts?.syncCustomer !== false;
    try {
      const h = await api.health();
      setHealth(h);
      setOllamaNarration(Boolean(h.ollama_narration_enabled));
      setOllamaNarrationEmoji(h.ollama_narration_burn_emoji !== false);
      setOllamaNarrationModel(String(h.ollama_narration_model || ""));
      if (h.workspace_state === "missing" || h.workspace_state === "mismatch" || h.status === "blocked") {
        setWorkspacePhase(h.workspace_state === "mismatch" ? "error" : "waiting_for_disk");
        setWorkspaceProbe({
          ok: false,
          state: String(h.workspace_state || "missing"),
          data_root: h.workspace?.data_root || (h.paths?.data_root ? String(h.paths.data_root) : null),
          reasons: h.workspace?.reasons || h.path_health?.errors || [],
          workspace_id: h.workspace?.workspace_id || null,
          volume_uuid: h.workspace?.volume_uuid || null,
          engine_status: h.status,
          engine_healthy: false,
        });
        setServices(null);
        return;
      }
      if (h.ollama) {
        setOllamaInfo((prev) => ({ ...(prev || {}), ...h.ollama }));
      }
      if (!formSyncPausedRef.current) {
        const ad = Boolean(h.auto_daily_enabled);
        const ah = Number(h.auto_daily_hour ?? 9);
        setAutoDaily(ad);
        setAutoHour(ah);
        scheduleBaselineRef.current = { autoDaily: ad, autoHour: ah };
        if (h.paths && typeof h.paths === "object") {
          if (h.paths.cache_root) setCacheRoot(String(h.paths.cache_root));
          if (h.paths.render_root) setRenderRoot(String(h.paths.render_root));
          if (h.paths.data_root) setDataRoot(String(h.paths.data_root));
        }
        if (h.render_root) setRenderRoot(String(h.render_root));
        if (h.vector_store && typeof h.vector_store === "object" && "db" in h.vector_store) {
          setVectorDbPath(String((h.vector_store as { db?: string }).db || ""));
        }
        pathsBaselineRef.current = {
          ...pathsBaselineRef.current,
          cache: h.paths?.cache_root ? String(h.paths.cache_root) : pathsBaselineRef.current.cache,
          render: String(h.render_root || h.paths?.render_root || pathsBaselineRef.current.render),
          data: h.paths?.data_root ? String(h.paths.data_root) : pathsBaselineRef.current.data,
        };
      }
      if (h.active_customer) setCustomerName(h.active_customer);
      if (h.active_customer_id) setActiveCustomerId(h.active_customer_id);
      if (syncCustomer) {
        const custs = await api.listCustomers();
        setCustomers(custs);
        const active = custs.find((c) => c.name === h.active_customer) ?? custs[0];
        if (active) {
          setActiveCustomerId(active.id);
          setCustomerName(active.name);
          const syncForms = !formSyncPausedRef.current;
          if (syncForms) {
            const lib = String(active.library_root || "");
            const libs = (active.library_roots || []).join("\n");
            const out = String(active.output_root || "");
            const kw = String(active.keyword_pack_path || "");
            setLibraryRoot(lib);
            setLibraryRootsText(libs);
            setOutputRoot(out);
            setKeywordPackPath(kw);
            setBrandLogo(brandFromProfile(active.profile || null));
            pathsBaselineRef.current = {
              ...pathsBaselineRef.current,
              lib,
              libs,
              out,
              kw,
            };
          }
          void api
            .keywordsActiveSummary(active.id)
            .then((s) =>
              setTitlePoolSummary({
                loaded: s.loaded,
                title_pool_count: s.title_pool_count,
                title_pool_sample: s.title_pool_sample || [],
                hooks_count: s.hooks_count,
                max_chars_per_line: s.max_chars_per_line,
                version: s.version,
                revision: s.revision,
                sha256: s.sha256,
                schema: s.schema,
              }),
            )
            .catch(() => setTitlePoolSummary(null));
          setWizName((prev) => prev || active.name);
          setWizLib((prev) => prev || String(active.library_root || ""));
          setWizOut((prev) => prev || String(active.output_root || ""));
          setWizKw((prev) => prev || String(active.keyword_pack_path || ""));
        }
        // First-run wizard only until setup is complete (never reopen every poll)
        if (h.onboarded) {
          setShowWizard(false);
        } else if (!wizardDismissedRef.current) {
          setShowWizard(true);
        }

      } else if (h.onboarded) {
        setShowWizard(false);
      }

      try {
        setServices(await api.servicesStatus());
      } catch {
        setServices(null);
      }
    } catch (e) {
      const text = String(e);
      const now = Date.now();
      const prev = healthNotifyRef.current;
      if (text !== prev.lastText || now - prev.lastAt > 60_000) {
        prev.lastText = text;
        prev.lastAt = now;
        notify(text, "err");
      }
      setHealth(null);
    }
  }, []);

  const refreshReach = useCallback(async () => {
    try {
      const [list, inbox, chrome, covers, platforms] = await Promise.all([
        api.reachList(),
        api.reachInbox(),
        api.reachChromeProfiles().catch(() => null),
        api.coverTemplatesList().catch(() => null),
        api.reachPlatforms().catch(() => null),
      ]);
      setReachItems(list.items || []);
      setReachInbox({ unread_count: inbox.unread_count || 0, notices: inbox.notices || [] });
      // Always merge API into built-in catalog — never shrink to old 3–5 platforms.
      setReachPlatforms(mergeReachPlatforms(platforms?.platforms));
      if (platforms?.slot_specs) {
        setCoverSlotSpecs(mergeCoverSlotSpecs(platforms.slot_specs));
      }
      if (chrome) {
        setChromeProfiles(chrome.profiles || []);
        setChromeRoot(chrome.root || "");
        setChromeInstalled(Boolean(chrome.chrome_installed));
        setChromeSelected(chrome.selected || chrome.profiles?.[0]?.name || "");
        if (chrome.selected_platform) {
          setChromeCreatePlatform(String(chrome.selected_platform));
        } else if (chrome.profiles?.[0]?.platform) {
          setChromeCreatePlatform(String(chrome.profiles[0].platform));
        }
      }
      if (covers) {
        setCoverTemplates(covers.templates || []);
        setCoverSelectedId(covers.selected_id ?? null);
        setCoverSlotCounts(mergeSlotCounts(covers.slot_counts || {}));
        if (covers.slot_specs) {
          setCoverSlotSpecs(mergeCoverSlotSpecs(covers.slot_specs));
        }
        setCoverEditId((prev) => prev || covers.selected_id || covers.templates?.[0]?.id?.toString() || "");
      }
    } catch {
      /* reach API may be unavailable on older engines */
    }
  }, []);

  const refreshReachMessages = useCallback(async (
    claimNotifications = false,
    query = reachMessageQueryRef.current,
  ) => {
    reachMessageQueryRef.current = query;
    try {
      const [accounts, messages, status] = await Promise.all([
        api.reachMessageAccounts(),
        api.reachMessages(query),
        api.reachMessageScanStatus(),
      ]);
      const messageRows = messages.messages || [];
      setReachMessageAccounts(accounts.accounts || []);
      setReachMessages(messageRows);
      setReachMessageUnread(
        typeof messages.unread_count === "number"
          ? messages.unread_count
          : messageRows.filter((message) => message.unread).length,
      );
      setReachMessageStatus(status.worker || null);
      setReachMessageBusy(Boolean(status.worker?.active));
      try {
        const ntfy = await api.reachNtfyConfig();
        setReachNtfyConfig(ntfy.config);
      } catch {
        setReachNtfyConfig(null);
      }
      if (claimNotifications) {
        const claimed = await api.reachMessageClaimNotifications();
        for (const message of claimed.messages || []) {
          notify(
            `${message.platform} · ${message.notification_sender || "新消息"}：${message.notification_summary || "请进入速影查看"} · ${message.reply_url}`,
            "info",
          );
          const appSound = playReachMessageSound();
          await api
            .reachNotificationReport(
              message.id,
              "app",
              appSound ? "sent" : "failed",
              appSound ? "" : "应用内提示音不可用",
            )
            .catch(() => undefined);
          await api
            .reachNotificationReport(message.id, "macos", "skipped_in_app_only")
            .catch(() => undefined);
        }
      }
    } catch {
      /* older engines */
    }
  }, [notify]);

  const refreshContentMessages = useCallback(async (
    query = contentMessageQueryRef.current,
  ) => {
    contentMessageQueryRef.current = query;
    try {
      const [accounts, messages, status] = await Promise.all([
        api.contentMessageAccounts(),
        api.contentMessages(query),
        api.contentMessageScanStatus(),
      ]);
      const messageRows = messages.messages || [];
      setContentMessageAccounts(accounts.accounts || []);
      setContentMessages(messageRows);
      setContentMessageUnread(
        typeof messages.unread_count === "number"
          ? messages.unread_count
          : messageRows.filter((m) => m.unread).length,
      );
      setContentMessageStatus(status.worker || null);
      setContentMessageBusy(Boolean(status.worker?.active));
    } catch {
      /* content message API may be unavailable */
    }
  }, []);

  const refreshSemanticStatus = useCallback(async () => {
    try {
      const st = await api.captionsStatus();
      setSemanticStatus(st);
      if (typeof st.full_backfill_enabled === "boolean") {
        setSemanticFullBackfill(st.full_backfill_enabled);
      }
      semanticPollFastRef.current = st.claimed > 0 || Boolean(st.full_backfill_enabled);
      return st;
    } catch {
      setSemanticStatus(null);
      semanticPollFastRef.current = false;
      return null;
    }
  }, []);

  const refreshAll = useCallback(async () => {
    await refreshHealth();
    try {
      setAssets(await api.listAssets());
      setJobs(await api.listJobs());
      setEvents(await api.listEvents());
      setOutputs(await api.listOutputs());
      try {
        setReport(await api.reportSummary());
      } catch {
        setReport(null);
      }
      try {
        setOpsReport(await api.reportOps());
      } catch {
        setOpsReport(null);
      }
      setCalendar(await api.listCalendar());
      try {
        const rr = await api.listReviewReasons();
        if (rr.reasons?.length) setReviewReasons(rr.reasons);
      } catch {
        /* older engines */
      }
      try {
        setTodayPlan(await api.calendarToday());
      } catch {
        setTodayPlan(null);
      }
      await refreshReach();
      await refreshReachMessages();
      await refreshContentMessages();
      try {
        broadcastContentChromeProfiles(await api.contentChromeProfiles());
      } catch (e) {
        notify(`软文登录态同步失败：${String(e)}`, "warn");
      }
      await refreshSemanticStatus();
      try {
        const st = await api.getSettings();
        const mode = String(st.semantic_analysis_mode || "on_demand").toLowerCase();
        setSemanticMode(mode === "off" ? "off" : "on_demand");
        setSemanticToggleEnabled(st.semantic_toggle_enabled !== false);
        setSemanticFullBackfill(st.semantic_full_backfill_enabled === true);
      } catch {
        /* older engines */
      }
      try {
        const vs = (await api.vectorizationStatus()) as {
          pending_assets?: number;
          pending_cliplets?: number;
        };
        const pending = Number(vs.pending_assets ?? vs.pending_cliplets ?? 0);
        setVectorPending(Number.isFinite(pending) ? Math.max(0, pending) : 0);
      } catch {
        setVectorPending(0);
      }
      try {
        const pr = await api.productionRulesList(
          false,
          category || "default",
          productionOrientation,
        );
        if (
          activeCustomerId != null &&
          pr.customer_id != null &&
          Number(pr.customer_id) !== Number(activeCustomerId)
        ) {
          // Stale response after customer switch — keep current tenant cards empty rather than leak.
          setRuleOptions([]);
          setActiveRuleSummary("未启用");
          setRuleRotation(true);
          setRotationPoolCount(0);
        } else {
          const rows = (pr.rules || []) as Array<Record<string, unknown>>;
          setRuleOptions(
            rows
              .filter((r) => String(r.status) === "approved")
              .map((r) => ({
                id: Number(r.id),
                contentCategory: String(r.content_category || "default"),
                label: `${String(r.name || "规则")} · r${Number(r.revision || 1)} · ${String(
                  r.content_category || "default",
                )}${
                  pr.active_by_category?.[String(r.content_category || "default")] === Number(r.id)
                    ? " · 该类别默认"
                    : ""
                }`,
              })),
          );
          const active = rows.find((r) => Number(r.id) === pr.active_id);
          const orientLabel =
            productionOrientation === "landscape" ? "横屏" : "竖屏";
          const cat = String(active?.content_category || category || "default");
          setActiveRuleSummary(
            active
              ? `${String(active.name)} · r${Number(active.revision || 1)} · ${orientLabel} · ${cat}`
              : "未启用",
          );
          setRuleRotation(pr.rotation_policy !== false);
          setRotationPoolCount((pr.rotation_pool || []).length);
        }
      } catch {
        setRuleOptions([]);
        setActiveRuleSummary("未启用");
        setRuleRotation(true);
        setRotationPoolCount(0);
      }
    } catch (e) {
      notify(String(e), "err");
    }
  }, [
    refreshHealth,
    refreshReach,
    refreshReachMessages,
    refreshContentMessages,
    refreshSemanticStatus,
    notify,
    productionOrientation,
    category,
    activeCustomerId,
  ]);

  // Boot once: never re-run heal loop when refreshAll/notify identity churns.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (isTauri()) {
        setEngineBusy(true);
        try {
          // GCustomerUX: bounded boot heal — retry start a few times after reboot/port races.
          let lastErr: unknown = null;
          for (let attempt = 1; attempt <= 3 && !cancelled; attempt++) {
            try {
              const st = await getEngineStatus();
              if (st.healthy) break;
              await startEngine();
              await new Promise((r) => window.setTimeout(r, 800 * attempt));
              const again = await getEngineStatus();
              if (again.healthy) break;
              lastErr = again.message || "引擎未就绪";
            } catch (e) {
              lastErr = e;
              await new Promise((r) => window.setTimeout(r, 600 * attempt));
            }
          }
          if (!cancelled) await refreshEngine();
          const finalSt = await getEngineStatus();
          if (!cancelled && !finalSt.healthy) {
            notify(
              `引擎启动失败（已重试）：${String(lastErr || finalSt.message || "请到运维页检查")}`,
              "err",
              { placement: "toast", actionTab: "ops" },
            );
          }
        } catch (e) {
          if (!cancelled) notify(`引擎启动: ${e}`, "err", { placement: "toast" });
        } finally {
          if (!cancelled) setEngineBusy(false);
        }
      }
      if (!cancelled) await refreshAll();
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only boot; polling is separate
  }, []);

  useEffect(() => {
    const t = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      healthTickRef.current += 1;
      // Poll budget: docs/APP_POLL_BUDGET.md — do not add ≤1s full polls here.
      void refreshHealth({
        syncCustomer: healthTickRef.current % POLL_TICK.customerSyncEvery === 0,
      });
      void refreshEngine().then(async () => {
        if (!isTauri()) return;
        const heal = engineHealRef.current;
        if (heal.busy) return;
        const st = await getEngineStatus();
        // Prefer control_plane (HTTP up) as heal success; business healthy may wait on disk.
        if (st.healthy || st.control_plane) {
          heal.failCount = 0;
          return;
        }
        const now = Date.now();
        if (now - heal.lastAttemptMs < 12_000) return;
        // Soft ceiling for toast only — keep retrying forever (reset every 20 attempts).
        heal.lastAttemptMs = now;
        heal.failCount += 1;
        if (heal.failCount > 20) heal.failCount = 1;
        heal.busy = true;
        setEngineBusy(true);
        try {
          await startEngine();
        } catch (e) {
          if (heal.failCount >= 3 && heal.failCount % 3 === 0) {
            const label = offlineClassLabel(st);
            notify(`引擎自愈失败（${label}）：${String(e)}`, "err", { placement: "toast", actionTab: "ops" });
          }
        } finally {
          heal.busy = false;
          setEngineBusy(false);
          await refreshEngine();
        }
      });
      void api
        .listJobs()
        .then((r) => setJobs(Array.isArray(r) ? r : []))
        .catch(() => undefined);
      if (healthTickRef.current % POLL_TICK.reportOpsEvery === 0) {
        void api
          .reportOps()
          .then((r) => setOpsReport(r))
          .catch(() => undefined);
      }
      if (healthTickRef.current % POLL_TICK.humanAlertsEvery === 0) {
        void api
          .humanAlerts()
          .then((r) => {
            for (const alert of r.alerts || []) {
              const kind = String(alert.kind || "");
              if (
                kind === "publish_soft_skip_exhausted" ||
                kind === "login_required" ||
                kind === "verification_required"
              ) {
                notify(String(alert.summary || "需要人工介入"), "err", {
                  placement: "toast",
                  action: notifyTargetFromDeepLink(alert.deep_link),
                  holdMs: 15_000,
                  sound: true,
                });
                void api.acknowledgeHumanAlert(Number(alert.id)).catch(() => undefined);
              }
            }
          })
          .catch(() => undefined);
      }
    }, POLL_BUDGET_MS.health);
    return () => {
      window.clearInterval(t);
    };
  }, [refreshEngine, refreshHealth, notify]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    let lastErrAt = 0;
    let lastErrText = "";
    void listenSystemState((snap) => {
      if (cancelled) return;
      const kind = snap.last_kind || "";
      if (snap.delivery_ok === false) {
        const text = String(snap.delivery_error || kind || "未知错误");
        const now = Date.now();
        // Rust already rate-limits emits; keep a UI floor so retries do not spam.
        if (text !== lastErrText || now - lastErrAt > 60_000) {
          lastErrText = text;
          lastErrAt = now;
          notify(`系统事件未被引擎确认：${text}`, "err");
        }
        return;
      }
      if (kind === "will_sleep" || kind === "screens_sleep" || kind === "session_inactive" || kind === "will_power_off") {
        notify(`系统事件：${kind} → 引擎已确认安全暂停`, "info");
      } else if (kind === "did_wake" || kind === "session_active" || kind === "screens_wake") {
        notify(`系统事件：${kind} → 引擎已确认恢复检查`, "info");
        window.setTimeout(() => {
          void refreshAll();
        }, 1200);
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [notify, refreshAll]);

  const applyWorkspaceSnapshot = useCallback((snap: WorkspaceStateSnapshot) => {
    setWorkspacePrefs(snap.prefs || WORKSPACE_DEFAULT_PREFS);
    setWorkspacePhase(snap.phase || "idle");
    setWorkspaceMessage(snap.message || null);
    setWorkspaceError(snap.error || null);
    if (snap.probe) setWorkspaceProbe(snap.probe);
  }, []);

  const runWorkspaceSync = useCallback(
    async (source: "manual" | "auto") => {
      if (workspaceSyncingRef.current) return;
      workspaceSyncingRef.current = true;
      setWorkspaceBusy(true);
      setWorkspaceError(null);
      setWorkspacePhase("checking");
      setWorkspaceMessage(source === "manual" ? "手动同步：检查工作区…" : "自动同步：检查工作区…");
      try {
        let snap = await reconnectWorkspaceNow();
        applyWorkspaceSnapshot(snap);
        if (snap.phase === "error" || snap.error) {
          const msg = String(snap.error || snap.message || "工作区同步失败");
          setWorkspacePhase("error");
          setWorkspaceError(msg);
          setWorkspaceMessage(null);
          notify(msg, "err");
          return;
        }
        if (snap.phase === "needs_engine_restart" || (snap.probe && !snap.probe.engine_healthy)) {
          setWorkspacePhase("reconnecting");
          setWorkspaceMessage("重启引擎以挂载权威工作区…");
          try {
            if (isTauri()) {
              await stopEngine().catch(() => undefined);
              await startEngine();
            }
          } catch (e) {
            const msg = String(e);
            setWorkspacePhase("error");
            setWorkspaceError(msg);
            setWorkspaceMessage(null);
            notify(msg, "err");
            return;
          }
          snap = await getWorkspaceSnapshot();
          applyWorkspaceSnapshot(snap);
          if (snap.phase === "error" || snap.error || (snap.probe && !snap.probe.engine_healthy)) {
            const msg = String(snap.error || snap.message || "引擎已重启，但工作区仍未就绪");
            setWorkspacePhase("error");
            setWorkspaceError(msg);
            setWorkspaceMessage(null);
            notify(msg, "err");
            return;
          }
        }
        setWorkspacePhase("refreshing");
        setWorkspaceMessage("刷新 App 数据…");
        await refreshAll();
        await refreshEngine();
        setWorkspacePhase("ready");
        setWorkspaceMessage("工作区已同步");
        notify(source === "manual" ? "已重新连接工作区并刷新数据" : "路径变化后已自动刷新工作区", "ok");
      } catch (e) {
        const msg = String(e);
        setWorkspacePhase("error");
        setWorkspaceError(msg);
        setWorkspaceMessage(null);
        notify(msg, "err");
      } finally {
        workspaceSyncingRef.current = false;
        setWorkspaceBusy(false);
      }
    },
    [applyWorkspaceSnapshot, notify, refreshAll, refreshEngine],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const prefs = await getWorkspaceSyncPrefs();
      if (!cancelled) setWorkspacePrefs(prefs);
      const snap = await getWorkspaceSnapshot();
      if (!cancelled) applyWorkspaceSnapshot(snap);
    })();
    return () => {
      cancelled = true;
    };
  }, [applyWorkspaceSnapshot]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void listenWorkspaceState((snap) => {
      if (cancelled) return;
      applyWorkspaceSnapshot(snap);
      const kind = snap.last_kind || "";
      if (kind === "did_unmount") {
        // Only warn when authority lives on a removable volume (not main-disk local).
        const st = snap.probe?.state || "";
        if (st && st !== "local") {
          notify("外接路径已卸载，请改回主盘工作区或重新接入路径", "warn");
        }
        return;
      }
      if (kind === "did_mount" && snap.prefs?.auto_reconnect_on_mount) {
        if (snap.phase === "ready" && snap.probe?.ok && !workspaceSyncingRef.current) {
          // Native side already probed; finish with engine restart + refresh if needed.
          if (snap.probe && !snap.probe.engine_healthy) {
            void runWorkspaceSync("auto");
          } else if (snap.message && /已同步|就绪|已刷新/.test(snap.message)) {
            void (async () => {
              setWorkspacePhase("refreshing");
              await refreshAll();
              setWorkspacePhase("ready");
              notify("路径变化后已自动刷新工作区", "ok");
            })();
          }
        } else if (snap.phase === "needs_engine_restart") {
          void runWorkspaceSync("auto");
        }
      }
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [applyWorkspaceSnapshot, notify, refreshAll, runWorkspaceSync]);

  const onWorkspaceToggleAuto = useCallback(
    async (enabled: boolean) => {
      const next = { ...workspacePrefs, auto_reconnect_on_mount: enabled };
      setWorkspacePrefs(next);
      try {
        const saved = await setWorkspaceSyncPrefs(next);
        setWorkspacePrefs(saved);
        notify(enabled ? "已开启路径变化时自动重连" : "已关闭路径变化时自动重连", "info");
      } catch (e) {
        notify(String(e), "err");
      }
    },
    [notify, workspacePrefs],
  );

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (document.visibilityState === "hidden") return;
      if (!cancelled) {
        await refreshReachMessages(true);
        await refreshContentMessages();
      }
    };
    const timer = window.setInterval(() => void tick(), POLL_BUDGET_MS.messages);
    const onVis = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [refreshReachMessages, refreshContentMessages]);

  // Publish / account login sync on open + when returning to foreground.
  // Force video + content login probes (list endpoints already DOM-probe when Chrome is managed).
  useEffect(() => {
    const syncPublishState = () => {
      if (document.visibilityState !== "visible") return;
      void (async () => {
        try {
          await Promise.all([
            refreshReach(),
            refreshContentMessages(),
            api
              .contentChromeProfiles()
              .then((snapshot) => {
                broadcastContentChromeProfiles(snapshot);
                return snapshot;
              })
              .catch((e: unknown) => {
                notify(`软文登录态同步失败：${String(e)}`, "warn");
                return null;
              }),
          ]);
        } catch (e) {
          notify(`发布登录态同步失败：${String(e)}`, "warn");
        }
      })();
    };
    document.addEventListener("visibilitychange", syncPublishState);
    window.addEventListener("focus", syncPublishState);
    syncPublishState();
    return () => {
      document.removeEventListener("visibilitychange", syncPublishState);
      window.removeEventListener("focus", syncPublishState);
    };
  }, [notify, refreshReach, refreshContentMessages]);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const tick = async () => {
      if (cancelled || !engine?.healthy) return;
      await refreshSemanticStatus();
      if (!cancelled) {
        timer = window.setTimeout(
          tick,
          semanticPollFastRef.current
            ? POLL_BUDGET_MS.semanticFast
            : POLL_BUDGET_MS.semanticIdle,
        );
      }
    };
    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [engine?.healthy, refreshSemanticStatus]);

  useEffect(() => {
    let cancelled = false;
    void getNotificationPermission().then((state) => {
      if (!cancelled) setNotificationPermission(state);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const FALLBACK = [
      { code: "zh", label_zh: "中文（简体）", label_native: "简体中文", region: "东亚" },
      { code: "en", label_zh: "英语", label_native: "English", region: "欧美" },
      { code: "none", label_zh: "关闭", label_native: "Off", region: "其他" },
    ];
    void api
      .listExpressionLanguages()
      .then((res) => {
        if (cancelled) return;
        if (Array.isArray(res.languages) && res.languages.length > 0) {
          setLangCatalog(res.languages);
        }
      })
      .catch(() => {
        if (!cancelled) {
          // Only seed tiny fallback when we still have no full catalog.
          setLangCatalog((prev) => (prev.length > 3 ? prev : FALLBACK));
        }
      });
    return () => {
      cancelled = true;
    };
    // Re-fetch after engine comes up; boot often races the language catalog API.
  }, [engine?.healthy]);

  async function onEngineToggle() {
    if (engine?.healthy) {
      {
        const stopOk = await askConfirm({
          title: "停止引擎",
          body: "停止引擎将中断正在进行的入库/渲染。确定？",
          confirmLabel: "停止",
          danger: true,
        });
        if (!stopOk) return;
      }
    }
    setEngineBusy(true);
    try {
      if (engine?.healthy) {
        await stopEngine();
      } else {
        const st = await startEngine();
        if (st.message && !st.healthy) {
          notify(st.message, "err");
        }
      }
      await refreshEngine();
      await refreshHealth();
    } catch (e) {
      notify(String(e), "err");
      await refreshEngine();
    } finally {
      setEngineBusy(false);
    }
  }

  async function savePaths() {
    setActionBusy("savePaths");
    try {
      const extra = libraryRootsText
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      if (activeCustomerId) {
        await api.updateCustomer(activeCustomerId, {
          library_root: libraryRoot,
          library_roots: extra,
          output_root: outputRoot,
          keyword_pack_path: keywordPackPath || null,
        });
      }
      await api.updateSettings({
        // Local-first: never re-force external disk when saving paths.
        external_required: false,
        auto_daily_enabled: autoDaily,
        auto_daily_hour: autoHour,
        ...(cacheRoot.trim() ? { cache_root: cacheRoot.trim() } : {}),
        ...(renderRoot.trim() ? { render_root: renderRoot.trim() } : {}),
        ...(dataRoot.trim() ? { data_root: dataRoot.trim() } : {}),
      });
      await refreshAll();
      pathsBaselineRef.current = {
        lib: libraryRoot,
        libs: libraryRootsText,
        out: outputRoot,
        kw: keywordPackPath,
        cache: cacheRoot,
        render: renderRoot,
        data: dataRoot,
      };
      scheduleBaselineRef.current = { autoDaily, autoHour };
      formSyncPausedRef.current = false;
      notify("路径与调度设置已保存", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function refreshOllamaDetail() {
    setActionBusy("ollama");
    try {
      const r = await api.ollamaHealth();
      setOllamaInfo(r);
      notify(r.message || "本地 AI 状态已刷新", r.ready ? "ok" : "warn");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function pullOllamaModel(model: string) {
    setActionBusy("ollamaPull");
    try {
      const r = await api.ollamaPull(model);
      notify(r.message, r.ok ? "ok" : "warn");
      for (let i = 0; i < 24; i++) {
        await new Promise((res) => setTimeout(res, 2500));
        const st = await api.ollamaHealth();
        setOllamaInfo(st);
        if (!st.pull?.running) break;
      }
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function pullRecommendedModels() {
    const ok = await askConfirm({
      title: "按本机配置安装模型",
      body:
        (ollamaInfo?.recommended?.reason || "将按内存档位选择向量与视觉模型。") +
        "\n\n会依次下载，可能需要数分钟到十几分钟，期间请保持网络畅通。",
      confirmLabel: "开始安装",
    });
    if (!ok) return;
    setActionBusy("ollamaPull");
    try {
      const r = await api.ollamaPullRecommended();
      notify(r.message, r.ok ? "ok" : "warn");
      for (let i = 0; i < 60; i++) {
        await new Promise((res) => setTimeout(res, 3000));
        const st = await api.ollamaHealth();
        setOllamaInfo(st);
        if (!st.pull?.running) {
          notify(String(st.pull?.message || st.message || "安装结束"), st.ready ? "ok" : "warn");
          break;
        }
      }
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function finishWizard(enableVec: boolean, seedId?: string) {
    if (!wizName.trim()) {
      notify("请填写客户名", "err");
      return;
    }
    try {
      const st = await api.zspaceSyncStatus();
      if (!st.zspace_match) {
        notify(
          `未绑定匹配的 T2S，不可完成安装：${String(st.zspace_reason || "请先登录极空间并绑定")}`,
          "err",
        );
        return;
      }
    } catch (e) {
      notify(`无法校验极空间绑定: ${e}`, "err");
      return;
    }
    // 主盘标准布局：~/Movies/速影工作区/速影客户/<名>/…（默认同步仅载体）
    let lib = wizLib.trim();
    let out = wizOut.trim();
    let kw = wizKw.trim();
    try {
      const ensured = await api.zspaceEnsureCustomer(wizName.trim(), true);
      const p = ensured.paths || {};
      if (!lib) lib = String(p.library_root || "");
      if (!out) out = String(p.output_root || "");
      if (!kw && p.keyword_pack_path) kw = String(p.keyword_pack_path);
      setWizLib(lib);
      setWizOut(out);
      if (kw) setWizKw(kw);
    } catch (e) {
      // 自动建目录失败时仍允许手填路径
      if (!lib || !out) {
        notify(`本机工作区目录创建失败（可手填路径）: ${e}`, "err");
        return;
      }
    }
    if (!lib || !out) {
      notify("请填写片库与输出目录，或留空由向导在主盘 Movies 工作区自动创建", "err");
      return;
    }
    const c = await api.createCustomer({
      name: wizName.trim(),
      library_root: lib,
      output_root: out,
      keyword_pack_path: kw || undefined,
    });
    await api.activateCustomer(c.name);
    if (seedId) {
      try {
        const seeded = await api.importCarrierSeed(seedId, false);
        if (seeded.keyword_pack_path) {
          kw = seeded.keyword_pack_path;
          setWizKw(kw);
        }
        notify(
          `已导入客户配置种子：${seeded.copied.length} 项，跳过已有 ${seeded.skipped.length} 项`,
          "ok",
        );
      } catch (e) {
        notify(`客户已创建，但配置种子导入失败：${e}`, "warn");
      }
    }
    if (kw) {
      await api.updateCustomer(c.id, { keyword_pack_path: kw });
      try {
        await api.reloadKeywordsFromPath(c.id);
      } catch (e) {
        notify(`词池导入失败: ${e}`, "err");
      }
    }
    try {
      await api.carrierEnsure();
    } catch {
      /* optional */
    }
    try {
      await api.carrierInstallUpdateAgent();
    } catch {
      /* optional — 运维页可重装 */
    }
    await api.updateSettings({
      onboarded: true,
      active_customer: c.name,
    });
    setShowWizard(false);
    if (enableVec) {
      try {
        const r = await api.enableVectorization(true, 50);
        const gaps = r.gaps || {};
        notify(
          `${r.message} 已完成 ${gaps.complete_assets ?? "?"} / 就绪 ${gaps.ready_assets ?? "?"}，缺口 ${gaps.gap_assets ?? "?"}。`,
          "ok",
        );
      } catch (e) {
        notify(`配置已保存，但开启向量化失败: ${e}`, "warn");
      }
    } else {
      notify(`客户「${c.name}」已就绪（T2S 仅载体；片库在本机）`, "ok");
    }
    await refreshAll();
  }

  async function applyStandardLayout() {
    if (!wizName.trim()) {
      notify("请先填写客户名称", "err");
      return;
    }
    try {
      const ensured = await api.zspaceEnsureCustomer(wizName.trim(), true);
      const p = ensured.paths || {};
      setWizLib(String(p.library_root || ""));
      setWizOut(String(p.output_root || ""));
      setWizKw(String(p.keyword_pack_path || wizKw));
      notify(`已创建标准目录并登记同步：${p.customer_root}`, "ok");
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function switchCustomer(name: string) {
    if (
      actionBusy ||
      reviewBusyId != null ||
      packBusyId != null ||
      batchBusy ||
      brandBusy ||
      chromeBusy ||
      coverBusy ||
      queueBusy ||
      reachMessageBusy ||
      engineBusy
    ) {
      notify("有操作进行中，请稍后再切换客户", "warn");
      return;
    }
    if (pathsDirty || scheduleDirty) {
      const ok = await askConfirm({
        title: "未保存的更改",
        body: "当前有未保存的路径或调度修改。切换客户将丢弃这些本地未保存更改。",
        confirmLabel: "仍要切换",
        cancelLabel: "留下",
        danger: true,
      });
      if (!ok) return;
      formSyncPausedRef.current = false;
    }
    setActionBusy("switchCustomer");
    try {
      // Drop stale cards immediately so preview URLs aren't hit under the new active customer
      setOutputs([]);
      setAssets([]);
      setJobs([]);
      activeJobIdsRef.current.clear();
      notifiedOutputIdsRef.current.clear();
      prevRunningJobsRef.current = null;
      setReachMessageAccounts([]);
      setReachMessages([]);
      setReachMessageUnread(0);
      setReachNtfyConfig(null);
      setReachMessageStatus(null);
      setContentMessageAccounts([]);
      setContentMessages([]);
      setContentMessageUnread(0);
      setContentMessageStatus(null);
      setContentReplyDrafts([]);
      setContentReplyDraftMessageId(null);
      setReplyDrafts([]);
      setReplyDraftMessageId(null);
      setChromeSelected("");
      setMediaEpoch((n) => n + 1);
      setJobRuleProfileId(null);
      setActiveRuleSummary("");
      setRuleOptions([]);
      setRuleRotation(true);
      setRotationPoolCount(0);
      try {
        const locked = await settingsPasswordLock();
        setPasswordStatus(locked);
      } catch {
        /* ignore */
      }
      const act = await api.activateCustomer(name);
      setCustomerName(act.active_customer || name);
      if (act.active_customer_id) setActiveCustomerId(act.active_customer_id);
      notify(`已切换客户：${act.active_customer || name}`, "ok");
      await refreshAll();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function saveBrandLogo(patch: Partial<BrandLogoState>) {
    if (!activeCustomerId) {
      notify("请先选择客户", "err");
      return;
    }
    const next = { ...brandLogo, ...patch };
    setBrandLogo(next);
    setBrandBusy(true);
    const quiet = !("logo_enabled" in patch || "logo_position" in patch);
    try {
      const updated = await api.updateCustomer(activeCustomerId, { brand: next });
      setCustomers((prev) => prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)));
      if (updated.profile) setBrandLogo(brandFromProfile(updated.profile));
      if (!quiet) notify("品牌标识已保存", "ok");
    } catch (e) {
      notify(String(e), "err");
      await refreshAll();
    } finally {
      setBrandBusy(false);
    }
  }

  async function runDryRun() {
    setActionBusy("dryRun");
    try {
      const body: Record<string, unknown> = {
        customer_name: customerName,
        theme,
        content_category: category,
        category,
        orientation: productionOrientation,
        use_active_rule: true,
        rule_rotation: ruleRotation,
      };
      if (assetCategory.trim()) {
        body.asset_category = assetCategory.trim();
      }
      if (topicMode) {
        body.topic_intent = {
          mode: topicMode,
          cluster_ids: topicClusterIds.split(",").map(Number).filter((id) => id > 0),
          official_evidence_ids: topicEvidenceIds.split(",").map(Number).filter((id) => id > 0),
          similarity_threshold: 0.82,
          requested_uses: [],
        };
      }
      const result = await api.dryRun(body);
      setDryResult(result);
      const n = Array.isArray(result.candidates)
        ? result.candidates.length
        : Number(result.candidate_count ?? result.count ?? 0);
      notify(n > 0 ? `选片预览完成：约 ${n} 条候选` : "选片预览完成", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function createJob(opts?: { rush?: boolean; count?: number }) {
    const rush = Boolean(opts?.rush);
    const count = opts?.count ?? targetCount;
    setActionBusy(rush ? "rushJob" : "createJob");
    try {
      const body: Record<string, unknown> = {
        mode: "count",
        target_count: count,
        customer_name: customerName,
        theme,
        content_category: category,
        category,
        orientation: productionOrientation,
        use_active_rule: true,
        rush,
        rule_rotation: ruleRotation,
      };
      if (assetCategory.trim()) {
        body.asset_category = assetCategory.trim();
      }
      if (topicMode) {
        body.topic_intent = {
          mode: topicMode,
          cluster_ids: topicClusterIds.split(",").map(Number).filter((id) => id > 0),
          official_evidence_ids: topicEvidenceIds.split(",").map(Number).filter((id) => id > 0),
          similarity_threshold: 0.82,
          requested_uses: [],
        };
      }
      const created = await api.createJob(body);
      const jobId = Number(created.id);
      const ahead = Number(created.queued_ahead ?? 0);
      const runningId = created.running_job_id;
      void jobId;
      if (rush) {
        notify(
          runningId
            ? `立即生产：已插队（当前条收尾后开跑）· 目标 ${count} 条 · 自动过审`
            : `立即生产：即将开跑 · 目标 ${count} 条 · 自动过审`,
          "ok",
        );
      } else if (ahead > 0 || runningId) {
        notify(
          `生产已入队：前面还有 ${ahead} 条排队` +
            (runningId ? "，当前有任务在跑" : "") +
            `。点「立即生产 1 条」可插队。`,
          "warn",
        );
      } else {
        notify(`生产已开始：目标 ${count} 条（门禁通过后自动过审）`, "ok");
      }
      setTab("produce");
      await refreshAll();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function createJobQuick() {
    setTargetCount(1);
    await createJob({ rush: true, count: 1 });
  }

  async function createSceneTourJob() {
    setActionBusy("sceneTourJob");
    try {
      const body: Record<string, unknown> = {
        mode: "count",
        target_count: 1,
        customer_name: customerName,
        theme: "scene_tour",
        content_category: "scene_tour",
        category: "scene_tour",
        orientation: productionOrientation,
        use_active_rule: true,
        rush: true,
        rule_rotation: false,
      };
      const created = await api.createJob(body);
      const runningId = created.running_job_id;
      notify(
        runningId
          ? "跟镜精品已插队：当前条收尾后开跑；旁白不走词池金句"
          : "跟镜精品即将开跑；旁白不走词池金句",
        "ok",
      );
      setTab("produce");
      await refreshAll();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function decideReview(id: number, status: "approved" | "rejected", rerender = false) {
    setReviewBusyId(id);
    try {
      const row = outputs.find((o) => Number(o.id) === id);
      const label =
        String(row?.display_label || "").trim() ||
        (row?.display_no != null ? `#${String(row.display_no).padStart(3, "0")}` : "成片");
      const result = await api.reviewOutput(id, status, reviewNote, {
        reason: status === "rejected" ? reviewReason : undefined,
        rerender: status === "rejected" && rerender,
      });
      let packReady = false;
      let packError = "";
      if (status === "approved") {
        try {
          await api.exportPublishPack(id);
          packReady = true;
        } catch (e) {
          packError = String(e);
        }
      }
      setReviewNote("");
      const human = Boolean((result as { human_review?: boolean } | undefined)?.human_review);
      const purged =
        status === "rejected"
          ? Number((result as { purge?: { removed?: number } } | undefined)?.purge?.removed || 0)
          : 0;
      const msg =
        status === "approved"
          ? packReady
            ? `${label} 已通过并准备好发布物料`
            : `${label} 已通过，但发布物料准备失败：${packError}`
          : `${label} 未通过：已删除成片并释放素材占用${purged ? `（清理 ${purged} 项文件）` : ""}${
              human ? "；需人工复核" : ""
            }`;
      notify(
        status === "approved" ? `${msg}（仍留在审片，可继续下一条）` : msg,
        status === "approved" && !packReady ? "warn" : "ok",
        status === "approved"
          ? {
              action: {
                tab: "publish",
                section: "pack",
                guideTarget: `publish-output-${id}`,
              },
              holdMs: 15_000,
            }
          : {
              holdMs: 12_000,
            },
      );
      await refreshAll();
      setMediaEpoch((n) => n + 1);
      // Stay on review for batch cinema workflow (plan §9)
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReviewBusyId(null);
    }
  }

  async function rerenderOnly(id: number) {
    setReviewBusyId(id);
    try {
      const row = outputs.find((o) => Number(o.id) === id);
      const label =
        String(row?.display_label || "").trim() ||
        (row?.display_no != null ? `#${String(row.display_no).padStart(3, "0")}` : "成片");
      await api.rerenderOutput(id, reviewReason);
      notify(`${label} 已排队重渲`, "ok");
      await refreshAll();
      setMediaEpoch((n) => n + 1);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReviewBusyId(null);
    }
  }

  async function exportPack(id: number) {
    setPackBusyId(id);
    setPackLast("");
    try {
      const res = await api.exportPublishPack(id);
      const dir = String((res.manifest || {}).pack_dir || "");
      const expr = (res.manifest?.expression || {}) as Record<string, unknown>;
      const ok = Boolean(res.manifest?.compliance_passed !== false);
      const exprHint = expr.subtitle_lang
        ? ` · 旁白 ${String(expr.voice_lang)} / 字幕 ${String(expr.subtitle_lang)} / ${String(expr.subtitle_burn)}${
            String(expr.subtitle_burn) === "burn_dual"
              ? `+${String(expr.dual_secondary_lang || "")}`
              : ""
          }`
        : "";
      const burnIntent = (res.manifest?.subtitle_burn_intent || {}) as Record<string, unknown>;
      const burned = Boolean(burnIntent.burned);
      const burnNote = burned ? " · 已烧录 video.burned.mp4" : "";
      const summary = ok
        ? `已导出：${dir}${exprHint}${burnNote}`
        : `已导出但合规未过：${dir}${exprHint}${burnNote}`;
      setPackLast(summary);
      {
        const row = outputs.find((o) => Number(o.id) === id);
        const label =
          String(row?.display_label || "").trim() ||
          (row?.display_no != null ? `#${String(row.display_no).padStart(3, "0")}` : "成片");
        notify(
          ok
            ? burned
              ? `${label} 物料包已导出并烧录字幕`
              : `${label} 物料包已导出`
            : `${label} 物料已导出但合规未过`,
          ok ? "ok" : "warn",
        );
      }
      if (dir) setReachPackDir(dir);
      await refreshAll();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setPackBusyId(null);
    }
  }

  async function reachEnqueueFromPack() {
    if (!reachPackDir.trim()) {
      notify("请填写 publish_pack 目录路径", "err");
      return;
    }
    setQueueBusy(true);
    setReachMsg("");
    try {
      const res = await api.reachFromPack({ pack_dir: reachPackDir.trim() });
      setReachMsg(`已入队 ${res.count} 条（默认人点发布；可选 Safari 辅助）`);
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setQueueBusy(false);
    }
  }

  async function reachOpenItem(id: number) {
    setQueueBusy(true);
    try {
      const res = await api.reachOpen(id, false, chromeSelected || undefined);
      setReachMsg(res.disclaimer || `已打开官方入口；粘贴卡：${res.paste_card || ""}`);
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setQueueBusy(false);
    }
  }

  async function reachMarkPublished(id: number) {
    try {
      await api.reachSetStatus(id, "published", "human_confirmed");
      notify("该条触达已标记为已发布", "ok");
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function reachMessageScanStart() {
    setReachMessageBusy(true);
    try {
      const res = await api.reachMessageScanStart();
      setReachMessageStatus({
        active: res.queued > 0,
        phase: res.queued > 0 ? "queued" : "idle",
        message: res.queued > 0 ? `已排队 ${res.queued} 个账号` : "没有新增待检查账号",
      });
      notify("已启动本人账号后台串行消息检查", "info");
      window.setTimeout(() => void refreshReachMessages(true), 1500);
    } catch (e) {
      setReachMessageBusy(false);
      notify(String(e), "err");
    }
  }

  async function reachMessageScanCancel() {
    try {
      await api.reachMessageScanCancel();
      notify("已请求停止消息检查", "info");
      await refreshReachMessages(false);
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function reachMessageOpen(message: ReachMessage) {
    try {
      const res = await api.reachMessageOpen(message.id);
      notify(
        res.opened?.opened ? "已用绑定账号打开官方消息页" : "已请求打开官方消息页",
        "ok",
      );
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function reachMessageRead(message: ReachMessage) {
    setReachMessages((current) => current.filter((item) => item.id !== message.id));
    setReachMessageUnread((current) => Math.max(0, current - (message.unread ? 1 : 0)));
    try {
      await api.reachMessageRead(message.id);
    } catch (e) {
      await refreshReachMessages(false);
      notify(String(e), "err");
    }
  }

  async function reachMessageCreateAccount(platform: string, displayName: string) {
    try {
      await api.reachMessageAccountCreate({ platform, display_name: displayName });
      notify(`已创建视频消息账号“${displayName}”`, "ok");
      await refreshReachMessages(false);
    } catch (e) {
      notify(String(e), "err");
      throw e;
    }
  }

  async function reachMessageOpenAccount(account: ReachMessageAccount) {
    try {
      await api.reachMessageAccountOpen(account);
      notify("已打开视频消息账号登录页", "ok");
    } catch (e) {
      notify(String(e), "err");
      throw e;
    }
  }

  async function reachMessageDeleteAccount(account: ReachMessageAccount) {
    try {
      await api.reachMessageAccountsDelete([account.id]);
      notify(`已删除视频消息账号“${account.display_name}”`, "ok");
      await refreshReachMessages(false);
    } catch (e) {
      notify(String(e), "err");
      throw e;
    }
  }

  async function reachMessagesReadAll(accountId?: number) {
    try {
      await api.reachMessagesReadAll(accountId);
      await refreshReachMessages(false);
    } catch (e) {
      notify(String(e), "err");
      throw e;
    }
  }

  async function suggestReplyDrafts(message: ReachMessage, extraContext: string) {
    setReachMessageBusy(true);
    try {
      const res = await api.reachReplyDrafts(message.id, extraContext);
      setReplyDraftMessageId(message.id);
      setReplyDrafts(res.drafts || []);
      notify(res.warning || "已生成回复草稿（不会自动发送）", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachMessageBusy(false);
    }
  }

  async function copyReplyDraft(draftId: number, body: string) {
    try {
      await navigator.clipboard.writeText(body);
      await api.reachMarkReplyCopied(draftId);
      notify("已复制回复草稿，请到官方页粘贴发送", "ok");
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function contentMessageScanStart() {
    setContentMessageBusy(true);
    try {
      const res = await api.contentMessageScanStart();
      setContentMessageStatus({
        active: res.queued > 0,
        phase: res.queued > 0 ? "queued" : "idle",
        message: res.queued > 0 ? `已排队 ${res.queued} 个软文账号` : "没有新增待检查账号",
      });
      notify("已启动软文消息检查", "info");
      window.setTimeout(() => void refreshContentMessages(), 1500);
    } catch (e) {
      setContentMessageBusy(false);
      notify(String(e), "err");
    }
  }

  async function contentMessageScanCancel() {
    try {
      await api.contentMessageScanCancel();
      notify("已请求停止软文消息检查", "info");
      await refreshContentMessages();
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function contentMessageOpen(message: ReachMessage) {
    try {
      const res = await api.contentMessageOpen(message.id);
      notify(
        res.opened?.opened ? "已用软文账号打开官方消息页" : "已请求打开官方消息页",
        "ok",
      );
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function contentMessagePreview(message: ReachMessage) {
    setContentMessages((current) => current.filter((item) => item.id !== message.id));
    setContentMessageUnread((current) => Math.max(0, current - (message.unread ? 1 : 0)));
    try {
      await api.contentMessageRead(message.id);
    } catch (e) {
      await refreshContentMessages();
      notify(String(e), "err");
    }
  }

  async function contentMessageCreateAccount(platform: string, displayName: string) {
    try {
      await api.contentMessageAccountCreate({ platform, display_name: displayName });
      notify(`已创建软文消息账号“${displayName}”`, "ok");
      await refreshContentMessages();
    } catch (e) {
      notify(String(e), "err");
      throw e;
    }
  }

  async function contentMessageOpenAccount(account: ReachMessageAccount) {
    try {
      await api.contentMessageAccountOpen(account);
      notify("已打开软文消息账号登录页", "ok");
    } catch (e) {
      notify(String(e), "err");
      throw e;
    }
  }

  async function contentMessageDeleteAccount(account: ReachMessageAccount) {
    try {
      await api.contentMessageAccountsDelete([account.id]);
      notify(`已删除软文消息账号“${account.display_name}”`, "ok");
      await refreshContentMessages();
    } catch (e) {
      notify(String(e), "err");
      throw e;
    }
  }

  async function contentMessagesReadAll(accountId?: number) {
    try {
      await api.contentMessagesReadAll(accountId);
      await refreshContentMessages();
    } catch (e) {
      notify(String(e), "err");
      throw e;
    }
  }

  async function contentMessageToggleAccount(account: ReachMessageAccount) {
    try {
      await api.contentMessageAccountUpdate(account.id, { enabled: !account.enabled });
      await refreshContentMessages();
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function suggestContentReplyDrafts(message: ReachMessage, extraContext: string) {
    setContentMessageBusy(true);
    try {
      const res = await api.contentReplyDrafts(message.id, extraContext);
      setContentReplyDraftMessageId(message.id);
      setContentReplyDrafts(res.drafts || []);
      notify(res.warning || "已生成软文回复草稿（不会自动发送）", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setContentMessageBusy(false);
    }
  }

  async function copyContentReplyDraft(draftId: number, body: string) {
    try {
      await navigator.clipboard.writeText(body);
      await api.contentMarkReplyCopied(draftId);
      notify("已复制软文回复草稿，请到官方页粘贴发送", "ok");
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function reachMessageToggleAccount(account: ReachMessageAccount) {
    try {
      await api.reachMessageAccountUpdate(account.id, { enabled: !account.enabled });
      await refreshReachMessages(false);
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function enableReachNotifications() {
    const state = await ensureNotificationPermission();
    setNotificationPermission(state);
    if (state === "granted") {
      notify("平台消息系统提醒已开启", "ok");
      await refreshReachMessages(true);
    } else if (state === "denied") {
      notify("系统通知权限被拒绝，请到系统设置中开启", "warn");
    } else {
      notify("浏览器预览模式不支持系统通知", "info");
    }
  }

  async function saveReachNtfy(body: {
    enabled: boolean;
    server_url: string;
    topic: string;
    auth_mode: "none" | "token" | "basic";
    token?: string;
    username?: string;
    password?: string;
  }) {
    const result = await api.reachNtfySave(body);
    setReachNtfyConfig(result.config);
    notify("ntfy 推送配置已安全保存", "ok");
  }

  async function testReachNtfy() {
    await api.reachNtfyTest();
    notify("ntfy 测试通知已发送", "ok");
  }

  function chromeProfilePlatform(name: string): string | undefined {
    const hit = chromeProfiles.find((p) => p.name === name);
    return hit?.platform || undefined;
  }

  async function reachSelectChromeProfile(name: string) {
    setChromeSelected(name);
    if (!name) return;
    setChromeBusy(true);
    try {
      const plat = chromeProfilePlatform(name);
      const res = await api.reachChromeSelect(name, plat);
      if (res.platform) setChromeCreatePlatform(res.platform);
      setReachMsg(`已选配置「${name}」${res.platform ? ` · 平台 ${res.platform}` : ""}`);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setChromeBusy(false);
    }
  }

  async function reachOpenChromeProfile() {
    if (!chromeSelected) {
      notify("请先选择 Chrome 本地配置", "err");
      return;
    }
    setChromeBusy(true);
    try {
      const plat = chromeProfilePlatform(chromeSelected) || chromeCreatePlatform;
      await api.reachChromeSelect(chromeSelected, plat);
      const res = await api.reachChromeOpen(chromeSelected, plat, false);
      setReachMsg(res.disclaimer || `已用「${chromeSelected}」打开 ${res.platform || plat} 官方页`);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setChromeBusy(false);
    }
  }

  async function reachBindMessageAccount() {
    if (!chromeSelected) {
      notify("请先选择 Chrome 本地配置", "err");
      return;
    }
    const platform = chromeProfilePlatform(chromeSelected) || chromeCreatePlatform;
    setChromeBusy(true);
    try {
      await api.reachMessageAccountCreate({
        platform,
        profile_name: chromeSelected,
        display_name: chromeSelected,
        cooldown_sec: 1800,
      });
      notify(`已将「${chromeSelected}」加入后台消息巡检`, "ok");
      await refreshReachMessages(false);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setChromeBusy(false);
    }
  }

  async function coverCreateTemplate() {
    setCoverBusy(true);
    try {
      const name = coverNewName.trim() || "未命名封面套";
      const res = await api.coverTemplateCreate(name);
      setCoverNewName("");
      setCoverEditId(String(res.template?.id || ""));
      setReachMsg(`已创建封面模板：${name}`);
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setCoverBusy(false);
    }
  }

  async function coverSelectTemplate(id: string) {
    setCoverBusy(true);
    try {
      await api.coverTemplateSelect(id);
      setCoverSelectedId(id);
      setCoverEditId(id);
      setReachMsg("已设为当前发布封面模板");
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setCoverBusy(false);
    }
  }

  async function coverDeleteTemplate(id: string) {
    if (
      !(await askConfirm({
        title: "删除封面模板",
        body: "删除该封面模板套？",
        confirmLabel: "删除",
        danger: true,
      }))
    )
      return;
    setCoverBusy(true);
    try {
      await api.coverTemplateDelete(id);
      setReachMsg("已删除封面模板");
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setCoverBusy(false);
    }
  }

  async function coverSetSlot(platform: string, slotIndex: number) {
    if (!coverEditId) {
      notify("请先选择或创建封面模板", "err");
      return;
    }
    const path = await pickImageFile();
    if (!path) return;
    setCoverBusy(true);
    try {
      await api.coverTemplateSetSlot(coverEditId, platform, slotIndex, path);
      setReachMsg(`已写入 ${platform} 槽位 ${slotIndex}`);
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setCoverBusy(false);
    }
  }

  async function coverSeedFromPack() {
    if (!coverEditId) {
      notify("请先选择封面模板", "err");
      return;
    }
    const dir = reachPackDir.trim() || (await pickDir());
    if (!dir) return;
    setCoverBusy(true);
    try {
      await api.coverTemplateSeed(coverEditId, dir);
      setReachMsg("已从物料包灌入空槽封面");
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setCoverBusy(false);
    }
  }

  async function coverPreviewResolve() {
    setCoverBusy(true);
    try {
      const res = await api.coverTemplatesResolve(
        coverPreviewPlat,
        reachPackDir.trim() || undefined,
        coverSelectedId || undefined,
      );
      const r = res.resolve || {};
      const covers = (r.covers as string[] | undefined) || [];
      const gate = res.gate;
      setCoverPreviewMsg(
        `${coverPreviewPlat} → ${covers.length}/${String(r.needed ?? "?")} 张（来源 ${String(r.source || "")}）` +
          (covers.length ? `：${covers.map((c) => c.split("/").pop()).join(", ")}` : "") +
          (gate && gate.ok === false ? ` · 门禁：${String(gate.error || "未通过")}` : gate?.ok ? " · 门禁通过" : ""),
      );
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setCoverBusy(false);
    }
  }

  async function saveCalendarDay() {
    setActionBusy("calSave");
    try {
      await api.upsertCalendar(calDay, {
        theme: calTheme,
        category,
        customer_name: customerName,
        template_name: "default-vertical",
        quota: calQuota,
        note: calNote,
        active: true,
      });
      notify(`日历已保存：${calDay}`, "ok");
      await refreshAll();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  const openUncertainOutputs = useMemo(
    () =>
      outputs.filter(
        (o) =>
          o.state === "review" &&
          (o.review_status == null ||
            o.review_status === "uncertain" ||
            o.review_status === "pending"),
      ),
    [outputs],
  );
  const readyOutputs = useMemo(
    () =>
      openUncertainOutputs.filter((o) => {
        if (reviewFilter === "missing_voice") return !o.has_voice;
        if (reviewFilter === "missing_sub") return !o.subtitle_burned;
        if (reviewFilter === "tts_bad") return Boolean(o.tts_noncompliant) || o.tts_compliant === false;
        return true;
      }),
    [openUncertainOutputs, reviewFilter],
  );
  const missingVoiceCount = useMemo(
    () => openUncertainOutputs.filter((o) => !o.has_voice).length,
    [openUncertainOutputs],
  );
  const ttsBadCount = useMemo(
    () =>
      openUncertainOutputs.filter(
        (o) => Boolean(o.tts_noncompliant) || o.tts_compliant === false,
      ).length,
    [openUncertainOutputs],
  );
  const tabLabel = TABS.find(([t]) => t === tab)?.[1] ?? "";
  const engineOn = Boolean(engine?.healthy);
  const statusSummary = !engineOn
    ? "设备未连接"
    : health?.runtime_state && health.runtime_state !== "ACTIVE"
      ? "任务已暂停"
      : health?.path_health?.ok === false
        ? "存储位置需检查"
        : "可正常使用";
  const engineDetail =
    engine?.message ||
    (engine?.python ? `Python: ${engine.python}` : "") ||
    engine?.repo ||
    "";

  const pendingReviewCount = useMemo(
    () => openUncertainOutputs.length,
    [openUncertainOutputs],
  );
  const readyCount = useMemo(
    () => Number(opsReport?.ready_available ?? report?.ready_available ?? 0),
    [opsReport, report],
  );
  const runningJobs = useMemo(
    () =>
      jobs.filter((j) => {
        const s = String(j.status || "");
        return s === "running" || s === "queued" || s === "pending";
      }).length,
    [jobs],
  );
  const remainingProductionCount = useMemo(
    () => countRemainingProduction(jobs),
    [jobs],
  );

  useEffect(() => {
    const prev = prevRunningJobsRef.current;
    const currentActiveIds = new Set(
      jobs
        .filter((job) => ["running", "queued", "pending"].includes(String(job.status || "")))
        .map((job) => Number(job.id))
        .filter(Number.isFinite),
    );
    const finishedJobIds = new Set(
      [...activeJobIdsRef.current].filter((jobId) => !currentActiveIds.has(jobId)),
    );
    if (prev !== null && prev !== runningJobs) {
      setJobsFlash(true);
      window.clearTimeout(jobsFlashTimerRef.current);
      jobsFlashTimerRef.current = window.setTimeout(() => setJobsFlash(false), 1200);
      if (prev > 0 && runningJobs === 0) {
        const previousIds = new Set(outputs.map((row) => Number(row.id)).filter(Number.isFinite));
        void api
          .listOutputs()
          .then((fresh) => {
            setOutputs(fresh);
            const completed = fresh
              .filter(
                (row) =>
                  !notifiedOutputIdsRef.current.has(Number(row.id)) &&
                  (finishedJobIds.has(Number(row.job_id)) ||
                    !previousIds.has(Number(row.id))) &&
                  (row.display_no != null || Boolean(row.display_label)),
              )
              .sort((a, b) => Number(a.display_no || 0) - Number(b.display_no || 0));
            if (!completed.length) {
              notify("本组任务已结束，未发现新的可用成片，请查看任务结果", "warn", {
                action: { tab: "produce", section: "tasks", guideTarget: "production-jobs" },
              });
              return;
            }
            for (const row of completed.slice(-5)) {
              notifiedOutputIdsRef.current.add(Number(row.id));
              const label =
                String(row.display_label || "").trim() ||
                `#${String(row.display_no).padStart(3, "0")}`;
              const jobId = Number(row.job_id);
              notify(`混剪完成 ${label} · 点击查看对应任务`, "ok", {
                holdMs: 15_000,
                action: {
                  tab: "produce",
                  section: "tasks",
                  guideTarget: Number.isFinite(jobId) ? `job-row-${jobId}` : "production-jobs",
                },
              });
            }
          })
          .catch((reason: unknown) =>
            notify(`任务已结束，但成片结果刷新失败：${String(reason)}`, "warn", {
              action: { tab: "produce", section: "tasks", guideTarget: "production-jobs" },
            }),
          );
      }
    }
    prevRunningJobsRef.current = runningJobs;
    activeJobIdsRef.current = currentActiveIds;
  }, [jobs, runningJobs, notify, outputs]);
  const semanticRunning = Boolean(semanticStatus && semanticStatus.claimed > 0);
  const semanticPct = semanticStatus
    ? Math.min(
        100,
        Math.round(
          (semanticStatus.processed / Math.max(semanticStatus.eligible, semanticStatus.processed, 1)) * 100,
        ),
      )
    : 0;
  const pathBlocked = Boolean(
    (health && health.path_health && !health.path_health.ok) ||
      health?.status === "blocked" ||
      health?.workspace_state === "missing" ||
      health?.workspace_state === "mismatch",
  );
  const pipelineNodes = useMemo(
    () => [
      {
        id: "produce" as Tab,
        label: "混剪剩余",
        count: remainingProductionCount,
        warn: remainingProductionCount > 0,
      },
      {
        id: "review" as Tab,
        label: "审片剩余",
        count: pendingReviewCount,
        warn: ttsBadCount > 0,
        blocked: ttsBadCount > 0,
      },
      {
        id: "publish" as Tab,
        label: "上传剩余",
        count: readyCount,
        warn: readyCount > 0,
      },
      {
        id: "data" as Tab,
        label: "向量剩余",
        count: vectorPending,
        warn: vectorPending > 0,
      },
      {
        id: "messages" as Tab,
        label: "消息待办",
        count: reachMessageUnread + contentMessageUnread,
        warn: reachMessageUnread + contentMessageUnread > 0,
      },
    ],
    [
      contentMessageUnread,
      pendingReviewCount,
      reachMessageUnread,
      readyCount,
      remainingProductionCount,
      runningJobs,
      ttsBadCount,
      vectorPending,
    ],
  );

  async function toggleSemanticMode() {
    if (!semanticToggleEnabled) {
      notify("当前机型档位未开放语义按需（lite 请升配或补装视觉包）", "warn");
      return;
    }
    const next = semanticMode === "on_demand" ? "off" : "on_demand";
    try {
      await api.updateSettings({ semantic_analysis_mode: next });
      setSemanticMode(next);
      notify(next === "on_demand" ? "已开启语义按需" : "已关闭语义按需", "ok");
      await refreshSemanticStatus();
    } catch (e) {
      notify(String(e), "err");
    }
  }

  async function toggleSemanticFullBackfill() {
    if (!semanticToggleEnabled) {
      notify("当前机型档位未开放严格语义（lite 请升配或补装视觉包）", "warn");
      return;
    }
    const next = !semanticFullBackfill;
    try {
      const st = (await api.updateSettings({
        semantic_full_backfill_enabled: next,
      })) as Record<string, unknown>;
      const enabled = st.semantic_full_backfill_enabled === true;
      setSemanticFullBackfill(enabled);
      notify(
        enabled
          ? "已开启严格语义全库回填（本机 lab，逐步拉高覆盖）"
          : "已关闭严格语义全库回填",
        "ok",
      );
      await refreshSemanticStatus();
    } catch (e) {
      notify(String(e), "err");
    }
  }

  const activityItems: ActivityItem[] = useMemo(() => {
    const items: ActivityItem[] = [];
    if (!engineOn) items.push({ id: "eng", text: "引擎离线 — 点击启动后开始日更", kind: "err", tab: "overview" });
    if (pathBlocked) {
      const wsReason =
        health?.workspace_state === "missing"
          ? "工作区路径不可用：请回到主盘 ~/Suying/data 或修正路径后点「刷新工作区」"
          : health?.workspace_state === "mismatch"
            ? "工作区身份不符：请确认路径与绑定卷一致"
            : "路径异常，请到设置修复片库/成片目录";
      items.push({
        id: "path",
        text: wsReason,
        kind: "warn",
        tab: health?.workspace_state === "missing" || health?.workspace_state === "mismatch" ? "overview" : "settings",
      });
    }
    if (runningJobs > 0) items.push({ id: "jobs", text: `生产中：${runningJobs} 个任务进行中`, kind: "info", tab: "produce" });
    if (pendingReviewCount > 0) items.push({ id: "rev", text: `待审片 ${pendingReviewCount} 条，可前往审片`, kind: "info", tab: "review" });
    if (ttsBadCount > 0) items.push({ id: "tts", text: `音色违规 ${ttsBadCount} 条，需批量修复`, kind: "warn", tab: "review" });
    if (reachMessageUnread > 0) items.push({ id: "msg", text: `视频消息未读 ${reachMessageUnread}`, kind: "warn", tab: "messages" });
    if (contentMessageUnread > 0) items.push({ id: "cmsg", text: `软文消息未读 ${contentMessageUnread}`, kind: "warn", tab: "messages" });
    if (readyCount > 0 && pendingReviewCount === 0) items.push({ id: "ready", text: `待发 ${readyCount} 条，可去发布`, kind: "ok", tab: "publish" });
    if (semanticStatus && semanticRunning) {
      items.push({
        id: "sem",
        text: `候选片段正在进行 9B 按需验证（严格覆盖 ${semanticPct}%）`,
        kind: "info",
        tab: "produce",
      });
    }
    if (!items.length) items.push({ id: "idle", text: "产线空闲 · 可从生产开跑或检查日历", kind: "ok", tab: "produce" });
    return items;
  }, [engineOn, pathBlocked, health, runningJobs, pendingReviewCount, ttsBadCount, reachMessageUnread, contentMessageUnread, readyCount, semanticStatus, semanticPct, semanticRunning]);

  const cmdExtra: CmdItem[] = [
    {
      id: "backup-center",
      label: "数据保护与备份",
      run: () => goTab("ops", { ops: "backup", guideTarget: "ops-backups" }),
    },
    {
      id: "operation-logs",
      label: "运行日志",
      run: () => goTab("ops", { ops: "logs", guideTarget: "operation-logs" }),
    },
    {
      id: "published-cleanup",
      label: "已发布视频保留",
      run: () =>
        goTab("settings", {
          settings: "storage",
          guideTarget: "settings-published-cleanup",
        }),
    },
    {
      id: "advanced-settings",
      label: "高级功能",
      run: () => goTab("ops", { ops: "advanced", guideTarget: "ops-advanced" }),
    },
    {
      id: "rule-history",
      label: "规则版本与文字生成",
      run: () => goTab("rules", { guideTarget: "rule-history" }),
    },
    {
      id: "shortcuts",
      label: "快捷键说明",
      hint: "?",
      run: () => {
        void askConfirm({
          title: "快捷键",
          body:
            "⌘/Ctrl+K  命令面板\n" +
            "1–9       切换九个页签\n" +
            "审片页：J/K 上下条 · A 通过 · R 重渲 · F 影院\n" +
            "?         打开本说明",
          confirmLabel: "知道了",
          cancelLabel: "关闭",
        });
      },
    },
  ];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((v) => !v);
        return;
      }
      if (!meta && e.key === "?" && !(e.target as HTMLElement)?.isContentEditable) {
        const tag = (e.target as HTMLElement)?.tagName;
        if (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") {
          e.preventDefault();
          void askConfirm({
            title: "快捷键",
            body:
              "⌘/Ctrl+K  命令面板\n" +
              "1–9       切换九个页签\n" +
              "审片页：J/K 上下条 · A 通过 · R 重渲 · F 影院\n" +
              "?         打开本说明",
            confirmLabel: "知道了",
            cancelLabel: "关闭",
          });
          return;
        }
      }
      const tag = (e.target as HTMLElement)?.tagName;
      const typing =
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        (e.target as HTMLElement)?.isContentEditable;
      if (typing || cmdOpen || dialog) return;
      if (e.key >= "1" && e.key <= String(TABS.length)) {
        const idx = Number(e.key) - 1;
        if (TABS[idx]) {
          e.preventDefault();
          goTab(TABS[idx][0]);
        }
      }
      if (tab === "review" && readyOutputs.length) {
        const ids = readyOutputs.map((o) => Number(o.id));
        const cur = reviewFocusId ?? ids[0];
        const ix = Math.max(0, ids.indexOf(cur));
        if (e.key === "j" || e.key === "J") {
          e.preventDefault();
          setReviewFocusId(ids[Math.min(ids.length - 1, ix + 1)] ?? cur);
        }
        if (e.key === "k" || e.key === "K") {
          e.preventDefault();
          setReviewFocusId(ids[Math.max(0, ix - 1)] ?? cur);
        }
        if ((e.key === "a" || e.key === "A") && cur) {
          e.preventDefault();
          const row = readyOutputs.find((o) => Number(o.id) === cur);
          if (row?.ready_gate_ok === true) {
            void decideReview(cur, "approved");
          } else {
            notify("门禁未证，不能快捷通过；请点「重验门禁」", "warn");
          }
        }
        if ((e.key === "r" || e.key === "R") && cur) {
          e.preventDefault();
          void rerenderOnly(cur);
        }
        if (e.key === "f" || e.key === "F") {
          e.preventDefault();
          setCinemaMode((v) => !v);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cmdOpen, dialog, tab, readyOutputs, reviewFocusId]);

  const advancedUnlocked = passwordStatus.unlocked;
  const passwordConfigured = passwordStatus.configured;

  return (
    <>
    <AppShell
      tab={tab}
      onSetTab={goTab}
      density={layout.density}
      tabIcons={TAB_ICONS}
      badges={{
        review: pendingReviewCount,
        publish: Number(reachInbox?.unread_count ?? 0),
        messages: reachMessageUnread + contentMessageUnread,
      }}
      brandSlot={
        <div className="brand-mark">
          <h1 className="brand-name">速影 Studio</h1>
          <span className="brand-ver">SUYING</span>
        </div>
      }
      railFoot={
        <div className="rail-foot">
          <div className="foot-label">
            <span>运行状态</span>
            <span title={engineDetail}>{statusSummary}</span>
          </div>
          <div className="rail-health">
            <div className="rail-health-row">
              <span className={`health-dot ${engineOn ? "ok" : engineBusy || engine?.running ? "warn" : "err"}`} />
              {engineOn ? "设备正常" : engineBusy || engine?.running ? "正在启动" : "需要检查"}
            </div>
            {health?.runtime_state && health.runtime_state !== "ACTIVE" ? (
              <div className="rail-health-row">
                <span className={`health-dot ${health.runtime_state === "PAUSED_BLOCKED" ? "err" : "warn"}`} />
                {health.runtime_state === "PAUSED_BLOCKED" ? "等待处理" : "已暂停"}
              </div>
            ) : null}
          </div>
          <button type="button" onClick={() => goTab("ops", { ops: "health" })}>
            查看设备状态
          </button>
        </div>
      }
      topbarTitle={
        <>
          <h2>{tabLabel}</h2>
          <span>
            {TAB_BLURB[tab]} · {customerName || "未选择客户"}
          </span>
        </>
      }
      topbarActions={
        <>
          <div className="pulse-bar" aria-label="实时脉冲">
            <button
              type="button"
              className={`pulse-chip${jobsFlash ? " pulse-chip--flash" : ""}`}
              onClick={() => goTab("produce")}
            >
              生产 {runningJobs}
            </button>
            <button type="button" className="pulse-chip" onClick={() => goTab("review")}>
              待审 {pendingReviewCount}
            </button>
            <button type="button" className="pulse-chip" onClick={() => goTab("publish", { publish: "desk" })}>
              待发 {readyCount}
            </button>
            <button type="button" className="pulse-chip" onClick={() => goTab("messages")}>
              消息 {reachMessageUnread + contentMessageUnread}
            </button>
            <button type="button" className="pulse-chip" onClick={() => goTab("ops", { ops: "ai" })}>
              向量待处理 {vectorPending}
            </button>
          </div>
          <button type="button" title="命令面板 ⌘K" onClick={() => setCmdOpen(true)}>
            ⌘K
          </button>
          <label className="field-inline">
            客户
            <select
              value={customerName}
              onChange={(e) => switchCustomer(e.target.value).catch((err) => notify(String(err), "err"))}
            >
              {customers.length === 0 && <option value="">—</option>}
              {customers.map((c) => (
                <option key={c.id} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        </>
      }
    >
      <AppNotifyHub
        toasts={toasts}
        celebration={celebration}
        banner={banner}
        onDismissToast={dismissToast}
        onDismissCelebration={dismissCelebration}
        onDismissBanner={dismissBanner}
        onAction={(target: NotifyTarget) => {
          if (target.outputId && target.tab === "review") {
            setReviewFocusId(target.outputId);
          }
          goTab(target.tab, {
            section: target.section,
            guideTarget: target.guideTarget,
          });
        }}
      />
      {!engineOn && !engineBusy && (
        <div className="banner warn engine-offline-banner">
          <span>
            {engine?.control_plane || engine?.listen
              ? `${offlineClassLabel(engine)}${engine?.offline_detail ? `：${engine.offline_detail}` : "。生产和审片暂不可用。"}`
              : `${offlineClassLabel(engine)}${engine?.offline_detail ? `：${engine.offline_detail}` : "，生产和审片暂不可用。"}`}
          </span>
          <button type="button" className="primary" onClick={() => goTab("ops", { ops: "services" })}>
            去设备服务
          </button>
        </div>
      )}

      {showWizard && (
        <CarrierInstallWizard
          wizName={wizName}
          setWizName={setWizName}
          wizLib={wizLib}
          setWizLib={setWizLib}
          wizOut={wizOut}
          setWizOut={setWizOut}
          wizKw={wizKw}
          setWizKw={setWizKw}
          onFinish={finishWizard}
          applyStandardLayout={applyStandardLayout}
          pickDir={pickDir}
          pickFile={pickFile}
          notify={notify}
          onDismiss={() => {
            wizardDismissedRef.current = true;
            setShowWizard(false);
            if (customers.some((c) => c.library_root && c.output_root)) {
              api.updateSettings({ onboarded: true }).catch(() => undefined);
            }
          }}
        />
      )}

      <main className="panel">
        {mountedTabs.includes("overview") && (
          <div className="tab-pane" hidden={tab !== "overview"} aria-hidden={tab !== "overview"}>
            <OverviewPage
            pipelineNodes={pipelineNodes}
            pathBlocked={pathBlocked}
            active={tab === "overview"}
            setTab={goTab}
            refreshAll={refreshAll}
            report={report}
            opsReport={opsReport}
            assets={assets}
            recentOutputs={outputs}
            health={health}
            todayPlan={todayPlan}
            actionBusy={actionBusy}
            setActionBusy={setActionBusy}
            notify={notify}
            autoDaily={autoDaily}
            autoHour={autoHour}
            setShowWizard={setShowWizard}
            activityItems={activityItems}
            workspacePhase={workspacePhase}
            workspaceMessage={workspaceMessage}
            workspaceError={workspaceError}
            workspaceProbe={workspaceProbe}
            workspacePrefs={workspacePrefs}
            workspaceBusy={workspaceBusy}
            onWorkspaceToggleAuto={(v) => void onWorkspaceToggleAuto(v)}
            onWorkspaceSyncNow={() => void runWorkspaceSync("manual")}
            />
          </div>
        )}
        {mountedTabs.includes("produce") && (
          <div className="tab-pane" hidden={tab !== "produce"} aria-hidden={tab !== "produce"}>
            <ProductionPage
            workspace={produceWorkspace}
            onWorkspaceChange={setProduceWorkspace}
            setTab={goTab}
            customerName={customerName}
            activeCustomerId={activeCustomerId}
            productionOrientation={productionOrientation}
            setProductionOrientation={(value) => {
              setProductionOrientation(value);
              setJobRuleProfileId(null);
            }}
            brandLogo={brandLogo}
            brandBusy={brandBusy}
            saveBrandLogo={saveBrandLogo}
            notify={notify}
            theme={theme}
            setTheme={setTheme}
            category={category}
            setCategory={setCategory}
            assetCategory={assetCategory}
            setAssetCategory={setAssetCategory}
            topicMode={topicMode}
            setTopicMode={setTopicMode}
            topicClusterIds={topicClusterIds}
            setTopicClusterIds={setTopicClusterIds}
            topicEvidenceIds={topicEvidenceIds}
            setTopicEvidenceIds={setTopicEvidenceIds}
            targetCount={targetCount}
            setTargetCount={setTargetCount}
            actionBusy={actionBusy}
            setActionBusy={setActionBusy}
            runDryRun={runDryRun}
            createJob={createJob}
            createJobQuick={createJobQuick}
            createSceneTourJob={createSceneTourJob}
            dryResult={dryResult}
            jobs={jobs}
            outputs={outputs}
            refreshAll={refreshAll}
            calDay={calDay}
            setCalDay={setCalDay}
            calTheme={calTheme}
            setCalTheme={setCalTheme}
            calQuota={calQuota}
            setCalQuota={setCalQuota}
            calNote={calNote}
            setCalNote={setCalNote}
            saveCalendarDay={saveCalendarDay}
            askConfirm={askConfirm}
            calendar={calendar}
            semanticStatus={semanticStatus}
            assets={assets}
            jobRuleProfileId={jobRuleProfileId}
            setJobRuleProfileId={setJobRuleProfileId}
            activeRuleSummary={activeRuleSummary}
            ruleRotation={ruleRotation}
            onRuleRotationChange={(enabled) => {
              const prev = ruleRotation;
              setRuleRotation(enabled);
              void api
                .productionRulesSetRotationPolicy(enabled)
                .then(() => refreshAll())
                .catch((e: unknown) => {
                  setRuleRotation(prev);
                  notify(String(e), "err");
                });
            }}
            rotationPoolCount={rotationPoolCount}
            ruleOptions={ruleOptions}
            pathHealthOk={health?.path_health?.ok !== false}
            pathHealthErrors={health?.path_health?.errors || []}
            onOpenJobOutput={({ outputId, needsReview, jobId }) => {
              if (outputId) {
                setReviewFocusId(outputId);
                goTab(needsReview ? "review" : "publish", {
                  publish: needsReview ? undefined : "desk",
                  guideTarget: needsReview
                    ? `review-output-${outputId}`
                    : `publish-output-${outputId}`,
                });
              } else {
                goTab("produce", {
                  produce: "tasks",
                  guideTarget: `job-row-${jobId}`,
                });
              }
            }}
            />
          </div>
        )}
        {mountedTabs.includes("rules") && (
          <div className="tab-pane" hidden={tab !== "rules"} aria-hidden={tab !== "rules"}>
            <section className="page-stack rules-page">
              <VideoRuleWorkbench
                notify={notify}
                activeCustomerId={activeCustomerId}
                customerName={customerName}
                orientation={productionOrientation}
                onOrientationChange={(value) => {
                  setProductionOrientation(value);
                  setJobRuleProfileId(null);
                }}
                pickFile={pickFile}
                languages={langCatalog}
                selectedRuleId={jobRuleProfileId}
                onSelectedRuleIdChange={setJobRuleProfileId}
                onGoTasks={() => goTab("produce", { produce: "tasks" })}
                runDryRunWithRule={(id) => {
                  setJobRuleProfileId(id);
                  goTab("produce", { produce: "tasks", guideTarget: "production-create" });
                }}
              />
            </section>
          </div>
        )}
        {mountedTabs.includes("review") && (
          <div className="tab-pane" hidden={tab !== "review"} aria-hidden={tab !== "review"}>
            <ReviewPage
            active={tab === "review"}
            setTab={goTab}
            readyOutputs={readyOutputs}
            cinemaMode={cinemaMode}
            setCinemaMode={setCinemaMode}
            reviewFilter={reviewFilter}
            setReviewFilter={setReviewFilter}
            missingVoiceCount={missingVoiceCount}
            ttsBadCount={ttsBadCount}
            batchBusy={batchBusy}
            setBatchBusy={setBatchBusy}
            notify={notify}
            refreshAll={refreshAll}
            setMediaEpoch={setMediaEpoch}
            reviewReason={reviewReason}
            setReviewReason={setReviewReason}
            reviewReasons={reviewReasons}
            reviewNote={reviewNote}
            setReviewNote={setReviewNote}
            reviewFocusId={reviewFocusId}
            setReviewFocusId={setReviewFocusId}
            mediaEpoch={mediaEpoch}
            activeCustomerId={activeCustomerId}
            reviewBusyId={reviewBusyId}
            decideReview={decideReview}
            rerenderOnly={rerenderOnly}
            />
          </div>
        )}
        {mountedTabs.includes("publish") && (
          <div className="tab-pane" hidden={tab !== "publish"} aria-hidden={tab !== "publish"}>
            <PublishPage
            workspace={publishWorkspace}
            onWorkspaceChange={setPublishWorkspace}
            setTab={goTab}
            notify={notify}
            refreshAll={refreshAll}
            outputs={outputs}
            mediaEpoch={mediaEpoch}
            activeCustomerId={activeCustomerId}
            packLast={packLast}
            packBusyId={packBusyId}
            exportPack={exportPack}
            reachMessageUnread={reachMessageUnread}
            reachInbox={reachInbox}
            reachItems={reachItems}
            reachMessageAccounts={reachMessageAccounts}
            reachPlatforms={reachPlatforms}
            coverTemplates={coverTemplates}
            coverEditId={coverEditId}
            coverSelectedId={coverSelectedId}
            coverNewName={coverNewName}
            coverSlotCounts={coverSlotCounts}
            coverSlotSpecs={coverSlotSpecs}
            coverPreviewPlat={coverPreviewPlat}
            coverPreviewMsg={coverPreviewMsg}
            coverBusy={coverBusy}
            setCoverNewName={setCoverNewName}
            setCoverEditId={setCoverEditId}
            setCoverPreviewPlat={setCoverPreviewPlat}
            coverCreateTemplate={coverCreateTemplate}
            coverSeedFromPack={coverSeedFromPack}
            coverSelectTemplate={coverSelectTemplate}
            coverDeleteTemplate={coverDeleteTemplate}
            coverSetSlot={coverSetSlot}
            coverPreviewResolve={coverPreviewResolve}
            chromeBusy={chromeBusy}
            chromeSelected={chromeSelected}
            reachSelectChromeProfile={reachSelectChromeProfile}
            chromeProfiles={chromeProfiles}
            chromeInstalled={chromeInstalled}
            reachOpenChromeProfile={reachOpenChromeProfile}
            reachBindMessageAccount={reachBindMessageAccount}
            refreshReach={refreshReach}
            chromeRoot={chromeRoot}
            reachPackDir={reachPackDir}
            setReachPackDir={setReachPackDir}
            queueBusy={queueBusy}
            reachEnqueueFromPack={reachEnqueueFromPack}
            reachMsg={reachMsg}
            reachOpenItem={reachOpenItem}
            reachMarkPublished={reachMarkPublished}
            />
          </div>
        )}
        {mountedTabs.includes("messages") && (
          <div className="tab-pane" hidden={tab !== "messages"} aria-hidden={tab !== "messages"}>
            <MessagesPage
            accounts={reachMessageAccounts}
            messages={reachMessages}
            unreadCount={reachMessageUnread}
            status={reachMessageStatus}
            notificationPermission={notificationPermission}
            ntfyConfig={reachNtfyConfig}
            busy={reachMessageBusy}
            onScan={reachMessageScanStart}
            onCancel={reachMessageScanCancel}
            onOpen={reachMessageOpen}
            onRead={reachMessageRead}
            onToggleAccount={reachMessageToggleAccount}
            onCreateAccount={reachMessageCreateAccount}
            onOpenAccount={reachMessageOpenAccount}
            onDeleteAccount={reachMessageDeleteAccount}
            onQueryChange={(query) => void refreshReachMessages(false, query)}
            onReadAll={reachMessagesReadAll}
            onEnableNotifications={enableReachNotifications}
            onSaveNtfy={saveReachNtfy}
            onTestNtfy={testReachNtfy}
            onSuggestReplies={suggestReplyDrafts}
            replyDrafts={replyDrafts}
            replyDraftMessageId={replyDraftMessageId}
            onCopyReplyDraft={copyReplyDraft}
            contentAccounts={contentMessageAccounts}
            contentMessages={contentMessages}
            contentUnreadCount={contentMessageUnread}
            contentStatus={contentMessageStatus}
            contentBusy={contentMessageBusy}
            onContentScan={contentMessageScanStart}
            onContentCancel={contentMessageScanCancel}
            onContentOpen={contentMessageOpen}
            onContentPreview={contentMessagePreview}
            onContentToggleAccount={contentMessageToggleAccount}
            onContentCreateAccount={contentMessageCreateAccount}
            onContentOpenAccount={contentMessageOpenAccount}
            onContentDeleteAccount={contentMessageDeleteAccount}
            onContentQueryChange={(query) => void refreshContentMessages(query)}
            onContentReadAll={contentMessagesReadAll}
            onContentSuggestReplies={suggestContentReplyDrafts}
            contentReplyDrafts={contentReplyDrafts}
            contentReplyDraftMessageId={contentReplyDraftMessageId}
            onContentCopyReplyDraft={copyContentReplyDraft}
            />
          </div>
        )}
        {mountedTabs.includes("data") && (
          <div className="tab-pane" hidden={tab !== "data"} aria-hidden={tab !== "data"}>
            <DataCenterPage
            setTab={goTab}
            report={report}
            opsReport={opsReport}
            assets={assets}
            semanticStatus={semanticStatus}
            titlePoolSummary={titlePoolSummary}
            events={events}
            notify={notify}
            refreshAll={refreshAll}
            activeCustomerId={activeCustomerId}
            setTitlePoolSummary={setTitlePoolSummary}
            />
          </div>
        )}
        {mountedTabs.includes("ops") && (
          <div className="tab-pane" hidden={tab !== "ops"} aria-hidden={tab !== "ops"}>
            <OpsPage
            section={opsSection}
            onSectionChange={setOpsSection}
            setTab={goTab}
            notify={notify}
            askConfirm={askConfirm}
            refreshAll={refreshAll}
            actionBusy={actionBusy}
            setActionBusy={setActionBusy}
            ollamaInfo={ollamaInfo}
            refreshOllamaDetail={refreshOllamaDetail}
            pullRecommendedModels={pullRecommendedModels}
            pullOllamaModel={pullOllamaModel}
            ollamaNarration={ollamaNarration}
            setOllamaNarration={setOllamaNarration}
            ollamaNarrationEmoji={ollamaNarrationEmoji}
            setOllamaNarrationEmoji={setOllamaNarrationEmoji}
            ollamaNarrationModel={ollamaNarrationModel}
            setOllamaNarrationModel={setOllamaNarrationModel}
            health={health}
            syncStatus={syncStatus}
            setSyncStatus={setSyncStatus}
            syncDryRunBusy={syncDryRunBusy}
            setSyncDryRunBusy={setSyncDryRunBusy}
            syncDryRunHint={syncDryRunHint}
            setSyncDryRunHint={setSyncDryRunHint}
            services={services}
            setServices={setServices}
            events={events}
            semanticMode={semanticMode}
            semanticToggleEnabled={semanticToggleEnabled}
            semanticFullBackfill={semanticFullBackfill}
            semanticPct={semanticPct}
            onToggleSemanticMode={() => void toggleSemanticMode()}
            onToggleSemanticFullBackfill={() => void toggleSemanticFullBackfill()}
            engineOn={engineOn}
            engineBusy={engineBusy}
            onEngineToggle={() => void onEngineToggle()}
            onLogNavigate={(target) => {
              if (target.outputId) {
                setReviewFocusId(target.outputId);
              }
              goTab(target.tab, {
                produce: target.tab === "produce" ? "tasks" : undefined,
                guideTarget: target.guideTarget,
              });
            }}
            />
          </div>
        )}
        {mountedTabs.includes("settings") && (
          <div className="tab-pane" hidden={tab !== "settings"} aria-hidden={tab !== "settings"}>
            <SettingsPage
            section={settingsSection}
            onSectionChange={setSettingsSection}
            setTab={goTab}
            notify={notify}
            askConfirm={askConfirm}
            refreshAll={refreshAll}
            actionBusy={actionBusy}
            setActionBusy={setActionBusy}
            libraryRoot={libraryRoot}
            setLibraryRoot={setLibraryRoot}
            libraryRootsText={libraryRootsText}
            setLibraryRootsText={setLibraryRootsText}
            outputRoot={outputRoot}
            setOutputRoot={setOutputRoot}
            keywordPackPath={keywordPackPath}
            setKeywordPackPath={setKeywordPackPath}
            pickDir={pickDir}
            pickFile={pickFile}
            activeCustomerId={activeCustomerId}
            titlePoolSummary={titlePoolSummary}
            setTitlePoolSummary={setTitlePoolSummary}
            savePaths={savePaths}
            customerName={customerName}
            customers={customers}
            newCustName={newCustName}
            setNewCustName={setNewCustName}
            remoteFolder={remoteFolder}
            setRemoteFolder={setRemoteFolder}
            remoteFolderOptions={remoteFolderOptions}
            setRemoteFolderOptions={setRemoteFolderOptions}
            remoteFoldersBusy={remoteFoldersBusy}
            setRemoteFoldersBusy={setRemoteFoldersBusy}
            syncStatus={syncStatus}
            setSyncStatus={setSyncStatus}
            editSourceCustomer={editSourceCustomer}
            setEditSourceCustomer={setEditSourceCustomer}
            editRemoteFolder={editRemoteFolder}
            setEditRemoteFolder={setEditRemoteFolder}
            layoutPref={layout.pref}
            setLayoutPref={layout.setPref}
            cacheRoot={cacheRoot}
            setCacheRoot={setCacheRoot}
            renderRoot={renderRoot}
            setRenderRoot={setRenderRoot}
            dataRoot={dataRoot}
            setDataRoot={setDataRoot}
            vectorDbPath={vectorDbPath}
            advancedUnlocked={advancedUnlocked}
            passwordConfigured={passwordConfigured}
            passwordStatusRemaining={passwordStatus.unlocked_remaining_sec}
            onUnlock={async (password) => {
              const next = await settingsPasswordVerify(password);
              setPasswordStatus(next);
              notify("高级配置已解锁", "ok");
            }}
            onLock={async () => {
              const next = await settingsPasswordLock();
              setPasswordStatus(next);
              notify("高级配置已锁定", "ok");
            }}
            />
          </div>
        )}
      </main>
    </AppShell>


      <CommandPalette
        open={cmdOpen}
        onClose={() => setCmdOpen(false)}
        extra={cmdExtra}
      />
      {dialog?.mode === "confirm" ? (
        <AppDialog
          open
          title={dialog.title}
          body={dialog.body}
          confirmLabel={dialog.confirmLabel}
          cancelLabel={dialog.cancelLabel}
          danger={dialog.danger}
          onConfirm={() => {
            dialog.resolve(true);
            setDialog(null);
          }}
          onCancel={() => {
            dialog.resolve(false);
            setDialog(null);
          }}
        />
      ) : null}
      {dialog?.mode === "prompt" ? (
        <AppDialog
          mode="prompt"
          open
          title={dialog.title}
          body={dialog.body}
          placeholder={dialog.placeholder}
          defaultValue={dialog.defaultValue}
          confirmLabel={dialog.confirmLabel}
          cancelLabel={dialog.cancelLabel}
          onConfirm={(v) => {
            dialog.resolve(v);
            setDialog(null);
          }}
          onCancel={() => {
            dialog.resolve(null);
            setDialog(null);
          }}
        />
      ) : null}
    </>
  );
}

export default App;
