import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { formatBeijingTime } from "../beijingTime";
import { formatDisplayNo, outputDisplayLabel } from "../displayId";
import { openMediaTarget } from "../openMediaTarget";
import { POLL_BUDGET_MS } from "../pollBudget";
import { EmptyState, PageHeader, PageSection, SegmentNav } from "../shell/PageChrome";
import type { Tab } from "../types";

const LEVEL_ZH: Record<string, string> = {
  info: "信息",
  warning: "警告",
  error: "错误",
};
const CATEGORY_ZH: Record<string, string> = {
  production: "生产",
  review: "审片",
  quality: "质检门禁",
  semantic: "语义",
  vector: "向量",
  publish: "发布",
  message: "消息",
  notification: "消息",
  system: "系统",
  backup: "备份",
  sync: "同步",
  update: "更新",
  cleanup: "清理",
  legacy: "旧数据",
};

const CATEGORIES = [
  ["all", "全部"],
  ["production", "生产"],
  ["review", "审片"],
  ["quality", "质检门禁"],
  ["semantic", "语义"],
  ["vector", "向量"],
  ["publish", "发布"],
  ["message", "消息"],
  ["system", "系统"],
  ["backup", "备份"],
  ["sync", "同步"],
  ["update", "更新"],
  ["cleanup", "清理"],
  ["error", "错误"],
] as const;

type LogRow = Record<string, unknown>;

export type LogNavigateTarget = {
  tab: Tab;
  outputId?: number;
  jobId?: number;
  guideTarget?: string;
};

function rowPath(row: LogRow): string {
  const payload = row.payload as Record<string, unknown> | undefined;
  for (const key of ["output_path", "path", "video_path", "file_path"]) {
    const v = payload?.[key] ?? row[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

function rowJobId(row: LogRow): number | null {
  const details = row.details as Record<string, unknown> | undefined;
  for (const raw of [row.job_id, details?.job_id, row.source_type === "job" ? row.source_id : null]) {
    const n = Number(raw);
    if (Number.isFinite(n) && n > 0) return Math.trunc(n);
  }
  const corr = String(row.correlation_id || "");
  if (corr.startsWith("job:")) {
    const n = Number(corr.slice(4));
    if (Number.isFinite(n) && n > 0) return Math.trunc(n);
  }
  return null;
}

function rowOutputId(row: LogRow): number | null {
  const details = row.details as Record<string, unknown> | undefined;
  for (const raw of [row.output_id, details?.output_id]) {
    const n = Number(raw);
    if (Number.isFinite(n) && n > 0) return Math.trunc(n);
  }
  if (row.source_type === "render_output") {
    const n = Number(row.source_id);
    if (Number.isFinite(n) && n > 0) return Math.trunc(n);
  }
  return null;
}

function rowOutputLabel(row: LogRow): string {
  return outputDisplayLabel({
    display_label:
      row.display_label ??
      (row.details as Record<string, unknown> | undefined)?.display_label,
    display_no:
      row.display_no ??
      (row.details as Record<string, unknown> | undefined)?.display_no ??
      (row.payload as { display_no?: unknown } | undefined)?.display_no,
  });
}

/** 任务编号 + 成片编号（可展示用）。 */
function rowTaskAndOutputLabel(row: LogRow): {
  text: string;
  outputLabel: string;
  jobId: number | null;
} {
  const jobId = rowJobId(row);
  const outputLabel = rowOutputLabel(row);
  const display = outputLabel || formatDisplayNo(row.display_no) || "";
  if (jobId != null && display) {
    return { text: `任务 #${jobId} · ${display}`, outputLabel: display, jobId };
  }
  if (jobId != null) {
    return { text: `任务 #${jobId}`, outputLabel: "", jobId };
  }
  if (display) {
    return { text: display, outputLabel: display, jobId: null };
  }
  return { text: "—", outputLabel: "", jobId: null };
}

function rowAccount(row: LogRow): string {
  const details = row.details as Record<string, unknown> | undefined;
  return String(
    row.account || details?.account || details?.chrome_profile || "—",
  );
}

function rowPlatform(row: LogRow): string {
  const details = row.details as Record<string, unknown> | undefined;
  return String(row.platform || details?.platform || "—");
}

export function LogsPage({
  embedded = false,
  onNavigate,
}: {
  embedded?: boolean;
  /** Jump to review/produce for a linked job or output. */
  onNavigate?: (target: LogNavigateTarget) => void;
} = {}) {
  const [category, setCategory] = useState("all");
  const [level, setLevel] = useState("all");
  const [search, setSearch] = useState("");
  const [searchDebounced, setSearchDebounced] = useState("");
  const [rows, setRows] = useState<LogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<LogRow | null>(null);
  const [busy, setBusy] = useState(false);
  const [active, setActive] = useState(true);

  useEffect(() => {
    const t = window.setTimeout(() => setSearchDebounced(search.trim()), 350);
    return () => window.clearTimeout(t);
  }, [search]);

  useEffect(() => {
    const onVis = () => setActive(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    onVis();
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const errorOnly = category === "error";
      const result = await api.operationLogs({
        category: errorOnly ? undefined : category === "all" ? undefined : category,
        level: errorOnly ? "error" : level === "all" ? undefined : level,
        search: searchDebounced || undefined,
        page: 1,
        page_size: 100,
      });
      const items = [...(result.items || [])];
      const legacy = [...(result.legacy_items || [])];
      setRows(
        [...items, ...legacy].sort(
          (a, b) =>
            Date.parse(String(b.created_at || "")) -
            Date.parse(String(a.created_at || "")),
        ),
      );
      // API total is structured rows only; show both counts without double-counting.
      setTotal(Number(result.total || items.length) + legacy.length);
    } finally {
      setBusy(false);
    }
  }, [category, level, searchDebounced]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => void refresh(), POLL_BUDGET_MS.logs);
    return () => window.clearInterval(id);
  }, [active, refresh]);

  async function download(format: "json" | "csv") {
    const errorOnly = category === "error";
    const all: LogRow[] = [];
    let page = 1;
    let structuredTotal = 0;
    do {
      const result = await api.operationLogs({
        category: errorOnly ? undefined : category === "all" ? undefined : category,
        level: errorOnly ? "error" : level === "all" ? undefined : level,
        search: searchDebounced || undefined,
        page,
        page_size: 200,
      });
      const items = result.items || [];
      structuredTotal = Number(result.total || 0);
      all.push(...items);
      if (page === 1) all.push(...(result.legacy_items || []));
      if (!items.length || all.length >= structuredTotal + (result.legacy_items?.length || 0)) break;
      page += 1;
    } while (page <= 100);
    const allExportRows = all
      .sort(
        (a, b) =>
          Date.parse(String(b.created_at || "")) -
          Date.parse(String(a.created_at || "")),
      )
      .map((row) => {
        const ref = rowTaskAndOutputLabel(row);
        return {
          created_at: row.created_at,
          level: row.level,
          category: row.category,
          event: row.event,
          message: row.message,
          job_id: ref.jobId,
          display: ref.text,
          account: rowAccount(row),
          platform: rowPlatform(row),
          path: rowPath(row),
          details: row.details,
          evidence: row.evidence,
          旧数据: Boolean(row.legacy),
        };
      });
    const content =
      format === "json"
        ? JSON.stringify(allExportRows, null, 2)
        : [
            Object.keys(allExportRows[0] || {}).join(","),
            ...allExportRows.map((row) =>
              Object.values(row)
                .map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`)
                .join(","),
            ),
          ].join("\n");
    const blob = new Blob([content], {
      type: format === "json" ? "application/json" : "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `suying-operation-logs.${format}`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function openPath(path: string) {
    if (!path) return;
    try {
      await openMediaTarget(path);
    } catch {
      /* ignore */
    }
  }

  function jumpToRef(row: LogRow) {
    const jobId = rowJobId(row);
    const outputId = rowOutputId(row);
    const path = rowPath(row);
    if (onNavigate && (outputId || jobId)) {
      if (outputId) {
        onNavigate({
          tab: "review",
          outputId,
          jobId: jobId ?? undefined,
          guideTarget: `review-output-${outputId}`,
        });
        return;
      }
      if (jobId) {
        onNavigate({
          tab: "produce",
          jobId,
          guideTarget: `job-row-${jobId}`,
        });
        return;
      }
    }
    if (path) void openPath(path);
  }

  function renderRefCell(row: LogRow) {
    const ref = rowTaskAndOutputLabel(row);
    const path = rowPath(row);
    const outputId = rowOutputId(row);
    const canJump = Boolean(onNavigate && (outputId || ref.jobId)) || Boolean(path);
    if (!canJump || ref.text === "—") {
      return <span className="hint">{ref.text}</span>;
    }
    return (
      <button
        type="button"
        className="linkish"
        title={path || (outputId ? "查看成片" : "查看任务")}
        onClick={(e) => {
          e.stopPropagation();
          jumpToRef(row);
        }}
      >
        {ref.text}
      </button>
    );
  }

  return (
    <section className="page-stack logs-page">
      {!embedded ? (
        <PageHeader title="日志" blurb="统一查看生产、质检、发布、消息和系统事件（可见时自动刷新）" />
      ) : (
        <div>
          <h3 className="section-title">运行日志</h3>
          <p className="hint">查看生产、质量、发布、消息、备份和系统恢复记录。</p>
        </div>
      )}
      <SegmentNav
        ariaLabel="日志分类"
        value={category}
        onChange={setCategory}
        items={CATEGORIES.map(([id, label]) => ({ id, label }))}
      />
      <PageSection
        id="operation-logs"
        title="结构化操作日志"
        description="按当前客户隔离。自动过审写入「质检门禁」；旧数据为兼容历史任务事件。生产事件显示「任务 #… · 成片编号」，可点跳转。"
        status={busy ? "加载中…" : `${total} 条`}
      >
        <div className="actions-inline">
          <select
            value={level}
            onChange={(event) => setLevel(event.target.value)}
            disabled={category === "error"}
          >
            <option value="all">全部级别</option>
            <option value="info">信息</option>
            <option value="warning">警告</option>
            <option value="error">错误</option>
          </select>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索账号、平台、成片编号"
          />
          <button type="button" onClick={() => void refresh()}>
            刷新
          </button>
          <button type="button" onClick={() => void download("json")}>
            导出数据文件
          </button>
          <button type="button" onClick={() => void download("csv")}>
            导出表格
          </button>
        </div>
        {rows.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>分类 / 级别</th>
                  <th>事件</th>
                  <th>摘要</th>
                  <th>任务 / 成片</th>
                  <th>账号</th>
                  <th>平台</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  return (
                    <tr
                      key={String(row.id)}
                      onClick={() => setSelected(row)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>{formatBeijingTime(row.created_at ? String(row.created_at) : null)}</td>
                      <td>
                        {CATEGORY_ZH[String(row.category || "legacy")] ||
                          String(row.category || "旧数据")}
                        {" · "}
                        {LEVEL_ZH[String(row.level || "info")] || String(row.level || "信息")}
                      </td>
                      <td>
                        {String(row.event || "事件")}
                        {row.legacy ? " · 旧数据" : ""}
                      </td>
                      <td>{String(row.message || "")}</td>
                      <td>{renderRefCell(row)}</td>
                      <td>{rowAccount(row)}</td>
                      <td>{rowPlatform(row)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="当前筛选下暂无日志" />
        )}
      </PageSection>
      {selected ? (
        <aside className="logs-detail-drawer" aria-label="日志详情">
          <div className="logs-detail-head">
            <strong>日志详情</strong>
            <button type="button" onClick={() => setSelected(null)}>
              关闭
            </button>
          </div>
          {rowPath(selected) || rowOutputId(selected) || rowJobId(selected) ? (
            <button type="button" onClick={() => jumpToRef(selected)}>
              {rowOutputId(selected) || rowPath(selected) ? "打开关联成片" : "跳转到任务"}
            </button>
          ) : null}
          {rowPath(selected) ? (
            <button type="button" onClick={() => void openPath(rowPath(selected))}>
              打开文件位置
            </button>
          ) : null}
          <dl className="logs-detail-list">
            <dt>时间</dt>
            <dd>{formatBeijingTime(selected.created_at ? String(selected.created_at) : null)}</dd>
            <dt>分类</dt>
            <dd>
              {CATEGORY_ZH[String(selected.category || "legacy")] ||
                String(selected.category || "旧数据")}
            </dd>
            <dt>级别</dt>
            <dd>{LEVEL_ZH[String(selected.level || "info")] || String(selected.level || "信息")}</dd>
            <dt>事件</dt>
            <dd>{String(selected.event || "事件")}</dd>
            <dt>摘要</dt>
            <dd>{String(selected.message || "")}</dd>
            <dt>任务 / 成片</dt>
            <dd>{renderRefCell(selected)}</dd>
            <dt>账号 / 平台</dt>
            <dd>
              {String(selected.account || "—")} / {String(selected.platform || "—")}
            </dd>
            <dt>处理详情</dt>
            <dd>
              <pre className="dry-raw">
                {JSON.stringify(selected.details || {}, null, 2)}
              </pre>
            </dd>
            <dt>核验依据</dt>
            <dd>
              <pre className="dry-raw">
                {JSON.stringify(selected.evidence || {}, null, 2)}
              </pre>
            </dd>
          </dl>
        </aside>
      ) : null}
    </section>
  );
}
