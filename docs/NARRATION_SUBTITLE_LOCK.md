# 旁白 × 字幕对齐锁（强制）

> **效力：写死。2026-07-26 用户明确：拟人化旁白可以，但「话说完了字幕还在」必须修；同日「以上规则全部写死」。**  
> **违反本文件 = 不允许进 ready / 不允许标 ✅。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 配套：`VIDEO_LOCK.json` · `tts.py` · `voice_subtitle.py` · `subtitles_burn.py` · [`SEMANTIC_OBJECT_VECTOR_LOCK.md`](SEMANTIC_OBJECT_VECTOR_LOCK.md)

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
---

## 1. 硬规则（不可协商）

| # | 规则 | 验收 |
|---|------|------|
| H1 | **字幕结束 ≤ 该句话音结束** | 句末静音段不得仍显示上一句字幕 |
| H2 | **句间静音 = 空屏** | `inter_sentence_gap` / oneshot 内停顿期间无字幕 |
| H3 | **时间轴以真实 VO 床为准** | oneshot 用绝对 `start_sec/end_sec`；写盘前 `tighten_srt_to_voiceover` |
| H4 | **禁止把「到下一句开始」当成字幕时长** | 旧 bug：`cue_dur = next_span.start - this.start`（含静音）已废止 |
| H5 | **tail_trim ≥ 0.08s（默认 0.12s）** | 字幕略早于话音收尾，避免拖尾观感 |
| H6 | **拟人化不得破坏对齐** | Ollama 改文案后仍走同一 TTS→SRT→tighten 链路 |
| H7 | **字幕不得贴边裁切** | 左右 `side_margin_px≥48`；CJK 按像素宽换行；禁止满幅 flush（Job87） |
| H8 | **旁白必须短句呼吸** | 先句号/断句再去标点；每呼吸 ≤18 字；Edge `rate=-8%`（Job89） |
| **H9** | **旁白字幕只烧一次** | Worker 已 `subtitle_burned` 的成片，`export_publish_pack` **禁止**再 `burn_srt_into_video`；复用成片 SRT/VO；`video.burned.mp4` = 拷贝已烧成片。二次烧录 = 字幕堆叠，一律视为事故 |
| **H10** | **旁白字幕冻结样式与安全几何** | portrait 默认底部 420px、安全范围 240–620px；landscape 默认 180px、安全范围 100–360px。颜色/描边/字号/对齐可由分画幅规则配置，但最终值必须等于 Job 冻结规则且不越界 |
| **H11** | **burn_mono 字幕语言 = 旁白语言** | `subtitle_burn=burn_mono` 时规则层强制 `subtitle_lang=voice_lang`；成片烧录文案必须来自真实 VO/SRT，**禁止**用品牌短循环模板顶替。双语仅允许 `burn_dual` |

H10 只控制显示样式与几何，**不改变 H1–H9 / H11 的旁白×字幕时间对齐、语言对齐、只烧一次、呼吸与留白规则**。禁止为满足位置而重算 cue 时间、合并句间空屏或生成第二套 SRT。

---

## 2. 正确时间模型

```text
VO 床:  [====句1====]....[====句2====]....[====句3====]
字幕:   [====句1==]      [====句2==]      [====句3==]
              ↑ trim           ↑ 空屏
```

- **说话区间**：silence detect / 分句 trim 得到的 speech span  
- **字幕区间**：`[speech_start, speech_end - tail_trim]`  
- **空档**：speech_end → next_speech_start（无 cue）

---

## 3. 实现落点

| 模块 | 职责 |
|------|------|
| `text_sanitize.ensure_zh_speech_breaks` | 无「。」长串 → 短句；`split_then_strip_sentences` |
| `tts.synthesize_script` oneshot | 先断句再 strip；`rate/pitch` 写入 manifest；绝对时间 |
| `tts` per-sentence | `_trim_wav_trailing_silence` 去掉 TTS 尾静音后再 concat |
| `srt_from_narration_segments` | 优先绝对时间；gap 不并入 cue end |
| `tighten_srt_to_voiceover` | 对照 VO 床再夹一次 end（核保险） |
| `subtitles_burn._wrap_cjk_line` | 像素宽换行 + side margin；无背景条 |
| `voice_subtitle.prepare_narration_for_plan` | 强制 tighten；zh 强制 lock rate |
| `export_publish_pack` | **H9**：`subtitle_burned` 则复用成片 SRT/VO，禁止二次烧录 |
| `VIDEO_LOCK.subtitle` | `side_margin_px` / `forbid_edge_clip` / align 字段 |
| `VIDEO_LOCK.voice/narration` | `rate=-8%` / `max_chars_per_breath=18` |
| GSemanticOps 渲染实现 | 按横/竖画幅冻结规则校准最终字形底边；不得用 ASS baseline/margin 近似值报告通过 |

---

## 4. 深度优化原则（拟人 + 对齐）

1. **拟人看口气，对齐看波形** — Ollama 只改「说什么」；「何时灭字」只信音频。  
2. **少字不如准时** — 宁可 cue 略短，不可拖过停顿。  
3. **断句服务呼吸** — Edge oneshot 保留句号停顿；字幕在停顿处让画面说话。  
4. **一句一意** — 文案分句与 TTS 呼吸一致，避免一字多 cue 漂移。  
5. **烧录前后同一 SRT** — burn_mono 不得另造估计时轴或品牌短模板；双语才用 burn_dual。  
6. **先断句、再去标点** — `strip_all_punctuation` 只影响显示/chunk 正文，不得发生在 split 之前。  
7. **字幕留白** — 颜色和描边按冻结规则；禁止贴左右边框；默认无半透明底条。
8. **只烧一次** — Worker 烧进成片后，publish_pack 只拷贝，不得再用另一套旁白文案叠烧。
9. **位置不改时轴** — 字幕样式与字形底部距离按冻结规则；几何调整不得触碰 VO/SRT 对齐结果。
10. **mono 语言对齐** — `burn_mono` 下 `subtitle_lang` 必须等于 `voice_lang`（规则 clamp + 渲染侧强制用 VO SRT）。
11. **文案优先画面、禁止模板注水**（2026-08-08）— 底稿装配与 Ollama 改写优先 visual hints / 行业 `narration_lines`；画面稀疏时降 fill ratio，禁止用「现场记录体」套话撑满时长；近 N 条开场去重。不改变 H1–H11 对齐与呼吸。
12. **阶段 B 文案口碑锁**（2026-08-08 用户人耳认可）—
    - **禁口水词**（整条）：你看 / 真的 / 就是这样 / 赶紧(看) / 看着就踏实 / 等扩展表（`ORAL_FILLER_BANS`）。
    - **装车/仓配**：连贯过程旁白（逗号串动作、略温情：忙碌/仔细/服务用心），**禁止电报碎句**；仍禁口水词。
    - **商品特写**（`product_display`）：商业品类+用途速览，禁包装盒/字样/盒内构图解说，禁装车发货叙事；收尾略带购买向（例：看中的到店直接拿），禁保证/第一/厂家直销/马上发货。
    - **生产音色**：Edge 晓晓；不改 VIDEO_LOCK 冻结字段、不动 L19 clone。
13. **运镜≠口播 + 价值句说人话**（2026-08-08 用户纠偏，**通用全部片型**）—
    - 关键词包 / 配方里的**拍摄说明**（「镜头从整体环境切到局部细节」「不同镜头对应不同现场环节」等）只指导选片/结构，**禁止念出口播**（`script_has_camera_direction` / `to_spoken_line` 丢弃）。
    - 问卷腔「可见细节帮助进一步确认需求」→ 服务完整句 **「我们展示细节来满足您的需求」**；禁止把句读拆成「可见｜细节帮助｜进一步确认需求」式电报腔。
    - 底稿与 Ollama 对「可用组件」一律先 `filter_grounding_for_speech`；冲突时以本原则与 `NARRATION_SUBTITLE_LOCK` 为准。
14. **商品/服务介绍 ≠ 画面描写腔**（2026-08-08 用户纠偏 Job291）—
    - 口播目标是**介绍在售商品或现场服务**，不是解说视频构图、主体位置、色块、背景车辆等。
    - 严禁：「画面主体为…」「视频展示了…」「背景停放/背景为…」「容器内部为…」「静止放置」「可见中心搅拌结构」及同类（`VIDEO_META_SPEAK_MARKERS` / `script_has_video_meta_speak`）。
    - 机器视觉 caption 仅作**点货线索**：经 `commercial_product_lines_from_hints` / `to_spoken_line` 抽用途句；**禁止**把长 CV 原文挂进 SRT。
    - 文案须与镜头里真实货品/服务动作对齐，避免「口播在说仓配、画面在摆桌面钻头」的错位。
15. **商品成片纯商品镜 + 时间轴点货**（2026-08-08 Job300 纠偏）—
    - `stable-product`：**硬偏好** `product_closeup`，排除 `warehouse/loading/delivery`；**禁止整片无 cliplet 回退**（避免「五立方/装车」混入商品旁白）。
    - 商品口播按 **镜头顺序** 从每镜 description 抽用途句，不shuffle；禁装车过程句与商品镜同编。

---

## 5. 冒烟

```bash
python3 scripts/smoke_subtitle_align.py
python3 scripts/smoke_subtitle_margin.py
python3 scripts/smoke_narration_breath.py
```

失败条件（任一）：

- 任一 cue 的 end 落在 VO 静音区间超过 80ms  
- cue_i.end > cue_{i+1}.start  
- 最后 cue.end > VO duration  
- cue PNG 左右边缘有不透明像素（贴边裁切）  
- zh 呼吸句过长（单句 >20 字）且未重断句  
- **`subtitle_burned=true` 的成片在出包时再次调用 `burn_srt_into_video`（字幕堆叠）**
- 最终帧字幕样式/字形底部距离与 Job 冻结规则不一致，或超出对应画幅安全范围

---

## 6. 锁变更记录

| 日期 | 变更 |
|------|------|
| 2026-07-26 | 用户强制：旁白字幕必须对齐；废止「含句间静音的 cue 时长」；默认 tail_trim 0.04→0.12；加 tighten 核保险与冒烟 |
| 2026-07-26 | Job87：字幕贴边裁切 → `side_margin_px=48` + CJK 像素换行；Job89：无句号连读 → `ensure_zh_speech_breaks` + 强制 `rate=-8%` |
| 2026-07-26 | B10：修复 `ollama_narration.py` 提示词 SyntaxError（否则 Ollama 整段静默跳过） |
| 2026-07-29 | **H9**：成片已烧旁白字幕时 publish_pack 禁止二次烧录（事故：五条成片字幕堆叠） |
| 2026-07-30 | **H10 文档开闸**：字幕白字黑描边，1080×1920 最终字形底边距底 420px；H1–H9 对齐规则保持不变，代码/真机验收仍为 GSO.4 TODO |
| 2026-08-01 | 用户批准规则实验室与双画幅：H10 从固定白/黑/420px 改为横/竖安全范围和 Job 冻结值验收；H1–H9 不变 |
| 2026-08-02 | **H11**：客户机 burn_mono + `voice=zh`/`subtitle=zh-TW` 烧出品牌繁体短句 ≠ 口播；规则 clamp + 渲染强制 VO SRT；见 [`INCIDENT_TITLE_SUBTITLE_MISMATCH.md`](INCIDENT_TITLE_SUBTITLE_MISMATCH.md) |
| 2026-08-08 | 文案侧：行业 narration_lines + 近作开场去重 + Ollama 禁套话/画面命中；稀疏不注水（原则 11）|
| 2026-08-08 | 阶段 B 人耳锁：商品「更卖」+ 仓配连贯过程口播（原则 12）；待仓库引擎产 ready 抽验 |
| 2026-08-08 | **原则 13**：运镜指导不入口播；价值句服务向（例：我们展示细节来满足您的需求）；词包/配方先 `to_spoken_line` |
| 2026-08-08 | **原则 14**：禁「画面主体为」等构图描写腔；vision 只点货；口播=商品/服务介绍 |
| 2026-08-08 | **原则 15**：stable-product 纯商品镜（禁仓配整片回退）+ 按镜头顺序点货 |
