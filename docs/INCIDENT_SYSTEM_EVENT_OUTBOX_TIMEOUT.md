# 事故记录 · 系统事件 outbox 超时与 toast 风暴（2026-08-03）

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。

## 现象（客户机 `xlf-remote`）

- UI 反复弹出：`系统事件未被引擎确认：引擎未确认系统事件: http://127.0.0.1:8766/system/events … timed out reading response`
- `~/Library/Application Support/com.qr.suying/runtime/system_event_outbox.json` 积压十余条 sleep/wake
- 引擎 `/health` 与生产仍可工作，但睡眠/唤醒确认链路失效，用户误以为「系统事件坏了」

## 根因（叠加）

1. **热路径同步写审计库**：`POST /system/events` 在返回前调用 `_audit_system` → SQLite `commit`。客户机 `montage.db` 首笔/争用下常见 **3–9s**。
2. **桌面端超时过短**：Tauri `ureq` 超时固定 **3s** → 必然 `timed out reading response`。
3. **把「已处理」当成失败**：引擎对重复 `event_id` 返回 `applied=false, duplicate=true`（策略忽略同理）。Rust 只认 `applied=true` 才出队 → **同一条永久卡在 outbox 头**，每 5s 重试，每次再撞超时。
4. **失败即 toast**：每次投递失败 `emit` → 前端无条件 `notify(err)` → 风暴。

因果链：慢审计 → 超时 → 事件其实可能已应用 → 重试变 duplicate → 仍当失败 → outbox 永不空 → toast 不停。

## 代码修正（本仓 · 0.6.14+）

| 层 | 改动 |
|----|------|
| 引擎 | `_audit_system` 改后台线程；重复 `event_id` 快速 ACK（跳过 snapshot）；响应带 `acknowledged=true` |
| Rust | 超时 20s；HTTP 200 且 `applied`/`duplicate`/`acknowledged`/`state` 任一成立即出队；错误 emit 60s 冷却；同 kind 折叠 + outbox 上限 32 |
| 前端 | 同类错误 toast 至少间隔 60s |

配套：[`SYSTEM_EVENT_PAUSE.md`](SYSTEM_EVENT_PAUSE.md)。

## 现场处置

1. 升级一体包至含本修复的版本（`deploy-remote --skip-models`）。
2. 升级后若仍有旧 outbox：清空 `system_event_outbox.json` 为 `[]`（或等新投递逻辑自动 ACK/出队），再重启 App。
3. 验证：`POST /system/events` 空闲应 **≪ 1s**；睡眠/唤醒不再弹超时；`pending_events=0`。

## 以后发现同类问题

1. 看 outbox 是否堆积；`system-events.jsonl` 是否已有相同 `event_id`。
2. 对同一 `event_id` 手动 POST：若返回 `duplicate/acknowledged` 而桌面仍报错 → 旧包未升级。
3. 对慢响应：先查 `operation_logs` / DB 是否被生产锁住，再查是否审计又被同步塞回热路径。
