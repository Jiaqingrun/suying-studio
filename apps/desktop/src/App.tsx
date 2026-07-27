import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api, Customer, Health, ReportSummary, ServicesStatus } from "./api";
import { getEngineStatus, isTauri, startEngine, stopEngine, type EngineStatus } from "./engineControl";
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
import { NextActionPill } from "./shell/NextAction";
import { AppDialog } from "./shell/AppDialog";
import { AppShell } from "./shell/AppShell";
import { CarrierInstallWizard } from "./CarrierInstallWizard";
import { AssistantChat } from "./AssistantChat";
import {
  ensureNotificationPermission,
  getNotificationPermission,
  notifyReachMessage,
  playReachMessageSound,
  type NotificationPermissionState,
} from "./notifications";
import {
  TAB_BLURB,
  TABS,
  type FlashKind,
  type LayoutDensityPref,
  type OpsSection,
  type ProduceWorkspace,
  type PublishWorkspace,
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
import {
  settingsPasswordStatus,
  settingsPasswordCreate,
  settingsPasswordVerify,
  settingsPasswordChange,
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
  Bot,
  type LucideIcon,
} from "lucide-react";
import "./App.css";

const TAB_ICONS: Record<Tab, LucideIcon> = {
  overview: LayoutDashboard,
  produce: Clapperboard,
  review: ScanSearch,
  publish: Send,
  messages: MessageCircle,
  data: BarChart3,
  ops: Wrench,
  settings: Settings2,
};

function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [produceWorkspace, setProduceWorkspace] = useState<ProduceWorkspace>("tasks");
  const [publishWorkspace, setPublishWorkspace] = useState<PublishWorkspace>("desk");
  const [opsSection, setOpsSection] = useState<OpsSection>("ai");
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("paths");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [passwordStatus, setPasswordStatus] = useState<SettingsPasswordStatus>({
    configured: false,
    unlocked: false,
    unlocked_remaining_sec: 0,
    fail_cooldown_sec: 0,
  });
  const layout = useLayoutDensity();

  const goTab = useCallback((t: Tab, opts?: { produce?: ProduceWorkspace; publish?: PublishWorkspace; settings?: SettingsSection; ops?: OpsSection }) => {
    setTab(t);
    if (opts?.produce) setProduceWorkspace(opts.produce);
    if (opts?.publish) setPublishWorkspace(opts.publish);
    if (opts?.settings) setSettingsSection(opts.settings);
    if (opts?.ops) setOpsSection(opts.ops);
    if (t !== "overview") setAssistantOpen(false);
  }, []);

  const [health, setHealth] = useState<Health | null>(null);
  const [services, setServices] = useState<ServicesStatus | null>(null);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState<{
    kind: FlashKind;
    text: string;
    actionLabel?: string;
    actionTab?: Tab;
  } | null>(null);
  const flashTimerRef = useRef(0);
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
  } | null>(null);
  const [cacheRoot, setCacheRoot] = useState("");
  const [renderRoot, setRenderRoot] = useState("");
  const [dataRoot, setDataRoot] = useState("");
  const [vectorDbPath, setVectorDbPath] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [activeCustomerId, setActiveCustomerId] = useState<number | null>(null);
  /** Bump on customer switch so <video>/<img> remount and bypass poisoned media cache. */
  const [mediaEpoch, setMediaEpoch] = useState(0);
  const [brandLogo, setBrandLogo] = useState<BrandLogoState>(() => brandFromProfile(null));
  const [brandBusy, setBrandBusy] = useState(false);
  const [theme, setTheme] = useState("default");
  const [category, setCategory] = useState("default");
  const [targetCount, setTargetCount] = useState(5);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewReason, setReviewReason] = useState("other");
  const [reviewReasons, setReviewReasons] = useState<Array<{ code: string; label: string }>>([
    { code: "other", label: "其他" },
  ]);
  const [packBusyId, setPackBusyId] = useState<number | null>(null);
  const [packLast, setPackLast] = useState<string>("");
  const [voiceLang, setVoiceLang] = useState("zh");
  const [subtitleLang, setSubtitleLang] = useState("zh");
  const [subtitleBurn, setSubtitleBurn] = useState("external");
  const [dualSecondaryLang, setDualSecondaryLang] = useState("en");
  const [exprSaveBusy, setExprSaveBusy] = useState(false);
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
  const [reachNtfyConfig, setReachNtfyConfig] = useState<ReachNtfyConfig | null>(null);
  const [notificationPermission, setNotificationPermission] =
    useState<NotificationPermissionState>("unavailable");
  const [reachQuota, setReachQuota] = useState<Record<string, unknown> | null>(null);
  const [quotaTotal, setQuotaTotal] = useState(5);
  const [quotaByPlatform, setQuotaByPlatform] = useState<Record<string, number>>({});
  const [quotaSplit, setQuotaSplit] = useState(false);
  const [quotaBusy, setQuotaBusy] = useState(false);
  const [reachPackDir, setReachPackDir] = useState("");
  const [chromeBusy, setChromeBusy] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [queueBusy, setQueueBusy] = useState(false);
  const [reachMsg, setReachMsg] = useState("");
  const [chromeProfiles, setChromeProfiles] = useState<
    Array<{ name: string; path: string; platform?: string | null; label?: string | null }>
  >([]);
  const [chromeSelected, setChromeSelected] = useState("");
  const [chromeCreatePlatform, setChromeCreatePlatform] = useState("douyin");
  const [chromeCreateCount, setChromeCreateCount] = useState(1);
  const [chromeRoot, setChromeRoot] = useState("");
  const [chromeInstalled, setChromeInstalled] = useState(false);
  const [autoUploadPhase, setAutoUploadPhase] = useState("");
  const [autoUploadMsg, setAutoUploadMsg] = useState("");
  const [autoUploadBusy, setAutoUploadBusy] = useState(false);
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
  const [vectorization, setVectorization] = useState(false);
  const [semanticStatus, setSemanticStatus] = useState<SemanticBackfillStatus | null>(null);
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
  } | null>(null);
  const [cursorApiKeyInput, setCursorApiKeyInput] = useState("");
  const [cursorKeyConfigured, setCursorKeyConfigured] = useState(false);
  const [cursorKeyHint, setCursorKeyHint] = useState("");
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
  const exprBaselineRef = useRef({ voice: "zh", sub: "zh", burn: "external", dual: "en" });
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
        title: "词池文件",
        body: "请输入词池文件路径 (.json/.md)",
        placeholder: "/path/to/keyword-pack.json",
        confirmLabel: "使用此路径",
      });
    }
    const selected = await open({
      multiple: false,
      filters: [{ name: "词池", extensions: ["json", "md", "txt"] }],
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

  const exprDirty = useMemo(() => {
    const b = exprBaselineRef.current;
    return (
      voiceLang !== b.voice ||
      subtitleLang !== b.sub ||
      subtitleBurn !== b.burn ||
      dualSecondaryLang !== b.dual
    );
  }, [voiceLang, subtitleLang, subtitleBurn, dualSecondaryLang]);

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
    formSyncPausedRef.current = exprDirty || pathsDirty || scheduleDirty;
  }, [exprDirty, pathsDirty, scheduleDirty]);

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
    (
      text: string,
      kind: FlashKind = "info",
      opts?: { actionLabel?: string; actionTab?: Tab; holdMs?: number },
    ) => {
      setFlash({
        text,
        kind,
        actionLabel: opts?.actionLabel,
        actionTab: opts?.actionTab,
      });
      if (kind === "err") setError(text);
      else setError("");
      window.clearTimeout(flashTimerRef.current);
      const hold = opts?.holdMs ?? (kind === "err" ? 10000 : opts?.actionLabel ? 8000 : 4200);
      flashTimerRef.current = window.setTimeout(
        () => setFlash((cur) => (cur?.text === text ? null : cur)),
        hold,
      );
    },
    [],
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
      // Healthy poll: clear transport/connection sticky errors only — do not wipe action feedback.
      setError((prev) => {
        if (!prev) return prev;
        return /Failed to fetch|NetworkError|ECONNREFUSED|引擎启动|引擎已启动|无法连接速影引擎|fetch failed|Load failed|CORS/i.test(
          prev,
        )
          ? ""
          : prev;
      });
      setHealth(h);
      setVectorization(Boolean(h.vectorization_enabled));
      setOllamaNarration(Boolean(h.ollama_narration_enabled));
      setOllamaNarrationEmoji(h.ollama_narration_burn_emoji !== false);
      setOllamaNarrationModel(String(h.ollama_narration_model || ""));
      if (h.ollama) {
        setOllamaInfo((prev) => ({ ...(prev || {}), ...h.ollama }));
      }
      setCursorKeyConfigured(Boolean(h.cursor_api_key_configured));
      setCursorKeyHint(String(h.cursor_api_key_hint || ""));
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
        const configured = Boolean(
          h.setup_complete ||
            (active && active.library_root && active.output_root) ||
            custs.some((c) => c.library_root && c.output_root),
        );
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
            const expr = (active.profile?.expression || {}) as Record<string, unknown>;
            const voice = expr.voice_lang ? String(expr.voice_lang) : "zh";
            const sub = expr.subtitle_lang ? String(expr.subtitle_lang) : "zh";
            const burn = expr.subtitle_burn ? String(expr.subtitle_burn) : "external";
            const dual = expr.dual_secondary_lang ? String(expr.dual_secondary_lang) : "en";
            if (expr.voice_lang) setVoiceLang(voice);
            if (expr.subtitle_lang) setSubtitleLang(sub);
            if (expr.subtitle_burn) setSubtitleBurn(burn);
            if (expr.dual_secondary_lang) setDualSecondaryLang(dual);
            exprBaselineRef.current = { voice, sub, burn, dual };
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
              }),
            )
            .catch(() => setTitlePoolSummary(null));
          setWizName((prev) => prev || active.name);
          setWizLib((prev) => prev || String(active.library_root || ""));
          setWizOut((prev) => prev || String(active.output_root || ""));
          setWizKw((prev) => prev || String(active.keyword_pack_path || ""));
        }
        // First-run wizard only until setup is complete (never reopen every poll)
        if (h.onboarded || configured) {
          setShowWizard(false);
        } else if (!wizardDismissedRef.current) {
          setShowWizard(true);
        }

      } else if (h.onboarded || h.setup_complete) {
        setShowWizard(false);
      }

      try {
        setServices(await api.servicesStatus());
      } catch {
        setServices(null);
      }
    } catch (e) {
      notify(String(e), "err");
      setHealth(null);
    }
  }, []);

  const refreshReach = useCallback(async () => {
    try {
      const [list, inbox, quota, chrome, covers, platforms] = await Promise.all([
        api.reachList(),
        api.reachInbox(),
        api.reachQuota(),
        api.reachChromeProfiles().catch(() => null),
        api.coverTemplatesList().catch(() => null),
        api.reachPlatforms().catch(() => null),
      ]);
      setReachItems(list.items || []);
      setReachInbox({ unread_count: inbox.unread_count || 0, notices: inbox.notices || [] });
      setReachQuota(quota);
      {
        const total = Number(quota?.daily_quota ?? 5);
        setQuotaTotal(Number.isFinite(total) ? total : 5);
        const pq = (quota?.platform_quotas || {}) as Record<string, number>;
        const hasSplit = Boolean(pq && Object.keys(pq).length > 0);
        setQuotaSplit(hasSplit);
        const plats = mergeReachPlatforms(platforms?.platforms);
        const next: Record<string, number> = {};
        for (const p of plats) {
          next[p.id] = hasSplit ? Number(pq[p.id] ?? 0) : 0;
        }
        setQuotaByPlatform(next);
      }
      // Always merge API into built-in catalog — never shrink to old 3–5 platforms.
      setReachPlatforms(mergeReachPlatforms(platforms?.platforms));
      if (platforms?.slot_specs) {
        setCoverSlotSpecs(mergeCoverSlotSpecs(platforms.slot_specs));
      }
      if (chrome) {
        setChromeProfiles(chrome.profiles || []);
        setChromeRoot(chrome.root || "");
        setChromeInstalled(Boolean(chrome.chrome_installed));
        const sel = chrome.selected || chrome.profiles?.[0]?.name || "";
        setChromeSelected((prev) => prev || sel);
        if (chrome.selected_platform) {
          setChromeCreatePlatform(String(chrome.selected_platform));
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

  const refreshReachMessages = useCallback(async (claimNotifications = false) => {
    try {
      const [accounts, messages, status] = await Promise.all([
        api.reachMessageAccounts(),
        api.reachMessages(),
        api.reachMessageScanStatus(),
      ]);
      const messageRows = messages.messages || [];
      setReachMessageAccounts(accounts.accounts || []);
      setReachMessages(messageRows);
      setReachMessageUnread(messageRows.filter((message) => message.unread).length);
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
          await api.reachNotificationReport(
            message.id,
            "app",
            appSound ? "sent" : "failed",
            appSound ? "" : "应用内提示音不可用",
          ).catch(() => undefined);
          const nativeSent = await notifyReachMessage(
            `${message.platform} · ${message.notification_sender || "新消息"}`,
            `${message.notification_summary || "有新的平台消息，请进入速影查看。"}\n${message.reply_url}`,
          );
          await api.reachNotificationReport(
            message.id,
            "macos",
            nativeSent ? "sent" : "permission_denied",
          ).catch(() => undefined);
        }
      }
    } catch {
      // Older engines do not expose G7 message APIs.
    }
  }, [notify]);

  const refreshSemanticStatus = useCallback(async () => {
    try {
      const st = await api.captionsStatus();
      setSemanticStatus(st);
      semanticPollFastRef.current = st.remaining > 0 || st.claimed > 0;
      return st;
    } catch {
      setSemanticStatus(null);
      semanticPollFastRef.current = false;
      return null;
    }
  }, []);

  const runSemanticBatch = useCallback(
    async (limit: number) => {
      setActionBusy("captions");
      try {
        const r = await api.runCaptionsBatch(limit, true);
        const passed = Number(r.passed ?? 0);
        const rejected = Number(r.rejected ?? 0);
        notify(`语义批次完成：通过 ${passed}，拒绝 ${rejected}`, passed > 0 ? "ok" : "info");
        semanticPollFastRef.current = true;
        await refreshSemanticStatus();
      } catch (e) {
        notify(String(e), "err");
      } finally {
        setActionBusy(null);
      }
    },
    [notify, refreshSemanticStatus],
  );

  const refreshAll = useCallback(async () => {
    await refreshHealth();
    try {
      setAssets(await api.listAssets());
      setJobs(await api.listJobs());
      setEvents(await api.listEvents());
      setOutputs(await api.listOutputs());
      setReport(await api.reportSummary());
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
      await refreshSemanticStatus();
    } catch (e) {
      notify(String(e), "err");
    }
  }, [refreshHealth, refreshReach, refreshReachMessages, refreshSemanticStatus, notify]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (isTauri()) {
        setEngineBusy(true);
        try {
          const st = await getEngineStatus();
          if (!cancelled && !st.healthy) {
            await startEngine();
          }
          if (!cancelled) await refreshEngine();
        } catch (e) {
          if (!cancelled) notify(`引擎启动: ${e}`, "err");
        } finally {
          if (!cancelled) setEngineBusy(false);
        }
      }
      if (!cancelled) await refreshAll();
    })();
    const t = setInterval(() => {
      healthTickRef.current += 1;
      // Light health every 5s; customer/keyword sync ~every 30s (pulse must not hammer DB)
      void refreshHealth({ syncCustomer: healthTickRef.current % 6 === 0 });
      void refreshEngine();
    }, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [refreshAll, refreshEngine, refreshHealth]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (!cancelled) {
        await refreshReachMessages(true);
      }
    };
    const timer = window.setInterval(() => void tick(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshReachMessages]);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const tick = async () => {
      if (cancelled || !engine?.healthy) return;
      await refreshSemanticStatus();
      if (!cancelled) {
        timer = window.setTimeout(tick, semanticPollFastRef.current ? 3000 : 15000);
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
    void api
      .listExpressionLanguages()
      .then((res) => {
        if (!cancelled && Array.isArray(res.languages)) setLangCatalog(res.languages);
      })
      .catch(() => {
        if (!cancelled) {
          setLangCatalog([
            { code: "zh", label_zh: "中文（简体）", label_native: "简体中文", region: "东亚" },
            { code: "en", label_zh: "英语", label_native: "English", region: "欧美" },
            { code: "none", label_zh: "关闭", label_native: "Off", region: "其他" },
          ]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
    setError("");
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
        external_required: true,
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

  async function enableVectorization() {
    let ollama = ollamaInfo;
    try {
      ollama = await api.ollamaHealth();
      setOllamaInfo(ollama);
    } catch {
      /* keep cached */
    }
    if (!ollama?.embed_ready) {
      const goOps = await askConfirm({
        title: "本地 AI 未就绪",
        body:
          (ollama?.message || "未检测到向量模型。") +
          "\n\n请先在「运维」→「本地 AI」安装 Ollama 并拉取向量模型，再开启向量化。\n" +
          "仍要强制开启？（将使用弱质量 hash 降级）",
        confirmLabel: "仍要开启",
        danger: true,
      });
      if (!goOps) {
        goTab("settings", { settings: "paths" });
        return;
      }
    }
    const ok = await askConfirm({
      title: "开启向量化",
      body:
        "开启增量向量化：已有向量会保留，只补齐尚未索引的素材。\n" +
        "新片库会自然跑完全部缺口；老片库不会整库重算。\n" +
        "需要本机 Ollama 与向量模型。确定开启？",
      confirmLabel: "开启",
    });
    if (!ok) return;
    setActionBusy("vecOn");
    try {
      const r = await api.enableVectorization(true, 50);
      setVectorization(true);
      const gaps = r.gaps || {};
      const gapPart =
        gaps.ready_assets != null
          ? ` 已完成 ${gaps.complete_assets ?? "?"} / 就绪 ${gaps.ready_assets ?? "?"}，缺口素材 ${gaps.gap_assets ?? "?"}。`
          : " 后台正在增量补齐缺口。";
      notify(`${r.message}${gapPart}`, "ok");
      await refreshHealth();
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

  async function disableVectorization() {
    const ok = await askConfirm({
      title: "关闭向量化",
      body: "关闭后新入库素材不再自动补向量索引；已有向量会保留。确定关闭？",
      confirmLabel: "关闭向量化",
      danger: true,
    });
    if (!ok) return;
    setActionBusy("vecOff");
    try {
      await api.updateSettings({ vectorization_enabled: false });
      setVectorization(false);
      notify("已关闭向量化", "ok");
      await refreshHealth();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function saveCursorApiKey() {
    const key = cursorApiKeyInput.trim();
    if (!key) {
      notify("请先粘贴 API Key", "err");
      return;
    }
    setActionBusy("cursorKey");
    try {
      const r = (await api.updateSettings({ cursor_api_key: key })) as {
        cursor_api_key_configured?: boolean;
        cursor_api_key_hint?: string;
      };
      setCursorKeyConfigured(Boolean(r.cursor_api_key_configured));
      setCursorKeyHint(String(r.cursor_api_key_hint || ""));
      setCursorApiKeyInput("");
      notify(`已保存 Cursor API Key${r.cursor_api_key_hint ? `（${r.cursor_api_key_hint}）` : ""}`, "ok");
      await refreshHealth();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function verifyCursorApiKey() {
    const typed = cursorApiKeyInput.trim();
    if (!typed && !cursorKeyConfigured) {
      notify("请先填写 API Key", "err");
      return;
    }
    setActionBusy("cursorVerify");
    try {
      const r = await api.verifyCursorKey(typed || undefined);
      setCursorKeyConfigured(Boolean(r.cursor_api_key_configured ?? true));
      setCursorKeyHint(String(r.cursor_api_key_hint || cursorKeyHint));
      if (typed) setCursorApiKeyInput("");
      notify(r.message || "验证通过", "ok");
      await refreshHealth();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function clearCursorApiKey() {
    if (
      !(await askConfirm({
        title: "清除 API Key",
        body: "清除已保存的 Cursor API Key？",
        confirmLabel: "清除",
        danger: true,
      }))
    )
      return;
    setActionBusy("cursorClear");
    try {
      await api.updateSettings({ cursor_api_key: "" });
      setCursorKeyConfigured(false);
      setCursorKeyHint("");
      setCursorApiKeyInput("");
      notify("已清除 Cursor API Key", "ok");
      await refreshHealth();
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
    // 标准外置盘布局：自动建目录（片库仍在本机；默认同步仅载体）
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
      // 外置盘未挂载时仍允许手填路径
      if (!lib || !out) {
        notify(`标准布局创建失败（可手填路径）: ${e}`, "err");
        return;
      }
    }
    if (!lib || !out) {
      notify("请填写片库与输出目录，或先插入外置盘后使用标准布局", "err");
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
        setVectorization(true);
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
      exprSaveBusy ||
      brandBusy ||
      chromeBusy ||
      coverBusy ||
      queueBusy ||
      autoUploadBusy ||
      reachMessageBusy ||
      engineBusy
    ) {
      notify("有操作进行中，请稍后再切换客户", "warn");
      return;
    }
    if (exprDirty || pathsDirty || scheduleDirty) {
      const ok = await askConfirm({
        title: "未保存的更改",
        body: "当前有未保存的表达设置、路径或调度修改。切换客户将丢弃这些本地未保存更改。",
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
      setReachMessageAccounts([]);
      setReachMessages([]);
      setReachMessageUnread(0);
      setReachNtfyConfig(null);
      setReachMessageStatus(null);
      setMediaEpoch((n) => n + 1);
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
      const result = await api.dryRun({ customer_name: customerName, theme, category });
      setDryResult(result);
      const n = Array.isArray(result.candidates)
        ? result.candidates.length
        : Number(result.candidate_count ?? result.count ?? 0);
      notify(n > 0 ? `Dry-run 完成：约 ${n} 条候选` : "Dry-run 完成", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function createJob() {
    setActionBusy("createJob");
    try {
      await api.createJob({
        mode: "count",
        target_count: targetCount,
        customer_name: customerName,
        theme,
        category,
        template_name: "default-vertical",
      });
      notify(`已创建生产任务：目标 ${targetCount} 条`, "ok");
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
      await api.reviewOutput(id, status, reviewNote, {
        reason: status === "rejected" ? reviewReason : undefined,
        rerender: status === "rejected" && rerender,
      });
      setReviewNote("");
      const msg =
        status === "approved"
          ? `成片 #${id} 已通过，可去「发布」台复制文案`
          : rerender
            ? `成片 #${id} 已打回并排队重渲`
            : `成片 #${id} 已打回`;
      notify(
        status === "approved" ? `${msg}（仍留在审片，可继续下一条）` : msg,
        "ok",
        status === "approved"
          ? { actionLabel: "去发布", actionTab: "publish", holdMs: 9000 }
          : undefined,
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
      await api.rerenderOutput(id, reviewReason);
      notify(`成片 #${id} 已排队重渲`, "ok");
      await refreshAll();
      setMediaEpoch((n) => n + 1);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReviewBusyId(null);
    }
  }

  async function saveExpressionPrefs() {
    if (!activeCustomerId) {
      notify("请先选择客户", "err");
      return;
    }
    setExprSaveBusy(true);
    try {
      const updated = await api.updateCustomer(activeCustomerId, {
        expression: {
          voice_lang: voiceLang,
          subtitle_lang: subtitleLang,
          subtitle_burn: subtitleBurn,
          dual_secondary_lang: dualSecondaryLang,
        },
      });
      const expr = ((updated as { profile?: Record<string, unknown> }).profile?.expression ||
        {}) as Record<string, unknown>;
      if (expr.voice_lang) setVoiceLang(String(expr.voice_lang));
      if (expr.subtitle_lang) setSubtitleLang(String(expr.subtitle_lang));
      if (expr.subtitle_burn) setSubtitleBurn(String(expr.subtitle_burn));
      if (expr.dual_secondary_lang) setDualSecondaryLang(String(expr.dual_secondary_lang));
      const burn = String(expr.subtitle_burn || subtitleBurn);
      const v = String(expr.voice_lang || voiceLang);
      const s = String(expr.subtitle_lang || subtitleLang);
      const d = String(expr.dual_secondary_lang || dualSecondaryLang);
      exprBaselineRef.current = { voice: v, sub: s, burn, dual: d };
      formSyncPausedRef.current = false;
      notify(
        `已保存表达设置：旁白 ${v} / 字幕 ${s} / ${burn}${
          burn === "burn_dual" ? `（双语副语言 ${d}）` : ""
        }。下次生产/重渲生效。`,
        "ok",
      );
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setExprSaveBusy(false);
    }
  }

  async function exportPack(id: number) {
    setPackBusyId(id);
    setPackLast("");
    try {
      if (activeCustomerId) {
        if (exprDirty) {
          const ok = await askConfirm({
            title: "导出将保存表达设置",
            body: "当前表达设置尚未点「保存」。继续导出会一并写入客户配置（旁白/字幕/烧录）。",
            confirmLabel: "保存并导出",
            cancelLabel: "取消",
          });
          if (!ok) {
            setPackBusyId(null);
            return;
          }
        }
        await api.updateCustomer(activeCustomerId, {
          expression: {
            voice_lang: voiceLang,
            subtitle_lang: subtitleLang,
            subtitle_burn: subtitleBurn,
            dual_secondary_lang: dualSecondaryLang,
          },
        });
        exprBaselineRef.current = {
          voice: voiceLang,
          sub: subtitleLang,
          burn: subtitleBurn,
          dual: dualSecondaryLang,
        };
      }
      const res = await api.exportPublishPack(id, {
        voice_lang: voiceLang,
        subtitle_lang: subtitleLang,
        subtitle_burn: subtitleBurn,
        dual_secondary_lang: dualSecondaryLang,
      });
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
      notify(
        ok
          ? burned
            ? `物料包已导出并烧录字幕 #${id}`
            : `物料包已导出 #${id}`
          : `物料已导出但合规未过 #${id}`,
        ok ? "ok" : "warn",
      );
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
      notify(`触达 #${id} 已标记为已发布`, "ok");
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
    try {
      await api.reachMessageRead(message.id);
      await refreshReachMessages(false);
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

  async function saveReachQuota() {
    const total = Math.max(0, Math.min(500, Math.floor(Number(quotaTotal) || 0)));
    const plats: Record<string, number> = {};
    if (quotaSplit) {
      for (const p of reachPlatforms) {
        plats[p.id] = Math.max(0, Math.floor(Number(quotaByPlatform[p.id]) || 0));
      }
      const sum = Object.values(plats).reduce((a, b) => a + b, 0);
      if (sum > total) {
        notify(`平台合计 ${sum} 超过日总额 ${total}`, "err");
        return;
      }
    }
    setQuotaBusy(true);
    try {
      const res = await api.updateReachQuota({
        daily_quota: total,
        platform_quotas: quotaSplit ? plats : {},
      });
      setReachQuota(res);
      setQuotaTotal(Number(res.daily_quota ?? total));
      const pq = (res.platform_quotas || {}) as Record<string, number>;
      setQuotaSplit(Object.keys(pq).length > 0);
      notify(
        quotaSplit
          ? `配额已保存：总额 ${res.daily_quota}，已分配 ${res.allocated ?? Object.values(plats).reduce((a, b) => a + b, 0)}`
          : `配额已保存：仅总额 ${res.daily_quota}（不限单平台）`,
        "ok",
      );
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setQuotaBusy(false);
    }
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

  async function reachCreateChromeProfiles() {
    const count = Math.max(1, Math.min(10, Number(chromeCreateCount) || 1));
    setChromeBusy(true);
    try {
      const res = await api.reachChromeCreate({ platform: chromeCreatePlatform, count });
      const names = (res.created || []).map((c) => c.name).join("、");
      setReachMsg(res.note || `已创建 ${res.label || res.platform} ×${res.count}：${names}`);
      if (res.selected) setChromeSelected(res.selected);
      await refreshReach();
      if (res.selected) setChromeSelected(res.selected);
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

  async function pollAutoUploadOnce() {
    try {
      const st = await api.reachAutoUploadStatus();
      setAutoUploadPhase(st.phase || "");
      setAutoUploadMsg(st.message || st.error || "");
      const active = Boolean(st.active) && !["done", "failed", "cancelled", "idle", "awaiting_confirm", "need_human"].includes(st.phase || "");
      setAutoUploadBusy(active);
      if (st.phase === "done" || st.phase === "awaiting_confirm") {
        setReachMsg(st.message || "自动上传流程结束");
        await refreshReach();
      }
      if (st.phase === "need_human") {
        setReachMsg(st.message || "需要你在 Chrome 完成验证后再试");
      }
      return st;
    } catch {
      return null;
    }
  }

  async function reachStartAutoUpload(queueId?: number) {
    if (!chromeSelected) {
      notify("请先选择 Chrome 本地配置", "err");
      return;
    }
    setAutoUploadBusy(true);
    setAutoUploadPhase("starting");
    setAutoUploadMsg("启动中…");
    try {
      const plat = chromeProfilePlatform(chromeSelected) || chromeCreatePlatform;
      await api.reachChromeSelect(chromeSelected, plat);
      const res = await api.reachAutoUploadStart({
        chrome_profile: chromeSelected,
        queue_id: queueId,
        platform: plat,
        accept_risk: true,
        timeout_sec: 300,
      });
      setAutoUploadPhase(res.phase || "waiting_login");
      setAutoUploadMsg(res.message || res.disclaimer || "等待登录就绪…");
      setReachMsg(res.disclaimer || "已启动：登录就绪后自动上传当前队列项（风控自负）");
      for (let i = 0; i < 180; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await pollAutoUploadOnce();
        const phase = st?.phase || "";
        if (["done", "failed", "cancelled", "need_human", "awaiting_confirm", "idle"].includes(phase)) {
          break;
        }
      }
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
      setAutoUploadBusy(false);
    }
  }

  async function reachCancelAutoUpload() {
    try {
      await api.reachAutoUploadCancel();
      setAutoUploadBusy(false);
      setAutoUploadPhase("cancelled");
      setAutoUploadMsg("已取消");
    } catch (e) {
      notify(String(e), "err");
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

  const readyOutputs = outputs.filter((o) => {
    const stateOk = o.state === "ready" || o.state === "review";
    if (!stateOk) return false;
    if (reviewFilter === "missing_voice") return !o.has_voice;
    if (reviewFilter === "missing_sub") return !o.subtitle_burned;
    if (reviewFilter === "tts_bad") return Boolean(o.tts_noncompliant) || o.tts_compliant === false;
    return true;
  });
  const missingVoiceCount = outputs.filter(
    (o) => (o.state === "ready" || o.state === "review") && !o.has_voice,
  ).length;
  const ttsBadCount = outputs.filter(
    (o) =>
      (o.state === "ready" || o.state === "review") &&
      (Boolean(o.tts_noncompliant) || o.tts_compliant === false),
  ).length;
  const tabLabel = TABS.find(([t]) => t === tab)?.[1] ?? "";
  const engineOn = Boolean(engine?.healthy);
  const engineHint = engineBusy
    ? "处理中…"
    : engineOn
      ? engine?.bundled
        ? "一体包 · :8766"
        : "运行中 · :8766"
      : engine?.running
        ? "启动中…"
        : "已停止";
  const statusSummary = !engineOn
    ? "引擎离线"
    : [
        engine?.bundled ? "一体包" : null,
        services?.watcher ? "监视" : null,
        services?.worker ? "Worker" : null,
        services?.scheduler ? "调度" : null,
      ]
        .filter(Boolean)
        .join(" · ") || "已连接";
  const engineDetail =
    engine?.message ||
    (engine?.python ? `Python: ${engine.python}` : "") ||
    engine?.repo ||
    "";

  const pendingReviewCount = outputs.filter(
    (o) =>
      (o.state === "ready" || o.state === "review") &&
      o.review_status !== "approved" &&
      o.review_status !== "rejected",
  ).length;
  const readyCount = Number(opsReport?.ready ?? report?.ready ?? 0);
  const runningJobs = jobs.filter((j) => {
    const s = String(j.status || "");
    return s === "running" || s === "queued" || s === "pending";
  }).length;
  const semanticAnalyzing = Boolean(
    semanticStatus && (semanticStatus.remaining > 0 || semanticStatus.claimed > 0 || actionBusy === "captions"),
  );
  const semanticPct = semanticStatus
    ? Math.min(
        100,
        Math.round(
          (semanticStatus.processed / Math.max(semanticStatus.eligible, semanticStatus.processed, 1)) * 100,
        ),
      )
    : 0;
  const pathBlocked = Boolean(health && health.path_health && !health.path_health.ok);
  const pipelineNodes = [
    { id: "produce" as Tab, label: "生产", count: runningJobs, warn: runningJobs > 0 },
    {
      id: "review" as Tab,
      label: "审片",
      count: pendingReviewCount,
      warn: ttsBadCount > 0,
      blocked: ttsBadCount > 0,
    },
    {
      id: "publish" as Tab,
      label: "发布",
      count: readyCount + Number(reachInbox?.unread_count ?? 0),
      warn: Number(reachInbox?.unread_count ?? 0) > 0,
    },
  ];

  const activityItems: ActivityItem[] = useMemo(() => {
    const items: ActivityItem[] = [];
    if (!engineOn) items.push({ id: "eng", text: "引擎离线 — 点击启动后开始日更", kind: "err", tab: "overview" });
    if (pathBlocked) items.push({ id: "path", text: "路径异常，请到设置修复片库/成片目录", kind: "warn", tab: "settings" });
    if (runningJobs > 0) items.push({ id: "jobs", text: `生产中：${runningJobs} 个任务进行中`, kind: "info", tab: "produce" });
    if (pendingReviewCount > 0) items.push({ id: "rev", text: `待审片 ${pendingReviewCount} 条，可前往审片`, kind: "info", tab: "review" });
    if (ttsBadCount > 0) items.push({ id: "tts", text: `音色违规 ${ttsBadCount} 条，需批量修复`, kind: "warn", tab: "review" });
    if (reachMessageUnread > 0) items.push({ id: "msg", text: `平台消息未读 ${reachMessageUnread}`, kind: "warn", tab: "messages" });
    if (readyCount > 0 && pendingReviewCount === 0) items.push({ id: "ready", text: `待发 Ready ${readyCount}，可去发布`, kind: "ok", tab: "publish" });
    if (semanticStatus && semanticStatus.remaining > 0) {
      items.push({
        id: "sem",
        text: `语义分析剩余 ${semanticStatus.remaining}（进度 ${semanticPct}%）`,
        kind: "info",
        tab: "produce",
      });
    }
    if (!items.length) items.push({ id: "idle", text: "产线空闲 · 可从生产开跑或检查日历", kind: "ok", tab: "produce" });
    return items;
  }, [engineOn, pathBlocked, runningJobs, pendingReviewCount, ttsBadCount, reachMessageUnread, readyCount, semanticStatus, semanticPct]);

  const cmdExtra: CmdItem[] = [
    {
      id: "assistant-open",
      label: "打开助手",
      run: () => setAssistantOpen(true),
    },
    {
      id: "engine-toggle",
      label: engineOn ? "停止引擎" : "启动引擎",
      run: () => {
        void onEngineToggle();
      },
    },
    {
      id: "refresh",
      label: "刷新全部数据",
      run: () => {
        void refreshAll();
      },
    },
    {
      id: "density",
      label: layout.pref === "compact" ? "布局：舒适" : layout.pref === "comfort" ? "布局：自动" : "布局：紧凑",
      run: () => {
        const order: LayoutDensityPref[] = ["auto", "comfort", "compact"];
        const i = order.indexOf(layout.pref);
        layout.setPref(order[(i + 1) % order.length]!);
      },
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
            "1–7       切换七个页签\n" +
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
              "1–7       切换七个页签\n" +
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
      if (e.key >= "1" && e.key <= "7") {
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
          void decideReview(cur, "approved");
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
        messages: reachMessageUnread,
      }}
      brandSlot={
        <div className="brand-mark">
          <h1 className="brand-name">速影</h1>
          <span className="brand-ver">SUYING</span>
        </div>
      }
      railFoot={
        <div className="rail-foot">
          <div className="rail-power">
            <button
              type="button"
              className={`toggle toggle--rail${engineOn ? " on" : ""}${engineBusy ? " is-busy" : ""}`}
              role="switch"
              aria-checked={engineOn}
              disabled={engineBusy}
              title={engineOn ? "停止引擎（关 App 不会杀引擎）" : `启动本地混剪引擎\n${engineDetail}`}
              onClick={() => onEngineToggle().catch((e) => notify(String(e), "err"))}
            >
              <span className="toggle-track" aria-hidden="true">
                <span className="toggle-thumb" />
              </span>
              <span className="toggle-label">
                <span className="toggle-title">引擎</span>
                <span className="toggle-state">{engineHint}</span>
              </span>
            </button>
          </div>
          <div className="foot-label">
            <span>系统状态</span>
            <span title={engineDetail}>{statusSummary}</span>
          </div>
          <div className="rail-health">
            <div className="rail-health-row">
              <span className={`health-dot ${engineOn ? "ok" : engineBusy || engine?.running ? "warn" : "err"}`} />
              引擎 {engineOn ? "在线" : engineBusy || engine?.running ? "启动中" : "离线"}
            </div>
            {engineOn && !layout.isCompact ? (
              <>
                <div className="rail-health-row">
                  <span className={`health-dot ${services?.watcher ? "ok" : "warn"}`} />
                  监视 {services?.watcher ? "开" : "停"}
                </div>
                <div className="rail-health-row">
                  <span className={`health-dot ${services?.worker ? "ok" : "warn"}`} />
                  Worker {services?.worker ? "开" : "停"}
                </div>
                <div className="rail-health-row">
                  <span className={`health-dot ${vectorization ? "ok" : "warn"}`} />
                  向量化 {vectorization ? "开" : "关"}
                </div>
              </>
            ) : null}
          </div>
          <div className="rail-port" title={engine?.python || ""}>
            :8766 · LOCAL
          </div>
        </div>
      }
      topbarTitle={
        <>
          <h2>{tabLabel}</h2>
          <span>
            {TAB_BLURB[tab]} · {customerName || "未选择客户"} · 布局{" "}
            {layout.pref === "auto" ? "自动" : layout.pref === "comfort" ? "舒适" : "紧凑"}
          </span>
        </>
      }
      topbarActions={
        <>
          <div className="pulse-bar" aria-label="实时脉冲">
            <button type="button" className="pulse-chip" onClick={() => goTab("publish", { publish: "desk" })}>
              Ready {readyCount}
            </button>
            <button type="button" className="pulse-chip" onClick={() => goTab("produce")}>
              任务 {runningJobs}
            </button>
            {semanticStatus && semanticStatus.eligible > 0 ? (
              <button
                type="button"
                className={`pulse-chip${semanticAnalyzing ? " pulse-chip--live" : ""}`}
                onClick={() => goTab("produce", { produce: "assets" })}
                title={`语义分析 ${semanticPct}%`}
              >
                分析 {semanticPct}%
              </button>
            ) : null}
            <button type="button" className="pulse-chip" onClick={() => goTab("review")}>
              TTS {ttsBadCount}
            </button>
          </div>
          <NextActionPill
            engineOn={engineOn}
            engineBusy={engineBusy}
            health={health}
            engine={engine}
            readyCount={readyCount}
            pendingReview={pendingReviewCount}
            ttsBad={ttsBadCount}
            runningJobs={runningJobs}
            hasTodayPlan={Boolean(todayPlan)}
            onStartEngine={() => {
              void onEngineToggle();
            }}
            onSetTab={goTab}
          />
          <button type="button" title="助手" onClick={() => setAssistantOpen(true)}>
            <Bot size={16} aria-hidden /> 助手
          </button>
          <button type="button" title="命令面板 ⌘K" onClick={() => setCmdOpen(true)}>
            ⌘K
          </button>
          <button
            type="button"
            title="布局密度"
            onClick={() => {
              const order: LayoutDensityPref[] = ["auto", "comfort", "compact"];
              const i = order.indexOf(layout.pref);
              layout.setPref(order[(i + 1) % order.length]!);
            }}
          >
            {layout.pref === "auto" ? "自动" : layout.pref === "comfort" ? "舒适" : "紧凑"}
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
      {(flash || error) && (
        <div
          className={`banner ${flash ? (flash.kind === "err" ? "error" : flash.kind) : "error"}`}
          role="status"
          aria-live="polite"
        >
          <span className="banner-text">{flash?.text || error}</span>
          {flash?.actionLabel && flash.actionTab ? (
            <button
              type="button"
              className="banner-action"
              onClick={() => {
                goTab(flash.actionTab!);
                setFlash(null);
              }}
            >
              {flash.actionLabel}
            </button>
          ) : null}
          <button
            type="button"
            className="banner-dismiss"
            aria-label="关闭提示"
            onClick={() => {
              setFlash(null);
              if (!flash) setError("");
              else if (flash.kind === "err") setError("");
            }}
          >
            ×
          </button>
        </div>
      )}
      {!vectorization && health && (
        <div className="banner warn">
          向量化尚未开启。入库只会做规范化；语义检索与自动增量补索引需在「运维」手动开启（不会整库重算）。
        </div>
      )}
      {!engineOn && !engineBusy && (
        <div className="banner warn engine-offline-banner">
          <span>引擎离线 — 生产、扫描与审片同步需先启动引擎。</span>
          <button type="button" className="primary" onClick={() => void onEngineToggle()}>
            启动引擎
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
        {tab === "overview" && (
          <OverviewPage
            pipelineNodes={pipelineNodes}
            pathBlocked={pathBlocked}
            setTab={goTab}
            refreshAll={refreshAll}
            report={report}
            opsReport={opsReport}
            assets={assets}
            reachQuota={reachQuota}
            health={health}
            services={services}
            todayPlan={todayPlan}
            actionBusy={actionBusy}
            setActionBusy={setActionBusy}
            notify={notify}
            autoDaily={autoDaily}
            autoHour={autoHour}
            setShowWizard={setShowWizard}
            activityItems={activityItems}
          />
        )}
        {tab === "produce" && (
          <ProductionPage
            workspace={produceWorkspace}
            onWorkspaceChange={setProduceWorkspace}
            setTab={goTab}
            customerName={customerName}
            brandLogo={brandLogo}
            brandBusy={brandBusy}
            saveBrandLogo={saveBrandLogo}
            notify={notify}
            theme={theme}
            setTheme={setTheme}
            category={category}
            setCategory={setCategory}
            targetCount={targetCount}
            setTargetCount={setTargetCount}
            actionBusy={actionBusy}
            setActionBusy={setActionBusy}
            runDryRun={runDryRun}
            createJob={createJob}
            dryResult={dryResult}
            jobs={jobs}
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
            runSemanticBatch={runSemanticBatch}
            assets={assets}
            vectorization={vectorization}
          />
        )}
        {tab === "review" && (
          <ReviewPage
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
        )}
        {tab === "publish" && (
          <PublishPage
            workspace={publishWorkspace}
            onWorkspaceChange={setPublishWorkspace}
            setTab={goTab}
            notify={notify}
            refreshAll={refreshAll}
            outputs={outputs}
            mediaEpoch={mediaEpoch}
            voiceLang={voiceLang}
            setVoiceLang={setVoiceLang}
            subtitleLang={subtitleLang}
            setSubtitleLang={setSubtitleLang}
            subtitleBurn={subtitleBurn}
            setSubtitleBurn={setSubtitleBurn}
            dualSecondaryLang={dualSecondaryLang}
            setDualSecondaryLang={setDualSecondaryLang}
            langCatalog={langCatalog}
            exprSaveBusy={exprSaveBusy}
            exprDirty={exprDirty}
            activeCustomerId={activeCustomerId}
            saveExpressionPrefs={saveExpressionPrefs}
            packLast={packLast}
            packBusyId={packBusyId}
            exportPack={exportPack}
            reachMessageUnread={reachMessageUnread}
            reachInbox={reachInbox}
            reachItems={reachItems}
            reachMessageAccounts={reachMessageAccounts}
            reachQuota={reachQuota}
            quotaTotal={quotaTotal}
            setQuotaTotal={setQuotaTotal}
            quotaSplit={quotaSplit}
            setQuotaSplit={setQuotaSplit}
            quotaByPlatform={quotaByPlatform}
            setQuotaByPlatform={setQuotaByPlatform}
            quotaBusy={quotaBusy}
            reachPlatforms={reachPlatforms}
            saveReachQuota={saveReachQuota}
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
            chromeCreatePlatform={chromeCreatePlatform}
            setChromeCreatePlatform={setChromeCreatePlatform}
            chromeCreateCount={chromeCreateCount}
            setChromeCreateCount={setChromeCreateCount}
            chromeBusy={chromeBusy}
            reachCreateChromeProfiles={reachCreateChromeProfiles}
            chromeSelected={chromeSelected}
            reachSelectChromeProfile={reachSelectChromeProfile}
            chromeProfiles={chromeProfiles}
            chromeInstalled={chromeInstalled}
            reachOpenChromeProfile={reachOpenChromeProfile}
            reachBindMessageAccount={reachBindMessageAccount}
            autoUploadBusy={autoUploadBusy}
            reachStartAutoUpload={reachStartAutoUpload}
            reachCancelAutoUpload={reachCancelAutoUpload}
            refreshReach={refreshReach}
            chromeRoot={chromeRoot}
            autoUploadPhase={autoUploadPhase}
            autoUploadMsg={autoUploadMsg}
            reachPackDir={reachPackDir}
            setReachPackDir={setReachPackDir}
            queueBusy={queueBusy}
            reachEnqueueFromPack={reachEnqueueFromPack}
            reachMsg={reachMsg}
            reachOpenItem={reachOpenItem}
            reachMarkPublished={reachMarkPublished}
          />
        )}
        {tab === "messages" && (
          <MessagesPage
            setTab={goTab}
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
            onEnableNotifications={enableReachNotifications}
            onSaveNtfy={saveReachNtfy}
            onTestNtfy={testReachNtfy}
          />
        )}
        {tab === "data" && (
          <DataCenterPage
            setTab={goTab}
            report={report}
            opsReport={opsReport}
            assets={assets}
            reachQuota={reachQuota}
            semanticStatus={semanticStatus}
            titlePoolSummary={titlePoolSummary}
            events={events}
            notify={notify}
            refreshAll={refreshAll}
            activeCustomerId={activeCustomerId}
            setTitlePoolSummary={setTitlePoolSummary}
          />
        )}
        {tab === "ops" && (
          <OpsPage
            section={opsSection}
            onSectionChange={setOpsSection}
            setTab={goTab}
            notify={notify}
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
            vectorization={vectorization}
            enableVectorization={enableVectorization}
            disableVectorization={disableVectorization}
            health={health}
            semanticStatus={semanticStatus}
            runSemanticBatch={runSemanticBatch}
            syncStatus={syncStatus}
            setSyncStatus={setSyncStatus}
            syncDryRunBusy={syncDryRunBusy}
            setSyncDryRunBusy={setSyncDryRunBusy}
            syncDryRunHint={syncDryRunHint}
            setSyncDryRunHint={setSyncDryRunHint}
            services={services}
            setServices={setServices}
            events={events}
          />
        )}
        {tab === "settings" && (
          <SettingsPage
            section={settingsSection}
            onSectionChange={setSettingsSection}
            setTab={goTab}
            notify={notify}
            askConfirm={askConfirm}
            refreshAll={refreshAll}
            actionBusy={actionBusy}
            setActionBusy={setActionBusy}
            cursorApiKeyInput={cursorApiKeyInput}
            setCursorApiKeyInput={setCursorApiKeyInput}
            cursorKeyConfigured={cursorKeyConfigured}
            cursorKeyHint={cursorKeyHint}
            saveCursorApiKey={saveCursorApiKey}
            verifyCursorApiKey={verifyCursorApiKey}
            clearCursorApiKey={clearCursorApiKey}
            autoDaily={autoDaily}
            setAutoDaily={setAutoDaily}
            autoHour={autoHour}
            setAutoHour={setAutoHour}
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
            onCreatePassword={async (password) => {
              const next = await settingsPasswordCreate(password);
              setPasswordStatus(next);
              notify("高级密码已创建并解锁", "ok");
            }}
            onChangePassword={async (oldPassword, newPassword) => {
              const next = await settingsPasswordChange(oldPassword, newPassword);
              setPasswordStatus(next);
              notify("高级密码已修改", "ok");
            }}
          />
        )}
      </main>
    </AppShell>

    {assistantOpen ? (
      <>
        <div className="assistant-drawer-backdrop" onClick={() => setAssistantOpen(false)} />
        <div className="assistant-drawer" role="dialog" aria-label="助手">
          <div className="panel-head" style={{ padding: "12px 14px" }}>
            <h2>助手</h2>
            <button type="button" onClick={() => setAssistantOpen(false)}>
              关闭
            </button>
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <AssistantChat
              notify={notify}
              keyConfigured={cursorKeyConfigured}
              onGoOps={() => {
                setAssistantOpen(false);
                goTab("settings", { settings: "customer" });
              }}
              onConfirmReset={() =>
                askConfirm({
                  title: "开启新对话",
                  body: "当前会话上下文将清空。确定？",
                  confirmLabel: "开启新对话",
                  danger: true,
                })
              }
            />
          </div>
        </div>
      </>
    ) : null}

      <CommandPalette
        open={cmdOpen}
        onClose={() => setCmdOpen(false)}
        onSetTab={goTab}
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
