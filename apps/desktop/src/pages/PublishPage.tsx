import { useState } from "react";
import { ArticleWorkbench } from "../ArticleWorkbench";
import { PublishBatchPanel } from "../PublishBatchPanel";
import { PublishDesk } from "../PublishDesk";
import { ReachCoverSection } from "../ReachCoverSection";
import { outputDisplayLabel } from "../displayId";
import type { CoverSlotSpec, ReachPlatform } from "../reachCatalog";
import { reviewStatusBadgeClass, uiStatusLabel } from "../reviewLabels";
import { EmptyState, PageHeader, SegmentNav, StepFooter } from "../shell/PageChrome";
import type {
  ChromeProfile,
  PublishWorkspace,
  ReachMessageAccount,
  SettingsSection,
  Tab,
} from "../types";
import type { NotifyFn } from "./pageTypes";

export interface PublishPageProps {
  workspace: PublishWorkspace;
  onWorkspaceChange: (w: PublishWorkspace) => void;
  setTab: (t: Tab, opts?: { settings?: SettingsSection }) => void;
  notify: NotifyFn;
  refreshAll: () => Promise<void>;
  outputs: Array<Record<string, unknown>>;
  mediaEpoch: number;
  activeCustomerId: number | null;
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
  reachPlatforms: ReachPlatform[];
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
  chromeBusy: boolean;
  chromeSelected: string;
  reachSelectChromeProfile: (name: string) => void;
  chromeProfiles: ChromeProfile[];
  chromeInstalled: boolean;
  reachOpenChromeProfile: () => void;
  reachBindMessageAccount: () => void;
  refreshReach: () => void;
  chromeRoot: string | null;
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
  activeCustomerId,
  packLast,
  packBusyId,
  exportPack,
  reachMessageUnread,
  reachInbox,
  reachItems,
  reachMessageAccounts,
  reachPlatforms,
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
  chromeBusy,
  chromeSelected,
  reachSelectChromeProfile,
  chromeProfiles,
  chromeInstalled,
  reachOpenChromeProfile,
  reachBindMessageAccount,
  refreshReach,
  chromeRoot,
  reachPackDir,
  setReachPackDir,
  queueBusy,
  reachEnqueueFromPack,
  reachMsg,
  reachOpenItem,
  reachMarkPublished,
}: PublishPageProps) {
  const readyCount = outputs.filter((o) => o.state === "ready").length;
  const [domain, setDomain] = useState<"video" | "content">(
    workspace === "articles" ? "content" : "video",
  );
  const publishProfiles = chromeProfiles.filter(
    (profile) => profile.login_status === "verified_logged_in",
  );
  const selectedChromeProfile = chromeProfiles.find((profile) => profile.name === chromeSelected);

  return (
    <section className="page-stack publish-page">
      <PageHeader title="发布" blurb="物料、发布台与触达" />
      <SegmentNav
        ariaLabel="发布业务域"
        value={domain}
        onChange={(next) => {
          setDomain(next);
          onWorkspaceChange(next === "content" ? "articles" : workspace === "articles" ? "reach" : workspace);
        }}
        items={[
          {
            id: "video",
            label: "视频发布",
            badge: reachMessageUnread + Number(reachInbox?.unread_count ?? 0),
          },
          { id: "content", label: "软文发布" },
        ]}
      />
      {domain === "video" ? (
        <SegmentNav
          ariaLabel="视频发布分区"
          value={workspace === "articles" ? "reach" : workspace}
          onChange={onWorkspaceChange}
          items={[
            { id: "pack", label: "准备视频", badge: readyCount },
            { id: "reach", label: "快速发布" },
            { id: "desk", label: "手动发布与历史" },
          ]}
        />
      ) : null}

      {domain === "content" ? (
        <ArticleWorkbench
          notify={notify}
          activeCustomerId={activeCustomerId}
          onGoBrand={() => setTab("settings", { settings: "brand" })}
          onGoAccounts={() => setTab("settings", { settings: "accounts" })}
        />
      ) : null}

      {workspace === "pack" ? (
        <>
          <div className="panel-head">
            <h2>发布物料</h2>
            <span className="count">{readyCount} 条可发布</span>
          </div>
          <p className="hint">
            为可发布成片生成视频、封面、平台文案和字幕。旁白、字幕、音色和画面样式由生产时保存的规则决定。
            <button type="button" className="linkish" onClick={() => setTab("rules")}>
              去规则实验室调整
            </button>
          </p>
          {packLast && <p className="path">{packLast}</p>}
          <div className="review-list">
            {outputs
              .filter((o) => o.state === "ready")
              .map((o) => (
                <article
                  key={String(o.id)}
                  className="review-card"
                  data-guide={`publish-output-${String(o.id)}`}
                >
                  <div className="review-meta">
                    <strong>{outputDisplayLabel(o) || "成片"}</strong>
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
            <h2>视频发布</h2>
            <span className="count">
              账号 {chromeProfiles.length} · 待处理 {reachInbox?.unread_count ?? 0}
            </span>
          </div>
          <p className="hint">
            登录一次本人账号，填写这次要发几条，然后点开始。合格视频、文案、封面和账号分配由系统自动处理。
          </p>
          <div className="actions-inline" style={{ marginBottom: 12 }} data-guide="publish-next-step">
            <button type="button" className="primary" onClick={() => onWorkspaceChange("desk")}>
              手动发布
            </button>
            <button type="button" onClick={() => setTab("messages")}>
              查看与回复消息
              {reachMessageUnread > 0 ? `（${reachMessageUnread}）` : ""}
            </button>
          </div>
          {chromeProfiles.length === 0 ? (
            <div className="publish-setup-card">
              <div>
                <span className="publish-step-kicker">第一次使用</span>
                <h3>先添加并登录发布账号</h3>
                <p className="hint">账号创建与删除统一在「设置 · 账号管理」；本页只负责排队与执行。</p>
              </div>
              <div className="actions-inline">
                <button
                  type="button"
                  className="primary"
                  onClick={() => setTab("settings", { settings: "accounts" })}
                >
                  打开账号管理
                </button>
              </div>
              {!chromeInstalled ? <p className="human-alert-card">未检测到 Google Chrome，请先安装。</p> : null}
            </div>
          ) : (
            <>
              {publishProfiles.length === 0 ? (
                <div className="publish-setup-card">
                  <div>
                    <span className="publish-step-kicker">账号状态待确认</span>
                    <h3>打开原账号确认登录后即可执行发布</h3>
                    <p className="hint">
                      定时发布和立即发布功能保留在下方；系统不会创建重复目录。
                    </p>
                  </div>
                  <div className="actions-inline">
                    <select
                      value={chromeSelected}
                      onChange={(event) => reachSelectChromeProfile(event.target.value)}
                      disabled={chromeBusy}
                    >
                      {chromeProfiles.map((profile) => (
                        <option key={profile.name} value={profile.name}>
                          {profile.name}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="primary"
                      disabled={chromeBusy || !chromeSelected || !chromeInstalled}
                      onClick={reachOpenChromeProfile}
                    >
                      {chromeBusy ? "正在打开…" : "打开原配置并确认登录"}
                    </button>
                  </div>
                </div>
              ) : null}
              <PublishBatchPanel
                chromeProfiles={chromeProfiles}
                activeCustomerId={activeCustomerId}
                notify={notify}
                onRefresh={() => refreshReach()}
              />
            </>
          )}

          <details className="publish-advanced">
            <summary>高级设置与手动发布</summary>
            <p className="hint">仅在需要调整封面或手工指定物料时使用。</p>

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
            <h3>视频发布账号</h3>
            <p className="hint" style={{ marginBottom: 10 }}>
              统一入口在「设置 · 账号管理」。当前 {chromeProfiles.length} 个账号
              {chromeSelected ? ` · 当前 ${chromeSelected}` : ""}
              {selectedChromeProfile?.login_status === "verified_logged_in"
                ? " · 已登录"
                : selectedChromeProfile?.login_status === "logged_out"
                  ? " · 未登录"
                  : ""}
              。
            </p>
            <div className="actions" style={{ marginBottom: 12 }}>
              <button
                type="button"
                className="primary"
                onClick={() => setTab("settings", { settings: "accounts" })}
              >
                打开账号管理
              </button>
              <button type="button" onClick={() => refreshReach()}>
                刷新登录状态
              </button>
              <button
                type="button"
                disabled={chromeBusy || !chromeSelected || !chromeInstalled}
                onClick={reachOpenChromeProfile}
              >
                打开当前账号确认登录
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
                  ? "已加入视频消息巡检"
                  : "加入视频消息巡检"}
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
                {selectedChromeProfile?.login_status === "verified_logged_in"
                  ? " · 当前受管 Chrome 实时确认已登录"
                  : selectedChromeProfile?.login_status === "logged_out"
                    ? " · 当前受管 Chrome 实时确认未登录"
                    : " · 登录状态未知或已过期"}
                {selectedChromeProfile?.last_profile_exit_type === "Crashed"
                  ? " · 上次 Chrome 未正常退出"
                  : ""}
              </p>
            ) : null}
            <p className="hint">
              Cookie 是否存在只作存储诊断，不代表已登录。只有当前受管 Chrome 的实时 DOM 探针通过后，
              账号才可进入立即发布或新建计划。
            </p>
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
                    {outputDisplayLabel(
                      outputs.find((output) => Number(output.id) === Number(it.output_id)) || {},
                    ) || "发布任务"}{" "}
                    · {String(it.platform)}
                  </strong>
                  <div className="review-meta-badges">
                    <em className={reviewStatusBadgeClass(it.status)}>{uiStatusLabel(it.status)}</em>
                  </div>
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
                      !chromeSelected ||
                      !chromeInstalled ||
                      it.status === "published" ||
                      it.status === "cancelled"
                    }
                    onClick={() => {
                      onWorkspaceChange("reach");
                      notify("请在上方“一键即时批量发布”中选择账号和内容后启动", "info");
                    }}
                  >
                    加入即时发布配置
                  </button>
                  {it.status === "awaiting_human" && (
                    <button type="button" onClick={() => reachMarkPublished(Number(it.id))}>
                      我已发布
                    </button>
                  )}
                </div>
              </article>
            ))}
            {reachItems.length === 0 && (
              <p className="empty">暂无手动发布任务</p>
            )}
          </div>
          </details>
        </>
      ) : null}

      <StepFooter current="publish" onJump={setTab} />
    </section>
  );
}
