import type { SemanticBackfillStatus } from "./types";

type Props = {
  status: SemanticBackfillStatus | null;
  busy?: boolean;
  compact?: boolean;
  onRunBatch?: (limit: number) => void;
};

function pct(status: SemanticBackfillStatus): number {
  const total = Math.max(status.eligible, status.processed, 1);
  return Math.min(100, Math.round((status.processed / total) * 100));
}

export function SemanticAnalysisProgress({ status, busy, compact, onRunBatch }: Props) {
  if (!status) {
    return (
      <div className={`semantic-progress ${compact ? "semantic-progress--compact" : ""}`}>
        <p className="hint">语义分析进度加载中…</p>
      </div>
    );
  }

  const active = status.remaining > 0 || status.claimed > 0 || Boolean(busy);
  const done = status.remaining <= 0 && status.claimed <= 0 && status.eligible > 0;
  const percent = pct(status);
  const rejectRate =
    status.processed > 0 ? Math.round((status.rejected / status.processed) * 100) : 0;

  if (compact) {
    return (
      <div className="semantic-progress semantic-progress--compact" aria-live="polite">
        <div className="semantic-progress-head">
          <span className={`health-dot ${active ? "pulse" : done ? "ok" : "warn"}`} />
          <span className="semantic-progress-title">
            语义分析 {active ? "进行中" : done ? "已完成" : "待跑"}
          </span>
          <span className="semantic-progress-pct">{percent}%</span>
        </div>
        <div className="semantic-progress-bar" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
          <div
            className={`semantic-progress-fill${active ? " is-active" : ""}`}
            style={{ width: `${percent}%` }}
          />
        </div>
        <p className="semantic-progress-meta">
          通过 {status.passed} · 拒绝 {status.rejected}
          {status.remaining > 0 ? ` · 待处理 ${status.remaining}` : ""}
          {status.claimed > 0 ? ` · 处理中 ${status.claimed}` : ""}
        </p>
      </div>
    );
  }

  return (
    <section className="semantic-progress panel" aria-live="polite">
      <div className="panel-head">
        <h3 className="section-title" style={{ margin: 0 }}>
          语义分析进度
        </h3>
        <span className={`semantic-badge${active ? " semantic-badge--active" : ""}`}>
          {active ? "分析中" : done ? "已完成" : status.eligible > 0 ? "可继续" : "无待分析"}
        </span>
      </div>
      <div
        className="semantic-progress-bar semantic-progress-bar--lg"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`semantic-progress-fill${active ? " is-active" : ""}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="semantic-progress-stats">
        <span>
          <strong>{percent}%</strong> 已处理
        </span>
        <span>合规 {status.passed}</span>
        <span>拒绝 {status.rejected}</span>
        <span>待处理 {status.remaining}</span>
        {status.claimed > 0 ? <span>占用 {status.claimed}</span> : null}
        {status.processed > 0 ? <span>拒绝率 {rejectRate}%</span> : null}
        {status.last_cursor != null ? <span>游标 #{status.last_cursor}</span> : null}
      </div>
      {onRunBatch ? (
        <div className="actions" style={{ marginTop: 12 }}>
          <button
            type="button"
            className="primary"
            disabled={Boolean(busy) || active}
            onClick={() => onRunBatch(10)}
          >
            {busy ? "提交中…" : active ? "分析进行中…" : "运行一批（10 条）"}
          </button>
          <button type="button" disabled={Boolean(busy) || active} onClick={() => onRunBatch(20)}>
            20 条
          </button>
          <span className="hint">9B 快筛 → 27B 升级；严格 semantic v1 门禁</span>
        </div>
      ) : null}
    </section>
  );
}
