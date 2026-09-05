import { useEffect, useState } from "react";
import { api } from "./api";
import { POLL_BUDGET_MS } from "./pollBudget";
import type { NotifyFn } from "./pages/pageTypes";
import type { VectorizationStatus } from "./types";

type OrientationLockStatus = {
  lock: string;
  lock_version: number;
  hard: boolean;
  integrity_ok: boolean;
  ready_total: number;
  ready_audited_ok: number;
  ready_unverified: number;
  rejected_orientation: number;
  ready_by_orientation: Record<string, number>;
  message: string;
};

function statusChipLabel(status: VectorizationStatus | null): { text: string; kind: string } {
  if (!status) return { text: "加载中", kind: "mute" };
  const run = status.status;
  const active =
    Boolean(status.run_id) && !["COMPLETED", "FAILED", "IDLE"].includes(run);
  if (status.schedule_enabled && !status.in_window && !status.enabled) {
    return { text: "定时窗外", kind: "mute" };
  }
  if (active && run === "RUNNING") return { text: "运行中", kind: "ok" };
  if (active && (run === "PAUSED" || run === "PAUSE_REQUESTED")) {
    return { text: "已暂停", kind: "warn" };
  }
  if (active && run === "BLOCKED") return { text: "已阻塞", kind: "warn" };
  if (run === "FAILED") return { text: "本轮失败", kind: "err" };
  if (status.enabled) return { text: "增量已开", kind: "ok" };
  if (
    status.auto_stop_when_done !== false &&
    (status.gaps?.total_pending ?? 0) === 0 &&
    !status.enabled
  ) {
    return { text: "已完成自动关", kind: "mute" };
  }
  return { text: "已关闭", kind: "mute" };
}

/**
 * 素材向量分析 + 定时窗口 + L16 横竖屏硬审核。
 * 横/竖分别排队；判断以含旋转的显示尺寸为准，不可关闭硬门禁。
 */
export function VectorControl({ notify }: { notify: NotifyFn }) {
  const [orientation, setOrientation] = useState<"portrait" | "landscape">("portrait");
  const [status, setStatus] = useState<VectorizationStatus | null>(null);
  const [orientLock, setOrientLock] = useState<OrientationLockStatus | null>(null);
  const [mode, setMode] = useState<"count" | "all">("count");
  const [count, setCount] = useState(50);
  const [busy, setBusy] = useState(false);
  const [switchBusy, setSwitchBusy] = useState(false);
  const [schedBusy, setSchedBusy] = useState(false);
  const [schedStart, setSchedStart] = useState("22:00");
  const [schedEnd, setSchedEnd] = useState("07:00");

  async function refresh() {
    const [a, lock] = await Promise.all([
      api.getVectorizationStatus(orientation),
      api.getOrientationLock().catch(() => null),
    ]);
    setStatus(a);
    if (a.schedule_start) setSchedStart(a.schedule_start.slice(0, 5));
    if (a.schedule_end) setSchedEnd(a.schedule_end.slice(0, 5));
    if (lock) setOrientLock(lock as OrientationLockStatus);
  }

  async function toggleIncremental(on: boolean) {
    setSwitchBusy(true);
    try {
      if (on) {
        const r = await api.enableVectorization(true, 50);
        notify(r.message || "已开启增量向量化", "ok");
      } else {
        await api.disableVectorization();
        notify("已关闭增量向量化（执行器停止领取新任务）", "ok");
      }
      await refresh();
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setSwitchBusy(false);
    }
  }

  async function saveSchedule(patch: {
    schedule_enabled?: boolean;
    schedule_start?: string;
    schedule_end?: string;
    auto_stop_when_done?: boolean;
  }) {
    setSchedBusy(true);
    try {
      const next = await api.updateVectorizationSchedule(patch);
      setStatus(next);
      if (next.schedule_start) setSchedStart(next.schedule_start.slice(0, 5));
      if (next.schedule_end) setSchedEnd(next.schedule_end.slice(0, 5));
      notify(next.message || "向量策略已保存", "ok");
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setSchedBusy(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      void refresh()
        .catch(() => undefined)
        .finally(() => {
          if (cancelled) return;
        });
    };
    tick();
    const busyRun = Boolean(
      status?.run_id && !["COMPLETED", "FAILED", "IDLE"].includes(status.status),
    );
    const interval = window.setInterval(
      tick,
      busyRun ? POLL_BUDGET_MS.vectorBusy : POLL_BUDGET_MS.semanticIdle,
    );
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orientation, status?.status, status?.run_id]);

  async function run(action: "start" | "pause" | "resume") {
    setBusy(true);
    try {
      const next =
        action === "start"
          ? await api.createVectorizationRun({
              mode,
              count: mode === "count" ? count : undefined,
              orientation,
            })
          : action === "pause"
            ? await api.pauseVectorization()
            : await api.resumeVectorization();
      setStatus(next);
      notify(
        action === "start"
          ? mode === "all"
            ? `${orientation === "portrait" ? "竖屏" : "横屏"}素材：已排队完成全部待处理`
            : `${orientation === "portrait" ? "竖屏" : "横屏"}素材分析已开始（${count} 条）`
          : action === "pause"
            ? "素材分析已暂停"
            : "素材分析已继续",
        "ok",
      );
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  const gaps = status?.gaps;
  const portraitDone = gaps?.portrait_completed ?? (orientation === "portrait" ? status?.completed_assets : 0) ?? 0;
  const portraitPending = gaps?.portrait_pending ?? (orientation === "portrait" ? status?.pending_assets : 0) ?? 0;
  const landscapeDone = gaps?.landscape_completed ?? (orientation === "landscape" ? status?.completed_assets : 0) ?? 0;
  const landscapePending = gaps?.landscape_pending ?? (orientation === "landscape" ? status?.pending_assets : 0) ?? 0;
  const completed = status?.completed_assets ?? 0;
  const pending = status?.pending_assets ?? 0;
  const total = status?.total_assets ?? completed + pending;
  const runDone = status?.run_completed_assets ?? 0;
  const frozen = status?.frozen_assets ?? 0;
  const clipDone = status?.completed_cliplets ?? 0;
  const clipPending = status?.pending_cliplets ?? 0;
  const activeRun =
    Boolean(status?.run_id) && !["COMPLETED", "FAILED", "IDLE"].includes(status?.status || "IDLE");

  const enabled = Boolean(status?.enabled);
  const scheduleOn = Boolean(status?.schedule_enabled);
  const autoStop = status?.auto_stop_when_done !== false;
  const chip = statusChipLabel(status);
  const rejectedOrient = orientLock?.rejected_orientation ?? 0;
  const readyPortrait = orientLock?.ready_by_orientation?.portrait ?? 0;
  const readyLandscape = orientLock?.ready_by_orientation?.landscape ?? 0;
  const lockHard = orientLock?.hard !== false;
  const anyBusy = busy || switchBusy || schedBusy;
  const portraitTotal = portraitDone + portraitPending;
  const landscapeTotal = landscapeDone + landscapePending;

  return (
    <section className="panel-block vector-control" data-guide="ops-vectorization">
      <div className="panel-head">
        <div>
          <h3 style={{ margin: 0 }}>素材向量分析</h3>
          <p className="hint" style={{ marginTop: 4, marginBottom: 0 }}>
            增量补齐 · 横竖分别排队 · 定时窗口边沿启停 · 全部完成可自动结束
          </p>
        </div>
        <div className="actions-inline" style={{ alignItems: "center" }}>
          <span className={`status-chip vector-status-chip is-${chip.kind}`} data-guide="ops-vector-chip">
            {chip.text}
          </span>
          <button type="button" disabled={anyBusy} onClick={() => void refresh()}>
            刷新
          </button>
        </div>
      </div>

      <div className="grid3 vector-summary" data-guide="ops-vector-counts">
        <div className="vector-stat">
          <span className="hint">竖屏</span>
          <strong>
            {portraitDone}/{portraitTotal || 0}
          </strong>
          <span className="hint">待处理 {portraitPending}</span>
          <progress
            value={portraitTotal > 0 ? portraitDone : 0}
            max={Math.max(1, portraitTotal || 1)}
          />
        </div>
        <div className="vector-stat">
          <span className="hint">横屏</span>
          <strong>
            {landscapeDone}/{landscapeTotal || 0}
          </strong>
          <span className="hint">待处理 {landscapePending}</span>
          <progress
            value={landscapeTotal > 0 ? landscapeDone : 0}
            max={Math.max(1, landscapeTotal || 1)}
          />
        </div>
        <div className="vector-stat">
          <span className="hint">当前画幅本轮</span>
          <strong>
            {activeRun ? `${runDone}/${Math.max(1, frozen)}` : status?.status || "IDLE"}
          </strong>
          <span className="hint">
            {clipDone + clipPending > 0
              ? `片段 ${clipDone}/${clipDone + clipPending}`
              : orientation === "portrait"
                ? "竖屏队列"
                : "横屏队列"}
          </span>
          <progress
            value={activeRun && frozen > 0 ? runDone : total > 0 ? completed : 0}
            max={activeRun && frozen > 0 ? Math.max(1, frozen) : Math.max(1, total || 1)}
          />
        </div>
      </div>

      <div className="vector-section" data-guide="ops-vector-switch">
        <h4 className="vector-section-title">运行策略</h4>
        <div className="actions-inline" style={{ flexWrap: "wrap" }}>
          <button
            type="button"
            className={enabled ? "primary" : undefined}
            disabled={anyBusy}
            onClick={() => void toggleIncremental(!enabled)}
            title="开启后调度器唤醒执行器，仅补齐无向量的素材与片段"
          >
            {switchBusy ? "切换中…" : enabled ? "增量：已开启" : "增量：已关闭"}
          </button>
          <button
            type="button"
            className={autoStop ? "primary" : undefined}
            disabled={anyBusy}
            onClick={() => void saveSchedule({ auto_stop_when_done: !autoStop })}
            title="横竖屏缺口均为 0 时自动关闭增量向量化"
          >
            全部完成自动结束：{autoStop ? "开" : "关"}
          </button>
        </div>
        <p className="hint" style={{ marginTop: 6, marginBottom: 0 }}>
          增量只补齐缺口，不重算已有向量。
          {enabled ? " · 约 30s 自动起批" : " · 关闭后不再自动补齐"}
          {scheduleOn
            ? status?.in_window
              ? " · 当前在定时窗内"
              : " · 当前在定时窗外（进入窗口瞬间才会自动开）"
            : ""}
        </p>
      </div>

      <div className="vector-section" data-guide="ops-vector-schedule">
        <h4 className="vector-section-title">定时窗口（上海时区）</h4>
        <div className="actions-inline" style={{ flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            className={scheduleOn ? "primary" : undefined}
            disabled={anyBusy}
            onClick={() => void saveSchedule({ schedule_enabled: !scheduleOn })}
          >
            定时：{scheduleOn ? "已启用" : "已关闭"}
          </button>
          <label className="field-inline">
            开始
            <input
              type="time"
              value={schedStart}
              disabled={anyBusy}
              onChange={(e) => setSchedStart(e.target.value)}
            />
          </label>
          <label className="field-inline">
            结束
            <input
              type="time"
              value={schedEnd}
              disabled={anyBusy}
              onChange={(e) => setSchedEnd(e.target.value)}
            />
          </label>
          <button
            type="button"
            disabled={anyBusy}
            onClick={() =>
              void saveSchedule({
                schedule_start: schedStart,
                schedule_end: schedEnd,
              })
            }
          >
            保存时段
          </button>
        </div>
        <p className="hint" style={{ marginTop: 6, marginBottom: 0 }}>
          {scheduleOn
            ? `${status?.in_window ? "窗内" : "窗外"} · ${status?.next_edge_hint || "边沿启停"} · 支持跨日（如 22:00–07:00）`
            : "开启后，在开始时刻边沿打开、结束时刻边沿暂停并关闭；窗内不会反复强开。"}
        </p>
      </div>

      <div className="vector-section" data-guide="ops-vector-manual">
        <h4 className="vector-section-title">手动队列</h4>
        <div className="actions-inline" style={{ marginBottom: 8 }}>
          <button
            type="button"
            className={orientation === "portrait" ? "primary" : undefined}
            onClick={() => setOrientation("portrait")}
          >
            竖屏
          </button>
          <button
            type="button"
            className={orientation === "landscape" ? "primary" : undefined}
            onClick={() => setOrientation("landscape")}
          >
            横屏
          </button>
          <span className="hint">
            当前 {orientation === "portrait" ? "竖屏" : "横屏"} 已完成 {completed}/{total || 0} · 待
            {pending}
          </span>
        </div>
        <div className="actions-inline" style={{ flexWrap: "wrap" }}>
          <label className="field-inline">
            本次处理
            <select
              value={mode}
              onChange={(event) => setMode(event.target.value === "all" ? "all" : "count")}
              style={{ minWidth: 110 }}
            >
              <option value="count">指定数量</option>
              <option value="all">完成全部</option>
            </select>
          </label>
          {mode === "count" ? (
            <label className="field-inline">
              条数
              <input
                type="number"
                min={1}
                max={100000}
                value={count}
                onChange={(event) => setCount(Math.max(1, Number(event.target.value) || 1))}
                style={{ width: 90 }}
              />
            </label>
          ) : (
            <span className="hint">将排队当前画幅全部待处理（到开始时快照）</span>
          )}
          <button
            type="button"
            className="primary"
            disabled={
              anyBusy || Boolean(status?.run_id && !["COMPLETED", "FAILED"].includes(status.status))
            }
            onClick={() => void run("start")}
          >
            开始分析
          </button>
          <button
            type="button"
            disabled={
              anyBusy || !["IDLE", "RUNNING", "PAUSE_REQUESTED"].includes(status?.status || "")
            }
            onClick={() => void run("pause")}
          >
            暂停
          </button>
          <button
            type="button"
            disabled={anyBusy || !["PAUSED", "BLOCKED"].includes(status?.status || "")}
            onClick={() => void run("resume")}
          >
            继续
          </button>
        </div>
      </div>

      <div
        className="banner vector-orientation-banner"
        data-guide="ops-orientation-hard-lock"
        style={{
          marginTop: 12,
          borderColor: lockHard ? "var(--lime, #9f6)" : "var(--rose, #f66)",
        }}
        title="ORIENTATION_LOCK L16：不可关闭"
      >
        <strong>横竖屏硬审核（不可关闭）</strong>
        <p className="hint" style={{ marginTop: 4, marginBottom: 0 }}>
          {orientLock?.message ||
            "以含旋转元数据的显示尺寸为准：源片与归一化成片方向必须一致。"}
          {" · "}
          竖屏可用 {readyPortrait} · 横屏可用 {readyLandscape}
          {rejectedOrient > 0 ? ` · 方向拒收 ${rejectedOrient}` : ""}
          {orientLock && orientLock.ready_unverified > 0
            ? ` · 待复核 ${orientLock.ready_unverified}`
            : ""}
          {!orientLock?.integrity_ok ? " · 锁完整性异常" : ""}
        </p>
      </div>
    </section>
  );
}
