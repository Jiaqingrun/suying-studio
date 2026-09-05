# INCIDENT · 成片无标题 + 字幕与旁白不匹配

> 状态：本机已修；**0.6.6 core** 重打一体包后按 `REMOTE_DEPLOY` 推客户机。  
> 时间：2026-08-02 · 客户机 `xlf-remote` · 成片约 `#698/#699`（job `511/512` 产线抽查）

## 现象

1. 画面**看不到片上标题**（抽帧 t≈2s 仅有底部字幕）。
2. **字幕与旁白内容完全不是一套**：口播为简体场景旁白（仓库/始峰现场…），烧录字幕为繁体品牌短句（「這裡是始峰…用心服務…」）。

## 证据

- Sidecar / DB：`title` 文案与 `title_bbox` 有值，`title_effect=fade`，颜色 `#FFAA00`。
- `effective_rules`：`voice_lang=zh`，`subtitle_lang=zh-TW`，`subtitle_burn=burn_mono`。
- 旁路 `*.zh_TW.srt` / 画面字幕 = `narration_script_for_lang("zh-TW")` 品牌短循环，**不是** VO 时轴文案。
- 客户机抽帧：`/tmp/suying_title_check.jpg` 无顶部标题层。

## 根因

| # | 问题 | 机制 |
|---|------|------|
| 1 | 标题「消失」 | `title_effect=fade` 时标题 PNG 以**单帧**喂入 ffmpeg；`fade=t=in:d=0.4:alpha=1` 在 ~1 帧内无法拉满透明度 → 片上几乎看不见标题。 |
| 2 | 字幕≠旁白 | `burn_mono` 允许 `subtitle_lang≠voice_lang`；`voice_subtitle.py` 对 `zh-TW` 走品牌短模板替换 cue 正文，丢掉真实 VO/SRT。 |

## 本机修复

| 项 | 改动 |
|----|------|
| `engine/render/ffmpeg.py` | 标题输入 `-loop 1 -framerate 25`；overlay `eof_action=repeat` |
| `engine/template/rule_schema.py` | `burn_mono` 强制 `subtitle_lang = voice_lang`（双语请用 `burn_dual`） |
| `engine/render/voice_subtitle.py` | `burn_mono` 有 VO SRT 时强制用 VO 文案与时轴 |
| `docs/NARRATION_SUBTITLE_LOCK.md` | 新增 **H11** |
| 版本 | **0.6.6**（正式交付 flavor=`core`） |

## 验收

1. 规则：`validate_and_clamp({voice_lang:zh, subtitle_lang:zh-TW, subtitle_burn:burn_mono})` → effective `subtitle_lang=zh` 且有 clamp 记录。
2. 新产竖屏成片：抽帧可见顶部标题；烧录字幕与口播同文（简体）。
3. 冒烟：`python3 scripts/smoke_test.py`；相关：`tests/test_production_rules.py`。

## 现场

- 存量错误成片不自动重渲；客户机更新 **0.6.6** 后新任务生效。
- 若规则实验室仍显示「字幕繁体」请求值，effective 会被 clamp 到旁白语言；要双语须改 `burn_dual`。
