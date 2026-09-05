# INCIDENT · 旁白改写假死占 ollama_heavy / TTS / render 槽

> 状态：**部分修复（0.6.18）+ 根因续修进行中（签名覆盖安装 / 可取消网关 / 机器级熔断）**。  
> 时间：2026-08-03/04 · 客户机 `xlf-remote` · 任务 `#592` / `#598` 及后续 `#600–619`。  
> 冲突时以 [`DEV_LOCK.md`](DEV_LOCK.md) §E 的 `CUX.SLOT_LEASE` 为准。

## 现象

1. UI 长期「生产中 · 旁白改写开始（Ollama）」· 0/N。
2. `ops/resource-gate`：`render` / `tts` / `ollama_heavy` 占满或租约过期后仍假绿。
3. `/api/tags` 可达，但 `/api/chat` 约 60s 返回 499；本机 `ollama ps` 空；日志刷 `llama runner process no longer running` / `signal: killed`。
4. `~/Library/Logs/DiagnosticReports/ollama-*.ips`：`Code Signature Invalid` / `Taskgated Invalid Signature`。

## 根因链（分层）

### A. 已由 0.6.18 覆盖的槽位假死

1. Worker 过早占 TTS；改写挂死 = TTS+render 全机堵。
2. 墙钟只放 `ollama_heavy`，`ThreadPoolExecutor` 超时后 **httpx 线程仍可能继续跑**。
3. `reap_stale_running_jobs` 曾跳过当前 held job。

### B. 0.6.18 **未**消除的主根因（2026-08-04 真机确认）

1. **远程安装用 `cp` 覆盖正在运行的受管 Ollama 二进制**（`~/Suying/runtime/tools/bin/ollama`）。
2. 主进程仍响应 `/api/tags`（假绿），但启动 runner 时被 macOS **SIGKILL（签名无效）**。
3. 旁白墙钟 90s → Job `consecutive_ollama_infra` → 批量 `circuit_open`，形成重试风暴。
4. 健康检查只看 tags，不区分「服务可达 / 模型存在 / 推理可用 / 熔断中」。

## 现场止血（2026-08-04 · 不冒充正式交付）

证据目录：`~/Suying/backups/incident-narration-20260804-014259/`。

1. 封存 db / install / crash / logs；暂停 616–619。
2. `codesign --verify --strict` 通过后 `launchctl kickstart -k`（**不**覆盖运行中文件）。
3. 功能探针：`/api/chat`（think=false,num_predict=8）与 embed 均 200。
4. 单条 Job #615：Ollama→TTS→FFmpeg 通路恢复；READY_GATE 因内容 `breath: cue too long` 质量熔断（非签名基建）。

## 彻底修复（代码目标态 · 复用 CUX.SLOT_LEASE，不新开 Gate）

| 项 | 状态 |
|----|------|
| [`scripts/install-ollama-atomic.sh`](../scripts/install-ollama-atomic.sh) 原子替换 + 签名 + 回滚 | 已进仓 |
| [`scripts/remote-install.sh`](../scripts/remote-install.sh) 禁止 cp 覆盖运行中二进制 | 已进仓 |
| [`engine/catalog/ollama_runtime.py`](../engine/catalog/ollama_runtime.py) 可取消子进程 + 统一 heavy + 机器级熔断 | 已进仓 |
| 旁白/视觉/向量/规则共用网关 | 已进仓 |
| `/health/ollama` 四层状态 + App 运维区分假绿 | 已进仓 |
| trigger 原子领取 + occurrence/Job 幂等 | 已进仓 |
| 正式 `release-to-t2s` + `deploy-remote` 真机故障注入验收 | **待做** |

## 相关

- [`INCIDENT_OLLAMA_NARRATION_IDLE.md`](INCIDENT_OLLAMA_NARRATION_IDLE.md)
- [`INCIDENT_STUCK_JOB_RENDER_SLOT.md`](INCIDENT_STUCK_JOB_RENDER_SLOT.md)
- [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md)

## 验收

- [x] 0.6.18 租约/TTS 后置/墙钟释槽（槽位假死 A 层）
- [x] 2026-08-04 客户机止血：签名重启 + chat/embed 探针 + 单条通路
- [x] 原子安装 / 可取消网关 / 机器熔断 / 调度幂等 **代码+单测**
- [x] 正式补丁 **0.6.19** → T2S `release_seq=23` → `xlf-remote` 覆盖：原子安装脚本执行、chat 功能探针 OK、无新增 Code Signature Invalid、资源槽归零
- [ ] （可选跟进）内容质检 `breath: cue too long` 导致 READY 打回——与签名基建根因分离
