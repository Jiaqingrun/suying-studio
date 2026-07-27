# 速影 V8 — 存储与 T2S 载体

## 定位（写死）

**T2S 只做速影载体**：安装包、种子配置、配置级备份、App 更新清单。
**不做**片库 / 成片 / 渲染缓存 / 引擎 db 的日常同步。

| 角色 | T2S `速影载体/` | 客户本机 |
|------|-----------------|----------|
| 安装包 + `latest.json` | ✓ `app/` | 更新服务读取本机镜像 |
| 行业包 / 默认种子 | ✓ `seed/` | 安装时落入本机 |
| 配置级备份 | ✓ `backups/<客户>/` | 一键导出/恢复（无大媒体） |
| 片库 / 成片 | ✗ | 客户自选本地或自有盘 |
| db / cache / render | ✗ | `~/Suying/…` 或外置工作区 |

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

推荐本机树（亦可落在外置盘，仍不进载体）：

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

### 可选：按客户文件夹隔离的媒体同步（v3）

若运维显式启用媒体同步（`--enable-media-sync --volume-uuid <UUID>`），配置使用 **`media_sources`**：每个客户绑定团队空间 `/public` 下的**一个**子文件夹（如 `手机相册备份/某人`），只拉取到该客户 `01-片库/` 下对应目录。

- 未配置有效来源时同步 fail-closed，不会拉整个 `/public`
- 第二客户 = 在 App 选择其远端文件夹并登记，与第一客户来源互不重叠
- 更换来源会归档旧目录下 ready 素材，避免跨来源 cliplet 混用

## 安装两段

1. **极空间账号**：本机装客户端 → 对方账号登录 → 绑定 T2S `nas_id` → 只同步 `速影载体/`
2. **速影本机**：从载体装 App → 建片库路径 → 注册更新 LaunchAgent（`scripts/install-carrier-update-agent.sh`）

未登录 / 绑错 NAS → 安装向导不可完成。

## 刻意不做

- 片库经极空间团队空间日更同步（非默认）
- db 全量上 T2S
- 公网 CDN 发版
