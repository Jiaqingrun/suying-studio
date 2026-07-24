import { useCallback, useEffect, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { api, Customer, Health, ReportSummary, ServicesStatus } from "./api";
import { getEngineStatus, isTauri, startEngine, stopEngine, type EngineStatus } from "./engineControl";
import "./App.css";

type Tab = "overview" | "produce" | "assets" | "review" | "pack" | "reach" | "ops";

const TABS: Array<[Tab, string, string]> = [
  ["overview", "总览", "01"],
  ["produce", "生产", "02"],
  ["assets", "素材", "03"],
  ["review", "审片", "04"],
  ["pack", "物料", "05"],
  ["reach", "触达", "06"],
  ["ops", "运维", "07"],
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

function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [health, setHealth] = useState<Health | null>(null);
  const [services, setServices] = useState<ServicesStatus | null>(null);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [error, setError] = useState("");
  const [assets, setAssets] = useState<Array<Record<string, unknown>>>([]);
  const [jobs, setJobs] = useState<Array<Record<string, unknown>>>([]);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [outputs, setOutputs] = useState<Array<Record<string, unknown>>>([]);
  const [report, setReport] = useState<ReportSummary | null>(null);
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
  const [cacheRoot, setCacheRoot] = useState("");
  const [renderRoot, setRenderRoot] = useState("");
  const [dataRoot, setDataRoot] = useState("");
  const [vectorDbPath, setVectorDbPath] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [activeCustomerId, setActiveCustomerId] = useState<number | null>(null);
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
  const [reachItems, setReachItems] = useState<Array<Record<string, unknown>>>([]);
  const [reachInbox, setReachInbox] = useState<{
    unread_count: number;
    notices: Array<Record<string, unknown>>;
  } | null>(null);
  const [reachQuota, setReachQuota] = useState<Record<string, unknown> | null>(null);
  const [reachPackDir, setReachPackDir] = useState("");
  const [reachBusy, setReachBusy] = useState(false);
  const [reachMsg, setReachMsg] = useState("");
  const [autoDaily, setAutoDaily] = useState(false);
  const [autoHour, setAutoHour] = useState(9);
  const [vectorization, setVectorization] = useState(false);
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
      setError("");
      const h = await api.health();
      setHealth(h);
      setVectorization(Boolean(h.vectorization_enabled));
      setAutoDaily(Boolean(h.auto_daily_enabled));
      setAutoHour(Number(h.auto_daily_hour ?? 9));
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
      setError(String(e));
      setHealth(null);
    }
  }, []);

  const refreshReach = useCallback(async () => {
    try {
      const [list, inbox, quota] = await Promise.all([
        api.reachList(),
        api.reachInbox(),
        api.reachQuota(),
      ]);
      setReachItems(list.items || []);
      setReachInbox({ unread_count: inbox.unread_count || 0, notices: inbox.notices || [] });
      setReachQuota(quota);
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
      setError(String(e));
    }
  }, [refreshHealth, refreshReach]);

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
          if (!cancelled) setError(`引擎启动: ${e}`);
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
          setError(st.message);
        }
      }
      await refreshEngine();
      await refreshHealth();
    } catch (e) {
      setError(String(e));
      await refreshEngine();
    } finally {
      setEngineBusy(false);
    }
  }

  async function savePaths() {
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
  }

  async function enableVectorization() {
    const ok = window.confirm(
      "开启增量向量化：已有向量会保留，只补齐尚未索引的素材。\n" +
        "新片库会自然跑完全部缺口；老片库不会整库重算。\n" +
        "需要本机 Ollama。确定开启？",
    );
    if (!ok) return;
    const r = await api.enableVectorization(true, 50);
    setVectorization(true);
    const gaps = r.gaps || {};
    const gapPart =
      gaps.ready_assets != null
        ? ` 已完成 ${gaps.complete_assets ?? "?"} / 就绪 ${gaps.ready_assets ?? "?"}，缺口素材 ${gaps.gap_assets ?? "?"}。`
        : " 后台正在增量补齐缺口。";
    setError(`${r.message}${gapPart}`);
    await refreshHealth();
  }

  async function disableVectorization() {
    await api.updateSettings({ vectorization_enabled: false });
    setVectorization(false);
    await refreshHealth();
  }

  async function finishWizard(enableVec: boolean) {
    if (!wizName.trim()) {
      setError("请填写客户名");
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
        setError(`标准布局创建失败（可手填路径）: ${e}`);
        return;
      }
    }
    if (!lib || !out) {
      setError("请填写片库与输出目录，或先插入外置盘后使用标准布局");
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
        setError(`词池导入失败: ${e}`);
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
        setError(
          `${r.message} 已完成 ${gaps.complete_assets ?? "?"} / 就绪 ${gaps.ready_assets ?? "?"}，缺口 ${gaps.gap_assets ?? "?"}。`,
        );
      } catch (e) {
        setError(`配置已保存，但开启向量化失败: ${e}`);
      }
    }
    await refreshAll();
  }

  async function applyStandardLayout() {
    if (!wizName.trim()) {
      setError("请先填写客户名称");
      return;
    }
    const ensured = await api.zspaceEnsureCustomer(wizName.trim(), true);
    const p = ensured.paths || {};
    setWizLib(String(p.library_root || ""));
    setWizOut(String(p.output_root || ""));
    setWizKw(String(p.keyword_pack_path || wizKw));
    setError(`已创建标准目录并登记同步：${p.customer_root}`);
  }

  async function switchCustomer(name: string) {
    await api.activateCustomer(name);
    setCustomerName(name);
    await refreshAll();
  }

  async function runDryRun() {
    const result = await api.dryRun({ customer_name: customerName, theme, category });
    setDryResult(result);
  }

  async function createJob() {
    await api.createJob({
      mode: "count",
      target_count: targetCount,
      customer_name: customerName,
      theme,
      category,
      template_name: "default-vertical",
    });
    await refreshAll();
  }

  async function decideReview(id: number, status: "approved" | "rejected", rerender = false) {
    await api.reviewOutput(id, status, reviewNote, {
      reason: status === "rejected" ? reviewReason : undefined,
      rerender: status === "rejected" && rerender,
    });
    setReviewNote("");
    await refreshAll();
  }

  async function rerenderOnly(id: number) {
    await api.rerenderOutput(id, reviewReason);
    await refreshAll();
  }

  async function exportPack(id: number) {
    setPackBusyId(id);
    setPackLast("");
    try {
      const res = await api.exportPublishPack(id);
      const dir = String((res.manifest || {}).pack_dir || "");
      const ok = Boolean(res.manifest?.compliance_passed !== false);
      setPackLast(ok ? `已导出：${dir}` : `已导出但合规未过：${dir}`);
      if (dir) setReachPackDir(dir);
      await refreshAll();
    } catch (e) {
      setError(String(e));
    } finally {
      setPackBusyId(null);
    }
  }

  async function reachEnqueueFromPack() {
    if (!reachPackDir.trim()) {
      setError("请填写 publish_pack 目录路径");
      return;
    }
    setReachBusy(true);
    setReachMsg("");
    try {
      const res = await api.reachFromPack({ pack_dir: reachPackDir.trim() });
      setReachMsg(`已入队 ${res.count} 条（人点发布，不自动发）`);
      await refreshReach();
    } catch (e) {
      setError(String(e));
    } finally {
      setReachBusy(false);
    }
  }

  async function reachOpenItem(id: number) {
    setReachBusy(true);
    try {
      const res = await api.reachOpen(id, false);
      setReachMsg(res.disclaimer || `已打开官方入口；粘贴卡：${res.paste_card || ""}`);
      await refreshReach();
    } catch (e) {
      setError(String(e));
    } finally {
      setReachBusy(false);
    }
  }

  async function reachMarkPublished(id: number) {
    await api.reachSetStatus(id, "published", "human_confirmed");
    await refreshReach();
  }

  async function saveCalendarDay() {
    await api.upsertCalendar(calDay, {
      theme: calTheme,
      category,
      customer_name: customerName,
      template_name: "default-vertical",
      quota: calQuota,
      note: calNote,
      active: true,
    });
    await refreshAll();
  }

  const readyOutputs = outputs.filter((o) => o.state === "ready" || o.state === "review");
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
              onClick={() => onEngineToggle().catch((e) => setError(String(e)))}
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
              onChange={(e) => switchCustomer(e.target.value).catch((err) => setError(String(err)))}
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

      <div className="workspace">
        {error && <div className="banner error">{error}</div>}
        {!vectorization && health && (
          <div className="banner error">
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
              <button type="button" className="primary" onClick={() => applyStandardLayout().catch((e) => setError(String(e)))}>
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
              <button type="button" onClick={() => finishWizard(false).catch((e) => setError(String(e)))}>
                完成（稍后开启向量化）
              </button>
              <button
                type="button"
                className="primary"
                onClick={() => finishWizard(true).catch((e) => setError(String(e)))}
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

        <main className="panel">
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
                  <div className="stat-value">{report?.ready ?? 0}</div>
                </div>
                <div className="stat">
                  <div className="stat-label">失败率</div>
                  <div className="stat-value">
                    {report ? `${(report.failure_rate * 100).toFixed(1)}%` : "—"}
                  </div>
                </div>
                <div className="stat">
                  <div className="stat-label">磁盘</div>
                  <div className="stat-value" style={{ fontSize: "1rem" }}>
                    {health ? `${health.path_health.free_disk_gb.toFixed(0)} GB` : "—"}
                  </div>
                </div>
              </div>
              {todayPlan ? (
                <div className="banner-ok">
                  今日计划 {String(todayPlan.day)} · {String(todayPlan.theme)} · 配额{" "}
                  {String(todayPlan.quota)}
                </div>
              ) : (
                <div className="banner error">今日无日历计划</div>
              )}
              <div className="actions">
                <button
                  type="button"
                  className="primary"
                  onClick={() =>
                    api
                      .createJobFromCalendar()
                      .then(refreshAll)
                      .catch((e) => setError(String(e)))
                  }
                >
                  按今日日历开跑
                </button>
                <button type="button" onClick={() => setTab("produce")}>
                  去生产
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
                <button type="button" onClick={() => runDryRun().catch((e) => setError(String(e)))}>
                  Dry-run
                </button>
                <button type="button" className="primary" onClick={() => createJob().catch((e) => setError(String(e)))}>
                  创建任务
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
                        <button type="button" onClick={() => api.pauseJob(Number(j.id)).then(refreshAll)}>
                          暂停
                        </button>
                        <button type="button" onClick={() => api.resumeJob(Number(j.id)).then(refreshAll)}>
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
                <button type="button" onClick={() => saveCalendarDay().catch((e) => setError(String(e)))}>
                  保存该日
                </button>
                <button
                  type="button"
                  onClick={() => api.deleteCalendar(calDay).then(refreshAll).catch((e) => setError(String(e)))}
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
                  onClick={() =>
                    api
                      .scanAssets(0)
                      .then((r) => setError(`全量扫描: ${JSON.stringify(r)}`))
                      .catch((e) => setError(String(e)))
                      .then(refreshAll)
                  }
                >
                  全量扫描
                </button>
                <button
                  type="button"
                  onClick={() =>
                    api.scanStatus().then((r) => setError(`扫描状态: ${JSON.stringify(r)}`))
                  }
                >
                  扫描进度
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={!vectorization}
                  title={vectorization ? "" : "请先在运维页开启向量化"}
                  onClick={() =>
                    api
                      .reconcileAssets(50, "incremental")
                      .then((r) => {
                        const gaps = (r.gaps_after || r.gaps_before || {}) as Record<string, unknown>;
                        setError(
                          `增量补齐: 处理 ${r.processed ?? 0}，新建片段 ${r.created_cliplets ?? 0}，新向量 ${r.indexed ?? 0}，保留已有 ${r.embeddings_kept ?? 0}；剩余缺口素材 ${gaps.gap_assets ?? "?"}`,
                        );
                      })
                      .catch((e) => setError(String(e)))
                      .then(refreshAll)
                  }
                >
                  增量补齐向量
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
                  return (
                    <article key={String(o.id)} className="review-card">
                      <div className="review-meta">
                        <strong>#{String(o.id)}</strong>
                        <span>{String(o.state)}</span>
                        <span>{String(o.title || "(无标题)")}</span>
                      </div>
                      <p className="path">{String(o.output_path)}</p>
                      {covers.length > 0 && (
                        <div className="cover-row">
                          {covers.map((c) => (
                            <img key={c} src={`file://${c}`} alt="" className="cover-thumb" />
                          ))}
                        </div>
                      )}
                      <div className="actions-inline">
                        <button
                          type="button"
                          className="primary"
                          onClick={() => decideReview(Number(o.id), "approved")}
                        >
                          通过
                        </button>
                        <button type="button" onClick={() => decideReview(Number(o.id), "rejected")}>
                          打回
                        </button>
                        <button
                          type="button"
                          className="primary"
                          onClick={() => decideReview(Number(o.id), "rejected", true)}
                        >
                          打回并重渲
                        </button>
                        <button type="button" onClick={() => rerenderOnly(Number(o.id))}>
                          仅重渲
                        </button>
                      </div>
                    </article>
                  );
                })}
                {readyOutputs.length === 0 && <p className="empty">暂无待抽检成片</p>}
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
                      </div>
                      <p className="path">{String(o.output_path)}</p>
                      <div className="actions-inline">
                        <button
                          type="button"
                          className="primary"
                          disabled={packBusyId === Number(o.id)}
                          onClick={() => exportPack(Number(o.id))}
                        >
                          {packBusyId === Number(o.id) ? "导出中…" : "导出物料包"}
                        </button>
                      </div>
                    </article>
                  ))}
                {outputs.filter((o) => o.state === "ready").length === 0 && (
                  <p className="empty">暂无 ready 成片</p>
                )}
              </div>
            </section>
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
                人点发布 · 不自动发帖 · 不提供绕检测 / 矩阵养号。详见 docs/REACH_NON_GOALS.md
              </p>
              {reachQuota && (
                <p className="path">
                  今日配额 {String(reachQuota.used_today)}/{String(reachQuota.daily_quota)}
                  {reachQuota.blocked ? " · 已阻塞" : ""}
                </p>
              )}
              <div className="actions-inline" style={{ marginBottom: 12, gap: 8, displayWrap: "wrap" }}>
                <input
                  value={reachPackDir}
                  onChange={(e) => setReachPackDir(e.target.value)}
                  placeholder="publish_pack 目录路径"
                  style={{ minWidth: 280, flex: 1 }}
                />
                <button type="button" className="primary" disabled={reachBusy} onClick={reachEnqueueFromPack}>
                  {reachBusy ? "处理中…" : "从物料包入队"}
                </button>
                <button type="button" onClick={() => refreshReach()}>
                  刷新
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

          {tab === "ops" && (
            <section>
              <div className="panel-head">
                <h2>运维与设置</h2>
              </div>

              <h3 className="section-title">向量化</h3>
              <div className="actions">
                {vectorization ? (
                  <button type="button" onClick={() => disableVectorization().catch((e) => setError(String(e)))}>
                    关闭向量化
                  </button>
                ) : (
                  <button
                    type="button"
                    className="primary"
                    onClick={() => enableVectorization().catch((e) => setError(String(e)))}
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
                      .then(setSyncStatus)
                      .catch((e) => setError(String(e)))
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
                        setError(`同步服务已安装: ${JSON.stringify(r)}`);
                        return api.zspaceSyncStatus().then(setSyncStatus);
                      })
                      .catch((e) => setError(String(e)))
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
                                setError(
                                  r.active_matches
                                    ? `已绑定且与当前登录一致: ${username} / ${nasId}`
                                    : `已绑定 ${username} / ${nasId}。${r.hint || "请切换客户端登录该账号后再同步。"}`,
                                );
                                return api.zspaceSyncStatus().then(setSyncStatus);
                              })
                              .catch((e) => setError(String(e)))
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
                          setError("请填写客户名");
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
                            setError(`已建客户并登记同步: ${c.name}`);
                            await refreshAll();
                            return api.zspaceSyncStatus().then(setSyncStatus);
                          })
                          .catch((e) => setError(String(e)));
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
                          .catch((e) => setError(String(e)))
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
                        .then((r) => setError(`词池已重载: ${JSON.stringify(r)}`))
                        .catch((e) => setError(String(e)))
                    }
                  >
                    从路径重载
                  </button>
                </div>
              </label>
              <div className="actions">
                <button type="button" className="primary" onClick={() => savePaths().catch((e) => setError(String(e)))}>
                  保存设置
                </button>
                <button
                  type="button"
                  onClick={() =>
                    api
                      .cleanCache(24)
                      .then((r) => setError(`清理缓存: ${r.removed_files} 文件 / ${r.freed_mb} MB`))
                      .catch((e) => setError(String(e)))
                  }
                >
                  清理缓存
                </button>
                <button
                  type="button"
                  onClick={() =>
                    api
                      .schedulerRunNow(true)
                      .then((r) => setError(`调度: ${JSON.stringify(r)}`))
                      .catch((e) => setError(String(e)))
                  }
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
