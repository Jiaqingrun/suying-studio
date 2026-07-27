# 旁白 × 字幕对齐锁（强制）

> **效力：写死。2026-07-26 用户明确：拟人化旁白可以，但「话说完了字幕还在」必须修；同日「以上规则全部写死」。**  
> **违反本文件 = 不允许进 ready / 不允许标 ✅。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 配套：`VIDEO_LOCK.json` · `tts.py` · `voice_subtitle.py` · `subtitles_burn.py`

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
| `VIDEO_LOCK.subtitle` | `side_margin_px` / `forbid_edge_clip` / align 字段 |
| `VIDEO_LOCK.voice/narration` | `rate=-8%` / `max_chars_per_breath=18` |

---

## 4. 深度优化原则（拟人 + 对齐）

1. **拟人看口气，对齐看波形** — Ollama 只改「说什么」；「何时灭字」只信音频。  
2. **少字不如准时** — 宁可 cue 略短，不可拖过停顿。  
3. **断句服务呼吸** — Edge oneshot 保留句号停顿；字幕在停顿处让画面说话。  
4. **一句一意** — 文案分句与 TTS 呼吸一致，避免一字多 cue 漂移。  
5. **烧录前后同一 SRT** — burn_mono/dual 不得另造估计时轴。  
6. **先断句、再去标点** — `strip_all_punctuation` 只影响显示/chunk 正文，不得发生在 split 之前。  
7. **字幕留白** — 白字黑描边即可；禁止贴左右边框；禁止半透明底条。

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

---

## 6. 锁变更记录

| 日期 | 变更 |
|------|------|
| 2026-07-26 | 用户强制：旁白字幕必须对齐；废止「含句间静音的 cue 时长」；默认 tail_trim 0.04→0.12；加 tighten 核保险与冒烟 |
| 2026-07-26 | Job87：字幕贴边裁切 → `side_margin_px=48` + CJK 像素换行；Job89：无句号连读 → `ensure_zh_speech_breaks` + 强制 `rate=-8%` |
| 2026-07-26 | B10：修复 `ollama_narration.py` 提示词 SyntaxError（否则 Ollama 整段静默跳过） |
