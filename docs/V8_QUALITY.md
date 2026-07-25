# 速影质量冲刺（冻结版 · V8+）

> 执行进度与 Gate 以 [`DEV_LOCK.md`](DEV_LOCK.md) §E 为准。本文只保留冲刺范围说明。

底座可用约 70–75%；可签字导演质量约 40–50%。  
**按 [`DEV_LOCK.md`](DEV_LOCK.md) 执行；禁止平行堆 Pack/Reach。**

## 冻结顺序

```text
L0 黄金样片标尺 → Sprint A 有声+字幕+熔断
 → Sprint B 开场差异+节奏模板+素材质量分
 → Sprint C Logo+打回降权+抽检报表
```

## L0 · 黄金样片 — 分数已落盘（见 DEV_LOCK E1.L0）

见 [`GOLDEN_SAMPLES.md`](GOLDEN_SAMPLES.md)。基线：`ready/2026-07-23` 的 G1/G2 有声片。  
**落盘位置：** 权威库表 `golden_samples` · 镜像 `速影工作区/db/golden_scores.json` · API `GET /reports/golden`。

## Sprint A — ✅ 已落地并实机验证

| ID | 项 | 验收 |
|----|----|------|
| A1 | 有声成片 + loudnorm | job13 两条 `has_audio=true`，QC pass |
| A2 | 字幕收紧（dual_chip：每行≤6 字、字号 96） | dry-run / 成片标题已截短 |
| A3 | 质量熔断 | `quality_circuit_threshold` |

音乐：`~/Suying/music/` 或客户 `04-音乐/`（无曲时用 placeholder）。默认 **仅 BGM**（`keep_source_audio=false`）。  
曲目清单与调用名（`bensound-*` / `km-*`）见 [`MUSIC.md`](MUSIC.md)。

## Sprint B — ✅ 代码已落地（行业包配置化）

1. Hook 池在 `configs/samples/industry/<pack>/pack.json`（非 hooks.py 硬编码）  
2. 节奏模板：`default-vertical` / `fast-ship` / `stable-product`；主题绑定在 pack `template_bindings`  
3. 新入库 cliplet 写视觉质量分；抽样过滤 `min_cliplet_quality`；存量已回填（`POST /cliplets/quality/backfill`，`score=1.0` 为未评分哨兵，实测满分封顶 0.99）  

## Sprint C — 进行中（见 DEV_LOCK）

1. Logo 角标（可关）— DONE（profile.brand + `05-品牌/logo.*`；缺文件则跳过）  
2. 打回原因码 → cliplet 降权 → 一键重渲 — DONE（`rerender=true` / `POST /review/{id}/rerender`）  
3. `/reports/quality` 日报 — PARTIAL  

## 明确后置（勿平行开工）

数字人、换更大 embedding、复杂转场、自动审美模型。  
多平台文案 / 口播 / 半自动发布 / 消息提醒 → [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) Phase 2–4。  
工程标准 → [`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md)。

## 整轮完成定义

黄金样片分数落盘；连续日更 20 条 ≥70% 直接 `ready`；抽检「能发」≥4/5；无声/黑场超标/同素材复用 = 0。

**2026-07-24 试跑：** `速影工作区/db/completion_trial.json` — 近 20 条 ready 率 95%；硬指标抽检无声/黑场/复用 = 0；G1/G2 已签字落盘。人眼「能发」仍建议再抽 5 条确认。

参见 [`BACKLOG.md`](BACKLOG.md) · [`DEV_LOCK.md`](DEV_LOCK.md)。
