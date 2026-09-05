# 速影 · 成品库门禁（READY GATE）

> **效力：写死。2026-07-26 用户明确：逐项校验 → 全部达标才能进成品库(ready)，否则打回重做。**  
> **本文件 = 进 `02-成片/ready` 的总门禁权威。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 配置：`VIDEO_LOCK` · 代码：`engine/qc/ready_gate.py` · 拒绝点：`engine/jobs/worker.py`（`evaluate_ready_gate` 失败 → `failed`，不成 ready）

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
---

## 0. 原则

1. **Fail-closed**：任一项失败 → **不得**标 `ready`；状态为 `failed`（或既有 QC/一致性路径的 `review`），reasons 必须可读。  
2. **单一实现**：引擎入库与 Agent/批次脚本共用 `evaluate_ready_gate`（`scripts/qa_ready_montage.py` 为 CLI 包装）。  
3. **强制参考**：渲染加载画幅规则与 `VIDEO_LOCK` 安全底线；入库按 Job 冻结样式跑本门禁。改安全范围 / 跳过检查 = 必须用户当面批准。
4. **打回重做**：失败成片进 `failed/`（或 `review/`），批次循环记 fails 后修规则再 `--resume`。

---

## 1. 检查项清单

| ID | 检查 | 验收标准 | 失败动作 |
|----|------|----------|----------|
| G.BASIC | 基础 QC | mp4 存在且够大；sidecar `qc.passed`；静音≤0.35；黑场≤0.08；有音轨；禁 mock TTS | 不成 ready |
| G.CANVAS | 双画幅 | Job 冻结 portrait 时必须 1080×1920；landscape 时必须 1920×1080；禁止按源分辨率漂移 | 不成 ready |
| G.TITLE | 标题样式 | 颜色、描边、字号、对齐、特效与最终 `title_bbox.top` 必须等于 Job 冻结规则；portrait 顶部安全范围 120–420px，landscape 60–260px；文字不得越界 | 不成 ready |
| G.SUBSTYLE | 字幕样式 | 颜色、描边、字号、对齐与最终底部距离必须等于冻结规则；portrait 240–620px，landscape 100–360px；仍须只烧一次 | 不成 ready |
| G.ALIGN | 字幕对齐 VO | cue end ≤ 话音结束（overhang≤0.08s）；不越过 VO；不重叠 | 不成 ready |
| G.MARGIN | 字幕边距 | 左右 margin≥48；像素级不得贴边裁切 | 不成 ready |
| G.BREATH | 短句呼吸 | **中文 cue/段** ≤22 字（锁 18）；`tts_rate=-8%`。`burn_dual` 以外语为首行时只量 CJK 行，禁止把阿语等误套 22 字 | 不成 ready |
| G.VOICE | 旁白锁 | Edge；`pitch=+35Hz`；`volume=+12%`；旁白正文无 emoji | 不成 ready |
| G.EMOJI | 表情 | 字幕行内有 emoji；已烧录；禁标题区贴纸；旁白不读；Ollama 旁白期望开时须有证据 | 不成 ready |
| G.BLUR | 画质 | sidecar clips 无 `rejected_blur`、无 quality_score&lt;0.35 | 不成 ready |
| G.SEMANTIC | 严格语义来源 | 任务启用 `strict_semantic_v1` 时，每个源必须是 `usable`、有 embedding、quality≥0.35 且通过 `suying.cliplet.semantic.v1`；禁止整片资产回退 | 不成 ready |
| G.DURATION | 成片×旁白时长 | 有旁白 wav 时：成片 duration **严格大于** 旁白 duration（至少 +0.05s）；失败码 `duration: video_must_exceed_narration` | 不成 ready |

细则权威：

- 标题 / 片型 → `VIDEO_LOCK` + `engine/pack/video_lock.py`  
- 字幕对齐 / 边距 / 呼吸 → [`NARRATION_SUBTITLE_LOCK.md`](NARRATION_SUBTITLE_LOCK.md)  
- 表情 → [`EMOJI_STICKER_LOCK.md`](EMOJI_STICKER_LOCK.md)  
- 虚焦 → [`QUALITY_LOCK.md`](QUALITY_LOCK.md)

---

## 2. 代码锚点

| 环节 | 行为 |
|------|------|
| 加载锁 | `load_video_lock` + `rule_schema.validate_and_clamp` 钳制横/竖安全范围与 quality/subtitle/voice/emoji |
| 规划标题 | `resolve_title_style`：`apply_lock` 后叠 Job 冻结规则（跟镜与日常同合同） |
| 渲染标题 | `engine/render/ffmpeg.render_title_png`：消费冻结颜色/描边/绝对顶部距离/对齐/淡入；单句≤24字 |
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
| 2026-07-30 | 用户批准 GSemanticOps：旧相对 offset 口径由最终字形顶边 220px 取代；字幕字形底边距底 420px |
| 2026-08-01 | 用户明确批准双画幅和规则实验室：固定样式值改为分画幅安全范围 + Job 冻结值精确验收；画质、对齐、只烧一次与成片>旁白继续写死 |
| 2026-07-31 | GCustomerUX：成片时长必须严格大于完整旁白（≥+0.05s）；HARD L15 |
