import { useCallback, useEffect, useRef, useState } from "react";
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
import { BrandLogoPanel, brandFromProfile, type BrandLogoState } from "./BrandLogoPanel";
import { ReachCoverSection } from "./ReachCoverSection";
import { PublishDesk } from "./PublishDesk";
import { AssistantChat } from "./AssistantChat";
import { bindOutputFileDrag } from "./mediaDrag";
import { previewCoverSrc, previewVideoSrc } from "./mediaPreview";
import { revealItemInDir } from "@tauri-apps/plugin-opener";
import "./App.css";

type Tab = "overview" | "produce" | "assets" | "review" | "pack" | "publish" | "reach" | "ops" | "assistant";
type FlashKind = "ok" | "err" | "info" | "warn";

const TABS: Array<[Tab, string, string]> = [
  ["overview", "总览", "01"],
  ["produce", "生产", "02"],
  ["assets", "素材", "03"],
  ["review", "审片", "04"],
  ["pack", "物料", "05"],
  ["publish", "发布", "06"],
  ["reach", "触达", "07"],
  ["ops", "运维", "08"],
  ["assistant", "助手", "09"],
];

async function pickDir(): Promise<string | null> {
  if (!isTauri()) {
    return window.prompt("请输入文件夹路径") || null;
  }
  const selected = await open({ directory: true, multiple: false });
  return typeof selected === "string" ? selected : null;
}

async function pickFile(): Promise<string | null> {
  if (!isTauri()) {
    return window.prompt("请输入词池文件路径 (.json/.md)") || null;
  }
  const selected = await open({
    multiple: false,
    filters: [{ name: "词池", extensions: ["json", "md", "txt"] }],
  });
  return typeof selected === "string" ? selected : null;
}

async function pickImageFile(): Promise<string | null> {
  if (!isTauri()) {
    return window.prompt("请输入封面图片路径 (.jpg/.png/.webp)") || null;
  }
  const selected = await open({
    multiple: false,
    filters: [{ name: "封面图", extensions: ["jpg", "jpeg", "png", "webp"] }],
  });
  return typeof selected === "string" ? selected : null;
}

function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [health, setHealth] = useState<Health | null>(null);
  const [services, setServices] = useState<ServicesStatus | null>(null);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState<{ kind: FlashKind; text: string } | null>(null);
  const flashTimerRef = useRef(0);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [reviewBusyId, setReviewBusyId] = useState<number | null>(null);
  const [assets, setAssets] = useState<Array<Record<string, unknown>>>([]);
  const [jobs, setJobs] = useState<Array<Record<string, unknown>>>([]);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [outputs, setOutputs] = useState<Array<Record<string, unknown>>>([]);
  const [report, setReport] = useState<ReportSummary | null>(null);
  const [opsReport, setOpsReport] = useState<Awaited<ReturnType<typeof api.reportOps>> | null>(null);
  const [reviewFilter, setReviewFilter] = useState<"all" | "missing_voice" | "missing_sub" | "tts_bad">("all");
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
  const [reachQuota, setReachQuota] = useState<Record<string, unknown> | null>(null);
  const [quotaTotal, setQuotaTotal] = useState(5);
  const [quotaByPlatform, setQuotaByPlatform] = useState<Record<string, number>>({});
  const [quotaSplit, setQuotaSplit] = useState(false);
  const [quotaBusy, setQuotaBusy] = useState(false);
  const [reachPackDir, setReachPackDir] = useState("");
  const [reachBusy, setReachBusy] = useState(false);
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
  /** Session-only dismiss so 5s health poll won't reopen the first-run wizard. */
  const wizardDismissedRef = useRef(false);

  const notify = useCallback((text: string, kind: FlashKind = "info") => {
    setFlash({ text, kind });
    if (kind === "err") setError(text);
    else setError("");
    window.clearTimeout(flashTimerRef.current);
    flashTimerRef.current = window.setTimeout(
      () => setFlash((cur) => (cur?.text === text ? null : cur)),
      kind === "err" ? 10000 : 4200,
    );
  }, []);

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

  const refreshHealth = useCallback(async () => {
    try {
      const h = await api.health();
      // Healthy poll: clear transport/connection sticky errors only — do not wipe action feedback.
      setError((prev) => {
        if (!prev) return prev;
        return /Failed to fetch|NetworkError|ECONNREFUSED|引擎启动|fetch failed|Load failed/i.test(prev)
          ? ""
          : prev;
      });
      setHealth(h);
      setVectorization(Boolean(h.vectorization_enabled));
      setAutoDaily(Boolean(h.auto_daily_enabled));
      setAutoHour(Number(h.auto_daily_hour ?? 9));
      setCursorKeyConfigured(Boolean(h.cursor_api_key_configured));
      setCursorKeyHint(String(h.cursor_api_key_hint || ""));
      if (h.paths && typeof h.paths === "object") {
        if (h.paths.cache_root) setCacheRoot(String(h.paths.cache_root));
        if (h.paths.render_root) setRenderRoot(String(h.paths.render_root));
        if (h.paths.data_root) setDataRoot(String(h.paths.data_root));
      }
      if (h.render_root) setRenderRoot(String(h.render_root));
      if (h.vector_store && typeof h.vector_store === "object" && "db" in h.vector_store) {
        setVectorDbPath(String((h.vector_store as { db?: string }).db || ""));
      }
      if (h.active_customer) setCustomerName(h.active_customer);
      if (h.active_customer_id) setActiveCustomerId(h.active_customer_id);
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
        setLibraryRoot(String(active.library_root || ""));
        setLibraryRootsText((active.library_roots || []).join("\n"));
        setOutputRoot(String(active.output_root || ""));
        setKeywordPackPath(String(active.keyword_pack_path || ""));
        setBrandLogo(brandFromProfile(active.profile || null));
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
        const expr = (active.profile?.expression || {}) as Record<string, unknown>;
        if (expr.voice_lang) setVoiceLang(String(expr.voice_lang));
        if (expr.subtitle_lang) setSubtitleLang(String(expr.subtitle_lang));
        if (expr.subtitle_burn) setSubtitleBurn(String(expr.subtitle_burn));
        if (expr.dual_secondary_lang) setDualSecondaryLang(String(expr.dual_secondary_lang));
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
    } catch (e) {
      notify(String(e), "err");
    }
  }, [refreshHealth, refreshReach, notify]);

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
      refreshHealth();
      refreshEngine();
    }, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [refreshAll, refreshEngine, refreshHealth]);

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

  function renderLangOptions(includeNone: boolean) {
    const REGION_ORDER = ["东亚", "东南亚", "南亚", "中东/北非", "欧亚", "欧美", "其他"];
    const rows = langCatalog.filter((l) => includeNone || l.code !== "none");
    const byRegion = new Map<string, typeof rows>();
    for (const row of rows) {
      const r = row.region || "其他";
      if (!byRegion.has(r)) byRegion.set(r, []);
      byRegion.get(r)!.push(row);
    }
    const ordered = [
      ...REGION_ORDER.filter((r) => byRegion.has(r)),
      ...[...byRegion.keys()].filter((r) => !REGION_ORDER.includes(r)),
    ];
    if (ordered.length === 0) {
      return (
        <>
          <option value="zh">中文（简体）</option>
          <option value="en">English</option>
          {includeNone ? <option value="none">关闭</option> : null}
        </>
      );
    }
    return ordered.map((region) => (
      <optgroup key={region} label={region}>
        {(byRegion.get(region) || []).map((l) => (
          <option key={l.code} value={l.code}>
            {l.label_zh}
            {l.code !== "none" && l.label_native && l.label_native !== l.label_zh
              ? ` · ${l.label_native}`
              : ""}
          </option>
        ))}
      </optgroup>
    ));
  }

  async function onEngineToggle() {
    if (engine?.healthy) {
      if (!window.confirm("停止引擎将中断正在进行的入库/渲染。确定？")) return;
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
      notify("路径与调度设置已保存", "ok");
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setActionBusy(null);
    }
  }

  async function enableVectorization() {
    const ok = window.confirm(
      "开启增量向量化：已有向量会保留，只补齐尚未索引的素材。\n" +
        "新片库会自然跑完全部缺口；老片库不会整库重算。\n" +
        "需要本机 Ollama。确定开启？",
    );
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

  async function disableVectorization() {
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
    if (!window.confirm("清除已保存的 Cursor API Key？")) return;
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

  async function finishWizard(enableVec: boolean) {
    if (!wizName.trim()) {
      notify("请填写客户名", "err");
      return;
    }
    // 标准外置盘布局：自动建目录 + 登记同步映射
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
    if (kw) {
      await api.updateCustomer(c.id, { keyword_pack_path: kw });
      try {
        await api.reloadKeywordsFromPath(c.id);
      } catch (e) {
        notify(`词池导入失败: ${e}`, "err");
      }
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
      notify(`客户「${c.name}」已就绪`, "ok");
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
    setActionBusy("switchCustomer");
    try {
      // Drop stale cards immediately so preview URLs aren't hit under the new active customer
      setOutputs([]);
      setAssets([]);
      setJobs([]);
      setMediaEpoch((n) => n + 1);
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
    try {
      const updated = await api.updateCustomer(activeCustomerId, { brand: next });
      setCustomers((prev) => prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)));
      if (updated.profile) setBrandLogo(brandFromProfile(updated.profile));
      notify("品牌标识已保存", "ok");
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
      notify(msg, "ok");
      await refreshAll();
      if (status === "approved") setTab("publish");
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
      notify(
        `已保存表达设置：旁白 ${String(expr.voice_lang || voiceLang)} / 字幕 ${String(expr.subtitle_lang || subtitleLang)} / ${burn}${
          burn === "burn_dual"
            ? `（双语副语言 ${String(expr.dual_secondary_lang || dualSecondaryLang)}）`
            : ""
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
        await api.updateCustomer(activeCustomerId, {
          expression: {
            voice_lang: voiceLang,
            subtitle_lang: subtitleLang,
            subtitle_burn: subtitleBurn,
            dual_secondary_lang: dualSecondaryLang,
          },
        });
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
    setReachBusy(true);
    setReachMsg("");
    try {
      const res = await api.reachFromPack({ pack_dir: reachPackDir.trim() });
      setReachMsg(`已入队 ${res.count} 条（默认人点发布；可选 Safari 辅助）`);
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachBusy(false);
    }
  }

  async function reachOpenItem(id: number) {
    setReachBusy(true);
    try {
      const res = await api.reachOpen(id, false, chromeSelected || undefined);
      setReachMsg(res.disclaimer || `已打开官方入口；粘贴卡：${res.paste_card || ""}`);
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachBusy(false);
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
    setReachBusy(true);
    try {
      const plat = chromeProfilePlatform(name);
      const res = await api.reachChromeSelect(name, plat);
      if (res.platform) setChromeCreatePlatform(res.platform);
      setReachMsg(`已选配置「${name}」${res.platform ? ` · 平台 ${res.platform}` : ""}`);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachBusy(false);
    }
  }

  async function reachCreateChromeProfiles() {
    const count = Math.max(1, Math.min(10, Number(chromeCreateCount) || 1));
    setReachBusy(true);
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
      setReachBusy(false);
    }
  }

  async function reachOpenChromeProfile() {
    if (!chromeSelected) {
      notify("请先选择 Chrome 本地配置", "err");
      return;
    }
    setReachBusy(true);
    try {
      const plat = chromeProfilePlatform(chromeSelected) || chromeCreatePlatform;
      await api.reachChromeSelect(chromeSelected, plat);
      const res = await api.reachChromeOpen(chromeSelected, plat, false);
      setReachMsg(res.disclaimer || `已用「${chromeSelected}」打开 ${res.platform || plat} 官方页`);
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachBusy(false);
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
    setReachBusy(true);
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
      // poll until terminal
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
    } finally {
      setReachBusy(false);
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
    setReachBusy(true);
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
      setReachBusy(false);
    }
  }

  async function coverSelectTemplate(id: string) {
    setReachBusy(true);
    try {
      await api.coverTemplateSelect(id);
      setCoverSelectedId(id);
      setCoverEditId(id);
      setReachMsg("已设为当前发布封面模板");
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachBusy(false);
    }
  }

  async function coverDeleteTemplate(id: string) {
    if (!window.confirm("删除该封面模板套？")) return;
    setReachBusy(true);
    try {
      await api.coverTemplateDelete(id);
      setReachMsg("已删除封面模板");
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachBusy(false);
    }
  }

  async function coverSetSlot(platform: string, slotIndex: number) {
    if (!coverEditId) {
      notify("请先选择或创建封面模板", "err");
      return;
    }
    const path = await pickImageFile();
    if (!path) return;
    setReachBusy(true);
    try {
      await api.coverTemplateSetSlot(coverEditId, platform, slotIndex, path);
      setReachMsg(`已写入 ${platform} 槽位 ${slotIndex}`);
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachBusy(false);
    }
  }

  async function coverSeedFromPack() {
    if (!coverEditId) {
      notify("请先选择封面模板", "err");
      return;
    }
    const dir = reachPackDir.trim() || (await pickDir());
    if (!dir) return;
    setReachBusy(true);
    try {
      await api.coverTemplateSeed(coverEditId, dir);
      setReachMsg("已从物料包灌入空槽封面");
      await refreshReach();
    } catch (e) {
      notify(String(e), "err");
    } finally {
      setReachBusy(false);
    }
  }

  async function coverPreviewResolve() {
    setReachBusy(true);
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
      setReachBusy(false);
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
      ? "运行中 · :8766"
      : engine?.running
        ? "启动中…"
        : "已停止";
  const statusSummary = !engineOn
    ? "引擎离线"
    : [
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

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <div className="brand-mark">
            <h1 className="brand-name">速影</h1>
            <span className="brand-ver">SUYING</span>
          </div>
          <p className="brand-tag">本地智能混剪控制台</p>
        </div>
        <nav className="nav">
          {TABS.map(([t, label, idx]) => (
            <button
              key={t}
              type="button"
              className={`nav-btn${tab === t ? " active" : ""}`}
              onClick={() => setTab(t)}
            >
              <span className="nav-idx">{idx}</span>
              {label}
            </button>
          ))}
        </nav>
        <div className="rail-foot">
          <div className="rail-power">
            <button
              type="button"
              className={`toggle toggle--rail${engineOn ? " on" : ""}${engineBusy ? " is-busy" : ""}`}
              role="switch"
              aria-checked={engineOn}
              disabled={engineBusy}
              title={
                engineOn
                  ? "停止引擎（关 App 不会杀引擎）"
                  : `启动本地混剪引擎\n${engineDetail}`
              }
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
            {engineOn && (
              <>
                <div className="rail-health-row">
                  <span className={`health-dot ${services?.watcher ? "ok" : "warn"}`} />
                  片库监视 {services?.watcher ? "运行" : "已停"}
                </div>
                <div className="rail-health-row">
                  <span className={`health-dot ${services?.worker ? "ok" : "warn"}`} />
                  Worker {services?.worker ? "运行" : "已停"}
                </div>
                <div className="rail-health-row">
                  <span className={`health-dot ${services?.scheduler ? "ok" : "warn"}`} />
                  调度器 {services?.scheduler ? "运行" : "已停"}
                </div>
                <div className="rail-health-row">
                  <span className={`health-dot ${vectorization ? "ok" : "warn"}`} />
                  向量化 {vectorization ? "已开" : "关闭"}
                </div>
              </>
            )}
          </div>
          <div className="rail-port" title={engine?.python || ""}>
            :8766 · LOCAL
          </div>
        </div>
      </aside>

      <header className="topbar">
        <div className="topbar-title">
          <h2>{tabLabel}</h2>
          <span>
            {customerName || "未选择客户"} · {vectorization ? "向量化开" : "向量化关"}
          </span>
        </div>
        <div className="topbar-actions">
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
        </div>
      </header>

      <div className={`workspace${tab === "assistant" ? " workspace--assistant" : ""}`}>
        {(flash || error) && (
          <div
            className={`banner ${flash ? (flash.kind === "err" ? "error" : flash.kind) : "error"}`}
            role="status"
            aria-live="polite"
          >
            <span className="banner-text">{flash?.text || error}</span>
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

        {showWizard && (
          <div className="panel wizard">
            <div className="panel-head">
              <h2>欢迎使用速影</h2>
            </div>
            <p className="wizard-lead">
              填写客户名后可一键生成外置盘标准目录（片库/成片/词池）并登记极空间同步。亦可手选路径。向量化默认关闭。
            </p>
            <div className="actions" style={{ marginBottom: 12 }}>
              <button type="button" className="primary" onClick={() => applyStandardLayout()}>
                使用标准外置盘布局
              </button>
              <span className="hint">需已挂载 QR 外置盘（~/QR-Volume）</span>
            </div>
            <div className="grid2">
              <label>
                客户名称
                <input value={wizName} onChange={(e) => setWizName(e.target.value)} placeholder="例如：我的门店" />
              </label>
              <label>
                词池文件（json/md）
                <div className="path-row">
                  <input value={wizKw} onChange={(e) => setWizKw(e.target.value)} />
                  <button type="button" onClick={() => pickFile().then((p) => p && setWizKw(p))}>
                    选择
                  </button>
                </div>
              </label>
              <label>
                片库目录
                <div className="path-row">
                  <input value={wizLib} onChange={(e) => setWizLib(e.target.value)} />
                  <button type="button" onClick={() => pickDir().then((p) => p && setWizLib(p))}>
                    选择
                  </button>
                </div>
              </label>
              <label>
                输出目录
                <div className="path-row">
                  <input value={wizOut} onChange={(e) => setWizOut(e.target.value)} />
                  <button type="button" onClick={() => pickDir().then((p) => p && setWizOut(p))}>
                    选择
                  </button>
                </div>
              </label>
            </div>
            <div className="actions">
              <button type="button" onClick={() => finishWizard(false).catch((e) => notify(String(e), "err"))}>
                完成（稍后开启向量化）
              </button>
              <button
                type="button"
                className="primary"
                onClick={() => finishWizard(true).catch((e) => notify(String(e), "err"))}
              >
                完成并开启向量化
              </button>
              <button
                type="button"
                onClick={() => {
                  wizardDismissedRef.current = true;
                  setShowWizard(false);
                  // Persist so the wizard never returns once paths already exist
                  if (customers.some((c) => c.library_root && c.output_root)) {
                    api.updateSettings({ onboarded: true }).catch(() => undefined);
                  }
                }}
              >
                稍后再说
              </button>
            </div>
          </div>
        )}

        <main className={tab === "assistant" ? "assistant-shell" : "panel"}>
          {tab === "overview" && (
            <section>
              <div className="panel-head">
                <h2>总览</h2>
                <button type="button" onClick={refreshAll}>
                  刷新
                </button>
              </div>
              <div className="stat-grid">
                <div className="stat">
                  <div className="stat-label">素材</div>
                  <div className="stat-value">{report?.assets ?? assets.length}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">Cliplet</div>
                  <div className="stat-value">
                    {report ? `${report.cliplets_indexed}/${report.cliplets}` : "—"}
                  </div>
                </div>
                <div className="stat">
                  <div className="stat-label">Ready 成片</div>
                  <div className="stat-value">{opsReport?.ready ?? report?.ready ?? 0}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">失败率</div>
                  <div className="stat-value">
                    {opsReport
                      ? `${(opsReport.failure_rate * 100).toFixed(1)}%`
                      : report
                        ? `${(report.failure_rate * 100).toFixed(1)}%`
                        : "—"}
                  </div>
                </div>
                <div className="stat">
                  <div className="stat-label">旁白覆盖</div>
                  <div className="stat-value" style={{ fontSize: "1rem" }}>
                    {opsReport ? `${(opsReport.voice_coverage * 100).toFixed(0)}%` : "—"}
                  </div>
                </div>
              </div>
              <div className="ops-bar">
                <span>
                  Ready 率{" "}
                  {opsReport ? `${(opsReport.ready_rate * 100).toFixed(0)}%` : "—"}
                  {" · "}
                  缺旁白 {opsReport?.missing_voice ?? "—"}
                  {" · "}
                  <span
                    className={
                      opsReport && (opsReport.tts_noncompliant ?? 0) > 0 ? "health-line is-warn" : undefined
                    }
                  >
                    音色违规 {opsReport?.tts_noncompliant ?? "—"}
                    {opsReport?.tts_say != null ? ` (say ${opsReport.tts_say})` : ""}
                  </span>
                  {" · "}
                  已发 {opsReport?.published_ready ?? "—"}
                  {" · "}
                  触达配额{" "}
                  {opsReport?.quota
                    ? `${String(opsReport.quota.used_total ?? opsReport.quota.used_today ?? 0)}/${String(opsReport.quota.daily_quota ?? "—")}`
                    : reachQuota
                      ? `${String(reachQuota.used_total ?? reachQuota.used_today ?? 0)}/${String(reachQuota.daily_quota ?? "—")}`
                      : "—"}
                </span>
                <span className={`health-line${opsReport && (!opsReport.library_ok || !opsReport.output_ok || (opsReport.tts_noncompliant ?? 0) > 0) ? " is-warn" : ""}`}>
                  {opsReport?.health_line ||
                    (health
                      ? `引擎正常 · 磁盘 ${health.path_health.free_disk_gb.toFixed(0)} GB`
                      : "引擎状态未知")}
                </span>
              </div>
              {todayPlan ? (
                <div className="banner-ok">
                  今日计划 {String(todayPlan.day)} · {String(todayPlan.theme)} · 配额{" "}
                  {String(todayPlan.quota)}
                </div>
              ) : (
                <div className="banner error">今日无日历计划</div>
              )}
              {health && !health.path_health.ok && (
                <div className="banner error">
                  路径异常，禁止生产：{(health.path_health.errors || []).join("；") || "请检查片库/成片目录"}
                </div>
              )}
              <div className="actions">
                <button
                  type="button"
                  className={`primary${actionBusy === "calJob" ? " is-busy" : ""}`}
                  disabled={actionBusy === "calJob" || (health ? !health.path_health.ok : false)}
                  onClick={() => {
                    setActionBusy("calJob");
                    api
                      .createJobFromCalendar()
                      .then(() => {
                        notify("已按今日日历创建生产任务", "ok");
                        return refreshAll();
                      })
                      .catch((e) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  {actionBusy === "calJob" ? "创建中…" : "按今日日历开跑"}
                </button>
                <button type="button" onClick={() => setTab("produce")}>
                  去生产
                </button>
                <button type="button" onClick={() => setTab("publish")}>
                  去发布台
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowWizard(false);
                    setTab("ops");
                  }}
                >
                  修改路径（运维）
                </button>
              </div>
              <ul className="meta">
                <li>
                  监视 {services?.watcher || health?.watcher_running ? "开" : "关"} · Worker{" "}
                  {services?.worker || health?.worker_running ? "开" : "关"} · 调度{" "}
                  {services?.scheduler || health?.scheduler_running ? "开" : "关"} · 每日自动{" "}
                  {autoDaily ? `开 @${autoHour}:00` : "关"}
                </li>
              </ul>
            </section>
          )}

          {tab === "produce" && (
            <section>
              <div className="panel-head">
                <h2>生产</h2>
              </div>
              <BrandLogoPanel
                customerName={customerName}
                state={brandLogo}
                busy={brandBusy}
                onChange={(patch) => saveBrandLogo(patch).catch((e) => notify(String(e), "err"))}
              />
              <h3 className="section-title">手动任务</h3>
              <div className="grid3">
                <label>
                  主题
                  <input value={theme} onChange={(e) => setTheme(e.target.value)} />
                </label>
                <label>
                  分类
                  <input value={category} onChange={(e) => setCategory(e.target.value)} />
                </label>
                <label>
                  目标条数
                  <input
                    type="number"
                    value={targetCount}
                    onChange={(e) => setTargetCount(Number(e.target.value))}
                  />
                </label>
              </div>
              <div className="actions">
                <button
                  type="button"
                  onClick={() => runDryRun()}
                  disabled={actionBusy === "dryRun"}
                  className={actionBusy === "dryRun" ? "is-busy" : undefined}
                >
                  {actionBusy === "dryRun" ? "试跑中…" : "Dry-run"}
                </button>
                <button
                  type="button"
                  className={`primary${actionBusy === "createJob" ? " is-busy" : ""}`}
                  onClick={() => createJob()}
                  disabled={actionBusy === "createJob"}
                >
                  {actionBusy === "createJob" ? "创建中…" : "创建任务"}
                </button>
              </div>
              {dryResult && <pre>{JSON.stringify(dryResult, null, 2)}</pre>}
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>状态</th>
                    <th>已产出</th>
                    <th>主题</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((j) => (
                    <tr key={String(j.id)}>
                      <td>{String(j.id)}</td>
                      <td>{String(j.status)}</td>
                      <td>{String(j.produced_count)}</td>
                      <td>{String(j.theme)}</td>
                      <td className="actions-inline">
                        <button
                          type="button"
                          onClick={() =>
                            api
                              .pauseJob(Number(j.id))
                              .then(() => {
                                notify(`任务 #${j.id} 已暂停`, "ok");
                                return refreshAll();
                              })
                              .catch((e) => notify(String(e), "err"))
                          }
                        >
                          暂停
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            api
                              .resumeJob(Number(j.id))
                              .then(() => {
                                notify(`任务 #${j.id} 已恢复`, "ok");
                                return refreshAll();
                              })
                              .catch((e) => notify(String(e), "err"))
                          }
                        >
                          恢复
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <h3 className="section-title">内容日历</h3>
              <div className="grid3">
                <label>
                  日期
                  <input type="date" value={calDay} onChange={(e) => setCalDay(e.target.value)} />
                </label>
                <label>
                  主题
                  <input value={calTheme} onChange={(e) => setCalTheme(e.target.value)} />
                </label>
                <label>
                  配额
                  <input
                    type="number"
                    value={calQuota}
                    onChange={(e) => setCalQuota(Number(e.target.value))}
                  />
                </label>
              </div>
              <label>
                备注
                <input value={calNote} onChange={(e) => setCalNote(e.target.value)} />
              </label>
              <div className="actions">
                <button
                  type="button"
                  disabled={actionBusy === "calSave"}
                  className={actionBusy === "calSave" ? "is-busy" : undefined}
                  onClick={() => saveCalendarDay()}
                >
                  {actionBusy === "calSave" ? "保存中…" : "保存该日"}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    api
                      .deleteCalendar(calDay)
                      .then(() => {
                        notify(`已删除日历日：${calDay}`, "ok");
                        return refreshAll();
                      })
                      .catch((e) => notify(String(e), "err"))
                  }
                >
                  删除该日
                </button>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>主题</th>
                    <th>配额</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  {calendar.map((c) => (
                    <tr
                      key={String(c.day)}
                      style={{ cursor: "pointer" }}
                      onClick={() => {
                        setCalDay(String(c.day));
                        setCalTheme(String(c.theme || "default"));
                        setCalQuota(Number(c.quota || 5));
                        setCalNote(String(c.note || ""));
                      }}
                    >
                      <td>{String(c.day)}</td>
                      <td>{String(c.theme)}</td>
                      <td>{String(c.quota)}</td>
                      <td>{String(c.note || "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {tab === "assets" && (
            <section>
              <div className="panel-head">
                <h2>素材库</h2>
                <span className="count">{assets.length} ASSETS</span>
              </div>
              <div className="actions">
                <button
                  type="button"
                  disabled={actionBusy === "scanFull"}
                  className={actionBusy === "scanFull" ? "is-busy" : undefined}
                  onClick={() => {
                    setActionBusy("scanFull");
                    api
                      .scanAssets(0)
                      .then((r) => {
                        notify(`全量扫描已触发：${JSON.stringify(r)}`, "ok");
                        return refreshAll();
                      })
                      .catch((e) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  {actionBusy === "scanFull" ? "扫描中…" : "全量扫描"}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    api
                      .scanStatus()
                      .then((r) => notify(`扫描状态：${JSON.stringify(r)}`, "info"))
                      .catch((e) => notify(String(e), "err"))
                  }
                >
                  扫描进度
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={!vectorization || actionBusy === "reconcile"}
                  title={vectorization ? "" : "请先在运维页开启向量化"}
                  onClick={() => {
                    setActionBusy("reconcile");
                    api
                      .reconcileAssets(50, "incremental")
                      .then((r) => {
                        const gaps = (r.gaps_after || r.gaps_before || {}) as Record<string, unknown>;
                        notify(
                          `增量补齐: 处理 ${r.processed ?? 0}，新建片段 ${r.created_cliplets ?? 0}，新向量 ${r.indexed ?? 0}，保留已有 ${r.embeddings_kept ?? 0}；剩余缺口素材 ${gaps.gap_assets ?? "?"}`,
                          "ok",
                        );
                        return refreshAll();
                      })
                      .catch((e) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  {actionBusy === "reconcile" ? "补齐中…" : "增量补齐向量"}
                </button>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>分类</th>
                    <th>时长</th>
                    <th>分辨率</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {assets.map((a) => (
                    <tr key={String(a.id)}>
                      <td>{String(a.id)}</td>
                      <td>{String(a.category)}</td>
                      <td>{String(a.duration_sec ?? "-")}s</td>
                      <td>
                        {String(a.width)}x{String(a.height)}
                      </td>
                      <td>{String(a.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {tab === "review" && (
            <section>
              <div className="panel-head">
                <h2>成片抽检</h2>
                <span className="count">{readyOutputs.length} PENDING</span>
              </div>
              <p className="hint">
                新渲成片默认含旁白+烧录字幕。旧片显示「无旁白/无烧录字幕」时，用「仅重渲」或下方批量补齐后再过审。过审后可到「发布」台复制文案并拖到网页。
              </p>
              <div className="actions-inline" style={{ marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
                <label>
                  筛选
                  <select
                    value={reviewFilter}
                    onChange={(e) => setReviewFilter(e.target.value as typeof reviewFilter)}
                  >
                    <option value="all">全部待抽检</option>
                    <option value="missing_voice">仅无旁白（{missingVoiceCount}）</option>
                    <option value="missing_sub">仅无烧录字幕</option>
                    <option value="tts_bad">音色违规 say（{ttsBadCount}）</option>
                  </select>
                </label>
                <button
                  type="button"
                  className={`primary${batchBusy ? " is-busy" : ""}`}
                  disabled={batchBusy || missingVoiceCount === 0}
                  onClick={async () => {
                    setBatchBusy(true);
                    try {
                      const r = await api.batchRerender({
                        missing_voice: true,
                        missing_subtitle: true,
                        limit: 20,
                        reason: "ops_voice_subtitle",
                      });
                      notify(
                        `已排队重渲 ${r.count} 条${r.errors.length ? `，失败 ${r.errors.length}` : ""}`,
                        r.count ? "ok" : "warn",
                      );
                      await refreshAll();
                    } catch (e) {
                      notify(String(e), "err");
                    } finally {
                      setBatchBusy(false);
                    }
                  }}
                >
                  {batchBusy ? "排队中…" : `批量补旁白/字幕（≤20）`}
                </button>
                <button
                  type="button"
                  className={`${batchBusy ? "is-busy" : ""}`}
                  disabled={batchBusy || ttsBadCount === 0}
                  onClick={async () => {
                    setBatchBusy(true);
                    try {
                      const r = await api.batchRerender({
                        noncompliant_tts: true,
                        missing_voice: false,
                        missing_subtitle: false,
                        limit: 20,
                        reason: "ops_tts_noncompliant",
                      });
                      notify(
                        `音色违规已排队 Edge 重渲 ${r.count} 条${r.errors.length ? `，失败 ${r.errors.length}` : ""}`,
                        r.count ? "ok" : "warn",
                      );
                      await refreshAll();
                    } catch (e) {
                      notify(String(e), "err");
                    } finally {
                      setBatchBusy(false);
                    }
                  }}
                >
                  {batchBusy ? "排队中…" : `批量重渲音色违规→Edge（≤20）`}
                </button>
              </div>
              <label>
                打回原因
                <select value={reviewReason} onChange={(e) => setReviewReason(e.target.value)}>
                  {reviewReasons.map((r) => (
                    <option key={r.code} value={r.code}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                批注
                <input value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} />
              </label>
              <div className="review-list">
                {readyOutputs.map((o) => {
                  const covers = (o.covers as string[]) || [];
                  const oid = Number(o.id);
                  const videoPath = String(o.output_path || "");
                  const mediaOk = o.media_ok !== false && Boolean(videoPath);
                  return (
                    <article key={String(o.id)} className="review-card">
                      <div className="review-meta">
                        <strong>#{String(o.id)}</strong>
                        <span>{String(o.state)}</span>
                        <span>{String(o.title || "(无标题)")}</span>
                        {o.has_voice ? <em className="badge-ok">旁白</em> : <em className="badge-mute">无旁白</em>}
                        {o.subtitle_burned ? (
                          <em className="badge-ok">字幕</em>
                        ) : (
                          <em className="badge-mute">无烧录字幕</em>
                        )}
                        {o.published || (Array.isArray(o.publish_trail) && o.publish_trail.length > 0) ? (
                          <em className="badge-ok">
                            已发
                            {Array.isArray(o.publish_trail) && o.publish_trail[0]
                              ? ` ${(o.publish_trail[0] as { platform?: string }).platform || ""}`
                              : ""}
                          </em>
                        ) : null}
                        {(o.tts_noncompliant || o.tts_compliant === false) && (
                          <em className="badge-warn">
                            音色违规 {String(o.tts_provider || o.tts_noncompliant || "say")}
                          </em>
                        )}
                        {!mediaOk && <span className="badge-warn">文件缺失</span>}
                      </div>
                      <div className="review-preview">
                        {mediaOk ? (
                          <div className="review-media-col">
                            <video
                              key={`v-${mediaEpoch}-${oid}`}
                              className="review-video"
                              controls
                              preload="metadata"
                              playsInline
                              src={previewVideoSrc(
                                oid,
                                videoPath,
                                `${activeCustomerId ?? 0}-${mediaEpoch}`,
                              )}
                              draggable
                              onDragStart={(e) =>
                                bindOutputFileDrag(e, { id: oid, path: videoPath, mediaOk })
                              }
                              onError={() =>
                                notify(
                                  `成片 #${oid} 预览失败：可点「访达中显示」直接打开文件；若持续失败请重启 App`,
                                  "warn",
                                )
                              }
                            />
                            <div
                              className="publish-drag-zone"
                              draggable
                              onDragStart={(e) =>
                                bindOutputFileDrag(e, { id: oid, path: videoPath, mediaOk })
                              }
                              title="拖到浏览器上传框；无效时用「访达中显示」再拖文件"
                            >
                              <strong>拖拽成片</strong>
                              <span>按住拖向网页上传框；推荐访达中拖文件</span>
                            </div>
                          </div>
                        ) : (
                          <div className="review-video missing">
                            <p>成片文件不在磁盘</p>
                            <p className="hint">路径失效或未同步，无法预览；可重渲生成</p>
                          </div>
                        )}
                        {covers.length > 0 && mediaOk && (
                          <div className="cover-row">
                            {covers.map((coverPath, i) => (
                              <img
                                key={`${mediaEpoch}-${oid}-cover-${i}`}
                                src={previewCoverSrc(
                                  oid,
                                  i,
                                  typeof coverPath === "string" ? coverPath : null,
                                  `${activeCustomerId ?? 0}-${mediaEpoch}`,
                                )}
                                alt=""
                                className="cover-thumb"
                              />
                            ))}
                          </div>
                        )}
                      </div>
                      <p className="path">{videoPath}</p>
                      <div className="actions-inline">
                        <button
                          type="button"
                          onClick={async () => {
                            if (!videoPath) {
                              notify("无本地路径", "err");
                              return;
                            }
                            try {
                              if (isTauri()) {
                                await revealItemInDir(videoPath);
                                notify("已在访达中显示，可拖到网页上传", "ok");
                              } else {
                                notify(`本地路径：${videoPath}`, "info");
                              }
                            } catch (e) {
                              notify(String(e), "err");
                            }
                          }}
                          disabled={!mediaOk}
                        >
                          访达中显示
                        </button>
                        <button
                          type="button"
                          className={`primary${reviewBusyId === oid ? " is-busy" : ""}`}
                          onClick={() => decideReview(oid, "approved")}
                          disabled={!mediaOk || reviewBusyId === oid}
                          title={!mediaOk ? "成片文件缺失，无法通过" : undefined}
                        >
                          {reviewBusyId === oid ? "处理中…" : "通过"}
                        </button>
                        <button
                          type="button"
                          onClick={() => decideReview(oid, "rejected")}
                          disabled={reviewBusyId === oid}
                          className={reviewBusyId === oid ? "is-busy" : undefined}
                        >
                          打回
                        </button>
                        <button
                          type="button"
                          className={`primary${reviewBusyId === oid ? " is-busy" : ""}`}
                          onClick={() => decideReview(oid, "rejected", true)}
                          disabled={reviewBusyId === oid}
                        >
                          打回并重渲
                        </button>
                        <button
                          type="button"
                          onClick={() => rerenderOnly(oid)}
                          disabled={reviewBusyId === oid}
                          className={reviewBusyId === oid ? "is-busy" : undefined}
                        >
                          仅重渲
                        </button>
                      </div>
                    </article>
                  );
                })}
                {readyOutputs.length === 0 && (
                  <div className="empty">
                    <p>暂无待抽检成片</p>
                    <button type="button" className="primary" onClick={() => setTab("produce")}>
                      去生产
                    </button>
                  </div>
                )}
              </div>
            </section>
          )}

          {tab === "pack" && (
            <section>
              <div className="panel-head">
                <h2>发布物料</h2>
                <span className="count">
                  {outputs.filter((o) => o.state === "ready").length} READY
                </span>
              </div>
              <p className="hint">
                为 ready 成片生成 publish_pack（视频、封面、四平台文案、字幕、禁词扫描）。人在回路，不自动发布。
                下方「旁白语言 / 字幕语言 / 字幕方式」会写入客户档案，并在「生产 / 重渲」时决定成片旁白与是否烧录字幕；导出物料包也会沿用同一套设置。
              </p>
              <div className="actions-inline" style={{ marginBottom: "0.75rem", flexWrap: "wrap", gap: "0.75rem" }}>
                <label>
                  旁白语言
                  <select value={voiceLang} onChange={(e) => setVoiceLang(e.target.value)}>
                    {renderLangOptions(true)}
                  </select>
                </label>
                <label>
                  字幕语言
                  <select value={subtitleLang} onChange={(e) => setSubtitleLang(e.target.value)}>
                    {renderLangOptions(true)}
                  </select>
                </label>
                <label>
                  字幕方式
                  <select value={subtitleBurn} onChange={(e) => setSubtitleBurn(e.target.value)}>
                    <option value="external">外挂 SRT（不烧录）</option>
                    <option value="burn_mono">单语烧录</option>
                    <option value="burn_dual">双语烧录</option>
                  </select>
                </label>
                {subtitleBurn === "burn_dual" ? (
                  <label>
                    双语副语言
                    <select
                      value={dualSecondaryLang}
                      onChange={(e) => setDualSecondaryLang(e.target.value)}
                    >
                      {renderLangOptions(false)}
                    </select>
                  </label>
                ) : null}
                <button
                  type="button"
                  className="primary"
                  disabled={exprSaveBusy || !activeCustomerId}
                  onClick={() => void saveExpressionPrefs()}
                >
                  {exprSaveBusy ? "保存中…" : "保存表达设置"}
                </button>
              </div>
              <p className="hint" style={{ marginTop: "-0.35rem" }}>
                旁白语言 = 成片语音；字幕语言 = 主字幕文案；双语烧录时「双语副语言」为第二行。共{" "}
                {Math.max(0, langCatalog.filter((l) => l.code !== "none").length)}{" "}
                种语言可选。改完请点「保存表达设置」，再去生产/重渲才会进成片。
              </p>
              {packLast && <p className="path">{packLast}</p>}
              <div className="review-list">
                {outputs
                  .filter((o) => o.state === "ready")
                  .map((o) => (
                    <article key={String(o.id)} className="review-card">
                      <div className="review-meta">
                        <strong>#{String(o.id)}</strong>
                        <span>{String(o.title || "(无标题)")}</span>
                        {o.has_voice ? <em className="badge-ok">旁白</em> : <em className="badge-mute">无旁白</em>}
                        {o.subtitle_burned ? (
                          <em className="badge-ok">字幕</em>
                        ) : (
                          <em className="badge-mute">无烧录字幕</em>
                        )}
                      </div>
                      <p className="path">{String(o.output_path)}</p>
                      <div className="actions-inline">
                        <button
                          type="button"
                          className={`primary${packBusyId === Number(o.id) ? " is-busy" : ""}`}
                          disabled={packBusyId === Number(o.id)}
                          onClick={() => exportPack(Number(o.id))}
                        >
                          {packBusyId === Number(o.id) ? "导出中…" : "导出物料包"}
                        </button>
                        <button type="button" onClick={() => setTab("publish")}>
                          去发布台
                        </button>
                      </div>
                    </article>
                  ))}
                {outputs.filter((o) => o.state === "ready").length === 0 && (
                  <div className="empty">
                    <p>暂无 ready 成片</p>
                    <button type="button" className="primary" onClick={() => setTab("produce")}>
                      去生产
                    </button>
                  </div>
                )}
              </div>
            </section>
          )}

          {tab === "publish" && (
            <PublishDesk
              outputs={outputs}
              onNotify={notify}
              onRefresh={refreshAll}
              onGoReach={() => setTab("reach")}
            />
          )}

          {tab === "reach" && (
            <section>
              <div className="panel-head">
                <h2>触达助手</h2>
                <span className="count">
                  未读 {reachInbox?.unread_count ?? 0} · 队列 {reachItems.length}
                </span>
              </div>
              <p className="hint">
                默认人点发布 · 选手台+数量按需建 Chrome 配置 · 封面模板多套按平台槽位 · 文案+封面齐套才允许自动点发 ·
                抖音可自动上传 / 其它平台开页或 CDP · 不提供绕检测 / Cookie 池 / 矩阵养号。详见 docs/REACH_NON_GOALS.md
                复制文案请先到「发布」台。
              </p>
              <div className="actions-inline" style={{ marginBottom: 12 }}>
                <button type="button" className="primary" onClick={() => setTab("publish")}>
                  去发布台复制文案
                </button>
              </div>
              {reachQuota && (
                <p className="path">
                  今日配额 {String(reachQuota.used_total ?? reachQuota.used_today)}/
                  {String(reachQuota.daily_quota)}
                  {reachQuota.blocked ? " · 已阻塞" : ""}
                </p>
              )}

              <div className="reach-quota panel-block">
                <h3>每日配额</h3>
                <p className="hint">
                  先设日总额，再按平台分配；各平台相加不能超过总额。关闭「按平台分配」时只限制总额。
                </p>
                <div className="actions-inline" style={{ marginBottom: 10, gap: 12, flexWrap: "wrap" }}>
                  <label className="field-inline">
                    日总额
                    <input
                      type="number"
                      min={0}
                      max={500}
                      value={quotaTotal}
                      disabled={quotaBusy}
                      onChange={(e) => setQuotaTotal(Number(e.target.value) || 0)}
                      style={{ width: 72 }}
                    />
                  </label>
                  <label className="field-inline" style={{ gap: 6 }}>
                    <input
                      type="checkbox"
                      checked={quotaSplit}
                      disabled={quotaBusy}
                      onChange={(e) => {
                        const on = e.target.checked;
                        setQuotaSplit(on);
                        if (on) {
                          const cur = Object.values(quotaByPlatform).reduce((a, b) => a + b, 0);
                          if (cur === 0 && reachPlatforms.length) {
                            const n = reachPlatforms.length;
                            const base = Math.floor(quotaTotal / n);
                            let rem = Math.max(0, quotaTotal - base * n);
                            const next: Record<string, number> = {};
                            for (const p of reachPlatforms) {
                              next[p.id] = base + (rem > 0 ? 1 : 0);
                              if (rem > 0) rem -= 1;
                            }
                            setQuotaByPlatform(next);
                          }
                        }
                      }}
                    />
                    按平台分配
                  </label>
                  <span className="hint">
                    {quotaSplit
                      ? `已分配 ${Object.values(quotaByPlatform).reduce((a, b) => a + b, 0)} / ${quotaTotal}`
                      : "仅限制总额"}
                  </span>
                </div>
                {quotaSplit && (
                  <div className="quota-plat-grid">
                    {reachPlatforms.map((p) => {
                      const usedRow = (
                        (reachQuota?.platforms as Array<Record<string, unknown>> | undefined) || []
                      ).find((r) => r.id === p.id);
                      const used = Number(usedRow?.used_today ?? 0);
                      return (
                        <label key={p.id} className="quota-plat-cell">
                          <span className="quota-plat-name">{p.short || p.label}</span>
                          <input
                            type="number"
                            min={0}
                            max={500}
                            value={quotaByPlatform[p.id] ?? 0}
                            disabled={quotaBusy}
                            onChange={(e) =>
                              setQuotaByPlatform((prev) => ({
                                ...prev,
                                [p.id]: Number(e.target.value) || 0,
                              }))
                            }
                          />
                          <span className="hint">今日已用 {used}</span>
                        </label>
                      );
                    })}
                  </div>
                )}
                {quotaSplit &&
                  Object.values(quotaByPlatform).reduce((a, b) => a + b, 0) > quotaTotal && (
                    <p className="hint" style={{ color: "var(--danger)" }}>
                      平台合计已超过日总额，请调整后再保存
                    </p>
                  )}
                <div className="actions-inline" style={{ marginTop: 10 }}>
                  <button
                    type="button"
                    className={`primary${quotaBusy ? " is-busy" : ""}`}
                    disabled={
                      quotaBusy ||
                      (quotaSplit &&
                        Object.values(quotaByPlatform).reduce((a, b) => a + b, 0) > quotaTotal)
                    }
                    onClick={() => saveReachQuota()}
                  >
                    {quotaBusy ? "保存中…" : "保存配额"}
                  </button>
                </div>
              </div>

              <ReachCoverSection
                platforms={reachPlatforms}
                templates={coverTemplates}
                editId={coverEditId}
                selectedId={coverSelectedId}
                newName={coverNewName}
                slotCounts={coverSlotCounts}
                slotSpecs={coverSlotSpecs}
                previewPlat={coverPreviewPlat}
                previewMsg={coverPreviewMsg}
                busy={reachBusy}
                onNewName={setCoverNewName}
                onEditId={setCoverEditId}
                onPreviewPlat={setCoverPreviewPlat}
                onCreate={coverCreateTemplate}
                onSeed={coverSeedFromPack}
                onSelect={coverSelectTemplate}
                onDelete={coverDeleteTemplate}
                onSetSlot={coverSetSlot}
                onPreview={coverPreviewResolve}
              />

              <div className="reach-chrome panel-block">
                <h3>Chrome 配置</h3>
              <div className="actions-inline" style={{ marginBottom: 12, gap: 8, flexWrap: "wrap" }}>
                <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span>平台</span>
                  <select
                    value={chromeCreatePlatform}
                    onChange={(e) => setChromeCreatePlatform(e.target.value)}
                    disabled={reachBusy}
                    style={{ minWidth: 120 }}
                  >
                    {reachPlatforms.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.short || p.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span>数量</span>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={chromeCreateCount}
                    onChange={(e) => setChromeCreateCount(Number(e.target.value) || 1)}
                    disabled={reachBusy}
                    style={{ width: 64 }}
                  />
                </label>
                <button type="button" className="primary" disabled={reachBusy} onClick={reachCreateChromeProfiles}>
                  创建配置
                </button>
              </div>
              <div className="actions-inline" style={{ marginBottom: 12, gap: 8, flexWrap: "wrap" }}>
                <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span>当前配置</span>
                  <select
                    value={chromeSelected}
                    onChange={(e) => reachSelectChromeProfile(e.target.value)}
                    disabled={reachBusy || chromeProfiles.length === 0}
                    style={{ minWidth: 160 }}
                  >
                    {chromeProfiles.length === 0 && <option value="">暂无本地配置</option>}
                    {chromeProfiles.map((p) => (
                      <option key={p.name} value={p.name}>
                        {p.platform ? `${p.name}（${p.platform}）` : p.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="primary"
                  disabled={reachBusy || !chromeSelected || !chromeInstalled}
                  onClick={reachOpenChromeProfile}
                >
                  打开官方页
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={reachBusy || autoUploadBusy || !chromeSelected || !chromeInstalled}
                  onClick={() => reachStartAutoUpload()}
                >
                  {autoUploadBusy ? "等待登录/上传中…" : "等待登录后自动上传"}
                </button>
                {autoUploadBusy ? (
                  <button type="button" onClick={reachCancelAutoUpload}>
                    取消自动上传
                  </button>
                ) : null}
                <button type="button" onClick={() => refreshReach()}>
                  刷新
                </button>
              </div>
              {chromeRoot ? (
                <p className="path">
                  配置目录 {chromeRoot}
                  {chromeInstalled ? "" : " · 未检测到 Google Chrome"}
                  {chromeSelected ? ` · 当前 ${chromeSelected}` : ""}
                </p>
              ) : null}
              {(autoUploadPhase || autoUploadMsg) && (
                <p className="path">
                  自动上传 {autoUploadPhase || "—"}: {autoUploadMsg || ""}
                </p>
              )}
              </div>
              <div className="actions-inline" style={{ marginBottom: 12, gap: 8, flexWrap: "wrap" }}>
                <input
                  value={reachPackDir}
                  onChange={(e) => setReachPackDir(e.target.value)}
                  placeholder="publish_pack 目录路径"
                  style={{ minWidth: 280, flex: 1 }}
                />
                <button type="button" className="primary" disabled={reachBusy} onClick={reachEnqueueFromPack}>
                  {reachBusy ? "处理中…" : "从物料包入队"}
                </button>
              </div>
              {reachMsg && <p className="path">{reachMsg}</p>}
              {(reachInbox?.notices || []).length > 0 && (
                <div className="review-list" style={{ marginBottom: 16 }}>
                  <h3>提醒</h3>
                  {(reachInbox?.notices || []).slice(0, 8).map((n) => (
                    <article key={String(n.id)} className="review-card">
                      <div className="review-meta">
                        <strong>{String(n.kind)}</strong>
                        <span>{String(n.summary)}</span>
                      </div>
                      {n.deep_link && typeof n.deep_link === "object" ? (
                        <p className="path">{String((n.deep_link as Record<string, unknown>).url || "")}</p>
                      ) : null}
                    </article>
                  ))}
                </div>
              )}
              <div className="review-list">
                {reachItems.map((it) => (
                  <article key={String(it.id)} className="review-card">
                    <div className="review-meta">
                      <strong>
                        #{String(it.id)} · {String(it.platform)}
                      </strong>
                      <span>{String(it.status)}</span>
                    </div>
                    <p>{String(it.title || "")}</p>
                    <p className="path">{String(it.video_path || "")}</p>
                    <div className="actions-inline">
                      <button
                        type="button"
                        className="primary"
                        disabled={reachBusy || it.status === "published" || it.status === "cancelled"}
                        onClick={() => reachOpenItem(Number(it.id))}
                      >
                        打开官方入口
                      </button>
                      <button
                        type="button"
                        disabled={
                          reachBusy ||
                          autoUploadBusy ||
                          !chromeSelected ||
                          !chromeInstalled ||
                          it.status === "published" ||
                          it.status === "cancelled"
                        }
                        onClick={() => reachStartAutoUpload(Number(it.id))}
                      >
                        此条自动上传
                      </button>
                      {it.status === "awaiting_human" && (
                        <button type="button" onClick={() => reachMarkPublished(Number(it.id))}>
                          我已发布
                        </button>
                      )}
                    </div>
                  </article>
                ))}
                {reachItems.length === 0 && <p className="empty">暂无触达队列项</p>}
              </div>
            </section>
          )}

          {tab === "assistant" && (
            <AssistantChat
              notify={notify}
              keyConfigured={cursorKeyConfigured}
              onGoOps={() => setTab("ops")}
            />
          )}

          {tab === "ops" && (
            <section>
              <div className="panel-head">
                <h2>运维与设置</h2>
              </div>

              <h3 className="section-title">向量化</h3>
              <div className="actions">
                {vectorization ? (
                  <button type="button" onClick={() => disableVectorization().catch((e) => notify(String(e), "err"))}>
                    关闭向量化
                  </button>
                ) : (
                  <button
                    type="button"
                    className="primary"
                    onClick={() => enableVectorization()}
                  >
                    手动开启向量化
                  </button>
                )}
                <span className="hint">
                  {vectorization
                    ? `增量模式已开${health?.vectorization_enabled_at ? ` · ${health.vectorization_enabled_at}` : ""}`
                    : "默认关闭；开启后仅增量补缺口，不整库重算"}
                </span>
              </div>

              <h3 className="section-title">Cursor API Key</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                用于速影内调用 Cursor Agent（auto 模型）。在{" "}
                <a href="https://cursor.com/dashboard/api" target="_blank" rel="noreferrer">
                  cursor.com/dashboard/api
                </a>{" "}
                创建 User API Key，粘贴后保存或验证即可。密钥保存在本机工作区，不会进同步目录。
              </p>
              <label>
                API Key
                <input
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={cursorApiKeyInput}
                  onChange={(e) => setCursorApiKeyInput(e.target.value)}
                  placeholder={
                    cursorKeyConfigured
                      ? `已保存 ${cursorKeyHint || "••••"} · 输入新密钥可覆盖`
                      : "粘贴 cursor_… 或 crsr_… 密钥"
                  }
                />
              </label>
              <div className="actions" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  className="primary"
                  disabled={actionBusy === "cursorKey" || actionBusy === "cursorVerify"}
                  onClick={() => saveCursorApiKey()}
                >
                  {actionBusy === "cursorKey" ? "保存中…" : "保存"}
                </button>
                <button
                  type="button"
                  disabled={actionBusy === "cursorKey" || actionBusy === "cursorVerify"}
                  onClick={() => verifyCursorApiKey()}
                >
                  {actionBusy === "cursorVerify" ? "验证中…" : "验证并启用"}
                </button>
                {cursorKeyConfigured ? (
                  <button
                    type="button"
                    disabled={actionBusy === "cursorClear"}
                    onClick={() => clearCursorApiKey()}
                  >
                    清除
                  </button>
                ) : null}
                <span className="hint">
                  {cursorKeyConfigured
                    ? `状态：已配置 ${cursorKeyHint || ""}`
                    : "状态：未配置"}
                </span>
              </div>

              <h3 className="section-title">极空间同步（预设置）</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                先绑定要同步的极空间账号+设备；客户端登录其他账号时同步会自动跳过，避免多台设备串数据。
              </p>
              <div className="actions">
                <button
                  type="button"
                  onClick={() =>
                    api
                      .zspaceSyncStatus()
                      .then((r) => {
                        setSyncStatus(r);
                        notify("同步状态已刷新", "ok");
                      })
                      .catch((e) => notify(String(e), "err"))
                  }
                >
                  刷新同步状态
                </button>
                <button
                  type="button"
                  className="primary"
                  onClick={() =>
                    api
                      .zspaceSyncInstall()
                      .then((r) => {
                        notify(`同步服务已安装: ${JSON.stringify(r)}`, "ok");
                        return api.zspaceSyncStatus().then(setSyncStatus);
                      })
                      .catch((e) => notify(String(e), "err"))
                  }
                >
                  安装 / 重装同步服务
                </button>
              </div>
              {syncStatus && (
                <div className="hint" style={{ margin: "8px 0 12px", whiteSpace: "pre-wrap" }}>
                  {[
                    `脚本: ${syncStatus.scripts_installed ? "已装" : "未装"}`,
                    `LaunchAgent: ${syncStatus.launchd_loaded ? `已加载(${syncStatus.launchd_state})` : "未加载"}`,
                    `外置盘同步根: ${syncStatus.local_root_exists ? "OK" : "未挂载"} · ${syncStatus.local_root}`,
                    `工作区: ${syncStatus.work_root_exists ? "OK" : "无"} · ${syncStatus.work_root}`,
                    `极空间代理: ${syncStatus.zspace_proxy_ok ? `OK :${syncStatus.zspace_proxy_port}` : "未通"}`,
                    `账号绑定: ${
                      syncStatus.zspace_match
                        ? "匹配"
                        : syncStatus.zspace_bound_ok
                          ? `未匹配 — ${syncStatus.zspace_reason || ""}`
                          : "未绑定"
                    }`,
                    (() => {
                      const b = syncStatus.zspace_bound as Record<string, string> | undefined;
                      return b?.username
                        ? `已绑定: ${b.username} / ${b.nas_id} (${b.nas_name || "—"})`
                        : "已绑定: —";
                    })(),
                    (() => {
                      const a = syncStatus.zspace_active as Record<string, string> | undefined;
                      return a?.username
                        ? `当前登录: ${a.username} / ${a.nas_id} (${a.nas_name || "—"})`
                        : "当前登录: 无";
                    })(),
                    `客户: ${Array.isArray(syncStatus.customers) ? (syncStatus.customers as string[]).join("、") || "—" : "—"}`,
                  ].join("\n")}
                </div>
              )}
              {Array.isArray(syncStatus?.zspace_accounts) && (syncStatus.zspace_accounts as unknown[]).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div className="hint" style={{ marginBottom: 6 }}>
                    选择绑定账号（来自极空间客户端历史 / 当前登录）
                  </div>
                  <div className="actions" style={{ flexWrap: "wrap" }}>
                    {(syncStatus.zspace_accounts as Array<Record<string, unknown>>).map((acc) => {
                      const username = String(acc.username || "");
                      const nasId = String(acc.nas_id || "");
                      const nasName = String(acc.nas_name || "");
                      const label = `${username} · ${nasName || nasId}${acc.active ? "（当前）" : ""}`;
                      return (
                        <button
                          key={`${username}-${nasId}`}
                          type="button"
                          onClick={() =>
                            api
                              .zspaceSyncBind({ username, nas_id: nasId, nas_name: nasName })
                              .then((r) => {
                                notify(
                                  r.active_matches
                                    ? `已绑定且与当前登录一致: ${username} / ${nasId}`
                                    : `已绑定 ${username} / ${nasId}。${r.hint || "请切换客户端登录该账号后再同步。"}`,
                                  r.active_matches ? "ok" : "warn",
                                );
                                return api.zspaceSyncStatus().then(setSyncStatus);
                              })
                              .catch((e) => notify(String(e), "err"))
                          }
                        >
                          绑定 {label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
              <div className="grid2" style={{ marginBottom: 16 }}>
                <label>
                  新建客户（标准布局）
                  <div className="path-row">
                    <input
                      value={newCustName}
                      onChange={(e) => setNewCustName(e.target.value)}
                      placeholder="客户全称"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        if (!newCustName.trim()) {
                          notify("请填写客户名", "err");
                          return;
                        }
                        api
                          .zspaceEnsureCustomer(newCustName.trim(), true)
                          .then(async (ensured) => {
                            const p = ensured.paths || {};
                            const c = await api.createCustomer({
                              name: newCustName.trim(),
                              library_root: String(p.library_root || ""),
                              output_root: String(p.output_root || ""),
                              keyword_pack_path: String(p.keyword_pack_path || "") || undefined,
                            });
                            await api.activateCustomer(c.name);
                            setNewCustName("");
                            notify(`已建客户并登记同步: ${c.name}`, "ok");
                            await refreshAll();
                            return api.zspaceSyncStatus().then(setSyncStatus);
                          })
                          .catch((e) => notify(String(e), "err"));
                      }}
                    >
                      创建并登记
                    </button>
                  </div>
                </label>
              </div>

              <h3 className="section-title">服务开关</h3>              <div className="actions">
                {(
                  [
                    ["watcher", "片库监视"],
                    ["worker", "任务 Worker"],
                    ["scheduler", "调度器"],
                  ] as const
                ).map(([name, label]) => {
                  const on =
                    name === "watcher"
                      ? services?.watcher
                      : name === "worker"
                        ? services?.worker
                        : services?.scheduler;
                  return (
                    <button
                      key={name}
                      type="button"
                      onClick={() =>
                        api
                          .serviceControl(name, on ? "stop" : "start")
                          .then(() => api.servicesStatus().then(setServices))
                          .catch((e) => notify(String(e), "err"))
                      }
                    >
                      {label}: {on ? "运行中 → 停止" : "已停 → 启动"}
                    </button>
                  );
                })}
              </div>

              <h3 className="section-title">每日自动开跑</h3>
              <div className="grid3">
                <label>
                  开关
                  <select value={autoDaily ? "1" : "0"} onChange={(e) => setAutoDaily(e.target.value === "1")}>
                    <option value="0">关闭</option>
                    <option value="1">开启</option>
                  </select>
                </label>
                <label>
                  整点
                  <input
                    type="number"
                    min={0}
                    max={23}
                    value={autoHour}
                    onChange={(e) => setAutoHour(Number(e.target.value))}
                  />
                </label>
              </div>

              <h3 className="section-title">路径与词池</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                首次配置完成后会记住；只需在变更片库/输出/词池时在此修改并保存。
              </p>
              <div className="grid2">
                <label>
                  主片库
                  <div className="path-row">
                    <input value={libraryRoot} onChange={(e) => setLibraryRoot(e.target.value)} />
                    <button type="button" onClick={() => pickDir().then((p) => p && setLibraryRoot(p))}>
                      选择
                    </button>
                  </div>
                </label>
                <label>
                  输出目录
                  <div className="path-row">
                    <input value={outputRoot} onChange={(e) => setOutputRoot(e.target.value)} />
                    <button type="button" onClick={() => pickDir().then((p) => p && setOutputRoot(p))}>
                      选择
                    </button>
                  </div>
                </label>
              </div>
              <label>
                追加片库（每行一个）
                <textarea rows={2} value={libraryRootsText} onChange={(e) => setLibraryRootsText(e.target.value)} />
              </label>
              <label>
                缓存目录（抽帧 / 代理 / 规范化）
                <div className="path-row">
                  <input
                    value={cacheRoot}
                    onChange={(e) => setCacheRoot(e.target.value)}
                    placeholder="/Users/…/速影工作区/cache"
                  />
                  <button type="button" onClick={() => pickDir().then((p) => p && setCacheRoot(p))}>
                    选择
                  </button>
                </div>
              </label>
              <label>
                渲染临时目录（不同步）
                <div className="path-row">
                  <input
                    value={renderRoot}
                    onChange={(e) => setRenderRoot(e.target.value)}
                    placeholder="/Users/…/速影工作区/render"
                  />
                  <button type="button" onClick={() => pickDir().then((p) => p && setRenderRoot(p))}>
                    选择
                  </button>
                </div>
              </label>
              <label>
                SQLite 数据目录（montage.db）
                <div className="path-row">
                  <input
                    value={dataRoot}
                    onChange={(e) => setDataRoot(e.target.value)}
                    placeholder="/Users/…/速影工作区/db"
                  />
                  <button type="button" onClick={() => pickDir().then((p) => p && setDataRoot(p))}>
                    选择
                  </button>
                </div>
              </label>
              <p className="hint" style={{ margin: "6px 0 12px" }}>
                当前库文件：{vectorDbPath || "…/montage.db"}（工作区在外置盘、不进极空间同步；外置盘请先挂载再开引擎）
              </p>
              <label>
                词池路径
                <div className="path-row">
                  <input value={keywordPackPath} onChange={(e) => setKeywordPackPath(e.target.value)} />
                  <button type="button" onClick={() => pickFile().then((p) => p && setKeywordPackPath(p))}>
                    选择
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      api
                        .reloadKeywordsFromPath(activeCustomerId ?? undefined)
                        .then(async (r) => {
                          notify(`词池已重载: ${JSON.stringify(r)}`, "ok");
                          try {
                            const s = await api.keywordsActiveSummary(activeCustomerId ?? undefined);
                            setTitlePoolSummary({
                              loaded: s.loaded,
                              title_pool_count: s.title_pool_count,
                              title_pool_sample: s.title_pool_sample || [],
                              hooks_count: s.hooks_count,
                              max_chars_per_line: s.max_chars_per_line,
                              version: s.version,
                            });
                          } catch {
                            /* ignore */
                          }
                        })
                        .catch((e) => notify(String(e), "err"))
                    }
                  >
                    从路径重载
                  </button>
                </div>
              </label>
              {titlePoolSummary ? (
                <div className="hint" style={{ margin: "8px 0 12px" }}>
                  <div style={{ marginBottom: 6 }}>
                    片上标题语库：{titlePoolSummary.loaded ? titlePoolSummary.title_pool_count : 0} 条
                    {titlePoolSummary.max_chars_per_line
                      ? `（单句≤${titlePoolSummary.max_chars_per_line}字）`
                      : ""}
                    {titlePoolSummary.version != null ? ` · 词池 v${titlePoolSummary.version}` : ""}
                    {` · hooks ${titlePoolSummary.hooks_count}`}
                    <button
                      type="button"
                      style={{ marginLeft: 8 }}
                      onClick={() =>
                        api
                          .keywordsActiveSummary(activeCustomerId ?? undefined)
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
                          .catch((e) => notify(String(e), "err"))
                      }
                    >
                      刷新预览
                    </button>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {(titlePoolSummary.title_pool_sample || []).slice(0, 24).map((t) => (
                      <span
                        key={t}
                        style={{
                          border: "1px solid var(--border, #ccc)",
                          borderRadius: 4,
                          padding: "2px 6px",
                          whiteSpace: "pre-line",
                          fontSize: 12,
                        }}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
              <div className="actions">
                <button
                  type="button"
                  className={`primary${actionBusy === "savePaths" ? " is-busy" : ""}`}
                  disabled={actionBusy === "savePaths"}
                  onClick={() => savePaths()}
                >
                  {actionBusy === "savePaths" ? "保存中…" : "保存设置"}
                </button>
                <button
                  type="button"
                  disabled={actionBusy === "cleanCache"}
                  className={actionBusy === "cleanCache" ? "is-busy" : undefined}
                  onClick={() => {
                    setActionBusy("cleanCache");
                    api
                      .cleanCache(24)
                      .then((r) => notify(`清理缓存: ${r.removed_files} 文件 / ${r.freed_mb} MB`, "ok"))
                      .catch((e) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  {actionBusy === "cleanCache" ? "清理中…" : "清理缓存"}
                </button>
                <button
                  type="button"
                  disabled={actionBusy === "scheduler"}
                  className={actionBusy === "scheduler" ? "is-busy" : undefined}
                  onClick={() => {
                    setActionBusy("scheduler");
                    api
                      .schedulerRunNow(true)
                      .then((r) => notify(`调度已触发: ${JSON.stringify(r)}`, "ok"))
                      .catch((e) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  立即按日历开跑
                </button>
              </div>

              <h3 className="section-title">日志</h3>
              <div className="actions">
                <button type="button" onClick={() => api.exportEvents()}>
                  导出事件 CSV
                </button>
                <button type="button" onClick={() => api.exportRenders()}>
                  导出成片 CSV
                </button>
                <button type="button" onClick={refreshAll}>
                  刷新
                </button>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>Job</th>
                    <th>级别</th>
                    <th>消息</th>
                  </tr>
                </thead>
                <tbody>
                  {events.slice(0, 40).map((e) => (
                    <tr key={String(e.id)}>
                      <td>{String(e.created_at)}</td>
                      <td>{String(e.job_id)}</td>
                      <td>{String(e.level)}</td>
                      <td>{String(e.message)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
