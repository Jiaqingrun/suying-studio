# 速影 · 硬规则总锁（HARD LOCKS）

> **效力：写死。2026-07-26 用户明确：「以上规则全部写死」。**  
> **本文件 = 索引。细则以各分锁为准；改阈值 / 绕开门禁 = 必须用户当面批准。**  
> Agent 改相关代码前必读本文件 + 对应分锁。

---

## 分锁清单（全部强制）

| ID | 文件 | 写死内容 |
|----|------|----------|
| L0 | [`DEV_LOCK.md`](DEV_LOCK.md) | 产品边界、Gate、DoD |
| L1 | [`VIDEO_LOCK.json`](../configs/customers/北京始峰伟业/brand/VIDEO_LOCK.json) + `engine/pack/video_lock.py` | 片型/标题/字幕/旁白/语速 + **quality**；标题 **黄字黑描边** + **`offset_y_px=120`** |
| L2 | [`NARRATION_SUBTITLE_LOCK.md`](NARRATION_SUBTITLE_LOCK.md) | H1–H8：话说完字幕灭；边距；短句呼吸；rate=-8% |
| L3 | [`EMOJI_STICKER_LOCK.md`](EMOJI_STICKER_LOCK.md) | 旁白不读表情；**字幕行内表情**；禁标题区贴纸 |
| L4 | [`QUALITY_LOCK.md`](QUALITY_LOCK.md) | 虚焦模糊不入库、不向量化、不选片 |
| **L5** | **[`READY_GATE.md`](READY_GATE.md)** | **进成品库总门禁：逐项全过才 ready，否则打回** |
| **L6** | **[`PAPER_SLIP_LOCK.md`](PAPER_SLIP_LOCK.md)** | **纸片规则：cliplet/词句本地日最多 2 次** |
| **L7** | [`DEV_LOCK.md`](DEV_LOCK.md) + `engine/reach/message_sync.py` | **消息静默巡检固定 1800 秒；普通配置不可缩短；通知脱敏、去重、可审计** |

---

## 不可协商底线（摘要）

1. **画质**：Laplacian≥48、score≥0.35；`rejected_blur` 无 embedding、不进成片候选。  
2. **字幕**：对齐 VO；左右 margin≥48；无背景条。  
3. **旁白**：先断句再去标点；呼吸≤18 字；Edge `rate=-8%`、`pitch=+35Hz`、`volume=+12%`（情感）。  
4. **表情**：只进字幕行内；禁标题区贴纸；旁白绝不朗读。  
5. **Ollama**：模块必须可 import；失败写 `ollama_narration_error`，禁止静默跳过。  
6. **标题**：`color=#FFE600`（黄字）+ `stroke=#000000`（黑描边）；`offset_y_px=120`（整体下移）；禁止红字黄描边。  
7. **成品库**：未过 [`READY_GATE.md`](READY_GATE.md) **不得**标 ready。  
8. **纸片**：cliplet / 词池短句本地日 ≤2；仅 ready 计数；禁止静默超发。
9. **消息巡检**：固定每 1800 秒；正式 Chrome `--headless=new` 无可见窗口；登录/验证码停人；App、macOS、可选 ntfy 仅发脱敏摘要和官方链接。

---

## 代码锚点

| 规则 | 锚点 |
|------|------|
| 画质常量 + `assert_quality_lock_integrity` | `engine/ingest/quality.py` |
| 入库拒糊 | `engine/ingest/metadata.py` |
| 切片拒糊 | `engine/ingest/cliplet.py` |
| 向量化拒糊 | `engine/catalog/vector_index.py` |
| 选片拒糊 | `engine/template/engine.py`（floor≥`MIN_QUALITY_SCORE`） |
| 加载时钳制锁（含标题黄/黑） | `engine/pack/video_lock.load_video_lock` |
| **进 ready 总门禁** | `engine/qc/ready_gate.evaluate_ready_gate` ← worker 拒绝 ready |
| **纸片日配额** | `engine/catalog/paper_slip` + 选片/词池硬过滤；ready 后记账 |
| **消息巡检与通知** | `engine/reach/message_sync.py` + `chrome_runtime.py` + `notifications.py`；SQLite CHECK/trigger 锁定 1800 秒 |

---

## 冒烟

```bash
python3 scripts/smoke_hard_locks.py
python3 scripts/smoke_ready_gate.py
python3 scripts/smoke_paper_slip.py
python3 scripts/smoke_subtitle_align.py
python3 scripts/smoke_subtitle_margin.py
python3 scripts/smoke_narration_breath.py
python3 scripts/smoke_emoji_stickers.py
```

---

## 变更

| 日期 | 事件 |
|------|------|
| 2026-07-26 | 用户：虚焦规则写死；「以上规则全部写死」→ 建本总锁 + VIDEO_LOCK.quality + 加载钳制 |
| 2026-07-26 | 用户：标题改黄字黑描边；全套审核门禁 → `READY_GATE.md` + worker 强制 |
| 2026-07-26 | 用户：标题整体下移 120px → `offset_y_px` 写死 |
| 2026-07-26 | 用户：纸片规则日≤2 → `PAPER_SLIP_LOCK.md` + `daily_usage` |
| 2026-07-26 | 全量自检：READY_GATE 缺 clips / 缺 tts_provider 改为 fail-closed；Twemoji 去硬编码盘路径；DEFAULT_LOCK 中性化 |
| 2026-07-26 | 收口复检：纸片上限下沉 SQLite CHECK/trigger；产品核心去客户及建材行业默认；打包后强制内嵌 health + codesign 验签 |
| 2026-07-26 | 用户明确授权消息巡检完整工作流：固定 1800 秒、正式 Chrome 静默 headless、三路通知与 ntfy 安全配置 |
