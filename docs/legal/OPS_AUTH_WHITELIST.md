# PL-07 · Ops 写面鉴权（方案 A · 偏温和）

> **状态**：已落地偏温和默认 · **是否收紧待真人确认**  
> **约束**：禁止把日常生产按钮锁死；禁止把 Ops-Token 打进 Git/App 源码。  
> 实现：`engine/api/ops_auth.py`

## 1. 写接口白名单（须 Ops-Token，除非开发旁路）

仅下列**高危写面**调用 `require_ops_token`（方案 A：不扩到全部 `POST /ops/*`）：

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/ops/resource-gate/force-release` | 强制释放资源槽 |
| POST | `/ops/backups` | 手动备份 |
| PUT | `/ops/backups/policy` | 备份策略 |
| POST | `/ops/backups/prune` | 修剪备份 |
| PUT | `/ops/published-cleanup/policy` | 已发布清理策略 |
| POST | `/ops/published-cleanup` | 执行已发布清理 |
| PUT | `/ops/disk-cleanup/policy` | 磁盘清理策略 |
| POST | `/ops/disk-cleanup/run` | 执行磁盘清理 |

**不在白名单内的**运维/生产常用写接口（如服务启停、调度 run-now、carrier、更新安装、ollama recover 等）**不**要求 Ops-Token，避免锁死日常生产按钮。

许可 / 密钥类破坏性操作若另有专用门禁，保持其既有路径，不借本名单「一刀切」扩面。

## 2. 本地开发旁路（须文档化）

满足任一即跳过 Token 校验：

1. 环境变量 **`SUYING_OPS_DEV=1`**（或 `true` / `yes`）  
2. 请求客户端地址为 **loopback**（`127.0.0.1` / `::1` / `localhost` / `127.*`）

> 引擎默认绑 `127.0.0.1`，故本机 App / 本机 curl 在偏温和默认下**可免 Token** 调用上表白名单写面。  
> App「高级密码」仍可锁 UI 入口；API 层是否与 UI 同严 **待真人确认是否收紧**。

**禁止**：静默永久关闭鉴权且不写进文档；把 token / 密码写入仓库。

## 3. 验收

- 白名单外：日常生产按钮不因缺 Token 而 401/403。  
- 非 loopback 且未设 `SUYING_OPS_DEV`：白名单写面无正确 Token → 403（或 token 文件缺失 → 503）。  
- 收紧选项（待裁）：取消裸 localhost 旁路，仅保留 `SUYING_OPS_DEV=1`；或改为方案 C（分组豁免）。

## 4. 待真人确认

- [ ] 是否取消「裸 localhost 旁路」，仅保留 `SUYING_OPS_DEV=1`  
- [ ] 是否将更多破坏性路径纳入白名单（仍禁止锁死日常生产按钮）  
- [ ] 是否升级为方案 B/C
