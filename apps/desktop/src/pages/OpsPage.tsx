import { api } from "../api";
import type { Customer, Health, ServicesStatus } from "../api";
import { CarrierOpsStrip } from "../CarrierOpsStrip";
import { SemanticAnalysisProgress } from "../SemanticAnalysisProgress";
import { briefResult } from "../shell/briefResult";
import { PageHeader, SegmentNav, StepFooter } from "../shell/PageChrome";
import type { OpsSection, SemanticBackfillStatus, Tab } from "../types";
import type { NotifyFn, OllamaInfo } from "./pageTypes";

const OPS_SEGMENTS: Array<{ id: OpsSection; label: string }> = [
  { id: "ai", label: "本地 AI" },
  { id: "services", label: "服务" },
  { id: "carrier", label: "T2S 载体" },
  { id: "health", label: "健康" },
  { id: "logs", label: "日志" },
];

export interface OpsPageProps {
  section: OpsSection;
  onSectionChange: (s: OpsSection) => void;
  setTab: (t: Tab) => void;
  notify: NotifyFn;
  actionBusy: string | null;
  setActionBusy: (v: string | null) => void;
  refreshAll: () => Promise<void>;
  ollamaInfo: OllamaInfo | null;
  refreshOllamaDetail: () => void | Promise<void>;
  pullRecommendedModels: () => void | Promise<void>;
  pullOllamaModel: (model: string) => void | Promise<void>;
  ollamaNarration: boolean;
  setOllamaNarration: (v: boolean) => void;
  ollamaNarrationEmoji: boolean;
  setOllamaNarrationEmoji: (v: boolean) => void;
  ollamaNarrationModel: string;
  setOllamaNarrationModel: (v: string) => void;
  vectorization: boolean;
  enableVectorization: () => void | Promise<void>;
  disableVectorization: () => Promise<void>;
  health: Health | null;
  semanticStatus: SemanticBackfillStatus | null;
  runSemanticBatch: (limit: number) => Promise<void> | void;
  syncStatus: Record<string, unknown> | null;
  setSyncStatus: (v: Record<string, unknown> | null) => void;
  syncDryRunBusy: boolean;
  setSyncDryRunBusy: (v: boolean) => void;
  syncDryRunHint: string;
  setSyncDryRunHint: (v: string) => void;
  services: ServicesStatus | null;
  setServices: (v: ServicesStatus | null) => void;
  events: Array<Record<string, unknown>>;
  /** Optional: jump to settings for customer media-source edits */
  customers?: Customer[];
}

function syncStatusLines(syncStatus: Record<string, unknown>): string {
  const bound = syncStatus.zspace_bound as Record<string, string> | undefined;
  const active = syncStatus.zspace_active as Record<string, string> | undefined;
  const fp = syncStatus.script_fingerprint as Record<string, string> | undefined;
  const mediaSources = Array.isArray(syncStatus.media_sources)
    ? (syncStatus.media_sources as unknown[])
    : [];
  const customers = Array.isArray(syncStatus.customers)
    ? (syncStatus.customers as string[])
    : [];
  return [
    `脚本: ${syncStatus.scripts_installed ? "已装" : "未装"}`,
    `LaunchAgent: ${syncStatus.launchd_loaded ? `已加载(${String(syncStatus.launchd_state)})` : "未加载"}`,
    `外置盘同步根: ${syncStatus.local_root_exists ? "OK" : "未挂载"} · ${String(syncStatus.local_root || "")}`,
    `工作区: ${syncStatus.work_root_exists ? "OK" : "无"} · ${String(syncStatus.work_root || "")}`,
    `极空间代理: ${syncStatus.zspace_proxy_ok ? `OK :${String(syncStatus.zspace_proxy_port)}` : "未通"}`,
    `账号绑定: ${
      syncStatus.zspace_match
        ? "匹配"
        : syncStatus.zspace_bound_ok
          ? `未匹配 — ${String(syncStatus.zspace_reason || "")}`
          : "未绑定"
    }`,
    bound?.username
      ? `已绑定: ${bound.username} / ${bound.nas_id} (${bound.nas_name || "—"})`
      : "已绑定: —",
    active?.username
      ? `当前登录: ${active.username} / ${active.nas_id} (${active.nas_name || "—"})`
      : "当前登录: 无",
    `客户: ${customers.join("、") || "—"}`,
    `同步模式: ${String(syncStatus.sync_mode || "carrier_only")} · 片库同步: ${
      syncStatus.media_sync_enabled ? `开(来源${mediaSources.length})` : "关(默认)"
    }`,
    syncStatus.script_stale ? "⚠ 同步脚本已过期：请点「安装 / 重装同步服务」" : "",
    fp?.installed_sha256
      ? fp.match
        ? `脚本哈希: ${fp.installed_sha256.slice(0, 12)}… (与仓库一致)`
        : `脚本哈希: ${fp.installed_sha256.slice(0, 12)}… (与仓库不一致)`
      : "",
  ]
    .filter(Boolean)
    .join("\n");
}

export function OpsPage({
  section,
  onSectionChange,
  setTab,
  notify,
  actionBusy,
  setActionBusy,
  refreshAll,
  ollamaInfo,
  refreshOllamaDetail,
  pullRecommendedModels,
  pullOllamaModel,
  ollamaNarration,
  setOllamaNarration,
  ollamaNarrationEmoji,
  setOllamaNarrationEmoji,
  ollamaNarrationModel,
  setOllamaNarrationModel,
  vectorization,
  enableVectorization,
  disableVectorization,
  health,
  semanticStatus,
  runSemanticBatch,
  syncStatus,
  setSyncStatus,
  syncDryRunBusy,
  setSyncDryRunBusy,
  syncDryRunHint,
  setSyncDryRunHint,
  services,
  setServices,
  events,
}: OpsPageProps) {
  const mediaSources = Array.isArray(syncStatus?.media_sources)
    ? (syncStatus.media_sources as Array<Record<string, string>>)
    : [];
  const zspaceAccounts = Array.isArray(syncStatus?.zspace_accounts)
    ? (syncStatus.zspace_accounts as Array<Record<string, unknown>>)
    : [];
  const reconcile = syncStatus?.reconcile as Record<string, unknown> | undefined;
  const reconcileErrs = (reconcile?.errors as Array<Record<string, string>>) || [];

  return (
    <section>
      <PageHeader
        title="运维"
        blurb="本机服务、本地 AI 与载体同步"
        actions={
          <button type="button" onClick={() => void refreshAll()}>
            刷新
          </button>
        }
      />

      <SegmentNav
        items={OPS_SEGMENTS}
        value={section}
        onChange={onSectionChange}
        ariaLabel="运维分区"
      />

      <div className="workspace-split">
        <nav className="side-nav" aria-label="运维侧栏">
          {OPS_SEGMENTS.map((it) => (
            <button
              key={it.id}
              type="button"
              className={`side-nav-btn${section === it.id ? " active" : ""}`}
              onClick={() => onSectionChange(it.id)}
            >
              {it.label}
            </button>
          ))}
        </nav>

        <div>
          {section === "ai" && (
            <>
              <h3 className="section-title">本地 AI（Ollama）</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                语义检索与画面理解依赖本机 Ollama。安装时请先完成 Python 环境，再装 Ollama，最后按本机内存下载推荐模型。
                命令行也可运行：
                <code style={{ marginLeft: 4 }}>~/Suying/montage-studio/scripts/install-runtime.sh</code>
              </p>
              {ollamaInfo?.host ? (
                <p className="hint" style={{ marginBottom: 8 }}>
                  本机约 {String(ollamaInfo.host.ram_gb ?? "?")}GB ·{" "}
                  {String(ollamaInfo.host.chip || ollamaInfo.host.arch || "")} · 推荐档{" "}
                  <strong>{String(ollamaInfo.recommended?.tier || ollamaInfo.host.tier || "")}</strong>
                  {ollamaInfo.recommended?.reason ? ` — ${ollamaInfo.recommended.reason}` : ""}
                </p>
              ) : null}
              {ollamaInfo?.setup_steps && ollamaInfo.setup_steps.length > 0 ? (
                <div className="actions" style={{ marginBottom: 8, flexWrap: "wrap" }}>
                  {ollamaInfo.setup_steps.map((s) => (
                    <span key={s.id} className={`health-line${s.ok ? "" : " is-warn"}`} title={s.detail}>
                      {s.title} {s.ok ? "✓" : "需安装"}
                    </span>
                  ))}
                </div>
              ) : null}
              <p className="hint" style={{ marginBottom: 10 }}>
                下载 Ollama：
                <a
                  href={ollamaInfo?.install_url || "https://ollama.com/download"}
                  target="_blank"
                  rel="noreferrer"
                >
                  ollama.com/download
                </a>
                ；Python 建议 3.11+（python.org 或 brew）。
              </p>
              <div className="actions" style={{ marginBottom: 8 }}>
                <span className={`health-line${ollamaInfo?.reachable ? "" : " is-warn"}`}>
                  服务 {ollamaInfo?.reachable ? "在线" : "未检测到"}
                </span>
                <span className={`health-line${ollamaInfo?.embed_ready ? "" : " is-warn"}`}>
                  向量 {ollamaInfo?.embed_model || "nomic-embed-text"}{" "}
                  {ollamaInfo?.embed_ready ? "已装" : "未装"}
                </span>
                <span className={`health-line${ollamaInfo?.vision_ready ? "" : " is-warn"}`}>
                  快筛 {ollamaInfo?.vision_model || ollamaInfo?.recommended?.vision_model || "—"}{" "}
                  {ollamaInfo?.vision_ready ? "已装" : "未装"}
                </span>
                {ollamaInfo?.cascade && ollamaInfo?.escalate_model ? (
                  <span
                    className={`health-line${ollamaInfo?.escalate_ready ? "" : " is-warn"}`}
                    title={`超时约 ${String(ollamaInfo.escalate_timeout_sec ?? "—")}s`}
                  >
                    升级 {ollamaInfo.escalate_model}{" "}
                    {ollamaInfo.escalate_ready ? "已装" : "未装"}
                  </span>
                ) : (
                  <span className="health-line" title="16GB 及以下不默认、不升级 27B">
                    级联 关
                  </span>
                )}
              </div>
              <p className="hint" style={{ marginBottom: 10 }}>
                {ollamaInfo?.message || "打开本页后点「刷新状态」检测。"}
                {ollamaInfo?.pull?.running
                  ? ` · 正在拉取 ${String(ollamaInfo.pull.model || "")}…`
                  : ""}
              </p>
              <div className="actions">
                <button
                  type="button"
                  disabled={actionBusy === "ollama" || actionBusy === "ollamaPull"}
                  onClick={() => void refreshOllamaDetail()}
                >
                  {actionBusy === "ollama" ? "检测中…" : "刷新状态"}
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={!ollamaInfo?.reachable || actionBusy === "ollamaPull"}
                  onClick={() => void pullRecommendedModels()}
                >
                  {actionBusy === "ollamaPull" ? "安装中…" : "一键安装推荐模型"}
                </button>
                <button
                  type="button"
                  disabled={
                    !ollamaInfo?.reachable ||
                    Boolean(ollamaInfo?.embed_ready) ||
                    actionBusy === "ollamaPull"
                  }
                  onClick={() => void pullOllamaModel(ollamaInfo?.embed_model || "nomic-embed-text")}
                >
                  仅装向量
                </button>
                <button
                  type="button"
                  disabled={
                    !ollamaInfo?.reachable ||
                    Boolean(ollamaInfo?.vision_ready) ||
                    actionBusy === "ollamaPull"
                  }
                  onClick={() =>
                    void pullOllamaModel(
                      ollamaInfo?.vision_model ||
                        ollamaInfo?.recommended?.vision_model ||
                        "moondream",
                    )
                  }
                >
                  仅装视觉
                </button>
              </div>

              <h3 id="ops-ollama-narration" className="section-title">
                Ollama 旁白文案
              </h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                开启后：用本机 Ollama 把片段视觉描述改写成口播文案，并生成贴合主题的表情包再烧录（TTS 仍走 Edge）。
              </p>
              <div className="actions" style={{ marginBottom: 8, flexWrap: "wrap" }}>
                <button
                  type="button"
                  className={ollamaNarration ? "primary" : undefined}
                  disabled={!ollamaInfo?.reachable || actionBusy === "ollamaNarr"}
                  onClick={() => {
                    const next = !ollamaNarration;
                    setActionBusy("ollamaNarr");
                    api
                      .updateSettings({ ollama_narration_enabled: next })
                      .then(() => {
                        setOllamaNarration(next);
                        notify(next ? "已开启 Ollama 旁白文案" : "已关闭 Ollama 旁白文案", "ok");
                      })
                      .catch((e: unknown) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  {ollamaNarration ? "旁白文案：开" : "旁白文案：关"}
                </button>
                <button
                  type="button"
                  disabled={!ollamaNarration || actionBusy === "ollamaNarr"}
                  onClick={() => {
                    const next = !ollamaNarrationEmoji;
                    setActionBusy("ollamaNarr");
                    api
                      .updateSettings({ ollama_narration_burn_emoji: next })
                      .then(() => {
                        setOllamaNarrationEmoji(next);
                        notify(next ? "表情包烧录：开" : "表情包烧录：关", "ok");
                      })
                      .catch((e: unknown) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  {ollamaNarrationEmoji ? "表情烧录：开" : "表情烧录：关"}
                </button>
                <button
                  type="button"
                  disabled={!ollamaInfo?.reachable || actionBusy === "ollamaNarr"}
                  onClick={() => {
                    setActionBusy("ollamaNarr");
                    api
                      .ollamaNarrationPreview()
                      .then((r) =>
                        notify(
                          r.ok
                            ? `试写：${String(r.script || "").slice(0, 48)}…`
                            : `试写失败：${r.error || "未知"}`,
                          r.ok ? "ok" : "err",
                        ),
                      )
                      .catch((e: unknown) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  试写一条
                </button>
              </div>
              <label>
                旁白模型（当前可用 qwen2.5:32b；视觉默认 qwen3.5:27b-q4_K_M）
                <div className="path-row">
                  <input
                    value={ollamaNarrationModel}
                    onChange={(e) => setOllamaNarrationModel(e.target.value)}
                    placeholder="qwen2.5:32b"
                  />
                  <button
                    type="button"
                    disabled={actionBusy === "ollamaNarr"}
                    onClick={() => {
                      setActionBusy("ollamaNarr");
                      api
                        .updateSettings({ ollama_narration_model: ollamaNarrationModel.trim() })
                        .then(() => notify("旁白模型已保存", "ok"))
                        .catch((e: unknown) => notify(String(e), "err"))
                        .finally(() => setActionBusy(null));
                    }}
                  >
                    保存模型
                  </button>
                </div>
              </label>
              <p className="hint" style={{ margin: "8px 0 16px" }}>
                状态：{ollamaNarration ? "已开启" : "已关闭"}
                {ollamaNarrationModel ? ` · 模型 ${ollamaNarrationModel}` : ""}
                {ollamaNarrationEmoji ? " · 表情烧录开" : " · 表情烧录关"}
              </p>

              <h3 className="section-title">向量化</h3>
              <div className="actions">
                {vectorization ? (
                  <button
                    type="button"
                    onClick={() => void disableVectorization().catch((e: unknown) => notify(String(e), "err"))}
                  >
                    关闭向量化
                  </button>
                ) : (
                  <button type="button" className="primary" onClick={() => void enableVectorization()}>
                    手动开启向量化
                  </button>
                )}
                <span className="hint">
                  {vectorization
                    ? `增量模式已开${health?.vectorization_enabled_at ? ` · ${health.vectorization_enabled_at}` : ""}`
                    : ollamaInfo?.embed_ready
                      ? "本地 AI 已就绪；开启后仅增量补缺口"
                      : "请先完成上方本地 AI；默认关闭向量化"}
                </span>
              </div>
              <SemanticAnalysisProgress
                status={semanticStatus}
                busy={actionBusy === "captions"}
                onRunBatch={(limit) => void runSemanticBatch(limit)}
              />
            </>
          )}

          {section === "services" && (
            <>
              <h3 className="section-title">服务开关</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                控制本机片库监视、任务 Worker 与调度器。客户路径请在「设置」修改。
              </p>
              <div className="actions">
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
                          .catch((e: unknown) => notify(String(e), "err"))
                      }
                    >
                      {label}: {on ? "运行中 → 停止" : "已停 → 启动"}
                    </button>
                  );
                })}
              </div>
              <ul className="meta">
                <li>
                  监视 {services?.watcher || health?.watcher_running ? "开" : "关"} · Worker{" "}
                  {services?.worker || health?.worker_running ? "开" : "关"} · 调度{" "}
                  {services?.scheduler || health?.scheduler_running ? "开" : "关"}
                </li>
              </ul>
              <div className="actions" style={{ marginTop: 12 }}>
                <button
                  type="button"
                  disabled={actionBusy === "scheduler"}
                  className={actionBusy === "scheduler" ? "is-busy" : undefined}
                  onClick={() => {
                    setActionBusy("scheduler");
                    api
                      .schedulerRunNow(true)
                      .then((r) => notify(briefResult("调度已触发", r), "ok"))
                      .catch((e: unknown) => notify(String(e), "err"))
                      .finally(() => setActionBusy(null));
                  }}
                >
                  立即按日历开跑
                </button>
              </div>
            </>
          )}

          {section === "carrier" && (
            <>
              <h3 className="section-title">极空间 · T2S 载体（默认）</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                默认只同步 T2S「速影载体/」（安装包 / 种子 / 配置备份 / 更新）。片库与成片留在本机。相册↔片库团队文件同步为
                legacy，默认关闭。先绑定对方极空间账号 + T2S 设备。新建客户与更换来源请到「设置」。
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
                      .catch((e: unknown) => notify(String(e), "err"))
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
                        notify(briefResult("同步服务已安装", r), "ok");
                        return api.zspaceSyncStatus().then(setSyncStatus);
                      })
                      .catch((e: unknown) => notify(String(e), "err"))
                  }
                >
                  安装 / 重装同步服务
                </button>
                <button type="button" onClick={() => setTab("settings")}>
                  去设置：客户与来源
                </button>
              </div>
              {syncStatus ? (
                <div className="hint" style={{ margin: "8px 0 12px", whiteSpace: "pre-wrap" }}>
                  {syncStatusLines(syncStatus)}
                </div>
              ) : null}
              {zspaceAccounts.length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div className="hint" style={{ marginBottom: 6 }}>
                    选择绑定账号（来自极空间客户端历史 / 当前登录）
                  </div>
                  <div className="actions" style={{ flexWrap: "wrap" }}>
                    {zspaceAccounts.map((acc) => {
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
                              .catch((e: unknown) => notify(String(e), "err"))
                          }
                        >
                          绑定 {label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              <h3 className="section-title">客户媒体来源（只读）</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                媒体同步与 T2S 载体是两条独立通道。更换绑定请到「设置」。
              </p>
              {mediaSources.length > 0 ? (
                <ul className="hint" style={{ marginBottom: 12, paddingLeft: 18 }}>
                  {mediaSources.map((s) => (
                    <li key={`${s.customer_key}-${s.remote_root}`}>
                      <strong>{s.display_name || s.customer_key}</strong>：远端 /public/{s.remote_root} →
                      本地 {s.local_target}
                      {s.pull_only ? "（仅拉取）" : ""}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="hint" style={{ marginBottom: 12 }}>
                  当前未登记任何客户媒体来源（默认仅载体同步）。
                </p>
              )}
              {reconcileErrs.length > 0 ? (
                <p className="hint" style={{ marginBottom: 12, color: "var(--warn, #b8860b)" }}>
                  对账异常：
                  {reconcileErrs.map((e) => `${e.type}:${e.customer || e.detail || ""}`).join("；")}
                </p>
              ) : null}
              <div className="actions" style={{ marginBottom: 16 }}>
                <button
                  type="button"
                  disabled={syncDryRunBusy || !syncStatus?.zspace_match}
                  onClick={() => {
                    void (async () => {
                      setSyncDryRunBusy(true);
                      setSyncDryRunHint("");
                      try {
                        const r = await api.zspaceSyncDryRun(true);
                        const pull = (r.summary as Record<string, unknown> | undefined)?.pull as
                          | Record<string, unknown>
                          | undefined;
                        const msg = pull
                          ? `dry-run 完成：待拉 ${pull.downloaded ?? 0} · 跳过 ${pull.skipped ?? 0} · 未映射跳过 ${pull.unmapped_skipped ?? 0}`
                          : r.ok
                            ? "dry-run 完成"
                            : `dry-run 失败 (exit ${r.exit_code})`;
                        setSyncDryRunHint(msg);
                        notify(msg, r.ok ? "ok" : "warn");
                        await api.zspaceSyncStatus().then(setSyncStatus);
                      } catch (e: unknown) {
                        notify(String(e), "err");
                      } finally {
                        setSyncDryRunBusy(false);
                      }
                    })();
                  }}
                >
                  {syncDryRunBusy ? "dry-run 中…" : "dry-run 同步（仅拉取）"}
                </button>
                {syncDryRunHint ? <span className="hint">{syncDryRunHint}</span> : null}
              </div>
            </>
          )}

          {section === "health" && (
            <>
              <h3 className="section-title">引擎与路径健康</h3>
              {health ? (
                <div className="hint" style={{ marginBottom: 12, whiteSpace: "pre-wrap" }}>
                  {[
                    `引擎 ${health.status} · ${health.product_name || "速影"} ${health.engine_version}`,
                    `客户 ${health.active_customer || "—"}`,
                    `磁盘剩余 ${health.path_health.free_disk_gb.toFixed(1)} GB · 路径 ${
                      health.path_health.ok ? "正常" : "异常"
                    }`,
                    ...(health.path_health.errors || []).map((e) => `错误: ${e}`),
                    ...(health.path_health.warnings || []).map((w) => `警告: ${w}`),
                    `向量化 ${health.vectorization_enabled ? "开" : "关"}`,
                    `本地 AI 旁白 ${health.ollama_narration_enabled ? "开" : "关"}`,
                  ].join("\n")}
                </div>
              ) : (
                <p className="hint">尚未拉取 /health。</p>
              )}
              {!health?.path_health.ok ? (
                <div className="banner error" style={{ marginBottom: 12 }}>
                  路径异常，禁止生产：
                  {(health?.path_health.errors || []).join("；") || "请检查片库/成片目录"}
                  <button type="button" style={{ marginLeft: 8 }} onClick={() => setTab("settings")}>
                    去设置
                  </button>
                </div>
              ) : null}
              <CarrierOpsStrip />
            </>
          )}

          {section === "logs" && (
            <>
              <h3 className="section-title">日志</h3>
              <div className="actions" style={{ marginBottom: 8 }}>
                <button type="button" onClick={() => void api.exportEvents()}>
                  导出事件 CSV
                </button>
                <button type="button" onClick={() => void api.exportRenders()}>
                  导出成片 CSV
                </button>
                <button type="button" onClick={() => void refreshAll()}>
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
            </>
          )}
        </div>
      </div>

      <StepFooter current="ops" onJump={setTab} />
    </section>
  );
}
