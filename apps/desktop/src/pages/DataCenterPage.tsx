import { api } from "../api";
import type { ReportSummary } from "../api";
import { SemanticAnalysisProgress } from "../SemanticAnalysisProgress";
import { EmptyState, PageHeader, StatCard, StepFooter } from "../shell/PageChrome";
import type { SemanticBackfillStatus, Tab } from "../types";
import type { NotifyFn, OpsReportView, TitlePoolSummary } from "./pageTypes";

export interface DataCenterPageProps {
  setTab: (t: Tab) => void;
  report: ReportSummary | null;
  opsReport: OpsReportView | null;
  assets: Array<Record<string, unknown>>;
  reachQuota: Record<string, unknown> | null;
  semanticStatus: SemanticBackfillStatus | null;
  titlePoolSummary: TitlePoolSummary | null;
  events: Array<Record<string, unknown>>;
  notify: NotifyFn;
  refreshAll: () => Promise<void>;
  activeCustomerId: number | null;
  setTitlePoolSummary: (v: TitlePoolSummary | null) => void;
}

export function DataCenterPage({
  setTab,
  report,
  opsReport,
  assets,
  reachQuota,
  semanticStatus,
  titlePoolSummary,
  events,
  notify,
  refreshAll,
  activeCustomerId,
  setTitlePoolSummary,
}: DataCenterPageProps) {
  const hasCore =
    report != null ||
    opsReport != null ||
    assets.length > 0 ||
    events.length > 0 ||
    semanticStatus != null ||
    titlePoolSummary != null;

  const failureRate =
    opsReport?.failure_rate != null
      ? `${(opsReport.failure_rate * 100).toFixed(1)}%`
      : report
        ? `${(report.failure_rate * 100).toFixed(1)}%`
        : null;

  const quotaText = opsReport?.quota
    ? `${String(opsReport.quota.used_total ?? opsReport.quota.used_today ?? 0)}/${String(opsReport.quota.daily_quota ?? "—")}`
    : reachQuota
      ? `${String(reachQuota.used_total ?? reachQuota.used_today ?? 0)}/${String(reachQuota.daily_quota ?? "—")}`
      : null;

  return (
    <section>
      <PageHeader
        title="数据中心"
        blurb="产能、质量与发布统计"
        actions={
          <button type="button" onClick={() => void refreshAll()}>
            刷新
          </button>
        }
      />

      {!hasCore ? (
        <EmptyState
          title="暂无统计数据"
          body="引擎连接后将显示素材、成片与事件摘要；不会展示虚构数字。"
          actionLabel="去总览"
          onAction={() => setTab("overview")}
        />
      ) : (
        <>
          <div className="stat-grid">
            <StatCard label="素材" value={report?.assets ?? assets.length} />
            <StatCard
              label="Cliplet"
              value={report ? `${report.cliplets_indexed}/${report.cliplets}` : "—"}
            />
            <StatCard
              label="待发 Ready"
              value={opsReport?.pending_ready ?? opsReport?.ready ?? report?.ready ?? "—"}
            />
            <StatCard
              label="已发归档"
              value={opsReport?.published_count ?? opsReport?.published_ready ?? "—"}
            />
            <StatCard label="失败率" value={failureRate ?? "—"} warn={Boolean(failureRate && failureRate !== "0.0%")} />
            <StatCard label="触达配额" value={quotaText ?? "—"} />
          </div>

          <h3 className="section-title">语义进度</h3>
          {semanticStatus ? (
            <SemanticAnalysisProgress status={semanticStatus} compact />
          ) : (
            <EmptyState title="语义进度未知" body="尚未拉取 captions/status。" />
          )}

          <h3 className="section-title">词库摘要</h3>
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
              {(titlePoolSummary.title_pool_sample || []).length > 0 ? (
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
              ) : (
                <p className="hint">暂无样例标题</p>
              )}
            </div>
          ) : (
            <EmptyState title="词库摘要未加载" body="可在设置页重载词池后再查看。" />
          )}

          <h3 className="section-title">最近事件</h3>
          <div className="actions" style={{ marginBottom: 8 }}>
            <button type="button" onClick={() => void api.exportEvents()}>
              导出事件 CSV
            </button>
            <button type="button" onClick={() => void api.exportRenders()}>
              导出成片 CSV
            </button>
          </div>
          {events.length === 0 ? (
            <EmptyState title="暂无事件" body="生产与审片操作产生的事件会显示在此。" />
          ) : (
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
                {events.slice(0, 20).map((e) => (
                  <tr key={String(e.id)}>
                    <td>{String(e.created_at)}</td>
                    <td>{String(e.job_id)}</td>
                    <td>{String(e.level)}</td>
                    <td>{String(e.message)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <StepFooter current="data" onJump={setTab} />
    </section>
  );
}
