import type { Tab } from "../types";

export type NextAction = {
  id: string;
  label: string;
  kind: "primary" | "warn" | "danger";
  run: () => void;
};

type HealthLite = {
  path_health?: { ok?: boolean };
} | null;

type EngineLite = {
  healthy?: boolean;
  running?: boolean;
} | null;

type Props = {
  engineOn: boolean;
  engineBusy: boolean;
  health: HealthLite;
  engine: EngineLite;
  readyCount: number;
  pendingReview: number;
  ttsBad: number;
  runningJobs: number;
  hasTodayPlan: boolean;
  onStartEngine: () => void;
  onSetTab: (t: Tab) => void;
};

export function computeNextAction(p: Props): NextAction | null {
  if (p.engineBusy) {
    return { id: "eng-busy", label: "引擎启动中…", kind: "warn", run: () => undefined };
  }
  if (!p.engineOn) {
    return { id: "eng", label: "下一步：启动引擎", kind: "danger", run: p.onStartEngine };
  }
  if (p.health && p.health.path_health && p.health.path_health.ok === false) {
    return {
      id: "path",
      label: "下一步：修复路径异常",
      kind: "warn",
      run: () => p.onSetTab("settings"),
    };
  }
  if (p.ttsBad > 0) {
    return {
      id: "tts",
      label: `下一步：处理音色违规（${p.ttsBad}）`,
      kind: "warn",
      run: () => p.onSetTab("review"),
    };
  }
  if (p.pendingReview > 0) {
    return {
      id: "rev",
      label: `下一步：去审片（${p.pendingReview}）`,
      kind: "primary",
      run: () => p.onSetTab("review"),
    };
  }
  if (p.readyCount > 0 && p.pendingReview === 0) {
    return {
      id: "pack",
      label: "下一步：导出物料 / 发布",
      kind: "primary",
      run: () => p.onSetTab("publish"),
    };
  }
  if (!p.hasTodayPlan && p.runningJobs === 0) {
    return {
      id: "prod",
      label: "下一步：去生产或排日历",
      kind: "primary",
      run: () => p.onSetTab("produce"),
    };
  }
  if (p.runningJobs > 0) {
    return {
      id: "jobs",
      label: `生产中（${p.runningJobs} 任务）`,
      kind: "primary",
      run: () => p.onSetTab("produce"),
    };
  }
  return {
    id: "ok",
    label: "产线畅通 · 可继续生产",
    kind: "primary",
    run: () => p.onSetTab("produce"),
  };
}

export function NextActionPill(props: Props) {
  const action = computeNextAction(props);
  if (!action) return null;
  return (
    <button
      type="button"
      className={`next-pill next-pill--${action.kind}`}
      onClick={() => action.run()}
      disabled={action.id === "eng-busy"}
    >
      {action.label}
    </button>
  );
}
