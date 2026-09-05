# 横竖屏硬审核锁 · ORIENTATION LOCK（强制 · 已写死）

> **效力：写死。2026-08-06 用户明确：横竖屏判断须严谨，不得出错；App 设定为硬性审核机制。**  
> **违反本文件 = 素材不得标 `ready` 入库完成 / 不得向量化 / 不得选片生产。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 代码：`engine/ingest/orientation.py` + `engine/ingest/metadata.py`

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。

---

## 1. 硬规则（不可协商）

| # | 规则 | 验收 |
|---|------|------|
| O1 | **以显示尺寸为准，禁止仅看编码宽高** | DJI/iPhone Camera 常见 `1920×1080 + Display Matrix 90°` → **竖屏** |
| O2 | **双源一致** | 源片显示方向 与 归一化成片显示方向 必须同为 `portrait` 或同为 `landscape` |
| O3 | **不一致 fail-closed** | 允许 **一次** 强制重归一化后复审；仍不一致 → `status=rejected_orientation`，不入向量 |
| O4 | **近平方 / 未知不得 ready** | `other` / `unknown` → 拒绝（非硬猜） |
| O5 | **向量化 / 选片入口拒收** | `enqueue_asset` / 起批 `create_run` 必须 `asset_may_vectorize` |
| O6 | **禁止二次手动 transpose 叠加 autorotate** | 归一化只信 ffmpeg 解码自动旋转；禁止再 transpose 导致横竖颠倒 |
| O7 | **审计落盘** | `metadata_json.orientation_audit` 含 `passed/violations/source/baked/lock_version` |
| O8 | **阈值只可加严** | 竖屏 ratio ≤0.9、横屏 ≥1.1；`smoke_hard_locks` 守卫 |
| O9 | **App 硬性展示** | 运维向量面板 + `/health.orientation_lock` 标明硬审核；不得暗示关闭 |

---

## 2. 分类阈值

| 条件 | 结果 |
|------|------|
| display_w / display_h ≥ **1.1** | `landscape` |
| display_w / display_h ≤ **0.9** | `portrait` |
| 中间区间 | `other`（拒） |
| 宽高不可用 | `unknown`（拒） |

`display_*` = 编码尺寸经 rotation∈{90,270} 后交换。

旋转来源优先级：`side_data.rotation` → displaymatrix 矩阵推断 → stream/format tags `rotate`。

---

## 3. 落点

| 环节 | 行为 |
|------|------|
| 入库 | `evaluate_orientation_audit` → 失败 `rejected_orientation` |
| 归一化 | `normalize_video` 仅 scale；禁止叠加 transpose |
| 向量 | `enqueue` / `create_run` 硬门禁 |
| App | `VectorControl` 硬条 + `GET /vectorization/orientation-lock` |
| 冒烟 | `assert_orientation_lock_integrity` in `smoke_hard_locks.py` |

状态常量：`rejected_orientation`（与 `rejected_blur` 同级隔离）。

---

## 4. 变更记录

| 日期 | 变更 |
|------|------|
| 2026-08-06 | 用户：完善横竖判断审核并 App 硬性化 → L16 + orientation.py + 入库双源门禁 |
