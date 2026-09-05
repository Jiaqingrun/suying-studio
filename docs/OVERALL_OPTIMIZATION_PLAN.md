# 速影整体优化方案（权威落地）

> 批准计划落地文档。冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。  
> 复盘依据：[`REMOTE_DEPLOY_POSTMORTEM.md`](REMOTE_DEPLOY_POSTMORTEM.md)。

## 目标与约束

**产品目标排序**（[`PRODUCT_PLAN.md`](PRODUCT_PLAN.md)）：成片能发 → 日更够条数 → 物料齐 → 分发省事可审计 → 触达可知。

**工程目标**：客户机「装得上、起得来、睡得醒、跑得稳、看得懂、可升级」。

**硬边界（不得为优化而破）**：

- [`HARD_LOCKS.md`](HARD_LOCKS.md) L6 纸片、L12 远程部署、L15 成片>旁白、READY_GATE、发布全机 Chrome 单槽
- Install Profile 默认 `max_render_concurrency=1`；提并发只挂档位门禁（[`install_profile.py`](../engine/ops/install_profile.py) / [`INSTALL_PROFILE.md`](INSTALL_PROFILE.md)）
- 热补客户机 `.py` 不算交付；正式修复必须重出包 + `deploy-remote`

主线 Gate 仍是 **GCustomerUX**；本方案稳态与交付加固定为可并行 P0 工程线，不抢占未授权 Pack/Reach 大开发。

## 五条优化线

| 线 | 名称 | 成功标准（可测） |
|----|------|------------------|
| L1 | 交付与冷启动 | `deploy-remote` 不因 lifespan I/O 回滚；health 门禁 ≤90s 内稳定 ok |
| L2 | 运行时稳态 | 睡眠/关机后可恢复；空闲 health ≤1/s；无假 PAUSED |
| L3 | 产能与资源隔离 | 生产/TTS/发布按 ResourceGate 错峰；占槽原因可见 |
| L4 | 客户体验与功能真相 | 通知/软跳过/规则生效可见；本机≠客户机有 diff/显式导入 |
| L5 | 多机运维与可观测 | 回执含版本指纹+关键指标；批量机同一剧本 |

## Phase 0 · 止血闭环（0.5.2） — DONE · 2026-08-01

**DoD（须同时满足，不得只看 health）**：

1. 一体包含：Chrome migrate 跳过 trash、**默认延期全部异卷 workspace merge**、`SUYING_SKIP_WORKSPACE_CHROME_MIGRATE`；`remote-install` 写 `local.env` skip + 加长 health 等待；pause / health 洪水修复指纹进包。
2. `deploy-remote` → 目标机：App/`engine_version` 对齐、pause=ACTIVE、空闲 health≤1/s、CORS、词池、Chrome APFS 体积、clone gate（若 VIDEO_LOCK=clone）。
3. 权威 Chrome 只认本机 APFS（[`STORAGE_SYNC_SAFETY.md`](STORAGE_SYNC_SAFETY.md)）；工作区 `chrome-profiles` 禁止当权威。
4. 验收条已写入 [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) §5 Phase0；DEV_LOCK §E 一句状态。

**真机回执（xlf-remote）**：套件 `~/Desktop/速影-0.5.2-product-macos-arm64`；App/引擎 **0.5.2**；`pause=ACTIVE` / `accepts_new_work=true`；health QPS≈**0.3–0.4**；CORS ok；词池 revision=3；Chrome APFS≈46G；clone gate ok；部署 exit PARTIAL=`SMOKE_NOT_REQUESTED`（未加 `--smoke-job`，符合门禁）。

## Phase 1 · 冷启动与控制面

| 项 | 落地 |
|----|------|
| 跨卷 merge | `migrate_legacy_chrome_profiles` 默认跳过异卷；`POST /ops/chrome-profiles/migrate-workspace?force=1` 显式执行 |
| 单测 | `scripts/smoke_chrome_migrate.py` |
| 轮询预算 | [`APP_POLL_BUDGET.md`](APP_POLL_BUDGET.md) + `apps/desktop/src/pollBudget.ts` |
| 可观测 | `GET /ops/runtime-health`（health QPS、pause、pipeline accepts、resource_gate） |

## Phase 2 · ResourceGate 吞吐

- `GET /ops/resource-gate` + `/jobs/pipeline.resource_gate`；总览「运行健康」聚合占槽。
- 档位：lite/standard/pro render=1；max 允许 2（TTS/publish 仍 1）。见 [`INSTALL_PROFILE.md`](INSTALL_PROFILE.md) §4。

## Phase 3 · 配置真相

- 只读 diff：`python3 scripts/config_truth_diff.py --customer "<名>" --seed configs/customers/<名>`
- 显式导入：`python3 scripts/config_truth_import.py …`（禁止静默覆盖 `profile_json` / chrome 绑定）
- GCustomerUX 工程项（通知中枢、软跳过 1B、标题 24、成片>旁白、运行健康条）已在 App/引擎落地；**真机发布验收**仍按 DEV_LOCK GCustomerUX 人眼收口，本阶段不伪造「本机规则已同步到客户机」。

## Phase 4 · 舰队回执与门禁

- `install-receipt.json` 增强：`engine_version`、runtime 指纹、pause、chrome 根、health QPS 采样、clone gate。
- 出包回归：`scripts/gate_package_regression.sh`
- 客户机覆盖：POSTMORTEM §4.4 + REMOTE_DEPLOY §5。

## Phase 5 · 自动任务与轻量交付（0.5.5）— LOCAL_VERIFIED · 2026-08-01

- 发布页收拢为“立即发布 + 自动任务”：多任务、多账号逐项条数、`HH:MM:SS`
  窗口、每 occurrence 独立随机秒；READY 足够跳过生产，不足按 slot 补产。
- 发布时钟从 30 秒粗轮询拆为 1 秒独立时钟；随机 seed/计划时间持久化，计划
  revision 更新后释放旧预留并重排，重启不重抽。
- 交付拆为产品 ZIP 与独立 DMG；成功清理本次 staging，失败/PARTIAL 保留
  24 小时，旧 App 只保留最近 1 个回滚点。
- `core|clone` 双 flavor 写入签名 `BUNDLE_FLAVOR`；`deploy-remote --build`
  按客户 VIDEO_LOCK 自动选择，错配在传输前 fail-closed。
- 本机 core 实测：App **330MiB**、产品 ZIP **129MiB**、独立 DMG
  **165MiB**；Python 311 tests、App 6 tests、Rust、主 smoke、打包内嵌
  health/CORS/codesign 均通过。客户机远程覆盖仍须按 REMOTE_DEPLOY 真机验收。

## 明确不做

- 不放松反检测 / Cookie 池 / 自动过验证码
- 不把片库或 chrome-profiles 塞进 T2S
- 不在未授权下重开 Pack/Reach 主开发
- 不把本机 DB 整库同步做成默认

## 里程碑

| 里程碑 | 度量 |
|--------|------|
| M0 止血包 | deploy 不因 chrome merge 回滚；health≤1/s |
| M1 冷启动 | 冷启动到 health 无跨卷 I/O |
| M2 吞吐 | 占槽原因 UI 可见；档位文档化 |
| M3 真相 | config_truth_* 可用 |
| M4 舰队 | 回执增强 + gate_package_regression |

## Phase 后续 · 关账（2026-08-03）

不再新开功能 Phase。稳态与交付收口改走 [`CLOSEOUT_PLAN.md`](CLOSEOUT_PLAN.md)（冻结新 Gate、PARTIAL 真机剧本、CDP 拆分起步、config_truth 默认运维动作）。  

**深度工程优化（2026-08-10）**：版本单源、事故防回归、巨石零语义拆分、App 控制面与仓卫生 → [`DEEP_OPTIMIZATION_PLAN.md`](DEEP_OPTIMIZATION_PLAN.md)。进度仅写 `DEV_LOCK` `OPT.*`，禁止与本文双表并行标 ✅。  

冲突时：`DEV_LOCK` / `HARD_LOCKS` > CLOSEOUT_PLAN / DEEP_OPTIMIZATION_PLAN > 本文。
