# INCIDENT · 生产卡住：Ollama embeddings 挂死 + 字幕空字形

> 状态：现场已出片；**0.6.3 已 `--skip-models` 推客户机**（含超时/心跳/空字幕修复）。  
> 时间：2026-08-02 · 客户机 `xlf-remote` · 任务 `#511` → 修复包 `0.6.3`

## 现象

1. `#510/#511` 长时间 `running phase=producing`，仅有「开始处理任务」，占 `render` 槽。
2. `POST /api/embeddings`（`nomic-embed-text`）**0 字节超时**；`/api/ps` 空；`ollama.log` 大量 `llama runner … signal: killed`。
3. 重启 Ollama 并预热 embedding 后任务继续，中间两次 `ValueError: 字幕 PNG 没有可见字形`，最终 `#511 completed produced=1`，成片 ready。

## 成片

- 路径：`…/02-成片/ready/2026-08-02/montage_511_748473124.mp4`（约 16.6MB）
- READY_GATE 通过；publish_pack ready；serial `SY-beijing-shifeng-weiye-20260802-000059`

## 根因

1. **语义选片依赖 Ollama embed**：`build_plan` → `search_cliplets` → `embed_text` 默认 60s；Ollama 挂死时多槽位多次请求会把任务钉死数分钟，且 pause 不释放持槽。
2. **字幕 Twemoji 路径**：失败时仍可能写出全透明 PNG（体积可 >200B）并返回成功，随后 `_crop_to_alpha` 报「没有可见字形」；空 cue / 空白 cue 也会踩中。

## 本机修复（本对话）

| 项 | 改动 |
|----|------|
| `engine/catalog/vector_index.py` | 非 strict embed：connect≤3s、read 默认 8s；超时走 hash_fallback |
| `engine/jobs/worker.py` | 「规划选片开始/完成」心跳事件 |
| `engine/render/subtitles_burn.py` | 拒空 cue；Twemoji 以 `getbbox` 判成功；烧录跳过空白 cue；错误带原文 |

## 现场止血

1. 重启 Ollama（`~/Suying/runtime/tools/bin/ollama serve`）并 warmup embeddings  
2. 重启 App/引擎清槽；resume `#511`  
3. 相关：[`INCIDENT_STUCK_JOB_RENDER_SLOT.md`](INCIDENT_STUCK_JOB_RENDER_SLOT.md)

## 本机修复（规则与模型稳态）

| 项 | 改动 |
|----|------|
| `engine/catalog/vector_index.py` | 非 strict 短超时 + hash 降级；hash/Ollama 向量禁止混算 cosine |
| `engine/catalog/ollama_runtime.py` | embedding 单槽、熔断、功能探针、`embed_gateway_snapshot` |
| `engine/ops/ollama_service.py` | 11434 所有权探测、外部进程 install gate、有界 kickstart |
| `scripts/install-ollama-agent.sh` | 用户级 `com.qr.suying.ollama` LaunchAgent |
| `engine/api/readiness.py` | `/readiness` 区分存活与业务可用 |
| `engine/jobs/queue.py` | 正式生产必须启用规则；禁止历史规则 ID 与任务级 expression 覆盖 |
| `engine/jobs/worker.py` | 规划选片心跳 |
| `engine/render/subtitles_burn.py` | 空 cue / Twemoji 字形修复 |

## 待办

- [ ] 客户机验证：受管 LaunchAgent + embed 网关 + 规则唯一源一并覆盖升级
- [ ] pause 协作退出：打断持槽中的 `_process_job`（resource_gate 部分已有）
