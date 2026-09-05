# 速影 · T2S 个人空间统一根目录

> **现行（2026-08-23）**：T2S 个人空间内所有速影相关目录收拢到唯一根 **`速影/`**。  
> 冲突时：`DEV_LOCK` / `HARD_LOCKS` > 本文 > 各分仓 Runbook。权威索引见 [`README.md`](README.md)。  
> 路径常量单源：`engine/ops/t2s_paths.py`。

## 1. 目标结构

| UI（个人空间 → M.2存储11） | API | 用途 |
|---------------------------|-----|------|
| **速影/** | `/nvme11/my/data/速影` | 统一项目根（唯一入口） |
| 速影/**更新包/** | `…/速影/更新包` | App / depot / F5 正式更新仓 → [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) |
| 速影/**主仓备份/** | `…/速影/主仓备份` | docs + git bundle + 运维密钥 → [`REPO_BACKUP_ZSPACE.md`](REPO_BACKUP_ZSPACE.md) |
| 速影/**行业包备份/** | `…/速影/行业包备份` | industry + seeds + 客户覆写 → [`INDUSTRY_PACK_BACKUP_ZSPACE.md`](INDUSTRY_PACK_BACKUP_ZSPACE.md) |
| 速影/**辅助工具/** | `…/速影/辅助工具` | 运维中心 releases + vault（`速影辅助工具` 仓） |
| 速影/**客户暂存/** | `…/速影/客户暂存` | 个人空间临时客户物料（非团队片库真相） |
| 速影/**离线交付/** | `…/速影/离线交付` | 远程部署：模型套件 + verify-tools → [`T2S_OFFLINE_DEPLOY.md`](T2S_OFFLINE_DEPLOY.md) |

```text
/nvme11/my/data/速影/
├── README.md
├── 更新包/          # App / depot / F5
├── 主仓备份/
├── 行业包备份/
├── 辅助工具/
├── 离线交付/        # models + verify-tools（部署经运维机中转）
│   ├── models/macos-arm64/{lite,standard,pro,max}/
│   └── verify-tools/macos-arm64/…/
└── 客户暂存/
```

## 2. 硬规则

1. **禁止**在个人空间根再新建 `速影更新包` / `速影主仓备份` / `速影行业包备份` / `速影辅助工具` 等并列目录。
2. 推送 / 备份 / 运维工具发布 **一律**写 `engine/ops/t2s_paths.py` 中的路径；禁止脚本内另写死旧根。
3. **客户机** `update_remote_root` / `carrier_remote_root` 默认：`/nvme11/my/data/速影/更新包`（`deploy-remote.sh`）。已装机下次覆盖升级时带新默认。
4. 片库 / 成片仍走**团队空间**「速影客户/…」；不得塞进本个人根。
5. 密钥只进 `速影/主仓备份/keys/`；禁止进 `更新包`、团队空间、客户交付物。

## 3. 迁移说明（2026-08-23）

| 旧路径（已迁走） | 新路径 |
|------------------|--------|
| `/nvme11/my/data/速影更新包` | `/nvme11/my/data/速影/更新包` |
| `/nvme11/my/data/速影主仓备份` | `/nvme11/my/data/速影/主仓备份` |
| `/nvme11/my/data/速影行业包备份` | `/nvme11/my/data/速影/行业包备份` |
| `/nvme11/my/data/速影辅助工具` | `/nvme11/my/data/速影/辅助工具` |
| `/nvme11/my/data/臻享丽人` | `/nvme11/my/data/速影/客户暂存/臻享丽人` |

旧路径已从个人空间根移除；书签/挂载若仍指向旧名，请改到上表新路径。

## 4. 相关入口

| 动作 | 命令 / 文档 |
|------|-------------|
| **QR 工作区备份**（与其它 `~/QR` 项目同构） | `qr t2s-backup -p dev/速影` → 极空间 `QR备份/dev/速影/` · [`~/QR/dev/qr/docs/T2S_WORKSPACE_BACKUP.md`](../../../dev/qr/docs/T2S_WORKSPACE_BACKUP.md) |
| App 更新推送 | `./scripts/release-to-t2s.sh` · [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) |
| 主仓备份 | `python3 scripts/backup_repo_to_zspace.py` · [`REPO_BACKUP_ZSPACE.md`](REPO_BACKUP_ZSPACE.md) |
| 行业包备份 | `python3 scripts/backup_industry_packs_to_zspace.py` · [`INDUSTRY_PACK_BACKUP_ZSPACE.md`](INDUSTRY_PACK_BACKUP_ZSPACE.md) |
| 运维中心推送 | `~/QR/dev/速影辅助工具/scripts/release-ops-to-t2s.sh` |
| 客户机部署 | [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md)（默认更新仓已对齐新路径） |
| 离线交付（模型/工具） | [`T2S_OFFLINE_DEPLOY.md`](T2S_OFFLINE_DEPLOY.md) |
