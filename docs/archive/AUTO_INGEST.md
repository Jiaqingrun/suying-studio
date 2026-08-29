> **历史归档**：本文仅作里程碑记录。现行以 [`DEV_LOCK.md`](../DEV_LOCK.md) / [`PRODUCT_PLAN.md`](../PRODUCT_PLAN.md) / [`DEVELOPMENT_STANDARDS.md`](../DEVELOPMENT_STANDARDS.md) 为准；冲突句作废。

# 自动入库与索引

## 实时路径（Watcher）

1. 监视**当前客户**片库根目录（递归）
2. 新视频创建/移入 → 等待拷贝稳定 → 规范化 + proxy
3. 当场切 cliplet（含视觉描述）→ embedding
4. 失败会写日志，并在 `metadata_json.index_pending` 标记

## 全量扫描（Scan）

`POST /assets/scan?limit=0&background=true` 遍历片库，只处理漏网文件。

- 默认 **defer_index**：只做规范化 + proxy，视觉/向量交给 Reconciler
- 入库前先以 `status=ingesting` 占位，避免重启后对同一文件开多个 ffmpeg
- 扫描进行中会暂停 Reconciler，减少 CPU/磁盘争抢
- `GET /assets/scan/status` / 桌面「扫描进度」查看进度

## 兜底路径（Reconciler）

调度器每约 **30 秒**处理当前客户下缺 cliplet / embedding 的素材（每轮最多 5 条，按 id 升序排空积压）。

手动：`POST /assets/reconcile?limit=50`

## 依赖

- **Ollama** 需常开。宕机时入库仍成功，索引标 `index_pending`，恢复后自动补齐。
