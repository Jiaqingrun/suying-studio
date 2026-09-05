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
| control_plane | HTTP 控制面 | `GET /health` 200（含 `status=starting`） |
| business_ready | 可生产 | `GET /readiness` → `ready=true` |

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
