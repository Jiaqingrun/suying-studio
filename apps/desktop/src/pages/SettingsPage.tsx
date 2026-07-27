import { useState } from "react";
import { api } from "../api";
import type { Customer } from "../api";
import { briefResult } from "../shell/briefResult";
import { PageHeader, SegmentNav, StepFooter } from "../shell/PageChrome";
import type { LayoutDensityPref, SettingsSection, Tab } from "../types";
import type { AskConfirmFn, NotifyFn, TitlePoolSummary } from "./pageTypes";

const SETTINGS_SEGMENTS: Array<{ id: SettingsSection; label: string }> = [
  { id: "customer", label: "客户" },
  { id: "defaults", label: "偏好" },
  { id: "paths", label: "路径" },
  { id: "advanced", label: "高级" },
];

export interface SettingsPageProps {
  section: SettingsSection;
  onSectionChange: (s: SettingsSection) => void;
  setTab: (t: Tab) => void;
  notify: NotifyFn;
  askConfirm: AskConfirmFn;
  actionBusy: string | null;
  setActionBusy: (v: string | null) => void;
  refreshAll: () => Promise<void>;

  cursorApiKeyInput: string;
  setCursorApiKeyInput: (v: string) => void;
  cursorKeyConfigured: boolean;
  cursorKeyHint: string;
  saveCursorApiKey: () => void | Promise<void>;
  verifyCursorApiKey: () => void | Promise<void>;
  clearCursorApiKey: () => void | Promise<void>;

  autoDaily: boolean;
  setAutoDaily: (v: boolean) => void;
  autoHour: number;
  setAutoHour: (v: number) => void;

  libraryRoot: string;
  setLibraryRoot: (v: string) => void;
  libraryRootsText: string;
  setLibraryRootsText: (v: string) => void;
  outputRoot: string;
  setOutputRoot: (v: string) => void;
  keywordPackPath: string;
  setKeywordPackPath: (v: string) => void;
  pickDir: () => Promise<string | null>;
  pickFile: () => Promise<string | null>;
  savePaths: () => void | Promise<void>;
  activeCustomerId: number | null;
  titlePoolSummary: TitlePoolSummary | null;
  setTitlePoolSummary: (v: TitlePoolSummary | null) => void;

  customerName: string;
  customers: Customer[];
  newCustName: string;
  setNewCustName: (v: string) => void;
  remoteFolder: string;
  setRemoteFolder: (v: string) => void;
  remoteFolderOptions: string[];
  setRemoteFolderOptions: (v: string[]) => void;
  remoteFoldersBusy: boolean;
  setRemoteFoldersBusy: (v: boolean) => void;
  editSourceCustomer: string;
  setEditSourceCustomer: (v: string) => void;
  editRemoteFolder: string;
  setEditRemoteFolder: (v: string) => void;
  syncStatus: Record<string, unknown> | null;
  setSyncStatus: (v: Record<string, unknown> | null) => void;

  layoutPref: LayoutDensityPref;
  setLayoutPref: (v: LayoutDensityPref) => void;

  advancedUnlocked: boolean;
  passwordConfigured: boolean;
  passwordStatusRemaining: number;
  onUnlock: (password: string) => Promise<void> | void;
  onLock: () => Promise<void> | void;
  onCreatePassword: (password: string) => Promise<void> | void;
  onChangePassword: (oldPassword: string, newPassword: string) => Promise<void> | void;

  cacheRoot: string;
  setCacheRoot: (v: string) => void;
  renderRoot: string;
  setRenderRoot: (v: string) => void;
  dataRoot: string;
  setDataRoot: (v: string) => void;
  vectorDbPath: string;
}

export function SettingsPage({
  section,
  onSectionChange,
  setTab,
  notify,
  askConfirm,
  actionBusy,
  setActionBusy,
  refreshAll,
  cursorApiKeyInput,
  setCursorApiKeyInput,
  cursorKeyConfigured,
  cursorKeyHint,
  saveCursorApiKey,
  verifyCursorApiKey,
  clearCursorApiKey,
  autoDaily,
  setAutoDaily,
  autoHour,
  setAutoHour,
  libraryRoot,
  setLibraryRoot,
  libraryRootsText,
  setLibraryRootsText,
  outputRoot,
  setOutputRoot,
  keywordPackPath,
  setKeywordPackPath,
  pickDir,
  pickFile,
  savePaths,
  activeCustomerId,
  titlePoolSummary,
  setTitlePoolSummary,
  customerName,
  customers,
  newCustName,
  setNewCustName,
  remoteFolder,
  setRemoteFolder,
  remoteFolderOptions,
  setRemoteFolderOptions,
  remoteFoldersBusy,
  setRemoteFoldersBusy,
  editSourceCustomer,
  setEditSourceCustomer,
  editRemoteFolder,
  setEditRemoteFolder,
  syncStatus,
  setSyncStatus,
  layoutPref,
  setLayoutPref,
  advancedUnlocked,
  passwordConfigured,
  passwordStatusRemaining,
  onUnlock,
  onLock,
  onCreatePassword,
  onChangePassword,
  cacheRoot,
  setCacheRoot,
  renderRoot,
  setRenderRoot,
  dataRoot,
  setDataRoot,
  vectorDbPath,
}: SettingsPageProps) {
  const [unlockPassword, setUnlockPassword] = useState("");
  const [createPassword, setCreatePassword] = useState("");
  const [createPassword2, setCreatePassword2] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [pwBusy, setPwBusy] = useState(false);

  const mediaSources = Array.isArray(syncStatus?.media_sources)
    ? (syncStatus.media_sources as Array<Record<string, string>>)
    : [];

  const navItems = SETTINGS_SEGMENTS.filter(
    (it) => it.id !== "brand" && it.id !== "notify",
  );

  async function loadRemoteFolders(preferSelectFirst: boolean) {
    setRemoteFoldersBusy(true);
    try {
      const r = await api.zspaceListMediaRemoteFolders("手机相册备份");
      const folders = r.folders || [];
      setRemoteFolderOptions(folders);
      if (preferSelectFirst && !remoteFolder && folders.length) setRemoteFolder(folders[0]);
      notify(`远端文件夹已加载：${folders.length} 项`, "ok");
    } catch (e: unknown) {
      notify(String(e), "err");
    } finally {
      setRemoteFoldersBusy(false);
    }
  }

  return (
    <section>
      <PageHeader
        title="设置"
        blurb="客户、偏好与路径"
        actions={
          <button type="button" onClick={() => void refreshAll()}>
            刷新
          </button>
        }
      />

      <SegmentNav
        items={navItems}
        value={
          section === "brand" || section === "notify" ? "defaults" : section
        }
        onChange={onSectionChange}
        ariaLabel="设置分区"
      />

      <div className="workspace-split">
        <nav className="side-nav" aria-label="设置侧栏">
          {navItems.map((it) => (
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
          {(section === "customer" || section === "brand") && (
            <>
              <h3 className="section-title">新建客户（标准布局）</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                在本机创建客户目录并登记极空间媒体来源。T2S 载体同步服务请在「运维」安装与绑定。
              </p>
              <div className="grid2" style={{ marginBottom: 16 }}>
                <label>
                  客户全称
                  <div className="path-row">
                    <input
                      value={newCustName}
                      onChange={(e) => setNewCustName(e.target.value)}
                      placeholder="客户全称"
                    />
                  </div>
                  <div className="path-row" style={{ marginTop: 8, flexWrap: "wrap" }}>
                    <select
                      value={remoteFolder}
                      onChange={(e) => setRemoteFolder(e.target.value)}
                      disabled={remoteFoldersBusy || remoteFolderOptions.length === 0}
                    >
                      <option value="">选择远端文件夹（/public/手机相册备份）</option>
                      {remoteFolderOptions.map((f) => (
                        <option key={f} value={f}>
                          {f}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      disabled={remoteFoldersBusy}
                      onClick={() => void loadRemoteFolders(true)}
                    >
                      {remoteFoldersBusy ? "加载中…" : "加载远端文件夹"}
                    </button>
                  </div>
                  <div className="hint" style={{ marginTop: 8 }}>
                    映射到：远端 <code>/public/手机相册备份/{remoteFolder || "…"}</code> → 本地{" "}
                    <code>01-片库/{remoteFolder || "…"}</code>
                  </div>
                  <div className="actions" style={{ marginTop: 12 }}>
                    <button
                      type="button"
                      className="primary"
                      onClick={() => {
                        if (!newCustName.trim()) {
                          notify("请填写客户名", "err");
                          return;
                        }
                        if (!remoteFolder) {
                          notify("请先选择远端文件夹", "err");
                          return;
                        }
                        api
                          .zspaceEnsureCustomer(newCustName.trim(), true)
                          .then(async (ensured) => {
                            const p = ensured.paths || {};
                            await api.zspaceSetMediaSource({
                              customer_name: newCustName.trim(),
                              remote_person: remoteFolder,
                              remote_base: "手机相册备份",
                            });
                            const c = await api.createCustomer({
                              name: newCustName.trim(),
                              library_root: String(p.library_root || ""),
                              output_root: String(p.output_root || ""),
                              keyword_pack_path: String(p.keyword_pack_path || "") || undefined,
                            });
                            await api.activateCustomer(c.name);
                            setNewCustName("");
                            setRemoteFolder("");
                            setRemoteFolderOptions([]);
                            notify(`已建客户并登记媒体来源: ${c.name} ← ${remoteFolder}`, "ok");
                            await refreshAll();
                            return api.zspaceSyncStatus().then(setSyncStatus);
                          })
                          .catch((e: unknown) => notify(String(e), "err"));
                      }}
                    >
                      创建并登记
                    </button>
                  </div>
                </label>
              </div>

              <h3 className="section-title">客户媒体来源（可选 · 按文件夹隔离）</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                若启用片库同步，每个客户必须绑定团队空间下的<strong>一个</strong>
                子文件夹。更换来源会归档旧目录 ready 素材。
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
              <div className="grid2" style={{ marginBottom: 12 }}>
                <label>
                  更换已有客户来源
                  <div className="path-row" style={{ marginTop: 6 }}>
                    <select
                      value={editSourceCustomer || customerName}
                      onChange={(e) => {
                        setEditSourceCustomer(e.target.value);
                        const hit = mediaSources.find((s) => s.customer_key === e.target.value);
                        if (hit?.remote_root) {
                          const parts = hit.remote_root.split("/");
                          setEditRemoteFolder(parts[parts.length - 1] || "");
                        } else {
                          setEditRemoteFolder("");
                        }
                      }}
                    >
                      <option value="">选择客户</option>
                      {customers.map((c) => (
                        <option key={c.id} value={c.name}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="path-row" style={{ marginTop: 8, flexWrap: "wrap" }}>
                    <select
                      value={editRemoteFolder}
                      onChange={(e) => setEditRemoteFolder(e.target.value)}
                      disabled={remoteFoldersBusy || remoteFolderOptions.length === 0}
                    >
                      <option value="">选择新远端文件夹</option>
                      {remoteFolderOptions.map((f) => (
                        <option key={`edit-${f}`} value={f}>
                          {f}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      disabled={remoteFoldersBusy}
                      onClick={() => void loadRemoteFolders(false)}
                    >
                      加载远端列表
                    </button>
                  </div>
                  <div className="actions" style={{ marginTop: 10 }}>
                    <button
                      type="button"
                      disabled={!(editSourceCustomer || customerName) || !editRemoteFolder}
                      onClick={() => {
                        void (async () => {
                          const cust = (editSourceCustomer || customerName).trim();
                          if (!cust || !editRemoteFolder) return;
                          try {
                            const prev = await api.zspacePreviewMediaSource({
                              customer_name: cust,
                              remote_person: editRemoteFolder,
                            });
                            if (prev.unchanged) {
                              notify("来源未变化", "info");
                              return;
                            }
                            const removed = (prev.removed_local_targets as string[]) || [];
                            const ok = await askConfirm({
                              title: "更换媒体来源",
                              body:
                                `客户「${cust}」将改为仅同步 /public/手机相册备份/${editRemoteFolder}。\n` +
                                (removed.length
                                  ? `旧来源目录将被退役并归档对应素材（${removed.join("、")}）。`
                                  : "无旧来源需要归档。") +
                                "\n\n确认继续？",
                              confirmLabel: "更换并归档旧来源",
                            });
                            if (!ok) return;
                            const r = await api.zspaceSetMediaSource({
                              customer_name: cust,
                              remote_person: editRemoteFolder,
                            });
                            notify(
                              `已更新来源：${cust} ← ${editRemoteFolder}` +
                                (r.archived_assets ? ` · 归档 ${r.archived_assets} 条素材` : ""),
                              "ok",
                            );
                            await api.zspaceSyncStatus().then(setSyncStatus);
                          } catch (e: unknown) {
                            notify(String(e), "err");
                          }
                        })();
                      }}
                    >
                      更换来源
                    </button>
                  </div>
                </label>
              </div>
            </>
          )}

          {(section === "defaults" || section === "notify") && (
            <>
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
                  onClick={() => void saveCursorApiKey()}
                >
                  {actionBusy === "cursorKey" ? "保存中…" : "保存"}
                </button>
                <button
                  type="button"
                  disabled={actionBusy === "cursorKey" || actionBusy === "cursorVerify"}
                  onClick={() => void verifyCursorApiKey()}
                >
                  {actionBusy === "cursorVerify" ? "验证中…" : "验证并启用"}
                </button>
                {cursorKeyConfigured ? (
                  <button
                    type="button"
                    disabled={actionBusy === "cursorClear"}
                    onClick={() => void clearCursorApiKey()}
                  >
                    清除
                  </button>
                ) : null}
                <span className="hint">
                  {cursorKeyConfigured ? `状态：已配置 ${cursorKeyHint || ""}` : "状态：未配置"}
                </span>
              </div>

              <h3 className="section-title">每日自动开跑</h3>
              <div className="grid3">
                <label>
                  开关
                  <select
                    value={autoDaily ? "1" : "0"}
                    onChange={(e) => setAutoDaily(e.target.value === "1")}
                  >
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
              <p className="hint" style={{ marginTop: 8 }}>
                与路径一并点「路径」分区的「保存设置」写入引擎；也可在保存路径时带上调度。
              </p>

              <h3 className="section-title">布局密度</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                auto 随窗口宽度；comfort 偏宽松；compact 压缩间距。仅影响本机界面。
              </p>
              <div className="actions">
                {(
                  [
                    ["auto", "自动"],
                    ["comfort", "舒适"],
                    ["compact", "紧凑"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    type="button"
                    className={layoutPref === id ? "primary" : undefined}
                    onClick={() => setLayoutPref(id)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </>
          )}

          {section === "paths" && (
            <>
              <h3 className="section-title">路径与词池</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                首次配置完成后会记住；只需在变更片库/输出/词池时在此修改并保存。缓存 / 渲染 / 数据库路径在「高级」。
              </p>
              <div className="grid2">
                <label>
                  主片库
                  <div className="path-row">
                    <input value={libraryRoot} onChange={(e) => setLibraryRoot(e.target.value)} />
                    <button type="button" onClick={() => void pickDir().then((p) => p && setLibraryRoot(p))}>
                      选择
                    </button>
                  </div>
                </label>
                <label>
                  输出目录
                  <div className="path-row">
                    <input value={outputRoot} onChange={(e) => setOutputRoot(e.target.value)} />
                    <button type="button" onClick={() => void pickDir().then((p) => p && setOutputRoot(p))}>
                      选择
                    </button>
                  </div>
                </label>
              </div>
              <label>
                追加片库（每行一个）
                <textarea
                  rows={2}
                  value={libraryRootsText}
                  onChange={(e) => setLibraryRootsText(e.target.value)}
                />
              </label>
              <label>
                词池路径
                <div className="path-row">
                  <input
                    value={keywordPackPath}
                    onChange={(e) => setKeywordPackPath(e.target.value)}
                  />
                  <button
                    type="button"
                    onClick={() => void pickFile().then((p) => p && setKeywordPackPath(p))}
                  >
                    选择
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      api
                        .reloadKeywordsFromPath(activeCustomerId ?? undefined)
                        .then(async (r) => {
                          notify(briefResult("词池已重载", r), "ok");
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
                        .catch((e: unknown) => notify(String(e), "err"))
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
                          .catch((e: unknown) => notify(String(e), "err"))
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
                  onClick={() => void savePaths()}
                >
                  {actionBusy === "savePaths" ? "保存中…" : "保存设置"}
                </button>
              </div>
            </>
          )}

          {section === "advanced" && (
            <>
              <h3 className="section-title">高级配置</h3>
              <p className="hint" style={{ marginBottom: 10 }}>
                缓存 / 渲染 / 数据库路径等危险项需密码解锁。密码仅防误操作，保存在本机钥匙串。
              </p>

              {!advancedUnlocked ? (
                <div className="advanced-lock">
                  <div className="advanced-lock-icon" aria-hidden>
                    锁
                  </div>
                  <p className="hint" style={{ marginBottom: 12 }}>
                    {passwordConfigured
                      ? "高级配置已锁定。输入密码解锁。"
                      : "尚未设置高级密码。首次请创建（至少 6 位）。"}
                  </p>
                  {passwordConfigured ? (
                    <>
                      <label>
                        密码
                        <input
                          type="password"
                          autoComplete="off"
                          value={unlockPassword}
                          onChange={(e) => setUnlockPassword(e.target.value)}
                          placeholder="输入高级密码"
                        />
                      </label>
                      <div className="actions" style={{ marginTop: 10, justifyContent: "center" }}>
                        <button
                          type="button"
                          className="primary"
                          disabled={pwBusy || !unlockPassword}
                          onClick={() => {
                            setPwBusy(true);
                            Promise.resolve(onUnlock(unlockPassword))
                              .then(() => {
                                setUnlockPassword("");
                                notify("高级配置已解锁", "ok");
                              })
                              .catch((e: unknown) => notify(String(e), "err"))
                              .finally(() => setPwBusy(false));
                          }}
                        >
                          解锁
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <label>
                        新密码
                        <input
                          type="password"
                          autoComplete="off"
                          value={createPassword}
                          onChange={(e) => setCreatePassword(e.target.value)}
                          placeholder="至少 6 位"
                        />
                      </label>
                      <label>
                        确认密码
                        <input
                          type="password"
                          autoComplete="off"
                          value={createPassword2}
                          onChange={(e) => setCreatePassword2(e.target.value)}
                        />
                      </label>
                      <div className="actions" style={{ marginTop: 10, justifyContent: "center" }}>
                        <button
                          type="button"
                          className="primary"
                          disabled={pwBusy || !createPassword}
                          onClick={() => {
                            if (createPassword !== createPassword2) {
                              notify("两次密码不一致", "err");
                              return;
                            }
                            setPwBusy(true);
                            Promise.resolve(onCreatePassword(createPassword))
                              .then(() => {
                                setCreatePassword("");
                                setCreatePassword2("");
                                notify("高级密码已创建并解锁", "ok");
                              })
                              .catch((e: unknown) => notify(String(e), "err"))
                              .finally(() => setPwBusy(false));
                          }}
                        >
                          创建密码
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ) : (
                <>
                  <div className="actions" style={{ marginBottom: 12 }}>
                    <span className="hint">
                      已解锁
                      {passwordStatusRemaining > 0
                        ? ` · 约 ${Math.ceil(passwordStatusRemaining / 60)} 分钟后自动锁定`
                        : ""}
                    </span>
                    <button
                      type="button"
                      disabled={pwBusy}
                      onClick={() => {
                        setPwBusy(true);
                        Promise.resolve(onLock())
                          .then(() => notify("高级配置已锁定", "ok"))
                          .catch((e: unknown) => notify(String(e), "err"))
                          .finally(() => setPwBusy(false));
                      }}
                    >
                      立即锁定
                    </button>
                  </div>

                  <label>
                    缓存目录（抽帧 / 代理 / 规范化）
                    <div className="path-row">
                      <input
                        value={cacheRoot}
                        onChange={(e) => setCacheRoot(e.target.value)}
                        placeholder="/Users/…/速影工作区/cache"
                      />
                      <button
                        type="button"
                        onClick={() => void pickDir().then((p) => p && setCacheRoot(p))}
                      >
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
                      <button
                        type="button"
                        onClick={() => void pickDir().then((p) => p && setRenderRoot(p))}
                      >
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
                      <button
                        type="button"
                        onClick={() => void pickDir().then((p) => p && setDataRoot(p))}
                      >
                        选择
                      </button>
                    </div>
                  </label>
                  <p className="hint" style={{ margin: "6px 0 12px" }}>
                    当前库文件：{vectorDbPath || "…/montage.db"}（只读提示；工作区在外置盘、不进极空间同步）
                  </p>
                  <div className="actions">
                    <button
                      type="button"
                      className={`primary${actionBusy === "savePaths" ? " is-busy" : ""}`}
                      disabled={actionBusy === "savePaths"}
                      onClick={() => void savePaths()}
                    >
                      {actionBusy === "savePaths" ? "保存中…" : "保存高级路径"}
                    </button>
                    <button
                      type="button"
                      disabled={actionBusy === "cleanCache"}
                      className={actionBusy === "cleanCache" ? "is-busy" : undefined}
                      onClick={() => {
                        setActionBusy("cleanCache");
                        api
                          .cleanCache(24)
                          .then((r) =>
                            notify(`清理缓存: ${r.removed_files} 文件 / ${r.freed_mb} MB`, "ok"),
                          )
                          .catch((e: unknown) => notify(String(e), "err"))
                          .finally(() => setActionBusy(null));
                      }}
                    >
                      {actionBusy === "cleanCache" ? "清理中…" : "清理缓存"}
                    </button>
                  </div>

                  <h3 className="section-title">修改高级密码</h3>
                  <div className="grid2">
                    <label>
                      旧密码
                      <input
                        type="password"
                        autoComplete="off"
                        value={oldPassword}
                        onChange={(e) => setOldPassword(e.target.value)}
                      />
                    </label>
                    <label>
                      新密码
                      <input
                        type="password"
                        autoComplete="off"
                        value={newPassword}
                        onChange={(e) => setNewPassword(e.target.value)}
                        placeholder="至少 6 位"
                      />
                    </label>
                  </div>
                  <div className="actions" style={{ marginTop: 8 }}>
                    <button
                      type="button"
                      disabled={pwBusy || !oldPassword || !newPassword}
                      onClick={() => {
                        setPwBusy(true);
                        Promise.resolve(onChangePassword(oldPassword, newPassword))
                          .then(() => {
                            setOldPassword("");
                            setNewPassword("");
                            notify("高级密码已修改", "ok");
                          })
                          .catch((e: unknown) => notify(String(e), "err"))
                          .finally(() => setPwBusy(false));
                      }}
                    >
                      修改密码
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>

      <StepFooter current="settings" onJump={setTab} />
    </section>
  );
}
