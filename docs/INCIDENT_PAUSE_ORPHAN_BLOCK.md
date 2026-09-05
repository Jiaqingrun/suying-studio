# 事故记录 · 睡眠恢复后 `PAUSED_BLOCKED` 空原因卡死（2026-07-31）


> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 现象（客户机 `xlf-remote`）

- App / 引擎进程在跑，`GET /health` 正常，片库挂载正常。
- `GET /jobs/pipeline` 显示 `pause_active=true`，`accepts_new_work=false`。
- `GET /system/pause-state`：`state=PAUSED_BLOCKED`，但 `pause_reasons=[]`、`pause_holds={}`，`resume_blockers=["quiesce_failed"]`。
- `POST /system/resume`（含 `system_sleep` / `quiesce_failed`）无效——人工恢复只清 `pause_holds` 里的原因。
- 队列堆积（当时约 queued 33 + paused 45 + circuit_open 21）；偶有陈旧 `running` 被 reap。

## 根因

睡眠安全停机（quiesce）与唤醒恢复存在竞态：

1. `will_sleep` → `PAUSING` + `system_sleep`，异步 `quiesce_units(timeout≈15s)`。
2. 唤醒 `did_wake` 先完成 → 清掉 `system_sleep` hold → `ACTIVE`。
3. 迟到的 quiesce 超时仍调用 `mark_blocked(["quiesce_failed"])`，**无 generation 校验**，把已清空原因的运行时再次打成 `PAUSED_BLOCKED`。
4. 结果：空 `pause_reasons` 的阻断态，正常 wake / manual resume 都解不开 → 长期不接新任务。

次要：多次睡眠打断生产后 READY_GATE / 熔断也会让任务看起来「不干活」，但本次「完全不能接单」的硬阻塞是上述暂停态卡死。

## 现场处置（已做）

1. 强制将 `pause_state.json` 写回 `ACTIVE` 并重启 App 引擎（临时止血）。
2. **清理全部生产队列**：将 `queued` / `running` / `paused` / `paused_system` / `circuit_open` 共 **100** 条标为 `cancelled`（写 job_events：`运维清理队列：任务已取消`）；清理后 `queued=0`、`pause=ACTIVE`。

## 代码修正（本仓）

1. `mark_blocked(..., generation=)`：generation 不匹配，或已是无原因的 `ACTIVE`/`RESUMING` 时忽略迟到阻断。
2. `quiesce_units` / `_after_pause_async`：阻断时带上本轮 `generation`。
3. `_heal_orphan_blocked_locked`：空原因的 `PAUSED_BLOCKED` 在 load / snapshot / `should_claim_jobs` / manual resume 时自愈回 `ACTIVE`。
4. 单测：`test_stale_quiesce_block_ignored_after_wake`、`test_orphan_paused_blocked_self_heals`。

> 客户机要吃到修复需 **重出一体包并 `deploy-remote`**。**2026-07-31** 已出 **0.5.0**（`release_seq=4`）并覆盖 `xlf-remote`；T2S 路径 `releases/0.5.0/20260731T134048Z`。

## 以后发现同类问题

1. `GET /system/pause-state`：若 `PAUSED_BLOCKED` 且 `pause_reasons` 为空 → 即本事故形态。
2. 新引擎：读 pause-state / pipeline 应自动自愈；或 `POST /system/resume` 任意 reasons 触发 heal。
3. 旧引擎：备份后把 `pause_state.json` 的 `state` 改为 `ACTIVE`，清空 `resume_blockers`，重启 App。
4. 参考：[`SYSTEM_EVENT_PAUSE.md`](SYSTEM_EVENT_PAUSE.md)。

## 续发（2026-07-31 夜）· `power_off` 假恢复卡死

### 现象

- 客户机重启后 App 极卡、几乎无响应；`load` 高、UI 狂刷 `/health`。
- `pause-state`：`PAUSED_BLOCKED` / `PAUSED`，`pause_reasons=["power_off"]`，`resume_blockers` 含 `restart_during_transition`。
- `POST /system/resume {"reasons":["power_off"]}` 返回 200 但状态仍暂停——`accepts_new_work=false`，worker/scheduler 停。

### 根因

1. `will_power_off` 落盘后冷启动不补发可匹配的 `did_wake`；且旧逻辑 `did_wake` 只清 `system_sleep`。
2. `complete_resume` 要求 hold.owner == pending.owner；人工 resume 的 owner=`manual`，系统 hold 为 `system`，**原因清不掉**，表现为「假恢复」。
3. 连带：任务被挂起后若运维把 DB 打回 `queued` 而 worker 仍占 `resource_gate`，会出现「有队列却不产」的假死。

### 代码修正

1. 启动 load：`_drop_stale_power_off_locked` 清残留 `power_off`。
2. `did_wake` 同时清 `system_sleep` + `power_off`。
3. `complete_resume`：`trigger=manual` 时按 generation 清 hold，不强制 owner 相同。
4. 单测：`test_manual_resume_clears_system_power_off` / `test_did_wake_clears_power_off` / `test_boot_load_clears_stale_power_off`。

> 客户机要吃到代码修复仍需重出包部署；当晚现场已写 `pause_state.json→ACTIVE` 并回收引擎，任务 #423 恢复 `running`。

## Gate

GSystemPause / 客户机运行时稳定性；与 GShip 现场验收相关。
