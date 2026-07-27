# 持续推进队列（自动循环）

> 更新：2026-07-26
> **当前主线：旁白×字幕对齐强制锁（30s 循环）。**

## 已完成（勿重复）

- 语义管线 P0–P4、字幕真烧录、零分叉
- C1–C6 人点发布确认
- App 优化 A1–A9；A10→E6.1
- **G6 E6.1–E6.5**
- **GStab / GCarrier / GFleet / GQual / GShip**（T2S 载体定位）
- **Ollama 旁白文案**：运维点选；视觉描述→口播+表情烧录；旁白 `qwen2.5:32b` + 视觉 `qwen3.5:27b-q4_K_M`（`gemma4` 仍可选）
- 载体 ensure 落 seed、更新 LaunchAgent、配置恢复 API/UI
- **路径迁移**：DB `/Users/xlf` → `/Users/qr`（渲染找不到片）

## 当前队列（画质 · 虚焦门禁）

| 序 | ID | 内容 | 状态 |
|----|-----|------|------|
| 1 | Q.BLUR | Laplacian 虚焦门禁 + 入库/向量化硬拒 | ✅ |
| 2 | Q.PURGE | 存量切片/素材筛除清 embedding | ✅ 623 扫 / 7 拒切片 / 1 拒素材 |
| 3 | Q.DOC | `docs/QUALITY_LOCK.md` | ✅ |
| 4 | HARD.ALL | 「以上规则全部写死」→ `HARD_LOCKS.md` + VIDEO_LOCK 钳制 + smoke | ✅ |
| 5 | TITLE.YB | 标题黄字黑描边 `#FFE600`/`#000000` | ✅ |
| 6 | READY.GATE | `docs/READY_GATE.md` + worker 入库强制 | ✅ |
| 7 | PAPER.SLIP | 纸片日配额≤2（cliplet/词句） | ✅ |

```bash
python3 scripts/purge_blur_catalog.py --limit 8000
python3 scripts/smoke_ready_gate.py
python3 scripts/smoke_paper_slip.py
```

## 成品 10 条（已完成）

| 序 | ID | 内容 | 状态 |
|----|-----|------|------|
| 1 | B10.QA | `scripts/qa_ready_montage.py` 严格检测 | ✅ |
| 2 | B10.LOOP | `scripts/suying_batch10_qa_loop.py` 逐片出片+检测 | ✅ |
| 3 | B10.FIX | 失败则修复并记规则后 `--resume` | ✅ Job91→修 Ollama SyntaxError |
| 4 | B10.REPORT | `速影工作区/cache/batch10_qa/report.md` | ✅ 10/10 通过（1 次失败已修复） |

哨兵：`AGENT_BATCH_FAIL` / `AGENT_BATCH_DONE` / `AGENT_BATCH_PASS`

```bash
# 新开 10 条（遇错停）
python3 scripts/suying_batch10_qa_loop.py --reset --target 10
# 修复后继续
python3 scripts/suying_batch10_qa_loop.py --resume --target 10
```

## 旁白×字幕（已完成）

| 序 | ID | 内容 | 状态 |
|----|-----|------|------|
| 1 | NS.H1 | 字幕结束 ≤ 话音结束（废止含静音 cue 时长） | ✅ 代码+冒烟 |
| 2 | NS.H2 | 句间静音空屏；tail_trim 默认 0.12 | ✅ VIDEO_LOCK |
| 3 | NS.H3 | oneshot 绝对时间 + `tighten_srt_to_voiceover` | ✅ |
| 4 | NS.DOC | `docs/NARRATION_SUBTITLE_LOCK.md` + DEV_LOCK 登记 | ✅ |
| 5 | NS.SMOKE | `scripts/smoke_subtitle_align.py` 绿 | ✅ |
| 6 | NS.ENGINE | 重启引擎使 worker 加载新对齐逻辑 | ✅ |
| 7 | NS.SAMPLE | 新出片抽检：话说完字幕即灭 | ✅ Job85 5/5；overhang≈-0.12s |
| 8 | NS.EMOJI | 表情包 cue：旁白不读 + Twemoji 可见烧录 | ✅ 代码；Job86 核验；终检循环中 |
| 9 | NS.MARGIN | 字幕左右 margin≥48，禁止贴边裁切 | ✅ Job87；`smoke_subtitle_margin.py` |
| 10 | NS.BREATH | 旁白短句≤18 + rate=-8% | ✅ Job89；`smoke_narration_breath.py` |

## 表情贴纸 30s 循环

哨兵：`AGENT_LOOP_TICK_suying_emoji_sticker`
脚本：`scripts/suying_emoji_sticker_tick.py`
权威锁：[`EMOJI_STICKER_LOCK.md`](EMOJI_STICKER_LOCK.md)


## 可选下一波（需你点头再开）

| ID | 内容 |
|----|------|
| G7 | 官方开放平台 API 发布（若授权） |
| — | 片库经团队空间日更同步（legacy，非默认） |

## 冒烟

```bash
python3 scripts/smoke_test.py
python3 scripts/smoke_ops.py
python3 scripts/smoke_carrier.py
python3 scripts/smoke_ollama_narration.py
python3 scripts/smoke_subtitle_align.py
python3 scripts/smoke_subtitle_margin.py
python3 scripts/smoke_narration_breath.py
curl -sS -X POST 'http://127.0.0.1:8766/ops/ollama-narration/preview'
```

## 30s 循环哨兵

```text
AGENT_LOOP_TICK_suying_subtitle_align
```

循环脚本：`scripts/suying_subtitle_align_tick.py`
