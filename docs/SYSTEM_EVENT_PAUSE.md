# 速影 · 系统事件暂停与恢复（GSystemPause）

> **效力：写死。2026-07-27 用户批准计划并授权新增 Gate。**
> 配套：[`DEV_LOCK.md`](DEV_LOCK.md) · [`HARD_LOCKS.md`](HARD_LOCKS.md)

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
---

## 1. 目标

Mac 盒盖/系统睡眠、熄屏时可自动暂停速影内部任务；开盖唤醒、解锁后可自动继续。四项独立开关，用户可自行选择。

| 用户说法 | 系统事件 | 默认 |
|----------|----------|------|
| 盒盖暂停 | `will_sleep`（无独立盒盖 API） | **开** |
| 熄屏暂停 | `screens_sleep` | **关**（避免外接屏误停） |
| 开盖继续 | `did_wake` | **开** |
| 解锁继续 | `session_active` | **关** |

---

## 2. 硬边界

1. **只控速影**：不冻结其他 App；只停速影拥有的 worker / 扫描 / 调度 / 消息 / 发布子进程。
2. **安全暂停**：计算任务在最小检查点停；当前 FFmpeg/TTS 原子步骤可等到边界，Ollama pull 可终止，UI 显示「正在安全暂停」。
3. **自动恢复只清系统原因**：人工暂停、质量熔断、验证码停人、路径/硬盘异常、发布结果不明 **不得**被自动解除。
4. **外部副作用不重放**：G5.V 上传、软文发布、同步写入、App 更新、Ollama pull → 中断后 `interrupted_system` / `need_human`，须人工确认。
5. **路径/磁盘健康阻断恢复**：`resume_requires_path_health` + `resume_requires_disk_health` 默认 true（配置路径可写与空间足够；不是「必须插外置盘」）。
6. **事件日志本机化**：写 `~/Library/Application Support/com.qr.suying/runtime/`（或 `SUYING_APP_RUNTIME_DIR`），不依赖外置工作区。

---

## 3. 状态机

```text
ACTIVE → PAUSING → PAUSED → RESUMING → ACTIVE
                ↘ PAUSED_BLOCKED ↗（健康/路径失败）
```

- `event_id` + `generation` 去重；乱序/重复 wake 不得恢复错误一轮。
- 睡眠/唤醒/关机/熄屏来自公开 `NSWorkspace` 通知；锁屏/解锁来自 `NSDistributedNotificationCenter`。
- 系统 token 只保存在本机 runtime 的 0600 普通文件；桌面端须在引擎 **HTTP 200 确认**（`applied` / `duplicate` / `acknowledged` / 合法 `state`）后才出队；**不得**仅因 `applied=false`（重复或策略忽略）把事件留在 outbox。
- 引擎 `POST /system/events` 热路径禁止同步阻塞写 `montage.db` 审计；审计须异步。超时与 toast 风暴见 [`INCIDENT_SYSTEM_EVENT_OUTBOX_TIMEOUT.md`](INCIDENT_SYSTEM_EVENT_OUTBOX_TIMEOUT.md)。
- 唤醒后 `wake_settle_seconds`（默认 5）再复检。
- `will_power_off` 在关机过程中只暂停，不在断电中途自动恢复。
- **冷启动 / 进程重启后**：残留的 `power_off` hold 必须自愈清除（macOS 冷启动不会补发可匹配的 `did_wake`）；**同样清除**残留的 `system_sleep` / `screen_sleep`（引擎已在运行即表示机器未在休眠）；`did_wake` 同时可清 `system_sleep`+`screen_sleep`+`power_off`（macOS 经常不投递 `screens_wake`）；`session_active` 亦可清残留休眠 hold；人工 `POST /system/resume` 必须能清 `system` 持有的原因（不得因 owner=manual/system 不匹配而假恢复）。
- **路径恢复**：若仅因 path/disk 健康失败而 `PAUSED_BLOCKED`，App 轮询 `GET /system/pause-state` 时路径已恢复则自动重试恢复，避免插盘后仍永久停产。
- **generation_hint**：桌面端 outbox 世代陈旧时仍须能清当前 `system` hold（勿因 hint 不匹配永久 `applied=false`）。

---

## 4. 任务类别

### 可自动安全恢复

- 生产 Job（output 边界；系统暂停后 `paused_system` → 恢复 `queued`）
- Ingest watcher / 全库扫描（文件边界 + 取消令牌）
- 向量补齐 / 语义回填（batch / cliplet 边界）
- 日更 scheduler（tick 边界；恢复前须曾运行）
- 消息巡检 scheduler（取消当前扫描；不自动重放被取消账号）

### 中断后人工确认

- G5.V 自动上传、SEO/GEO 浏览器发布
- Ollama pull、载体同步写、App 更新原子替换阶段
- Cursor 流式任务

---

## 5. API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/system/events` | 需 `X-Suying-System-Token` |
| GET | `/system/pause-state` | 当前状态 |
| POST | `/system/pause` | 人工/诊断暂停 |
| POST | `/system/resume` | 仅清指定 reason |
| GET | `/system/events` | 审计 |
| GET | `/health` | 含 `runtime_state` / `pause_reasons` / `resume_blockers` |

暂停期间长任务写入口返回 **423**；查询/取消/健康/系统事件接口仍可用。

---

## 6. 配置（机器级）

```json
{
  "system_event_control": {
    "enabled": true,
    "pause_on_system_sleep": true,
    "pause_on_power_off": true,
    "pause_on_session_inactive": false,
    "pause_on_screen_sleep": false,
    "auto_resume_on_wake": true,
    "auto_resume_on_session_active": false,
    "wake_settle_seconds": 5,
    "quiesce_timeout_seconds": 15,
    "event_debounce_ms": 500,
    "resume_requires_path_health": true,
    "resume_requires_disk_health": true
  }
}
```

---

## 7. 验收

- 重复/乱序事件幂等；人工暂停经 sleep/wake 仍暂停。
- 拔盘后保持 `PAUSED_BLOCKED`。
- 发布中断不自动重发。
- **禁止**迟到 quiesce 在唤醒清 hold 后把运行时打成空原因的 `PAUSED_BLOCKED`；该类孤儿态须自愈回 `ACTIVE`（见 [`INCIDENT_PAUSE_ORPHAN_BLOCK.md`](INCIDENT_PAUSE_ORPHAN_BLOCK.md)）。
- 冒烟：`python3 scripts/smoke_system_events.py` + 相关单测。
- 真机：两轮睡眠/唤醒；熄屏/解锁开关；生产与发布中断。未完成人眼项标 `PARTIAL`。
