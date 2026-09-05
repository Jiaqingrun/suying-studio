# 速影 · 深度优化方案（关账相容）

> **效力：关账期工程优化的权威执行方案。2026-08-10 落盘。**  
> **终态：W4 收口 · 2026-08-10**（`CLOSE.W4` · 证据 `~/Suying/logs/closeout-w4-20260810/`）· **禁止再挂 NEED_HUMAN 关账进度**。  
> **裁决**：`DEV_LOCK` / `HARD_LOCKS` > 本文 > [`OVERALL_OPTIMIZATION_PLAN.md`](OVERALL_OPTIMIZATION_PLAN.md) > [`CLOSEOUT_PLAN.md`](CLOSEOUT_PLAN.md)。  
> **进度真相源唯一**：[`DEV_LOCK.md`](DEV_LOCK.md) §F `OPT.*` / `CLOSE.*` 行。禁止另立平行进度表。  
> **版本单源**：[`VERSION_SOURCE.md`](VERSION_SOURCE.md)。

## 0. 目标与硬边界

**目标**：版本/探针诚实 → 事故指纹不回退 → 巨石零语义拆分 → 控制面节流 → 交付可审计 → 真机关账。

**禁止**：新开 Gate；`desktop-v2`；反检测/自动验证码；无授权碰 L17–L20/L15/READY_GATE；虚报人眼 DONE。

## 1. Phase 核对表

| Phase | 主题 | DEV_LOCK | 状态 |
|-------|------|----------|------|
| **P0** | 版本单源 + supervisor 修复 + VERSION_SOURCE | OPT.P0 | **DONE · 2026-08-10** |
| **P1** | ResourceGate/pause/publish 回归 + readiness 可观测 | OPT.P1 | **DONE · 2026-08-10** |
| **P2** | app.py 域路由 + CDP/publish_runner 分层 | OPT.P2 | **DONE · 2026-08-10** · app.py ~2k；job/catalog/reach_console/library_review；`publish_fail_forward`/`publish_prep_heal` 表面模块；平台 fill facade |
| **P3** | poll 预算审计 + App/api 拆分 | OPT.P3 | **DONE · 2026-08-10** |
| **P4** | 仓卫生 + 交付模板 | OPT.P4 | **DONE · 2026-08-10** |
| **P5** | CLOSEOUT 真机剧本 | OPT.P5 | **DONE · 2026-08-10** · 用户确认 ACCEPTANCE A→F · [`CLOSEOUT_HUMAN_BOARD.md`](CLOSEOUT_HUMAN_BOARD.md) |
| **W4** | 关账本机全量自检 + decision | CLOSE.W4 | **DONE · 2026-08-10** · 不再挂 NEED_HUMAN 进度 |

## 2. 每 Phase 退出门禁

1. `python3 scripts/smoke_test.py`
2. 相关专项 smoke（hard_locks / system_events 等）
3. `pytest tests/`（voice live 可为 ENV_KNOWN skip）
4. `cd apps/desktop && npx tsc --noEmit && npx vitest run`
5. `/health` · `/ops/engine-supervisor` · 包版本三位一致
6. 空闲 health QPS≤1（`APP_POLL_BUDGET`）
7. 回写 DEV_LOCK `OPT.*`
8. 一体包验收须重建 `appsdesktop` 壳

## 3. 真机（P5）

严格按 [`CLOSEOUT_ACCEPTANCE.md`](CLOSEOUT_ACCEPTANCE.md)；**A→F 已于 2026-08-10 用户确认 PASS**；证据 `~/Suying/logs/closeout-opt-20260810/ACCEPTANCE_PASS.txt`。

## 4. 与 OVERALL 的关系

OVERALL Phase0–4 已落地；其后功能权转 CLOSEOUT。**结构/诚实度/防回归** 执行权转本文。冲突句以 DEV_LOCK 为准。
