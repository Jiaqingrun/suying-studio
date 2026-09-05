# INCIDENT · 生产空转：Ollama 旁白冷启超时后整轮重渲

> 状态：**已堵死空转路径并交付** · App **0.6.16** · T2S `release_seq=20` · 客户机 `xlf-remote` 已 `--skip-models` 覆盖。  
> 时间：2026-08-03 · 客户机 `xlf-remote` · 任务 `#585` 等（`produced=0` 长时间重做）

## 现象

1. 任务长时间 `running`，事件停在「规划选片完成」附近，客户以为卡在选片。
2. 旁白日志反复 `timed out` / `ollama_narration=false`；READY_GATE `require_ollama` 打回。
3. Worker 仍用底稿走完 TTS + ffmpeg，再失败重做，单轮约 1.5–2 分钟 → 体感死机（空转）。

## 根因链

1. **`keep_alive=0`**：旁白与 embed 共用卸载策略；`qwen3.5:9b` 每条冷加载，易触达读超时。
2. **（已修）`think` 默认开**：content 空、token 烧在 thinking → 假失败（0.6.15）。
3. **失败不早停**：`prepare_narration` 旁白失败后仍 TTS+整轮渲染；READY 再打回，直到 `quality_circuit_threshold`（默认 5）。

## 彻底修复（本对话）

| 项 | 改动 |
|----|------|
| `engine/catalog/ollama_runtime.py` | `OLLAMA_NARRATION_KEEP_ALIVE="30m"`（embed 仍为 0） |
| `engine/pack/ollama_narration.py` | `think:false` + `num_predict` + 超时/空 content **重试 1 次** + 占 `ollama_heavy`；`infra` 标记 |
| `engine/render/voice_subtitle.py` | 旁白失败 **立即 return**，禁止底稿继续 TTS |
| `engine/jobs/worker.py` | 「旁白改写开始/完成」事件；失败跳过渲染；基建连续 **2 次** → `circuit_open` |
| `engine/ops/install_profile.py` | 旁白默认档改为 `qwen3.5:9b` |
| 单测 | `tests/test_ollama_narration_idle.py` |

## 运维注意

- 客户机旁白模型保持 **`qwen3.5:9b`**；覆盖升级后若被安装计划冲回旧值，再 `PUT /settings` 写回。
- 熔断后：检查 Ollama LaunchAgent / `ollama ps`，预热旁白模型，再 resume / 新开任务。
- 相关：[`INCIDENT_OLLAMA_EMBED_HANG.md`](INCIDENT_OLLAMA_EMBED_HANG.md) · [`READY_GATE.md`](READY_GATE.md)

## 验收

- [x] 单测 `test_ollama_narration_idle` 绿
- [x] 冒烟 `scripts/smoke_test.py` 绿
- [x] 一体包 **0.6.16** → T2S `release_seq=20` → 本机 + `xlf-remote` 覆盖（`--skip-models`）
- [ ] 客户机人眼：单条 fast-ship 一次过；人为停 Ollama 时应快速熔断且**不**整轮空渲
