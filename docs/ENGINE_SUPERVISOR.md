# 速影 · 引擎常驻与监督（Engine Supervisor）

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。

## 目标

关 App、注销再登、引擎进程被杀后：**8766 仍由 LaunchAgent 自愈**；UI 不再把「工作区未挂载 / 暂停」说成「引擎掉线」。

## 权威模型

| 角色 | 职责 |
|------|------|
| `com.qr.suying.engine` | RunAtLoad + KeepAlive；唯一长期权威 |
| 桌面 App | 探测 / kickstart / 安装 agent；**不**默认持有长期子进程 |
| 开发机 | 无 agent 时可 `spawn` 源码引擎（`scripts/start-engine.sh`） |

## 三态探针

| 层 | 含义 | 探针 |
|----|------|------|
| listen | 端口 LISTEN | `lsof` / supervisor `listen` |
| control_plane | HTTP 控制面 | 桌面 kickstart **与** `engine_status.control_plane` 均用 `GET /readiness`（须亚秒）；勿用慢 `/health` 判定控制面 |
| business_ready | 可生产 | `GET /readiness` → `ready=true`（工作区详情仍可读 `GET /health`） |

### `/health` / `/readiness` 与 Ollama（防卡死）

- `GET /health` **不得**同步等待 Ollama HTTP。Ollama 块用进程内缓存，后台刷新。
- `GET /readiness`（桌面 kickstart，约 800ms 预算）同样不得调用同步 `check_ollama()`；旁白模型是否缺失只读缓存。
- macOS 睡眠/唤醒后 Homebrew/App Ollama 可能停在 `STAT=T` 仍占 `11434`，TCP 会黑洞到客户端超时。引擎会：
  1. 对挂起的 ollama 进程发 `SIGCONT`（不杀外部进程）
  2. 唤醒路径与 scheduler tick 自动巡检
  3. 功能探针失败前先 `ensure_ollama_listener_responsive`
- 细则：`engine/ops/ollama_service.py` · `engine/catalog/ollama_status.py` · `engine/api/readiness.py`

相关：

- Rust：`apps/desktop/src-tauri/src/engine_supervisor.rs`、`engine_status` 扩展字段  
- 引擎：`GET /ops/engine-supervisor`、`engine/runtime/boot_state.py`  
- 安装：`scripts/install-engine-agent.sh`（`install` / `kickstart` / `status`）  
- 验收：`scripts/check-engine-supervisor.sh`

## 启动序

1. 进程启动 → **尽快** `uvicorn` 接受连接；`/health` 在服务未完成前返回 `status=starting`。  
2. 后台线程：init_db、词池 seed、worker/scheduler/watcher。  
3. 完成 → `boot_phase=ready` 或工作区缺失时 `blocked`。  
4. 许可：常驻默认 `SUYING_ALLOW_CACHED_LICENSE_BINDING=1` + 磁盘 `device-key-id`。

## 离线 class（`offline_class`）

`ok` · `starting` · `control_plane_not_ready` · `not_listening` · `no_agent` · `license_cache_missing` · `license` · `workspace` · `integrity` · …

## 运维动作

```bash
bash scripts/install-engine-agent.sh install     # 写 plist + wrapper + 起服务
bash scripts/install-engine-agent.sh kickstart   # 重启 agent
bash scripts/install-engine-agent.sh status
bash scripts/check-engine-supervisor.sh
```

交付：`remote-install` 在已有许可证缓存时 **禁止** agent 安装软失败。

## 明确不做

- 不因 App「停止设备服务」杀掉 LaunchAgent 权威进程（仅清理 App 子进程）。  
- 不把热补 `.py` 当交付；完整性失败须重装包。
