# 事故记录 · App `/health` 轮询洪水导致客户机卡顿（2026-07-31）


> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 现象（客户机 `xlf-remote` · App 0.5.0）

- UI 明显卡顿、几乎无响应；引擎 `python3 -m engine.main` CPU ≈ **36%**（空闲生产时仍高）。
- `engine.log`：**约 6 次/秒** `GET /health`（采样 3s → 18 次），几乎无其他 API。
- 设计预期：主轮询 **每 5s** 轻量 health + `engine_status`。
- 同期次因：Chrome GPU Helper ≈ 31% CPU；外置卷 `/Volumes/xlf` 写延迟高于本机 APFS。
- 叠加历史：`power_off` 假恢复（见 [`INCIDENT_PAUSE_ORPHAN_BLOCK.md`](INCIDENT_PAUSE_ORPHAN_BLOCK.md)）会放大「假死感」。

## 本机对比（开发机）

| 项 | 本机 | 客户机 |
|----|------|--------|
| App | Desktop 产物 0.3.0 + 源码引擎 | 正式一体包 **0.5.0** |
| 引擎 | anaconda `engine.main`（在产，CPU 高属预期） | 包内 python，**仅 health 洪水也占 ~36%** |
| pause | ACTIVE | ACTIVE（曾 `ops_clear_power_off`） |
| `/health` 节奏 | 正常轮询量级（无客户机级洪水日志） | **~6/s** |
| 权威库 | 本机 `~/Suying/data` | `/Users/xlf/Suying/data` |

结论：卡顿主因不是「客户机硬件更弱」 alone，而是 **0.5.0 App 前端轮询自激 + Rust 双倍 health 探测**。

## 根因

1. **`useNotifyHub().notify` 每次 render 新建函数**（未 `useCallback`）。
2. `App.tsx` 主 effect 依赖 `[refreshAll, refreshEngine, refreshHealth]`，而 `refreshAll` → `notify` → **每帧新 identity**。
3. effect 重挂时会 **重新跑整段 boot heal**（多次 `getEngineStatus` / `startEngine`）并重置 5s interval → `/health` 洪水。
4. 放大：`engine_status` 内 `health_ok()` + `workspace_healthy()` = **每次 2× `/health`**；`probe_from_engine` 再额外打一次。

## 代码修正（本仓）

1. `AppNotifyHub.tsx`：`notify` / `celebrate` / dismiss* 全部 `useCallback` 稳定。
2. `App.tsx`：**启动 heal 与 5s 轮询拆成两个 effect**；boot 仅 mount 一次。
3. `workspace_events.rs` + `lib.rs`：`engine_reach_and_healthy()` / probe 复用单次 `/health` body。
4. `ProductionPage`：pipeline 轮询不再依赖会每 5s 变的 `jobs` 数组（避免无谓 remount）。

## 验证标准

- 客户机 `engine.log` 空闲时：`GET /health` ≈ **≤ 1 次/秒**（理想 ≈ 0.4–0.6/s：UI health + 合并后的 engine_status）。
- 引擎空闲 CPU 明显下降；App 可正常点击切换页。

## 交付

须 **重出一体包（建议 0.5.1）并 `deploy-remote`**。热补前端/ Rust 进已签名包不算交付。

### 2026-07-31 夜 · 0.5.1 落地

| 项 | 结果 |
|----|------|
| 出包 | `~/Desktop/速影-0.5.1-product-macos-arm64`（ZIP ≈6.1G） |
| T2S | `release_seq=5` · `releases/0.5.1/20260731T152602Z` · SHA 回读通过 |
| `deploy-remote` | 首次失败并回滚：引擎 lifespan 卡在 `migrate_legacy_chrome_profiles` 跨卷拷贝 **30G** 工作区 `chrome-profiles`（含 `.trash-*`），40s health 门禁超时 |
| 现场处置 | 整树隔离 `/Volumes/xlf/速影工作区/chrome-profiles` → `*.quarantined-full-*`；本机 APFS `~/Library/.../chrome-profiles`（46G）保留；从 incoming 原子装入 **0.5.1** |
| 验收 | App/引擎 **0.5.1**；空闲 `GET /health` ≈ **0.8/s**（修复前 ~6.2/s）；`pause=ACTIVE` |

本仓续修：`migrate_legacy_chrome_profiles` **跳过 `.trash*` / 隐藏目录**（下一版进包）；勿再把工作区 Chrome 树当权威。

## Gate

GCustomerUX / 客户机运行时稳定性；与 GShip 现场验收相关。
