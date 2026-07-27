import { api } from "../api";
import { BrandLogoPanel, type BrandLogoState } from "../BrandLogoPanel";
import { SemanticAnalysisProgress } from "../SemanticAnalysisProgress";
import { briefResult, dryRunSummary } from "../shell/briefResult";
import { PageHeader, SegmentNav, StepFooter } from "../shell/PageChrome";
import type { ProduceWorkspace, SemanticBackfillStatus, Tab } from "../types";
import type { AskConfirmFn, NotifyFn } from "./pageTypes";

export interface ProductionPageProps {
  workspace: ProduceWorkspace;
  onWorkspaceChange: (w: ProduceWorkspace) => void;
  setTab: (t: Tab) => void;
  customerName: string;
  brandLogo: BrandLogoState;
  brandBusy: boolean;
  saveBrandLogo: (patch: Partial<BrandLogoState>) => Promise<void>;
  notify: NotifyFn;
  theme: string;
  setTheme: (v: string) => void;
  category: string;
  setCategory: (v: string) => void;
  targetCount: number;
  setTargetCount: (v: number) => void;
  actionBusy: string | null;
  setActionBusy: (v: string | null) => void;
  runDryRun: () => void;
  createJob: () => void;
  dryResult: Record<string, unknown> | null;
  jobs: Array<Record<string, unknown>>;
  refreshAll: () => Promise<void>;
  calDay: string;
  setCalDay: (v: string) => void;
  calTheme: string;
  setCalTheme: (v: string) => void;
  calQuota: number;
  setCalQuota: (v: number) => void;
  calNote: string;
  setCalNote: (v: string) => void;
  saveCalendarDay: () => void;
  askConfirm: AskConfirmFn;
  calendar: Array<Record<string, unknown>>;
  semanticStatus: SemanticBackfillStatus | null;
  runSemanticBatch: (limit: number) => Promise<void> | void;
  assets: Array<Record<string, unknown>>;
  vectorization: boolean;
}

export function ProductionPage({
  workspace,
  onWorkspaceChange,
  setTab,
  customerName,
  brandLogo,
  brandBusy,
  saveBrandLogo,
  notify,
  theme,
  setTheme,
  category,
  setCategory,
  targetCount,
  setTargetCount,
  actionBusy,
  setActionBusy,
  runDryRun,
  createJob,
  dryResult,
  jobs,
  refreshAll,
  calDay,
  setCalDay,
  calTheme,
  setCalTheme,
  calQuota,
  setCalQuota,
  calNote,
  setCalNote,
  saveCalendarDay,
  askConfirm,
  calendar,
  semanticStatus,
  runSemanticBatch,
  assets,
  vectorization,
}: ProductionPageProps) {
  return (
    <section>
      <PageHeader title="生产" blurb="素材、任务与内容日历" />
      <SegmentNav
        ariaLabel="生产分区"
        value={workspace}
        onChange={onWorkspaceChange}
        items={[
          { id: "tasks", label: "任务" },
          { id: "assets", label: "素材库", badge: assets.length },
        ]}
      />

      {workspace === "tasks" ? (
        <>
          <BrandLogoPanel
            customerName={customerName}
            state={brandLogo}
            busy={brandBusy}
            onChange={(patch) => saveBrandLogo(patch).catch((e: unknown) => notify(String(e), "err"))}
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
          {dryResult &&
            (() => {
              const s = dryRunSummary(dryResult);
              return (
                <div className="dry-card">
                  <div className="dry-card-head">
                    <strong>Dry-run 结果</strong>
                    <span className="dry-count">{s.count} 条候选</span>
                  </div>
                  <p className="hint">
                    主题 {s.theme} · 分类 {s.category}
                  </p>
                  {s.samples.length > 0 ? (
                    <ul className="dry-samples">
                      {s.samples.map((t, i) => (
                        <li key={`${i}-${t}`}>{t}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="hint">无候选样本</p>
                  )}
                  <details>
                    <summary>原始 JSON</summary>
                    <pre className="dry-raw">{JSON.stringify(dryResult, null, 2)}</pre>
                  </details>
                </div>
              );
            })()}
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
                          .catch((e: unknown) => notify(String(e), "err"))
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
                          .catch((e: unknown) => notify(String(e), "err"))
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
              onClick={() => {
                void (async () => {
                  const ok = await askConfirm({
                    title: "删除日历日",
                    body: `删除 ${calDay} 的日历计划？此操作不可撤销。`,
                    confirmLabel: "删除",
                    danger: true,
                  });
                  if (!ok) return;
                  try {
                    await api.deleteCalendar(calDay);
                    notify(`已删除日历日：${calDay}`, "ok");
                    await refreshAll();
                  } catch (e) {
                    notify(String(e), "err");
                  }
                })();
              }}
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
        </>
      ) : (
        <>
          <SemanticAnalysisProgress
            status={semanticStatus}
            busy={actionBusy === "captions"}
            onRunBatch={(limit) => void runSemanticBatch(limit)}
          />
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
                    notify(briefResult("全量扫描已触发", r), "ok");
                    return refreshAll();
                  })
                  .catch((e: unknown) => notify(String(e), "err"))
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
                  .then((r) => notify(briefResult("扫描状态", r), "info"))
                  .catch((e: unknown) => notify(String(e), "err"))
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
                  .catch((e: unknown) => notify(String(e), "err"))
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
        </>
      )}

      <StepFooter current="produce" onJump={setTab} />
    </section>
  );
}
