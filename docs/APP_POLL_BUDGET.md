# App 轮询预算表（控制面）

> 禁止新页面私自以 ≤1s 间隔打全量接口。实现常量：`apps/desktop/src/pollBudget.ts`。

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
| 通道 | 间隔 | 接口 | 说明 |
|------|------|------|------|
| health | **5s** | `GET /health`（经 Rust `engine_status` 单次） | 主心跳；勿在其它 effect 再叠一层 |
| jobs | **5s** | `GET /jobs` | 与 health 同 tick，勿单独 ≤1s |
| reportOps | **10s** | `GET /reports/ops` | health tick 每 2 次 |
| humanAlerts | **15s** | `GET /ops/human-alerts` | health tick 每 3 次 |
| messages | **30s** | 消息同步相关 | 勿在列表页开 1s 全量 |
| semantic（忙） | **3s** | 语义进度 | 仅运行中 |
| semantic（闲） | **15s** | 语义进度 | 空闲 |
| vector（忙） | **1s** | 向量执行器 | **仅** RUNNING 等忙态；禁止空闲 1s |
| runtime-health | **10s** | `GET /ops/runtime-health` | 总览运行健康条 |

运维观察：空闲时引擎侧 `health_qps_approx` 应 **≤1**（见 `GET /ops/runtime-health`）。
