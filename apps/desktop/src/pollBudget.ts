/**
 * Control-plane poll intervals (ms). See docs/APP_POLL_BUDGET.md.
 * Do not introduce new full-payload polls faster than these floors.
 */
export const POLL_BUDGET_MS = {
  health: 5_000,
  jobs: 5_000,
  reportOps: 10_000,
  humanAlerts: 15_000,
  messages: 30_000,
  semanticFast: 3_000,
  semanticIdle: 15_000,
  vectorBusy: 1_000,
  runtimeHealth: 10_000,
  /** Logs tab while visible */
  logs: 4_000,
} as const;

/** health tick multiples for slower channels (base = health interval). */
export const POLL_TICK = {
  /** reportOps every N health ticks → ~10s */
  reportOpsEvery: 2,
  /** humanAlerts every N health ticks → ~15s */
  humanAlertsEvery: 3,
  /** customer/keyword sync every N health ticks → ~30s */
  customerSyncEvery: 6,
} as const;
