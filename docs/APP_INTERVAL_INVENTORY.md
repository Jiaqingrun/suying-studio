# App `setInterval` / poll inventory（PL-25 · S4）

> 权威预算：[`APP_POLL_BUDGET.md`](APP_POLL_BUDGET.md) · 常量 `apps/desktop/src/pollBudget.ts`。  
> CI：`scripts/check-app-poll-budget.sh`（挂入 `gate_package_regression.sh`）。  
> **禁止**新增 ≤1s 全量控制面轮询；消息未读预算 **≥30s** 保留。

| 位置 | 间隔 | 接口 / 作用 | 备注 |
|------|------|-------------|------|
| `App.tsx` 主 tick | **5s** | `/health` · `/jobs` · Rust `engine_status`（单次 `/readiness`） | `isAppSurfaceActive()`：document.hidden **或** Tauri 最小化/失焦时跳过（P0.3） |
| `App.tsx` services | **~30s**（`POLL_TICK.servicesEvery=6`） | `GET /ops/services` | S1：已从每 5s health 剥离；Ops 开关后即时刷新 |
| `App.tsx` reportOps | **~10s** | `/reports/ops` | tick×2 |
| `App.tsx` humanAlerts | **~15s** | `/ops/human-alerts` | tick×3 |
| `App.tsx` messages | **30s** | 消息未读同步 | **硬底线 ≥30s**；表面不活跃时暂停，回前台 flush |
| `ProductionPage.tsx` pipeline | 闲 **15s** / 忙 **3s** | `/jobs/pipeline` | S3：有 running/queued 才 3s |
| `OverviewPage.tsx` | **10s** | reportOps 条 | FrozenTab `active` |
| `OpsPage.tsx` | **10s** | 运维页局部 | 仅 `active` |
| `LogsPage.tsx` | **4s** | 日志 | 仅日志页可见 |
| `VectorControl.tsx` | 忙 **1s** / 闲 **15s** | 向量状态 | **唯一**允许的 1s API（忙态） |
| `PublishBatchPanel.tsx` | 活动 **1s** / 闲 **5s** | 发布批次状态 | 活动窗 due 灵敏；非全 App 洪水 |
| `LicenseGate.tsx` | **15s** | 许可 | |
| `CarrierOpsStrip.tsx` | **60s** | 载体状态 | |
| `CarrierInstallWizard.tsx` | **3s** | 模型安装进度 | 仅安装中 |
| `ActivityTicker.tsx` | **4.5s** | UI 轮播 | **无 API** |

## 允许的 ≤1s（白名单）

1. `VectorControl` + `POLL_BUDGET_MS.vectorBusy`（向量 RUNNING）  
2. `PublishBatchPanel` 发布批次进行中（due/进度）  
3. `ActivityTicker` UI-only（不计控制面 QPS）

新增 ≤1s `setInterval(..., 1000|500|…)` 须先改本表 + 预算门禁，否则 CI 红。
