# 速影 · 行业包备份（T2S 极空间）

> **现行（2026-08-23）**：行业包 + 起步种子 + 客户覆写 → **极空间本地 API** 推到 T2S「速影/行业包备份」。  
> **禁止**默认依赖 NFS 挂载 `~/QR/dev/T2s/ZSPACE`。  
> 与主仓备份 [`REPO_BACKUP_ZSPACE.md`](REPO_BACKUP_ZSPACE.md)、客户更新仓 [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) **分离**；统一根见 [`T2S_PERSONAL_ROOT.md`](T2S_PERSONAL_ROOT.md)。权威索引见 [`README.md`](README.md)。

## 1. 目标与边界

| 项 | 约定 |
|----|------|
| 目标 NAS | T2S `T0210023G0UWV` |
| UI 路径 | 个人空间 → **M.2存储11/速影/行业包备份/** |
| API 路径 | `/nvme11/my/data/速影/行业包备份` |
| 本机暂存 | `~/Suying/ops/industry-pack-backup/` |
| 状态 | `~/.qr/suying-industry-pack-backup-state.json` |
| 日志 | `~/.qr/logs/suying-industry-pack-backup.log` |

**包含：**

- `current/industry/<id>/`：去品牌行业包（`pack.json` + `meta.json`）
- `current/seeds/<id>/`：起步种子（如 `building-supply-starter`）
- `current/customers-overlay/<客户名>/`：客户覆写（品牌、词池、禁词样例等）
- `releases/<stamp>_<label>/`：冻结基线（完整树，只增不改）
- 根 `MANIFEST.json` / `README.md`

**不包含：** 音色包（`configs/voice_packs/`）、片库、成片、db、Cookie、密钥、App 更新包；客户目录内的演示视频/压缩包（`.mp4` / `.mov` / `.zip` 等，避免 overlay 膨胀）。小体积品牌图（如 `logo.png`）可进。

> 注：首次 `…_baseline` 快照若曾含 `demo_branded.mp4`，可在极空间手动删该文件；此后推送不再上传视频。

**仓库仍是编辑真相源**；极空间是灾备 + 人眼对照仓。禁止只在极空间手工建文件夹当真相。

## 2. 远程目录合同

```text
/nvme11/my/data/速影/行业包备份/
  README.md
  MANIFEST.json
  current/
    industry/
      building-supply/
      life-service/
      _blank/
    seeds/
      building-supply-starter/
    customers-overlay/
      北京始峰伟业/
      北京始峰五金/
      臻享丽人/
  releases/
    2026-08-21T……Z_baseline/
      MANIFEST.json
      industry/…
      seeds/…
      customers-overlay/…
```

### 防混乱硬规则

1. **一级目录只用行业 id / seed id / 客户目录名**；中文别名只写在 `meta.json` 的 `aliases`。
2. **禁止**用客户名建行业目录（禁止「始峰版行业包/」）。
3. **禁止**写入「速影/更新包」或团队空间；禁止混入密钥 / Cookie / 片库 / 成片。
4. **一个行业在 `current/industry/` 只有一份**；客户差异只在 `customers-overlay/`。
5. `releases/<stamp>_*/` **创建后禁止覆盖**；纠错只能新打一版。
6. 规范与脚本**只认现有 id**（`life-service`，不另立 `beauty`）。

### 样板映射（口语文案 ≠ 目录名）

| 行业 id | 中文名 / 别名 | 样板客户 |
|---------|---------------|----------|
| `building-supply` | 建材仓配 / 五金批发 | 北京始峰伟业、北京始峰五金 |
| `life-service` | 生活服务·门店护理 / 美容护理 | 臻享丽人 |
| `_blank` | 空行业模板 | — |

## 3. 双轨：current 与 releases

| 模式 | 触发 | 行为 |
|------|------|------|
| 日常 | 手动或 LaunchAgent | 指纹未变 → 跳过；有变 → **整树替换 `current/`** |
| 基线 | `--snapshot` 或首次 `--init-baseline` | 在 `releases/` **新建**带 UTC 时间戳的目录；**不删旧版** |
| 强制 | `--force` | 忽略指纹，重推 `current/` |
| 演练 | `--dry-run` | 只打印将推路径与指纹 |

日常**不自动**打 `releases`，避免每次改一词就堆重复快照。  
保留策略：默认永久保留全部 releases；超过约 50 个时人工归档，**脚本不自动删**。

对 JSON 级资产采用「指纹决定是否推 + 推送时整包替换 current」；`releases` 永远是完整树拷贝。

## 4. 手动推送

```bash
cd ~/QR/dev/速影
python3 scripts/backup_industry_packs_to_zspace.py                 # 有变才推 current
python3 scripts/backup_industry_packs_to_zspace.py --force
python3 scripts/backup_industry_packs_to_zspace.py --snapshot       # 推 current + 新 releases 戳
python3 scripts/backup_industry_packs_to_zspace.py --init-baseline  # 首次：current + …_baseline
python3 scripts/backup_industry_packs_to_zspace.py --dry-run
```

须本机极空间客户端已登录且选中 T2S。

## 5. 如何对照基线（少碰 Git）

1. 打开极空间 → `速影/行业包备份/MANIFEST.json`
2. 看 `packs[].content_sha256`（current）与某条 `releases[].packs[].content_sha256`
3. 若不同：并排打开  
   `current/industry/<id>/pack.json`  
   与  
   `releases/<stamp>_…/industry/<id>/pack.json`  
   对照差异

客户品牌/禁词对照：用 `current/customers-overlay/<客户>/` 与同路径下某 release。

## 6. 何时打基线（`--snapshot`）

- 行业 hooks / 主题大改后
- 新行业 id 入库时
- 客户正式交付前
- 关账或里程碑

新增行业：先在仓库加 `configs/samples/industry/<new-id>/`，再推。  
客户改品牌：只动 `configs/customers/…`，不得复制进 industry。

## 7. 自动更新（LaunchAgent · 仅 current）

```bash
./scripts/install-industry-pack-backup-agent.sh install    # 默认每 3600s；无变动则跳过
./scripts/install-industry-pack-backup-agent.sh status
./scripts/install-industry-pack-backup-agent.sh uninstall
INTERVAL=1800 ./scripts/install-industry-pack-backup-agent.sh install
```

标签：`com.qr.suying-industry-pack-backup`。**不会**自动创建 `releases/`。

## 8. 相关入口

| 入口 | 用途 |
|------|------|
| `scripts/backup_industry_packs_to_zspace.py` | 指纹检测 + API 推送 |
| `scripts/install-industry-pack-backup-agent.sh` | 安装/卸载自动任务（仅 current） |
| [`REPO_BACKUP_ZSPACE.md`](REPO_BACKUP_ZSPACE.md) | 主仓备份（不要混用） |
| [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) | App 更新仓（不要混用） |
| [`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md) §4 | 行业包工程标准 |
