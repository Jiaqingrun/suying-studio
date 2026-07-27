# 画质锁 · 虚焦 / 模糊（强制 · 已写死）

> **效力：写死。2026-07-26 用户明确：虚焦模糊的切片和视频素材不予入库、不进行向量化；同日再确认「以上规则全部写死」。**  
> **违反本文件 = 不允许进 ready 片库索引 / 不允许向量化 / 不允许进成片候选。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 配置：`VIDEO_LOCK.quality` · 代码：`engine/ingest/quality.py`（`assert_quality_lock_integrity`）

---

## 1. 硬规则（不可协商）

| # | 规则 | 验收 |
|---|------|------|
| Q1 | **虚焦/严重发糊切片不得创建为 usable** | `status=rejected_blur`；不写 embedding |
| Q2 | **虚焦素材整条不得标 ready 向量化** | 入库 `status=rejected_blur`（与横屏 `rejected_landscape` 同级） |
| Q3 | **向量化入口拒收** | `index_cliplet` / `index_pending` 跳过 rejected_blur 与低分 |
| Q4 | **语义检索与选片拒收** | `search_cliplets` + planner floor≥`MIN_QUALITY_SCORE` |
| Q5 | **存量必须筛除** | `purge_blur_from_catalog` 重打分 → SQL NULL 清 embedding → 打标 |
| Q6 | **未打分（legacy score=1.0）不得当清晰** | 未重打分前禁止向量化（fail-closed） |
| Q7 | **阈值只可加严、不可放宽** | `load_video_lock` 钳制；`smoke_hard_locks.py` 守卫 |
| Q8 | **画质通过不等于可检索** | 还须通过 `suying.cliplet.semantic.v1` 严格语义门禁 |

---

## 2. 冻结阈值

| 常量 | 值 | 含义 |
|------|-----|------|
| `MIN_LAPLACIAN_VAR` | `48.0` | 低于此 = 虚焦 |
| `MIN_QUALITY_SCORE` | `0.35` | 综合画质下限 |
| `ASSET_BLUR_FAIL_RATIO` | `0.6` | 采样失败≥60% → 整条拒绝 |

检测：切片中帧；素材 15% / 50% / 85% 三帧。

画质门禁之后的语义入库标准、三次有限重试及 `rejected_semantic` 隔离规则，
以 [`SEMANTIC_PIPELINE.md`](SEMANTIC_PIPELINE.md) §2.0 为准。任何
`rejected_semantic` 行均为审计记录，不得生成 embedding 或进入检索。

---

## 3. 落点

| 环节 | 行为 |
|------|------|
| 入库 | `score_asset_focus` → 失败 `rejected_blur` |
| 切片 | 低分只建 audit `rejected_blur` |
| 向量化 | 硬拒 + SQL `embedding_json=NULL` |
| 选片 | `is_usable_quality` + `max(template, MIN_QUALITY_SCORE)` |
| VIDEO_LOCK | `quality.*` 写死；加载时钳制 |

---

## 4. 运维

```bash
python3 scripts/purge_blur_catalog.py --limit 8000
python3 scripts/smoke_hard_locks.py
curl -sS -X POST 'http://127.0.0.1:8766/cliplets/quality/purge-blur?limit=8000&rescore=true&purge_assets=true'
```

预览拒绝样例：`速影工作区/cache/blur_reject_preview/`

---

## 5. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-07-26 | 用户强制：虚焦不入库不向量化；Laplacian 门禁；存量 purge |
| 2026-07-26 | 用户：「以上规则全部写死」→ VIDEO_LOCK.quality + HARD_LOCKS + 加载钳制 + smoke |
| 2026-07-27 | 语义理解视觉默认切 `qwen3.5:27b-q4_K_M`；Ollama `format`=完整 JSON Schema + `temperature=0`；画质/语义阈值不放宽 |
| 2026-07-27 | 分档级联：≤16GB 默认 `qwen3.5:9b`（禁默认 27B）；pro/max 9B→27B 升级；门禁 0.65/0.72 与最多 3 次总尝试不变 |
