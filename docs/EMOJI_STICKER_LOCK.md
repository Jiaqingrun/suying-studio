# 表情 × 字幕锁（强制 · 已修订）

> **效力：写死。2026-07-26 用户：表情包不要读进旁白；字幕中出现表情；删除标题附近表情包；同日批次 10 条严检。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md)

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
---

## 1. 硬规则（不可协商）

| # | 规则 | 验收 |
|---|------|------|
| E1 | **旁白禁止任何 emoji** | `strip_emoji_for_speech` 后 TTS；`narration_script` 无表情 |
| E2 | **禁止口播念「表情包/emoji」** | 文案与 TTS 不含该类词 |
| E3 | **表情必须出现在字幕里** | SRT cue 含 emoji；烧录后成片字幕行可见彩色 Twemoji（**任意** `subtitle_stroke_width` / 色值） |
| E4 | **禁止标题附近浮动贴纸** | `emoji_burned=false`；标题区无圆盘贴纸 |
| E5 | **emoji 只经 `emoji_cues` → 注入 SRT** | Ollama：`script` 纯文字，`emoji_cues` 数组；`inject_emojis_into_srt` |
| E6 | **必须 burn_mono** | `subtitle.force_burn_mono`；否则行内表情观众看不见 |
| E7 | **优先单字符表情** | 🚚📦👍🏠✨🔧；禁 ZWJ 复合 |
| E8 | **Twemoji fail-closed** | 含 emoji 的 cue 合成失败 → 打回（禁止字体 mono/tofu 冒充） |
| E9 | **升级冻结** | 已验收的「有 emoji → 必 Twemoji + 继承色/描边」路径**禁止**在后续升级中回退为 `default_style/stroke_width==3` 门栏或其它静默 mono 路径 |

---

## 2. 落点

| 模块 | 职责 |
|------|------|
| `emoji_stickers.inject_emojis_into_srt` | 按 `at_sec` 把表情写入字幕 cue |
| `subtitles_burn._render_cue_png` | **有 emoji 必走** `_render_cue_png_with_twemoji`（继承色/描边宽度） |
| `subtitles_burn.cue_png_twemoji_chroma_count` | 像素级证明彩色 Twemoji（排除字色本身） |
| `ready_gate._check_emoji` | SRT + 重渲 cue PNG chroma；禁标题贴纸 |
| `worker` | **不再**调用 `burn_emoji_stickers_inplace` |
| `voice_subtitle` | strip 后 TTS；写 SRT 后注入；强制 burn_mono |
| `ollama_narration` | 情感口播 + 短句断句 + cues 给字幕 |

Smoke：`python3 scripts/smoke_emoji_stickers.py`

---

## 3. 变更

| 日期 | 变更 |
|------|------|
| 2026-07-26 | 初版：右上角贴纸 + 禁旁白读 |
| 2026-07-26 | **改硬规则**：贴纸移出标题区 → **字幕行内**；禁用浮动贴纸 |
| 2026-08-07 | **修 E3 盲区**：去掉 `stroke_width==3` 才能 Twemoji 的门；生产默认描边 4 / 彩色字幕同样行内烧录；READY 加 chroma 验收；cache 外盘不可写时回落本机 |
