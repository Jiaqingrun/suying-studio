# 速影 · T2S 离线交付（远程部署资产）

> **现行（2026-08-23）**：模型套件、verify-tools（ollama+ffmpeg）**真相源在极空间**；运维 Mac 仅作 **NAS → 客户机** 中转，不长期占用本机磁盘。  
> 签名 CAS **depot** 仍在 `速影/更新包/depot/`（与 App 更新仓同树，已上传 T2S）。  
> 冲突时：`DEV_LOCK` / `HARD_LOCKS` > 本文 > [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md)。索引见 [`README.md`](README.md)。

## 1. 极空间目录

| UI | API | 内容 |
|----|-----|------|
| 速影/**离线交付/** | `/nvme11/my/data/速影/离线交付` | 远程部署离线资产根 |
| …/models/macos-arm64/`<profile>`/ | `…/离线交付/models/macos-arm64/<profile>` | Ollama 离线模型套件 |
| …/verify-tools/macos-arm64/`<profile>`/ | `…/离线交付/verify-tools/macos-arm64/<profile>` | ollama + ffmpeg（无 App/模型） |
| 速影/更新包/**depot/** | `/nvme11/my/data/速影/更新包/depot` | 签名 CAS 仓（引用，不重复存放） |

```text
速影/
├── 离线交付/
│   ├── README.md
│   ├── MANIFEST.json
│   ├── models/macos-arm64/{lite,standard,pro,max}/
│   └── verify-tools/macos-arm64/{lite,standard,pro,max}/
└── 更新包/
    └── depot/          ← 已含 CAS 对象 + depot.json(.sig)
```

## 2. 数据流

```text
[极空间 T2S] 离线交付 + 更新包/depot
       ↓  API 下载（deploy 前暂存）
[运维 Mac]   ~/Suying/incoming/zspace-offline/
       ↓  rsync / SSH（LAN）
[客户 Mac]   ~/Suying/incoming/remote-deploy/…
```

**硬规则**

1. 本机 `~/Suying/offline/速影-offline-models-*` **不是**真相源；推送 T2S 后可删（见 §4）。
2. `deploy-remote.sh` 默认 `OFFLINE_ZSPACE=1`：本机无套件时自动从极空间暂存；`--no-offline-zspace` 关闭。
3. 禁止客户机公网 `ollama pull`（仍走 [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) 离线合同）。
4. `~/.ollama/models/` 仅本机 Ollama 运行时使用，**不**上传极空间、不与离线交付混用。

## 3. 运维命令

### 3.1 推送到极空间（更新模型/工具后）

```bash
cd ~/QR/dev/速影

# 若本机尚无套件，先从 Ollama 生成
python3 scripts/ollama_model_bundle.py create \
  --profile lite --profile standard --profile pro --profile max \
  --output-root "$HOME/Suying/offline/速影-offline-models-macos-$(uname -m)"

# 上传 models + verify-tools → T2S
python3 scripts/publish_offline_deploy_to_zspace.py

# depot 已在 更新包/depot；若重建仓：
# python3 scripts/publish_offline_depot.py --depot ~/Suying/offline/速影-offline-depot-arm64-ready
```

推送成功后清理本机副本（保留 `~/.ollama`）：

```bash
python3 scripts/publish_offline_deploy_to_zspace.py --prune-local
# 或
./scripts/prune-dev-disk.sh --aggressive
```

### 3.2 部署（自动从极空间拉）

```bash
./scripts/deploy-remote.sh \
  --host <SSH别名> \
  --customer-name "<客户名>" \
  --customer-config configs/customers/<客户名> \
  --license ~/Suying/runtime/security/license-ledger/<许可>.suying-license
```

仅手动暂存（不部署）：

```bash
python3 scripts/stage_offline_deploy_from_zspace.py --profile pro --arch arm64
```

## 4. 本机目录约定

| 路径 | 用途 |
|------|------|
| `~/Suying/incoming/zspace-offline/` | 极空间 → 本机暂存（可删，deploy 会重建） |
| `~/Suying/offline/速影-offline-*` | **仅**打包/upload 前的工作区；推送后应清空 |
| `~/.ollama/models/` | 本机 Ollama；生成套件来源 |

## 5. 相关

| 文档 / 脚本 | 说明 |
|-------------|------|
| [`T2S_PERSONAL_ROOT.md`](T2S_PERSONAL_ROOT.md) | 极空间统一根 |
| [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) | 远程部署 Runbook |
| `engine/ops/t2s_paths.py` | 路径常量 |
| `engine/ops/zspace_offline_deploy.py` | 上传/下载/暂存 |
| `scripts/publish_offline_deploy_to_zspace.py` | 推送离线交付 |
| `scripts/stage_offline_deploy_from_zspace.py` | 部署前暂存 |
