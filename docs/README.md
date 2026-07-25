# 速影文档索引（权威顺序）

> **冲突时只信上面的文件。** 下面「历史归档」仅供考古，不得指导当前开发。

## 现行（必读）

| 序 | 文档 | 用途 |
|----|------|------|
| 1 | [`DEV_LOCK.md`](DEV_LOCK.md) | **写死**流程 / Gate / 核对表 / 禁令 |
| 2 | [`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md) | 通用多客户工程标准 |
| 3 | [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) | 产品是什么、三档、分期 |
| 3b | [`REACH_NON_GOALS.md`](REACH_NON_GOALS.md) | Reach 触达非目标（人在回路） |
| 3c | [`SEMANTIC_PIPELINE.md`](SEMANTIC_PIPELINE.md) | 语义切片 / 表达层落地顺序 |
| 3d | [`ZERO_FORK.md`](ZERO_FORK.md) | 第二客户零分叉验收 |
| 3e | [`CONTINUOUS_QUEUE.md`](CONTINUOUS_QUEUE.md) | 自动循环推进队列（当前 G6） |
| 3f | [`HUMAN_PUBLISH_CONFIRM.md`](HUMAN_PUBLISH_CONFIRM.md) | 人点发布补记（已确认） |
| 3g | [`APP_OPT_QUEUE.md`](APP_OPT_QUEUE.md) | App 优化收口（A10→G6） |
| 4 | [`V8_QUALITY.md`](V8_QUALITY.md) | 质量冲刺执行细节 |
| 5 | [`GOLDEN_SAMPLES.md`](GOLDEN_SAMPLES.md) | L0 标尺（须落分） |
| 6 | [`V8_STORAGE.md`](V8_STORAGE.md) | 同步区 vs 工作区 |
| 7 | [`V8_SUYING_APP.md`](V8_SUYING_APP.md) | App 能力 |
| 8 | [`MUSIC.md`](MUSIC.md) | BGM 曲库调用（`bensound-*` / `km-*`）与合规 |
| 9 | [`BACKLOG.md`](BACKLOG.md) | 问题清单（以 DEV_LOCK §E 为准更新状态） |

运维手册：`INSTALL.md` · `SOP.md` · `ACCEPTANCE.md`（入口以 App 向导 + DEV_LOCK 为准）。

## 历史归档（勿当现行需求）

以下文档描述的是已完成或过时的阶段性工作。**若与 DEV_LOCK / PRODUCT_PLAN 冲突，一律作废冲突句。**

| 文档 | 说明 |
|------|------|
| `MVP_PASSED.md` · `V1.md` … `V7.md` | 历史里程碑 |
| `AUTO_INGEST.md` | 摄入说明，细节以代码为准 |
| `V6.md` | 多客户基线；默认客户名等旧句已废 |

归档文件正文顶部均有「历史归档」标记。
