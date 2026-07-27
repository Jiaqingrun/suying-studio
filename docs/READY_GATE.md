# 速影 · 成品库门禁（READY GATE）

> **效力：写死。2026-07-26 用户明确：逐项校验 → 全部达标才能进成品库(ready)，否则打回重做。**  
> **本文件 = 进 `02-成片/ready` 的总门禁权威。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 配置：`VIDEO_LOCK` · 代码：`engine/qc/ready_gate.py` · 拒绝点：`engine/jobs/worker.py`（`evaluate_ready_gate` 失败 → `failed`，不成 ready）

---

## 0. 原则

1. **Fail-closed**：任一项失败 → **不得**标 `ready`；状态为 `failed`（或既有 QC/一致性路径的 `review`），reasons 必须可读。  
2. **单一实现**：引擎入库与 Agent/批次脚本共用 `evaluate_ready_gate`（`scripts/qa_ready_montage.py` 为 CLI 包装）。  
3. **强制参考**：渲染加载 `VIDEO_LOCK`（标题黄字黑描边等）；入库跑本门禁。改阈值 / 跳过检查 = 必须用户当面批准。  
4. **打回重做**：失败成片进 `failed/`（或 `review/`），批次循环记 fails 后修规则再 `--resume`。

---

## 1. 检查项清单

| ID | 检查 | 验收标准 | 失败动作 |
|----|------|----------|----------|
| G.BASIC | 基础 QC | mp4 存在且够大；sidecar `qc.passed`；静音≤0.35；黑场≤0.08；有音轨；禁 mock TTS | 不成 ready |
| G.TITLE | 标题样式 | sidecar `meta.title_style`：`color=#FFE600`（黄字）、`stroke_color=#000000`（黑描边）、`stroke_width≥6`、`offset_y_px=120`（整体下移）；禁止红字黄描边 | 不成 ready |
| G.ALIGN | 字幕对齐 VO | cue end ≤ 话音结束（overhang≤0.08s）；不越过 VO；不重叠 | 不成 ready |
| G.MARGIN | 字幕边距 | 左右 margin≥48；像素级不得贴边裁切 | 不成 ready |
| G.BREATH | 短句呼吸 | cue/段 ≤22 字（锁 18）；`tts_rate=-8%` | 不成 ready |
| G.VOICE | 旁白锁 | Edge；`pitch=+35Hz`；`volume=+12%`；旁白正文无 emoji | 不成 ready |
| G.EMOJI | 表情 | 字幕行内有 emoji；已烧录；禁标题区贴纸；旁白不读；Ollama 旁白期望开时须有证据 | 不成 ready |
| G.BLUR | 画质 | sidecar clips 无 `rejected_blur`、无 quality_score&lt;0.35 | 不成 ready |
| G.SEMANTIC | 严格语义来源 | 任务启用 `strict_semantic_v1` 时，每个源必须是 `usable`、有 embedding、quality≥0.35 且通过 `suying.cliplet.semantic.v1`；禁止整片资产回退 | 不成 ready |

细则权威：

- 标题 / 片型 → `VIDEO_LOCK` + `engine/pack/video_lock.py`  
- 字幕对齐 / 边距 / 呼吸 → [`NARRATION_SUBTITLE_LOCK.md`](NARRATION_SUBTITLE_LOCK.md)  
- 表情 → [`EMOJI_STICKER_LOCK.md`](EMOJI_STICKER_LOCK.md)  
- 虚焦 → [`QUALITY_LOCK.md`](QUALITY_LOCK.md)

---

## 2. 代码锚点

| 环节 | 行为 |
|------|------|
| 加载锁 | `load_video_lock` 钳制标题黄字黑描边 + `offset_y_px=120` + quality/subtitle/voice/emoji |
| 规划标题 | `apply_lock_to_title_style`（强制参考） |
| 渲染标题 | `engine/render/ffmpeg.render_title_png`：`#FFE600` / `#000000` + `offset_y_px` |
| **拒绝 ready** | `JobWorker`：`evaluate_ready_gate(temp_out)` → `ok=False` → `final_state="failed"` |
| 批次 / CLI | `scripts/qa_ready_montage.py` → 同函数 |
| 冒烟 | `scripts/smoke_hard_locks.py` + `scripts/smoke_ready_gate.py` |

---

## 3. 运维命令

```bash
python3 scripts/smoke_ready_gate.py
python3 scripts/qa_ready_montage.py /path/to/montage_XX.mp4
python3 scripts/suying_batch10_qa_loop.py --resume --target 10
```

---

## 4. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-07-26 | 用户：全套审核门禁写死；标题改为黄字黑描边；建本文件 + `ready_gate` 入库强制 |
| 2026-07-26 | 用户：标题整体下移 120px → `title.offset_y_px=120` 写死 |
