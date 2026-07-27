import type { Dispatch, SetStateAction } from "react";
import { PublishDesk } from "../PublishDesk";
import { ReachCoverSection } from "../ReachCoverSection";
import type { CoverSlotSpec, ReachPlatform } from "../reachCatalog";
import { LangCombobox } from "../shell/LangCombobox";
import { EmptyState, PageHeader, SegmentNav, StepFooter } from "../shell/PageChrome";
import type {
  PublishWorkspace,
  ReachMessageAccount,
  Tab,
} from "../types";
import type { LangCatalogItem, NotifyFn } from "./pageTypes";

export interface PublishPageProps {
  workspace: PublishWorkspace;
  onWorkspaceChange: (w: PublishWorkspace) => void;
  setTab: (t: Tab) => void;
  notify: NotifyFn;
  refreshAll: () => Promise<void>;
  outputs: Array<Record<string, unknown>>;
  mediaEpoch: number;
  voiceLang: string;
  setVoiceLang: (v: string) => void;
  subtitleLang: string;
  setSubtitleLang: (v: string) => void;
  subtitleBurn: string;
  setSubtitleBurn: (v: string) => void;
  dualSecondaryLang: string;
  setDualSecondaryLang: (v: string) => void;
  langCatalog: LangCatalogItem[];
  exprSaveBusy: boolean;
  exprDirty: boolean;
  activeCustomerId: number | null;
  saveExpressionPrefs: () => Promise<void> | void;
  packLast: string;
  packBusyId: number | null;
  exportPack: (id: number) => void;
  reachMessageUnread: number;
  reachInbox: {
    unread_count?: number;
    notices?: Array<Record<string, unknown>>;
  } | null;
  reachItems: Array<Record<string, unknown>>;
  reachMessageAccounts: ReachMessageAccount[];
  reachQuota: Record<string, unknown> | null;
  quotaTotal: number;
  setQuotaTotal: (v: number) => void;
  quotaSplit: boolean;
  setQuotaSplit: (v: boolean) => void;
  quotaByPlatform: Record<string, number>;
  setQuotaByPlatform: Dispatch<SetStateAction<Record<string, number>>>;
  quotaBusy: boolean;
  reachPlatforms: ReachPlatform[];
  saveReachQuota: () => void;
  coverTemplates: Array<Record<string, unknown>>;
  coverEditId: string;
  coverSelectedId: string | null;
  coverNewName: string;
  coverSlotCounts: Record<string, number>;
  coverSlotSpecs: Record<string, CoverSlotSpec[]>;
  coverPreviewPlat: string;
  coverPreviewMsg: string;
  coverBusy: boolean;
  setCoverNewName: (v: string) => void;
  setCoverEditId: (v: string) => void;
  setCoverPreviewPlat: (v: string) => void;
  coverCreateTemplate: () => void;
  coverSeedFromPack: () => void;
  coverSelectTemplate: (id: string) => void;
  coverDeleteTemplate: (id: string) => void;
  coverSetSlot: (platform: string, slotIndex: number) => void;
  coverPreviewResolve: () => void;
  chromeCreatePlatform: string;
  setChromeCreatePlatform: (v: string) => void;
  chromeCreateCount: number;
  setChromeCreateCount: (v: number) => void;
  chromeBusy: boolean;
  reachCreateChromeProfiles: () => void;
  chromeSelected: string;
  reachSelectChromeProfile: (name: string) => void;
  chromeProfiles: Array<{ name: string; path?: string; platform?: string | null; label?: string | null }>;
  chromeInstalled: boolean;
  reachOpenChromeProfile: () => void;
  reachBindMessageAccount: () => void;
  autoUploadBusy: boolean;
  reachStartAutoUpload: (itemId?: number) => void;
  reachCancelAutoUpload: () => void;
  refreshReach: () => void;
  chromeRoot: string | null;
  autoUploadPhase: string;
  autoUploadMsg: string;
  reachPackDir: string;
  setReachPackDir: (v: string) => void;
  queueBusy: boolean;
  reachEnqueueFromPack: () => void;
  reachMsg: string;
  reachOpenItem: (id: number) => void;
  reachMarkPublished: (id: number) => void;
}

export function PublishPage({
  workspace,
  onWorkspaceChange,
  setTab,
  notify,
  refreshAll,
  outputs,
  mediaEpoch,
  voiceLang,
  setVoiceLang,
  subtitleLang,
  setSubtitleLang,
  subtitleBurn,
  setSubtitleBurn,
  dualSecondaryLang,
  setDualSecondaryLang,
  langCatalog,
  exprSaveBusy,
  exprDirty,
  activeCustomerId,
  saveExpressionPrefs,
  packLast,
  packBusyId,
  exportPack,
  reachMessageUnread,
  reachInbox,
  reachItems,
  reachMessageAccounts,
  reachQuota,
  quotaTotal,
  setQuotaTotal,
  quotaSplit,
  setQuotaSplit,
  quotaByPlatform,
  setQuotaByPlatform,
  quotaBusy,
  reachPlatforms,
  saveReachQuota,
  coverTemplates,
  coverEditId,
  coverSelectedId,
  coverNewName,
  coverSlotCounts,
  coverSlotSpecs,
  coverPreviewPlat,
  coverPreviewMsg,
  coverBusy,
  setCoverNewName,
  setCoverEditId,
  setCoverPreviewPlat,
  coverCreateTemplate,
  coverSeedFromPack,
  coverSelectTemplate,
  coverDeleteTemplate,
  coverSetSlot,
  coverPreviewResolve,
  chromeCreatePlatform,
  setChromeCreatePlatform,
  chromeCreateCount,
  setChromeCreateCount,
  chromeBusy,
  reachCreateChromeProfiles,
  chromeSelected,
  reachSelectChromeProfile,
  chromeProfiles,
  chromeInstalled,
  reachOpenChromeProfile,
  reachBindMessageAccount,
  autoUploadBusy,
  reachStartAutoUpload,
  reachCancelAutoUpload,
  refreshReach,
  chromeRoot,
  autoUploadPhase,
  autoUploadMsg,
  reachPackDir,
  setReachPackDir,
  queueBusy,
  reachEnqueueFromPack,
  reachMsg,
  reachOpenItem,
  reachMarkPublished,
}: PublishPageProps) {
  const readyCount = outputs.filter((o) => o.state === "ready").length;

  return (
    <section>
      <PageHeader title="发布" blurb="物料、发布台与触达" />
      <SegmentNav
        ariaLabel="发布分区"
        value={workspace}
        onChange={onWorkspaceChange}
        items={[
          { id: "pack", label: "物料", badge: readyCount },
          { id: "desk", label: "发布台" },
          {
            id: "reach",
            label: "触达",
            badge: reachMessageUnread + Number(reachInbox?.unread_count ?? 0),
          },
        ]}
      />

      {workspace === "pack" ? (
        <>
          <div className="panel-head">
            <h2>发布物料</h2>
            <span className="count">{readyCount} READY</span>
          </div>
          <p className="hint">
            为 ready 成片生成 publish_pack（视频、封面、四平台文案、字幕、禁词扫描）。人在回路，不自动发布。
            下方「旁白语言 / 字幕语言 / 字幕方式」会写入客户档案，并在「生产 / 重渲」时决定成片旁白与是否烧录字幕；导出物料包也会沿用同一套设置。
          </p>
          <div
            className="actions-inline"
            style={{ marginBottom: "0.75rem", flexWrap: "wrap", gap: "0.75rem" }}
          >
            <label>
              旁白语言
              <LangCombobox
                value={voiceLang}
                onChange={setVoiceLang}
                languages={langCatalog}
                includeNone
              />
            </label>
            <label>
              字幕语言
              <LangCombobox
                value={subtitleLang}
                onChange={setSubtitleLang}
                languages={langCatalog}
                includeNone
              />
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
                <LangCombobox
                  value={dualSecondaryLang}
                  onChange={setDualSecondaryLang}
                  languages={langCatalog}
                />
              </label>
            ) : null}
            <button
              type="button"
              className="primary"
              disabled={exprSaveBusy || !activeCustomerId}
              onClick={() => void saveExpressionPrefs()}
            >
              {exprSaveBusy ? "保存中…" : "保存表达设置"}
              {exprDirty ? <span className="dirty-dot" title="未保存" /> : null}
            </button>
          </div>
          {exprDirty ? (
            <p className="expr-dirty-hint">表达设置已修改未保存；导出物料包前会再次确认。</p>
          ) : null}
          <p className="hint" style={{ marginTop: "-0.35rem" }}>
            旁白语言 = 成片语音；字幕语言 = 主字幕文案；双语烧录时「双语副语言」为第二行。共{" "}
            {Math.max(0, langCatalog.filter((l) => l.code !== "none").length)} 种语言可选。改完请点「保存表达设置」，再去生产/重渲才会进成片。
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
                    {o.has_voice ? (
                      <em className="badge-ok">旁白</em>
                    ) : (
                      <em className="badge-mute">无旁白</em>
                    )}
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
                    <button type="button" onClick={() => onWorkspaceChange("desk")}>
                      去发布台
                    </button>
                  </div>
                </article>
              ))}
            {readyCount === 0 && (
              <EmptyState
                title="暂无 ready 成片"
                actionLabel="去生产"
                onAction={() => setTab("produce")}
              />
            )}
          </div>
        </>
      ) : null}

      {workspace === "desk" ? (
        <PublishDesk
          outputs={outputs}
          onNotify={notify}
          onRefresh={refreshAll}
          onGoReach={() => onWorkspaceChange("reach")}
          mediaEpoch={mediaEpoch}
        />
      ) : null}

      {workspace === "reach" ? (
        <>
          <div className="panel-head">
            <h2>触达助手</h2>
            <span className="count">
              平台消息 {reachMessageUnread} · 发布待办 {reachInbox?.unread_count ?? 0} · 队列{" "}
              {reachItems.length}
            </span>
          </div>
          <p className="hint">
            默认人点发布 · 选手台+数量按需建 Chrome 配置 · 封面模板多套按平台槽位 · 文案+封面齐套才允许自动点发 ·
            本人账号可后台串行只读消息摘要并跳官方页人工回复 · 不提供自动回复 / 绕检测 / Cookie 池 / 矩阵养号。
            详见 docs/REACH_NON_GOALS.md；复制文案请先到「发布」台。
          </p>
          <div className="actions-inline" style={{ marginBottom: 12 }}>
            <button type="button" className="primary" onClick={() => onWorkspaceChange("desk")}>
              去发布台复制文案
            </button>
            <button type="button" onClick={() => setTab("messages")}>
              查看与回复消息
              {reachMessageUnread > 0 ? `（${reachMessageUnread}）` : ""}
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
            busy={coverBusy}
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
                  disabled={chromeBusy}
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
                  disabled={chromeBusy}
                  style={{ width: 64 }}
                />
              </label>
              <button
                type="button"
                className="primary"
                disabled={chromeBusy}
                onClick={reachCreateChromeProfiles}
              >
                创建配置
              </button>
            </div>
            <div className="actions-inline" style={{ marginBottom: 12, gap: 8, flexWrap: "wrap" }}>
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span>当前配置</span>
                <select
                  value={chromeSelected}
                  onChange={(e) => reachSelectChromeProfile(e.target.value)}
                  disabled={chromeBusy || chromeProfiles.length === 0}
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
                disabled={chromeBusy || !chromeSelected || !chromeInstalled}
                onClick={reachOpenChromeProfile}
              >
                打开官方页
              </button>
              <button
                type="button"
                disabled={
                  chromeBusy ||
                  !chromeSelected ||
                  reachMessageAccounts.some((account) => account.profile_name === chromeSelected)
                }
                onClick={reachBindMessageAccount}
              >
                {reachMessageAccounts.some((account) => account.profile_name === chromeSelected)
                  ? "已加入消息巡检"
                  : "加入消息巡检"}
              </button>
              <button
                type="button"
                className="primary"
                disabled={chromeBusy || autoUploadBusy || !chromeSelected || !chromeInstalled}
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
            <p className="hint reach-risk-hint">
              自动上传会用本机 Chrome 打开官方页并代填；平台风控与账号安全由你自行承担（accept_risk）。
            </p>
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
            <button
              type="button"
              className="primary"
              disabled={queueBusy}
              onClick={reachEnqueueFromPack}
            >
              {queueBusy ? "处理中…" : "从物料包入队"}
            </button>
          </div>
          {reachMsg && <p className="path">{reachMsg}</p>}
          {(reachInbox?.notices || []).length > 0 && (
            <div className="review-list" style={{ marginBottom: 16 }}>
              <h3>发布待办</h3>
              <p className="hint">本机队列提醒（不是平台私信）</p>
              {(reachInbox?.notices || []).slice(0, 8).map((n) => (
                <article key={String(n.id)} className="review-card">
                  <div className="review-meta">
                    <strong>{String(n.kind)}</strong>
                    <span>{String(n.summary)}</span>
                  </div>
                  {n.deep_link && typeof n.deep_link === "object" ? (
                    <p className="path">
                      {String((n.deep_link as Record<string, unknown>).url || "")}
                    </p>
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
                    disabled={queueBusy || it.status === "published" || it.status === "cancelled"}
                    onClick={() => reachOpenItem(Number(it.id))}
                  >
                    打开官方入口
                  </button>
                  <button
                    type="button"
                    disabled={
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
        </>
      ) : null}

      <StepFooter current="publish" onJump={setTab} />
    </section>
  );
}
