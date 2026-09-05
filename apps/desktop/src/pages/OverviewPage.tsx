import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Health, ReportSummary } from "../api";
import { outputDisplayLabel } from "../displayId";
import { openMediaTarget } from "../openMediaTarget";
import { ActivityTicker, type ActivityItem } from "../shell/ActivityTicker";
import { OverviewPipeline } from "../shell/OverviewPipeline";
import { EmptyState, PageHeader, PageSection, StepFooter } from "../shell/PageChrome";
import type { HumanAlert, PublishWorkspace, Tab } from "../types";
import { WorkspaceSyncControl } from "../WorkspaceSyncControl";
import type { WorkspaceProbeView, WorkspaceSyncPrefs } from "../workspaceSync";
import { POLL_BUDGET_MS } from "../pollBudget";
import { productionThemeLabel } from "../sceneTourLabels";
import type { NotifyFn, OpsReportView } from "./pageTypes";

export type OverviewPipelineNode = {
  id: Tab;
  label: string;
  count: number;
  blocked?: boolean;
  warn?: boolean;
};

export interface OverviewPageProps {
  pipelineNodes: OverviewPipelineNode[];
  pathBlocked: boolean;
  active: boolean;
  setTab: (t: Tab, opts?: { guideTarget?: string; publish?: PublishWorkspace }) => void;
  refreshAll: () => Promise<void>;
  report: ReportSummary | null;
  opsReport: OpsReportView | null;
  assets: Array<Record<string, unknown>>;
  /** Newest finished outputs for「刚完成」jump cards */
  recentOutputs?: Array<Record<string, unknown>>;
  health: Health | null;
  todayPlan: Record<string, unknown> | null;
  actionBusy: string | null;
  setActionBusy: (v: string | null) => void;
  notify: NotifyFn;
  autoDaily: boolean;
  autoHour: number;
  setShowWizard: (v: boolean) => void;
  activityItems: ActivityItem[];
  workspacePhase: string;
  workspaceMessage?: string | null;
  workspaceError?: string | null;
  workspaceProbe?: WorkspaceProbeView | null;
  workspacePrefs: WorkspaceSyncPrefs;
  workspaceBusy: boolean;
  onWorkspaceToggleAuto: (enabled: boolean) => void;
  onWorkspaceSyncNow: () => void;
}

export function OverviewPage({
  pipelineNodes,
  pathBlocked,
  active,
  setTab,
  refreshAll,
  report,
  opsReport,
  recentOutputs = [],
  health,
  todayPlan,
  actionBusy,
  setActionBusy,
  notify,
  autoDaily,
  autoHour,
  setShowWizard,
  activityItems,
  workspacePhase,
  workspaceMessage,
  workspaceError,
  workspaceProbe,
  workspacePrefs,
  workspaceBusy,
  onWorkspaceToggleAuto,
  onWorkspaceSyncNow,
}: OverviewPageProps) {
  const [humanAlerts, setHumanAlerts] = useState<HumanAlert[]>([]);

  useEffect(() => {
    api
      .humanAlerts()
      .then((result) => setHumanAlerts(result.alerts || []))
      .catch(() => null);
  }, []);

  // Dynamic task board: refresh counts while overview is visible.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    const tick = () => {
      if (cancelled || document.visibilityState !== "visible") return;
      void refreshAll();
    };
    const timer = window.setInterval(tick, POLL_BUDGET_MS.reportOps);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, refreshAll]);

  const justDone = useMemo(() => {
    return [...recentOutputs]
      .filter((o) => o.display_no != null || o.display_label || o.output_path)
      .sort((a, b) => Number(b.display_no || b.id || 0) - Number(a.display_no || a.id || 0))
      .slice(0, 5);
  }, [recentOutputs]);

  return (
    <section className="page-stack overview-page">
      <PageHeader
        title="总览"
        blurb="先看动态与泳道：有日历则开跑，无计划去生产填日历"
        actions={
          <WorkspaceSyncControl
            phase={workspacePhase}
            message={workspaceMessage}
            error={workspaceError}
            probe={workspaceProbe}
            prefs={workspacePrefs}
            busy={workspaceBusy}
            onToggleAuto={onWorkspaceToggleAuto}
            onSyncNow={onWorkspaceSyncNow}
            onRefresh={() => void refreshAll()}
          />
        }
      />
      <PageSection
        id="just-done"
        title="刚完成"
        description="最近成片（唯一编号）。可打开文件位置或跳转下一步。"
        status={justDone.length ? `${justDone.length} 条` : "暂无"}
      >
        {justDone.length ? (
          <div className="just-done-list">
            {justDone.map((o) => {
              const label = outputDisplayLabel(o) || "成片";
              const path = String(o.output_path || "");
              const needsReview =
                o.review_status === "uncertain" ||
                o.review_status === "pending" ||
                o.state === "review";
              const nextTab: Tab = needsReview ? "review" : "publish";
              return (
                <article key={String(o.id)} className="just-done-card">
                  <strong>{label}</strong>
                  <span className="hint">{String(o.title || "")}</span>
                  <div className="actions-inline">
                    <button
                      type="button"
                      className="primary"
                      onClick={() =>
                        setTab(nextTab, {
                          guideTarget: needsReview
                            ? `review-dock-${String(o.id)}`
                            : "publish-next-step",
                          publish: needsReview ? undefined : "reach",
                        })
                      }
                    >
                      {needsReview ? "去审片" : "去发布"}
                    </button>
                    <button
                      type="button"
                      disabled={!path}
                      onClick={async () => {
                        if (!path) return;
                        try {
                          await openMediaTarget(path);
                          notify("已打开文件位置", "ok");
                        } catch (e) {
                          notify(String(e), "err");
                        }
                      }}
                    >
                      打开文件位置
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <EmptyState title="还没有刚完成的成片" actionLabel="去生产" onAction={() => setTab("produce")} />
        )}
      </PageSection>
      <PageSection
        id="human-work"
        title="唯一待人工队列"
        description="自动步骤不会出现在这里；只汇总规则确认、登录/验证码、结果不明、熔断和错过窗口。"
        status={humanAlerts.length ? `${humanAlerts.length} 项` : "已清空"}
        tone={humanAlerts.length ? "attention" : "success"}
      >
        {humanAlerts.map((alert) => (
          <article key={alert.id} className="human-alert-card">
            <strong>{alert.summary}</strong>
            <div className="actions-inline">
              <button type="button" className="primary" onClick={() => setTab("publish")}>
                去处理
              </button>
              <button
                type="button"
                onClick={() =>
                  void api.acknowledgeHumanAlert(alert.id).then(() =>
                    setHumanAlerts((current) => current.filter((item) => item.id !== alert.id)),
                  )
                }
              >
                我已知晓
              </button>
            </div>
          </article>
        ))}
        {!humanAlerts.length ? <EmptyState title="当前没有必须人工处理的事项" /> : null}
      </PageSection>
      <ActivityTicker items={activityItems} onJump={setTab} />
      <OverviewPipeline nodes={pipelineNodes} onJump={setTab} pathBlocked={pathBlocked} />
      <div className="stat-grid">
        <div className="stat">
          <div className="stat-label">素材</div>
          <div className="stat-value">{report?.assets ?? "—"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">可用片段</div>
          <div className="stat-value">
            {report ? `${report.cliplets_indexed}/${report.cliplets}` : "—"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">可用成片</div>
          <div className="stat-value">
            {opsReport?.ready_available ?? report?.ready_available ?? "—"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">已发布 / 已退役</div>
          <div className="stat-value">
            {opsReport
              ? `${opsReport.published} / ${opsReport.retired}`
              : report
                ? `${report.published} / ${report.retired}`
                : "—"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">今日生产通过 / 新发</div>
          <div className="stat-value" style={{ fontSize: "1rem" }}>
            {opsReport ? `${opsReport.production_passed_today} / ${opsReport.published_today}` : "—"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">失败率</div>
          <div className="stat-value">
            {opsReport?.failure_rate != null
              ? `${(opsReport.failure_rate * 100).toFixed(1)}%`
              : report?.failure_rate != null
                ? `${(report.failure_rate * 100).toFixed(1)}%`
                : "—"}
          </div>
        </div>
      </div>
      <div className="ops-bar">
        <span>
          生产质量通过率{" "}
          {opsReport?.quality_pass_rate != null
            ? `${(opsReport.quality_pass_rate * 100).toFixed(0)}%`
            : "—"}
          {" · "}
          自动通过 {opsReport?.auto_approved ?? "—"}
          {" · "}
          待人工 {opsReport?.uncertain_open ?? "—"}
        </span>
        <span
          className={`health-line${
            opsReport &&
            (!opsReport.library_ok ||
              !opsReport.output_ok ||
              opsReport.db_ok === false)
              ? " is-warn"
              : ""
          }`}
        >
          {opsReport?.health_line ||
            (health
              ? `设备可用 · 剩余空间 ${health.path_health.free_disk_gb.toFixed(0)} GB`
              : "正在读取设备状态")}
        </span>
      </div>
      <div className="hint" style={{ marginTop: -4 }}>
        统计时间 {opsReport?.generated_at ? new Date(opsReport.generated_at).toLocaleString("zh-CN") : "—"}
        {" · "}业务日 {opsReport?.business_date ?? "—"}
        {" · "}时区 {opsReport?.timezone ?? "—"}
        {" · "}来源 {opsReport?.source === "database" ? "数据库" : "—"}
        {" · "}文件核对{" "}
        {opsReport?.reconciliation
          ? opsReport.reconciliation.in_sync
            ? "一致"
            : "有差异"
          : "—"}
      </div>
      {todayPlan ? (
        <div className="banner-ok">
          今日计划 {String(todayPlan.day)} · {productionThemeLabel(String(todayPlan.theme || ""))} · 配额 {String(todayPlan.quota)}
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
              .catch((e: unknown) => notify(String(e), "err"))
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
            setTab("settings");
          }}
        >
          修改路径（设置）
        </button>
      </div>
      <p className="hint">
        每日自动生产：{autoDaily ? `${autoHour}:00 开始` : "未开启"}
      </p>
      <StepFooter current="overview" onJump={setTab} />
    </section>
  );
}
