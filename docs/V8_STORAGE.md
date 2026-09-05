# 速影 V8 — 存储与 T2S 载体


> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 默认原则（Local-first · 2026-08 写死）

> **硬锁 L18（冻结 · 2026-08-07）**：下列拓扑与语义**禁止再改**，除非用户当面授权修订 HARD_LOCKS。详见 [`HARD_LOCKS.md`](HARD_LOCKS.md) L18。

1. **主盘默认**：权威库 `~/Suying/data`；媒体 `~/Movies/速影工作区`；生产/同步不依赖外置盘。
2. **自动同步默认目标 = 主盘**：载体 → `~/Suying/carrier`；可选媒体拉取 → Movies 工作区。
3. **外置盘仅路径覆盖**：用户或运维显式改路径后才走外卷；`external_required` 默认 `false`。
4. **外卷权威库仍 fail-closed**：`data_root` 在外卷且缺失时拒绝 mkdir 空库。
5. **禁止回退**：不得再将 `QR-Volume` / 外置盘布局写成默认真相；不得默认 `external_required=true`；不得把「插盘」当自动同步前置条件。

## 定位（写死）

**T2S 只做速影载体**：安装包、种子配置、配置级备份、App 更新清单。
**不做**片库 / 成片 / 渲染缓存 / 引擎 db 的日常同步。

| 角色 | T2S `速影载体/` | 客户本机 |
|------|-----------------|----------|
| 安装包 + `latest.json` | ✓ `app/` | 更新服务读取本机镜像 |
| 行业包 / 默认种子 | ✓ `seed/` | 安装时落入本机 |
| 配置级备份 | ✓ `backups/<客户>/` | 一键导出/恢复（无大媒体） |
| 片库 / 成片 | ✗ | 客户自选本地或自有盘 |
| db / cache / render | ✗ | `~/Suying/…` 本机 APFS 工作区 |

## 载体目录契约

```text
T2S:/速影载体/          # 或本机镜像 ~/Suying/carrier
├── app/
│   ├── latest.json     # version, force, sha256, notes, package
│   └── 速影-<ver>.dmg
├── seed/
│   ├── industry/
│   ├── templates/
│   └── install-defaults.json
└── backups/
    └── <customer_id>/<timestamp>/
```

同步白名单：**仅**上述根。禁止把 `01-片库` 配进默认同步。

## 本机客户工作区（媒体，不同步进 T2S）

推荐本机树（数据库、缓存和渲染中间物固定在本机 APFS；客户媒体可落外置盘）：

```text
~/Suying/customers/<客户名>/
├── 01-片库/     ← library_root
├── 02-成片/     ← output_root（ready / published / review / failed）
└── 03-词池/     ← keyword_pack_path
~/Suying/data/   ← db / settings（工作区）
~/Suying/cache/
~/Suying/carrier/  ← 极空间同步下来的载体镜像（只读安装/更新）
```

历史布局 `~/QR-Volume/极空间团队文件同步/速影客户/…` 仍可用；**相册↔片库团队同步为 legacy，默认关**（`media_sync_enabled: false`，`sync_mode: carrier_only`）。

`montage.db`、Chrome profile 与其他依赖 SQLite/WAL 的状态禁止放在 ExFAT、SMB 或任何同步目录。同步脚本对 `~/Suying/data`、`cache`、`render`、`runtime` 和 App Support 状态目录执行 fail-closed 写入保护。

### 可选：按客户文件夹隔离的媒体同步（v3）

若运维显式启用媒体同步（`--enable-media-sync --volume-uuid <UUID>`），配置使用 **`media_sources`**：每个客户绑定团队空间 `/public` 下的**一个**子文件夹（如 `手机相册备份/某人`），只拉取到该客户 `01-片库/` 下对应目录。

- 未配置有效来源时同步 fail-closed，不会拉整个 `/public`
- 第二客户 = 在 App 选择其远端文件夹并登记，与第一客户来源互不重叠
- 更换来源会归档旧目录下 ready 素材，避免跨来源 cliplet 混用
- `--only-media-customer <customer_key>` 可拆分独立进程；仅目的地不重叠的规则允许并行，重叠规则由目的地租约拒绝

## 安装两段

1. **极空间账号**：本机装客户端 → 对方账号登录 → 绑定 T2S `nas_id` → 只同步 `速影载体/`
2. **速影本机**：从载体装 App → 建片库路径 → 注册更新 LaunchAgent（`scripts/install-carrier-update-agent.sh`）

未登录 / 绑错 NAS → 安装向导不可完成。

## 刻意不做

- 片库经极空间团队空间日更同步（非默认）
- db 全量上 T2S
- 公网 CDN 发版

## 磁盘清理

可删范围与禁删合同见 [`DISK_CLEANUP_LOCK.md`](DISK_CLEANUP_LOCK.md)（`cache/temp|frames|proxies`、`render`、过期 failed、退休 published；禁止 `cache/library`、片库、DB、待审 ready/review）。
