# 速影 · 主仓备份（T2S 极空间）

> **现行（2026-08-23）**：开发文档 + Git 历史 + **运维 Keychain 明文** → **极空间本地 API** 推到 T2S「速影/主仓备份」。  
> **禁止**默认依赖 NFS 挂载 `~/QR/dev/T2s/ZSPACE`。  
> 与客户更新仓 [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) **分离**；统一根见 [`T2S_PERSONAL_ROOT.md`](T2S_PERSONAL_ROOT.md)。权威索引见 [`README.md`](README.md)。

## 1. 目标与边界

| 项 | 约定 |
|----|------|
| 目标 NAS | T2S `T0210023G0UWV` |
| UI 路径 | 个人空间 → **M.2存储11/速影/主仓备份/** |
| API 路径 | `/nvme11/my/data/速影/主仓备份` |
| 本机暂存 | `~/Suying/ops/repo-backup/` |
| 状态 | `~/.qr/suying-repo-backup-state.json` |
| 日志 | `~/.qr/logs/suying-repo-backup.log`（**不含**密钥明文） |

**包含：**

- `git/速影.bundle`：全部分支/标签（`git bundle create --all`）
- `snapshot/docs/`：工作区文档快照（含 `docs/archive/`）
- 根元数据：`PROJECT.md`、`AGENTS.md`、`.cursor/rules/*` 等
- `keys/ops-keychain.json`：**运维机 Keychain 明文**（所有者授权：个人 NAS、不加密）
- `manifest.json`：HEAD、指纹、文件清单（仅账户名，不含密钥）

**不包含：** 片库 / 成片 / Cookie / `apps` 构建产物 / `docs/legal/source-offer` 大源码包。  
**仍禁止：** 密钥进入 Git、`速影/更新包`、团队空间、客户交付物。

### 密钥范围（换机要什么）

| Keychain | 账户 | 用途 |
|----------|------|------|
| `com.qr.suying.release-signing` | `release-v1` | **发布/许可签名信任根**（换机必须） |
| `com.qr.suying-ops` | `vault-master-key-v1` | 辅助工具保险库主密钥 |
| `com.qr.suying` | `advanced-settings-password` | 本机高级设置锁 |
| `com.qr.suying.license` | `device-binding-secret-v1` 等 | **本机**设备绑定（仅恢复同一运维机安装；客户换机仍须重签） |

## 2. 手动推送

```bash
cd ~/QR/dev/速影
python3 scripts/backup_repo_to_zspace.py          # 有变动才推（含密钥指纹）
python3 scripts/backup_repo_to_zspace.py --force  # 强制重建上传
python3 scripts/backup_repo_to_zspace.py --dry-run

# 仅查看/导出密钥（不打印明文）
python3 scripts/ops_keychain_backup.py list
python3 scripts/ops_keychain_backup.py export
```

须本机极空间客户端已登录且选中 T2S。

## 3. 换电脑恢复

```bash
# 新机：登录同一 T2S 账号，拉代码仓后：
cd ~/QR/dev/速影
python3 scripts/ops_keychain_backup.py restore --from-t2s

# 或网页/客户端下载 keys/ops-keychain.json 后：
python3 scripts/ops_keychain_backup.py restore --from ~/Downloads/ops-keychain.json

# 确认发布公钥仍匹配仓库内 trusted_release_keys.json（不要 --rotate）
python3 scripts/sign_update_release.py init-key

# 如需整仓代码：
git clone /path/to/速影.bundle 速影-restored
```

客户机换机：**不要**拷贝客户设备密钥；按 [`TRUSTED_OFFLINE_DELIVERY.md`](TRUSTED_OFFLINE_DELIVERY.md) 新机申请 → 运维新签发。

## 4. 自动更新（LaunchAgent）

```bash
./scripts/install-repo-backup-agent.sh install    # 默认每 600s；无变动则跳过
./scripts/install-repo-backup-agent.sh status
./scripts/install-repo-backup-agent.sh uninstall
INTERVAL=300 ./scripts/install-repo-backup-agent.sh install   # 可选改间隔
```

标签：`com.qr.suying-repo-backup`。比对 `HEAD` + 文档树指纹 + **密钥指纹**；钥匙串锁定导致导出失败时仍推文档/Git，并在日志标明 keys skipped。

## 5. 相关入口

| 入口 | 用途 |
|------|------|
| `scripts/backup_repo_to_zspace.py` | 指纹检测 + API 推送（含 keys） |
| `scripts/ops_keychain_backup.py` | Keychain 导出 / 换机恢复 |
| `scripts/install-repo-backup-agent.sh` | 安装/卸载自动任务 |
| [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) | App 更新仓（不要混用） |
| [`TRUSTED_OFFLINE_DELIVERY.md`](TRUSTED_OFFLINE_DELIVERY.md) | 发布密钥职责与客户换机合同 |
