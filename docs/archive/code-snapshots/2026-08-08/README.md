# 历史归档 · code-snapshots 2026-08-08

> **不得指导现行开发。** 仅作死代码清理前的人眼可读备份；权威真相以 git 历史与现行 `engine/` / `apps/` 为准。

| 快照 | 删除前路径 | 删除原因 | 现行替代 |
|------|------------|----------|----------|
| `pipeline.py` | `engine/ingest/pipeline.py` | 零仓外引用 | `engine.catalog.vector_index.index_asset_cliplets` + `vectorization_runtime` |
| `NextAction.tsx` | `apps/desktop/src/shell/NextAction.tsx` | UI 从未 import | 无；流水线引导由各页面/Overview 承担 |

审计过程见现行文 [`docs/AUDIT_CLEANUP_2026-08-08.md`](../../../AUDIT_CLEANUP_2026-08-08.md)。
