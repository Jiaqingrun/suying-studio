# 速影 · T2S App 更新推送规范

> **现行默认（2026-08-15）**：本机出包并签名 → **极空间本地 API 直传**到 T2S 更新仓（须本机极空间客户端会话可用）。  
> **禁止**默认依赖本机 NFS 挂载 `~/QR/dev/T2s/ZSPACE`。  
> 冲突时：`DEV_LOCK` / `HARD_LOCKS` > 本文 > [`CUSTOMER_INSTALL.md`](CUSTOMER_INSTALL.md) §8。权威索引见 [`README.md`](README.md)。

## 1. 目标与边界

| 项 | 约定 |
|----|------|
| 目标 NAS | T2S `T0210023G0UWV` |
| UI 路径 | 个人空间 → **M.2存储11/速影/更新包/** |
| API 路径 | `/nvme11/my/data/速影/更新包` |
| 本机暂存 | `~/Suying/releases/`（**仅出包中转**；推送并覆盖安装后立即清空，不保留历史包） |
| 本机指针 | `~/Suying/releases/LAST_PUBLISH.json`（仅版本/seq 元数据） |
| 网页暂存（备用） | `~/Suying/releases/t2s-web-stage/<版本>-<构建>/`（上传前保留；产品 ZIP/套件仍立即删） |
| 客户拉更新 | 极空间同步到客户机 `~/Suying/carrier`（不受运维机卸挂载影响） |

**禁止：**

- 把 ZIP 直接拖进共享区 `/nvme11/public`
- 用 NFS 挂载当默认推送通道（可唤醒 NAS、路径易断）
- 覆盖或删除旧 **T2S 仓** `releases/<版本>/<构建>/`（远端历史必须保留）
- 本机 `~/Suying/releases` **堆历史**交付包 / ZIP / DMG / f5 kit
- 先改 `latest.json`、后传 ZIP（顺序写死，见 §3）
- 客户配置 / Cookie / 私钥 / 片库进入更新仓

## 2. 默认闭环（运维机）

```bash
cd ~/QR/dev/速影
RUNTIME_FLAVOR=core ./scripts/release-to-t2s.sh
# 默认：升 patch → 暂存出包 → 极空间 API 直传（PUBLISH=1）
#        → 本机覆盖安装（INSTALL_LOCAL=1）→ 立即清空本机 releases 产物（KEEP_LOCAL=0）
open -a "速影 Studio"
```

须本机极空间客户端已登录且本地 API 会话可用；脚本会回读并递增 `release_seq`。仍禁止把 NFS 挂载写成默认通道。调试需留本地套件时显式 `KEEP_LOCAL=1`。

仅当用户**当面**要求「网页推送 / 网页暂存」时才允许：

```bash
PUBLISH=0 WEB_STAGE=1 RUNTIME_FLAVOR=core ./scripts/release-to-t2s.sh
# 再按暂存目录 WEB_UPLOAD.txt 顺序上传（见 §3）
```

仅打本地、不推仓：

```bash
PUBLISH=0 WEB_STAGE=0 RUNTIME_FLAVOR=core ./scripts/release-to-t2s.sh
```

## 3. 网页上传顺序（备用通道硬顺序）

仅在 `PUBLISH=0 WEB_STAGE=1` 时使用。对应当前暂存树（与远端一致）：

```text
速影/更新包/
├── releases/<版本>/<构建日期>/   ← 先传整目录
│   ├── 速影-…-product-….zip
│   ├── SHA256.txt
│   ├── release.json
│   ├── release.json.sig
│   └── 更新说明.md
├── releases/<版本>/更新说明.md
├── README.md / 更新说明.md（若有）
├── latest.json.sig                 ← 倒数第二
└── latest.json                     ← 最后
```

上传后至少核对 ZIP **大小**；有条件再回读 SHA256。`release_seq` 必须严格递增。API 直传由 `publish_update_repo.py` 按同序完成并回读复核。

## 4. 与客户机的关系

- **推送**：运维把签名包放进 T2S 更新仓（本规范）。  
- **拉取**：客户机极空间只读同步「速影/更新包」→ `~/Suying/carrier`。  
- 运维机卸掉 `~/QR/dev/T2s/ZSPACE` **不影响**客户拉更新；只影响「把挂载当本地盘写」的旧习惯。

路径总表见 [`T2S_PERSONAL_ROOT.md`](T2S_PERSONAL_ROOT.md)。

## 5. 相关入口

| 入口 | 用途 |
|------|------|
| `scripts/release-to-t2s.sh` | 正式升版出包；**默认 API 直传**（`PUBLISH=1`） |
| `PUBLISH=0 WEB_STAGE=1 …` | 备用：签名网页暂存，再人工网页上传 |
| `scripts/publish_update_repo.py` | 底层签发/上传（默认 API；`--stage-dir` 仅暂存） |
| [`CUSTOMER_INSTALL.md`](CUSTOMER_INSTALL.md) §8 | 安装侧说明 |
| [`.cursor/rules/product-package.mdc`](../.cursor/rules/product-package.mdc) | Agent 打包规则 |
| [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) | 客户机远程覆盖（与推仓分离） |
