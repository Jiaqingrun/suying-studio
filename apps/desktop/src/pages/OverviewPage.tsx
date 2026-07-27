import { api } from "../api";
import type { Health, ReportSummary, ServicesStatus } from "../api";
import { CarrierOpsStrip } from "../CarrierOpsStrip";
import { ActivityTicker, type ActivityItem } from "../shell/ActivityTicker";
import { OverviewPipeline } from "../shell/OverviewPipeline";
import { PageHeader, StepFooter } from "../shell/PageChrome";
import type { Tab } from "../types";
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
  setTab: (t: Tab) => void;
  refreshAll: () => Promise<void>;
  report: ReportSummary | null;
  opsReport: OpsReportView | null;
  assets: Array<Record<string, unknown>>;
  reachQuota: Record<string, unknown> | null;
  health: Health | null;
  services: ServicesStatus | null;
  todayPlan: Record<string, unknown> | null;
  actionBusy: string | null;
  setActionBusy: (v: string | null) => void;
  notify: NotifyFn;
  autoDaily: boolean;
  autoHour: number;
  setShowWizard: (v: boolean) => void;
  activityItems: ActivityItem[];
}

export function OverviewPage({
  pipelineNodes,
  pathBlocked,
  setTab,
  refreshAll,
  report,
  opsReport,
  assets,
  reachQuota,
  health,
  services,
  todayPlan,
  actionBusy,
  setActionBusy,
  notify,
  autoDaily,
  autoHour,
  setShowWizard,
  activityItems,
}: OverviewPageProps) {
  return (
    <section>
      <PageHeader
        title="总览"
        blurb="今日产线与下一步"
        actions={
          <button type="button" onClick={() => void refreshAll()}>
            刷新
          </button>
        }
      />
      <ActivityTicker items={activityItems} onJump={setTab} />
      <OverviewPipeline nodes={pipelineNodes} onJump={setTab} pathBlocked={pathBlocked} />
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
          <div className="stat-label">待发 Ready</div>
          <div className="stat-value">
            {opsReport?.pending_ready ?? opsReport?.ready ?? report?.ready ?? 0}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">已发归档</div>
          <div className="stat-value">{opsReport?.published_count ?? opsReport?.published_ready ?? 0}</div>
        </div>
        <div className="stat">
          <div className="stat-label">今日 Ready / 新发</div>
          <div className="stat-value" style={{ fontSize: "1rem" }}>
            {opsReport ? `${opsReport.ready_today ?? 0} / ${opsReport.published_today ?? 0}` : "—"}
          </div>
        </div>
        <div className="stat">
          <div className="stat-label">失败率</div>
          <div className="stat-value">
            {opsReport?.failure_rate != null
              ? `${(opsReport.failure_rate * 100).toFixed(1)}%`
              : report
                ? `${(report.failure_rate * 100).toFixed(1)}%`
                : "—"}
          </div>
        </div>
      </div>
      <div className="ops-bar">
        <span>
          Ready 率 {opsReport?.ready_rate != null ? `${(opsReport.ready_rate * 100).toFixed(0)}%` : "—"}
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
          触达配额{" "}
          {opsReport?.quota
            ? `${String(opsReport.quota.used_total ?? opsReport.quota.used_today ?? 0)}/${String(opsReport.quota.daily_quota ?? "—")}`
            : reachQuota
              ? `${String(reachQuota.used_total ?? reachQuota.used_today ?? 0)}/${String(reachQuota.daily_quota ?? "—")}`
              : "—"}
        </span>
        <span
          className={`health-line${
            opsReport &&
            (!opsReport.library_ok ||
              !opsReport.output_ok ||
              (opsReport.tts_noncompliant ?? 0) > 0 ||
              opsReport.db_ok === false)
              ? " is-warn"
              : ""
          }`}
        >
          {opsReport?.health_line ||
            (health
              ? `引擎正常 · 磁盘 ${health.path_health.free_disk_gb.toFixed(0)} GB`
              : "引擎状态未知")}
        </span>
      </div>
      <CarrierOpsStrip />
      {todayPlan ? (
        <div className="banner-ok">
          今日计划 {String(todayPlan.day)} · {String(todayPlan.theme)} · 配额 {String(todayPlan.quota)}
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
      <ul className="meta">
        <li>
          监视 {services?.watcher || health?.watcher_running ? "开" : "关"} · Worker{" "}
          {services?.worker || health?.worker_running ? "开" : "关"} · 调度{" "}
          {services?.scheduler || health?.scheduler_running ? "开" : "关"} · 每日自动{" "}
          {autoDaily ? `开 @${autoHour}:00` : "关"}
        </li>
      </ul>
      <StepFooter current="overview" onJump={setTab} />
    </section>
  );
}
