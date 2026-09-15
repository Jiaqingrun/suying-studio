import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Health, ReportSummary } from "../api";
import { outputDisplayLabel } from "../displayId";
import { openMediaTarget } from "../openMediaTarget";
import { ActivityTicker, type ActivityItem } from "../shell/ActivityTicker";
import { OverviewPipeline } from "../shell/OverviewPipeline";
import {
  EmptyState,
  PageHeader,
  PageSection,
  StatusStrip,
  StepFooter,
} from "../shell/PageChrome";
import { AvReviewPublishGallery } from "../shell/AvReviewPublishGallery";
import { previewCoverSrc } from "../mediaPreview";
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
      .catch((e: unknown) => {
        setHumanAlerts([]);
        notify(`人工告警拉取失败：${String(e)}`, "warn");
      });
  }, [notify]);

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

  const failureRate =
    opsReport?.failure_rate != null
      ? `${(opsReport.failure_rate * 100).toFixed(1)}%`
      : report?.failure_rate != null
        ? `${(report.failure_rate * 100).toFixed(1)}%`
        : "—";

  return (
    <section className="page-stack overview-page">
      <PageHeader
        title="总览"
        blurb="今日产线 · 待办与刚完成；跳转生产 / 审片 / 发布"
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
      <AvReviewPublishGallery
        tasks={(recentOutputs || [])
          .filter((o) => {
            const reviewStatus = o.review_status;
            return (
              o.state === "review" &&
              (reviewStatus == null ||
                reviewStatus === "uncertain" ||
                reviewStatus === "pending" ||
                o.needs_human === true)
            );
          })
          .slice(0, 3)
          .map((o) => {
            const oid = Number(o.id);
            const label = outputDisplayLabel(o) || "成片";
            const st = String(o.review_status || o.status || "pending");
            const tone =
              st.includes("pass") || st.includes("ok") || st === "approved"
                ? ("ok" as const)
                : st.includes("revise") || st.includes("reject")
                  ? ("revise" as const)
                  : ("pending" as const);
            const status = tone === "ok" ? "已通过" : tone === "revise" ? "修订中" : "待审校";
            let thumb: string | null = null;
            try {
              const coverPath = Array.isArray(o.cover_paths)
                ? String(o.cover_paths[0] || "")
                : String(o.cover_path || "");
              thumb = previewCoverSrc(oid, 0, coverPath || null) || null;
            } catch {
              thumb = null;
            }
            return {
              id: String(oid),
              title: label,
              meta: o.duration_label
                ? String(o.duration_label)
                : o.shot_count != null
                  ? `${o.shot_count} 镜头`
                  : "待人工",
              status,
              statusTone: tone,
              thumb,
            };
          })}
        publishTitle={
          opsReport
            ? `今日新发 ${opsReport.published_today}`
            : report
              ? `可用成片 ${report.ready_available ?? "—"}`
              : "发布工作台"
        }
        publishMeta={
          opsReport
            ? `可用成片 ${opsReport.ready_available} · 待人工 ${opsReport.uncertain_open}`
            : "前往发布页处理成片与触达"
        }
        onOpenAllTasks={() => setTab("review")}
        onOpenPublish={() => setTab("publish")}
      />

      <div className="overview-command">
        {health && !health.path_health.ok ? (
          <div className="banner error">
            路径异常，禁止生产：{(health.path_health.errors || []).join("；") || "请检查片库/成片目录"}
          </div>
        ) : null}

        {todayPlan ? (
          <div className="banner-ok">
            今日计划 {String(todayPlan.day)} · {productionThemeLabel(String(todayPlan.theme || ""))} · 配额{" "}
            {String(todayPlan.quota)}
            {" · "}
            自动 {autoDaily ? `${autoHour}:00` : "关"}
          </div>
        ) : (
          <div className="banner error">今日无日历计划 — 去生产填日历</div>
        )}

        <OverviewPipeline nodes={pipelineNodes} onJump={setTab} pathBlocked={pathBlocked} />

        <div className="overview-kpi-strip">
          <StatusStrip
            items={[
              {
                label: "可用成片",
                value: opsReport?.ready_available ?? report?.ready_available ?? "—",
                tone: "ok",
              },
              {
                label: "今日通过/新发",
                value: opsReport
                  ? `${opsReport.production_passed_today} / ${opsReport.published_today}`
                  : "—",
              },
              {
                label: "待人工",
                value: opsReport?.uncertain_open ?? "—",
                tone: Number(opsReport?.uncertain_open ?? 0) > 0 ? "warn" : "neutral",
              },
              {
                label: "失败率",
                value: failureRate,
                tone: failureRate !== "—" && failureRate !== "0.0%" ? "warn" : "neutral",
              },
              {
                label: "设备",
                value: opsReport?.health_line
                  ? "见运维"
                  : health
                    ? `${health.path_health.free_disk_gb.toFixed(0)} GB`
                    : "—",
                tone:
                  opsReport &&
                  (!opsReport.library_ok || !opsReport.output_ok || opsReport.db_ok === false)
                    ? "danger"
                    : "ok",
              },
            ]}
          />
          <p className="hint" style={{ marginTop: 6 }}>
            {opsReport?.health_line ||
              (health
                ? `设备可用 · 剩余空间 ${health.path_health.free_disk_gb.toFixed(0)} GB`
                : "正在读取设备状态")}
            {" · "}
            来源 {opsReport?.source === "database" ? "数据库" : "—"}
            {" · "}
            {opsReport?.generated_at
              ? new Date(opsReport.generated_at).toLocaleString("zh-CN")
              : "—"}
          </p>
        </div>

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
            去发布
          </button>
          <button
            type="button"
            onClick={() => {
              setShowWizard(false);
              setTab("settings");
            }}
          >
            修改路径
          </button>
        </div>
      </div>

      <PageSection
        id="human-work"
        title="待人工"
        description="仅规则确认、登录/验证码、结果不明、熔断与错过窗口。"
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

      <PageSection
        id="just-done"
        title="刚完成"
        description="最近成片（唯一编号）。"
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

      <ActivityTicker items={activityItems} onJump={setTab} />
      <StepFooter current="overview" onJump={setTab} />
    </section>
  );
}
