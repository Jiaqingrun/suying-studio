/** Customer-facing Chinese labels for status / source / notes shown in the App. */

const REVIEW_STATUS_ZH: Record<string, string> = {
  approved: "已通过",
  rejected: "已拒绝",
  uncertain: "待人工",
  pending: "待处理",
};

const REVIEW_SOURCE_ZH: Record<string, string> = {
  worker: "自动任务",
  manual: "人工",
  legacy: "兼容记录",
  test: "测试",
  smoke: "冒烟",
  backfill: "补录",
  migration: "迁移",
};

const OUTPUT_STATE_ZH: Record<string, string> = {
  ready: "待发布",
  review: "待审",
  failed: "失败",
  rejected: "已拒绝",
  rendering: "渲染中",
  queued: "排队中",
  packing: "打包中",
  published: "已发布",
};

const GENERIC_STATUS_ZH: Record<string, string> = {
  ...REVIEW_STATUS_ZH,
  ...OUTPUT_STATE_ZH,
  draft: "草稿",
  generating: "生成中",
  running: "运行中",
  completed: "已完成",
  cancelled: "已取消",
  canceled: "已取消",
  interrupted_system: "系统中断",
  waiting: "等待中",
  scheduled: "已排期",
  publishing: "发布中",
  open: "待处理",
  done: "已完成",
  error: "错误",
  active: "启用",
  inactive: "停用",
  paused: "已暂停",
  paused_system: "系统暂停",
  circuit_open: "已熔断",
  failed: "失败",
  asset_blocked: "物料受阻",
  pack_failed: "出包失败",
  validated: "已校验",
  need_human: "待人工发布",
};

/** Known auto-decision notes (English legacy + Chinese current). */
const REVIEW_NOTE_ZH: Array<{ match: RegExp | string; label: string }> = [
  {
    match: /\[auto_approve\]\s*ready_gate|\[自动通过\].*出片门禁|自动通过：成片已通过出片门禁/,
    label: "自动通过：成片已通过出片门禁",
  },
  {
    match: /\[auto_reject\]\s*hard_failure|\[自动拒绝\].*硬失败|自动拒绝：硬失败不可发布/,
    label: "自动拒绝：硬失败不可发布",
  },
  {
    match:
      /\[auto_uncertain\]\s*review_or_evidence_conflict|\[待人工\].*证据冲突|待人工：审片状态或证据冲突/,
    label: "待人工：审片状态或证据冲突",
  },
  {
    match: /一键通过：人工批量审核/,
    label: "一键通过：人工批量审核",
  },
];

export function reviewStatusLabel(status: unknown): string {
  const key = String(status || "").trim();
  if (!key) return "未知";
  return REVIEW_STATUS_ZH[key] || key;
}

export function reviewStatusBadgeClass(status: unknown): string {
  const key = String(status || "").trim();
  if (key === "approved" || key === "completed" || key === "published" || key === "ready") {
    return "badge-ok";
  }
  if (
    key === "rejected" ||
    key === "uncertain" ||
    key === "failed" ||
    key === "error" ||
    key === "cancelled" ||
    key === "canceled"
  ) {
    return "badge-warn";
  }
  return "badge-mute";
}

export function reviewSourceLabel(source: unknown): string {
  const key = String(source || "legacy").trim() || "legacy";
  return REVIEW_SOURCE_ZH[key] || key;
}

export function outputStateLabel(state: unknown): string {
  const key = String(state || "").trim();
  if (!key) return "未知";
  return OUTPUT_STATE_ZH[key] || key;
}

/** Generic UI status → Chinese. */
export function uiStatusLabel(status: unknown): string {
  const key = String(status || "").trim();
  if (!key) return "未知";
  if (GENERIC_STATUS_ZH[key]) return GENERIC_STATUS_ZH[key];
  if (/[\u4e00-\u9fff]/.test(key)) return key;
  return key;
}

export function reviewNoteLabel(note: unknown): string {
  const raw = String(note || "").trim();
  if (!raw) return "无批注";
  for (const row of REVIEW_NOTE_ZH) {
    if (typeof row.match === "string" ? raw.includes(row.match) : row.match.test(raw)) {
      return row.label;
    }
  }
  // Strip leftover machine tags like [foo_bar] when present.
  const cleaned = raw.replace(/^\[[^\]]+\]\s*/, "").trim();
  if (/^[a-z0-9_]+(?:\s+[a-z0-9_]+)*$/i.test(cleaned)) {
    return "系统批注（详情见日志）";
  }
  return cleaned || "无批注";
}

export function isAutoApproveNote(note: unknown): boolean {
  const raw = String(note || "");
  return (
    raw.includes("[auto_approve]") ||
    raw.includes("[自动通过]") ||
    raw.includes("自动通过：成片已通过出片门禁") ||
    raw.includes("自动通过：出片门禁重验通过")
  );
}
