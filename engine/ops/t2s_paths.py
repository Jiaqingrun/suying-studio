"""T2S 个人空间 · 速影统一根目录路径合同。

权威说明见 docs/T2S_PERSONAL_ROOT.md。
禁止再在个人空间根散落「速影*」并列目录。
"""

from __future__ import annotations

# NAS 个人空间根（API）
PERSONAL_ROOT = "/nvme11/my/data"

# 速影统一项目根（唯一入口）
PROJECT_ROOT = f"{PERSONAL_ROOT}/速影"
PROJECT_UI = "M.2存储11/速影"

# 子目录（短名；不再带「速影」前缀）
UPDATE_ROOT = f"{PROJECT_ROOT}/更新包"
UPDATE_UI = f"{PROJECT_UI}/更新包"

REPO_BACKUP_ROOT = f"{PROJECT_ROOT}/主仓备份"
REPO_BACKUP_UI = f"{PROJECT_UI}/主仓备份"

INDUSTRY_BACKUP_ROOT = f"{PROJECT_ROOT}/行业包备份"
INDUSTRY_BACKUP_UI = f"{PROJECT_UI}/行业包备份"

OPS_TOOLS_ROOT = f"{PROJECT_ROOT}/辅助工具"
OPS_TOOLS_UI = f"{PROJECT_UI}/辅助工具"
OPS_VAULT_ROOT = f"{OPS_TOOLS_ROOT}/vault"
OPS_RELEASES_ROOT = f"{OPS_TOOLS_ROOT}/releases"

CUSTOMER_STAGING_ROOT = f"{PROJECT_ROOT}/客户暂存"
CUSTOMER_STAGING_UI = f"{PROJECT_UI}/客户暂存"

# 远程部署离线资产（模型套件 / verify-tools；depot 见 DEPOT_ROOT）
OFFLINE_DEPLOY_ROOT = f"{PROJECT_ROOT}/离线交付"
OFFLINE_DEPLOY_UI = f"{PROJECT_UI}/离线交付"
OFFLINE_MODELS_ROOT = f"{OFFLINE_DEPLOY_ROOT}/models"
OFFLINE_VERIFY_TOOLS_ROOT = f"{OFFLINE_DEPLOY_ROOT}/verify-tools"
DEPOT_ROOT = f"{UPDATE_ROOT}/depot"  # 签名 CAS 仓（与更新包同树）

# 运维机部署前暂存（极空间 → 本机 → 客户机 LAN）
LOCAL_ZSPACE_OFFLINE_CACHE = "incoming/zspace-offline"  # under ~/Suying/

# 旧路径（2026-08-23 前并列在个人空间根；仅兼容提示，禁止再写入）
LEGACY_ROOTS = (
    f"{PERSONAL_ROOT}/速影更新包",
    f"{PERSONAL_ROOT}/速影主仓备份",
    f"{PERSONAL_ROOT}/速影行业包备份",
    f"{PERSONAL_ROOT}/速影辅助工具",
)

# 兼容旧名
REMOTE_ROOT = UPDATE_ROOT  # App 更新仓
