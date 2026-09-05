# 速影 · 磁盘清理范围锁

> **效力：领域合同。** 冲突时：`DEV_LOCK` / `HARD_LOCKS` > 本文 > 运维文案。  
> 存储边界见 [`V8_STORAGE.md`](V8_STORAGE.md)、[`STORAGE_SYNC_SAFETY.md`](STORAGE_SYNC_SAFETY.md)。  
> 代码入口：`engine/ops/disk_cleanup.py` · API：`/ops/disk-cleanup/*`。

## 1. 目标

用文档 + 代码双重白名单写死**可删 / 禁删**范围，防止误删片库、数据库、待审成片。  
清理面统一为运维「磁盘清理」；**不**第二套调度、**不**新 Gate。

## 2. 允许删除（白名单）

代码删除前必须：`resolve` → 非软链 → `is_relative_to` 活跃客户允许根。

| 档 | 目标 | 触发 | 保留何物 | 默认 |
|----|------|------|----------|------|
| A | `RenderOutput.state=failed` 且过保留期的媒体 | 调度或 `tiers=failed` | DB 审计行 | **开** · 72h |
| A | `output_root/failed/**` 孤儿（不在 ready/review 路径集） | 同上 | — | **开** · 72h |
| A | 审片/归档**驳回**后的成片与 pack | 驳回路径 `purge_output_media` | 评审记录 | **立即** |
| B | `cache_root/temp/**` 陈旧文件 | 调度或 `tiers=work_cache` | — | **开** · 与失败保留小时对齐（或不低于 24h） |
| B | `render_root/**` 陈旧文件 | 同上 | — | **开** |
| C | `retired_published` 且过保留期的 `published/**/output.mp4` | 策略开启 + 调度/ `tiers=published` | 发布记录、编号、物料；先 trash | **关** · 默认 30 天 · trash 7 天 |
| D | `cache_root/frames/**`、`proxies/**` 陈旧文件 | `tiers=rebuild_cache`；自动仅当 `frames_proxies_enabled` | 可重建 | **自动默认关** · 默认 14 天 mtime |

### 2.1 允许根（仅这些）

```text
{cache_root}/temp
{cache_root}/frames
{cache_root}/proxies
{render_root}
{output_root}/failed
{output_root}/…/published 相关（仅 via published_cleanup 路径与状态守卫）
{output_root}/retired/.trash/published-videos   # 宽限期后硬删 trash
```

**永不**进入白名单：`{cache_root}/library`。

## 3. 禁止删除（黑名单）

| 禁止目标 | 原因 |
|----------|------|
| `data_root`、`montage.db`、WAL/SHM | 状态真相源 |
| `settings.json`、许可/bootstrap、runtime token | 引导与安全 |
| `library_root` / `01-片库` 及原始媒体 | 不可替代源素材 |
| `cache_root/library/**` | 规范化素材，本阶段永不进 facade |
| `output_root/ready/**`、`review/**` 的批量年龄扫 | 待审/可发成片 |
| 未 `retired_published` 的 published 成片 | 可能仍待发 |
| `carrier/`、T2S 镜像、App bundle | 交付更新 |
| Chrome `user-data-dir` | 触达登录态 |
| `03-词池` / keyword-pack 源文件 | 配置资产 |
| 备份树（除非现有 backup prune） | 独立合同 |
| 同步区 / NAS / 片库映射整树 | fail-closed |

传入 facade 的禁止档名（`library` / `ready` / `review` / `data` 等）→ **HTTP 400**，不得静默忽略当成功。

## 4. 「未过审」拆分（写死）

| 含义 | 可否清媒体 | 入口 |
|------|------------|------|
| 已打回 / 归档驳回 | **立即**删媒体 | 审片 API → `purge_output_media` |
| 仍待审 ready / review | **禁止**磁盘清理页按年龄批量删 | 仅人在审片点驳回后 purge |

## 5. 确认与回收

- 已发布：进 trash → `trash_grace_days` 后硬删。  
- 失败 / temp / render / frames / proxies：直接 unlink；run 前 report 提供 count/bytes。  
- 写策略与手动 `run`：需 Ops 高级 token。  
- 每次 facade `run` 写 `category=cleanup` 运维审计。

## 6. API 档位

| tier | 行为 |
|------|------|
| `failed` | 过期 failed 媒体 + failed/ 孤儿树 |
| `work_cache` | temp + render |
| `rebuild_cache` | frames + proxies（从不扫 library） |
| `published` | 已退休可删成片 + trash 宽限期 |

调度：`failed_resource_cleanup` + `published_cleanup` 现有 tick；frames/proxies 仅 `frames_proxies_enabled=true` 时附带。

## 7. 明确不做

- 不自动清 ready/review 积压  
- 不扫 `cache/library`  
- 不删片库、DB、Chrome、承运体  
- 不新开 DEV_LOCK Gate  
- 不引入第二套调度循环
