# 速影 · 客户安装说明

> 产品对外名称仅为 **速影**。推荐交付路径：**T2S 载体 → 本机镜像 → 安装**。一体 macOS App 内含引擎与嵌入式 Python。不含客户片库、成片、词池或密钥。

## 0. 交付模型（必读）

| 内容 | 在哪里 |
|------|--------|
| 安装包 / 更新 / 配置种子 / 配置备份 | T2S「速影载体/」→ 同步到本机 `~/Suying/carrier` |
| 片库 / 成片 / 词池 | **仅本机**（客户自选路径） |
| 引擎 db / cache | 本机工作区，不上 T2S |

极空间官方账号在**客户电脑**上登录；速影只做引导与检测，不静默造号。

## 1. 你拿到的包里有什么

| 内容 | 说明 |
|------|------|
| `速影.app` / `速影-*.dmg` | 一体包（亦可从载体 `app/` 取得） |
| `安装到-应用程序.sh` | 拷到桌面 / Applications |
| `install-sync-service.sh` | 按当前 macOS 用户安装/卸载极空间载体同步服务 |
| `安装说明.md` / `SHA256.txt` | 本说明与校验 |

**不会包含：** 客户片库视频、成片 mp4、本机密钥、Ollama 模型。

## 2. 安装流程（两段）

### 段 A · 极空间 + T2S 载体

1. 安装 [极空间客户端](https://www.zspace.cn/)，用**对方账号**注册或登录  
2. 添加/选中运维指定的 **T2S** 设备  
3. 打开「速影」→ 首启向导：复查登录 → 绑定 `nas_id` = T2S → 确认本机出现载体镜像（`~/Suying/carrier` 或约定路径）  
4. **只同步** T2S 上的 `速影载体/`（含 `app/` `seed/` `backups/`）；不要同步 `01-片库`

### 段 B · 速影本机工作区

1. 从载体或本包安装 `速影.app`  
2. 向导填写客户名、本机片库/成片/词池路径（可「标准本机目录布局」）
3. （可选）选择载体中的「客户配置种子」；先显示名称、说明和文件清单，确认后仅导入词池、品牌样式、视频规则。默认不导入、不覆盖已有文件，不含视频、数据库、账号或密钥
4. 启动引擎；运维页确认 health、载体可见、`latest.json`
5. （可选）注册更新检查，并安装极空间同步服务
6. 安装 [Ollama](https://ollama.com/download) + 运维页「一键安装推荐模型」；本机 **FFmpeg**

新电脑也可直接运行包内脚本（无需假设用户名是 `qr`）：

```bash
./install-sync-service.sh --nas-user "<极空间账号>" --nas-id "<设备 nas_id>" --nas-name "<显示名称>"

# 卸载服务；默认保留配置，便于重装
./install-sync-service.sh uninstall
```

脚本以当前 `$HOME` 生成所有路径，绑定依据是极空间账号与 `nas_id`，不依赖 NAS 型号名。默认 `carrier_only`，只同步 `/public/速影载体` 与 `~/Suying/carrier`。只有明确同时传入 `--enable-media-sync --volume-uuid <UUID>` 才启用客户媒体同步；启用后须在 App 或为每个客户在 `media_sources` 中绑定团队空间下的**一个**子文件夹（v3 隔离，fail-closed）。服务每次运行都按 UUID 查找当前挂载点，不依赖硬盘名称或固定 `/Users/...` 路径。

数据仍落本机：

```text
~/Suying/data/      ← 工作区 / 设置 / 引擎日志
~/Suying/cache/     ← 抽帧等缓存
~/Suying/carrier/   ← T2S 载体镜像（安装与更新）
```

## 3. 装后日常

- 极空间保持登录并绑 T2S，持续同步载体  
- 速影总览「载体」条：可见性 / 远端版本 / 备份数；有更新时**点击安装**（强制更新会阻断）  
- 配置级「备份到 T2S」：不含片库大媒体  
- **默认不启用**相册/片库团队文件同步  
- **Ollama 旁白文案（可选）**：运维 →「Ollama 旁白文案」打开后，出片时用本机模型把视觉描述改写成口播，并烧录主题表情包；语音合成仍走 Edge 晓晓

## 4. 第二客户预演

用同一 T2S 载体流程：新 Mac → 对方账号登录 → 绑定同一 T2S → 只同步载体 → 新建第二客户本机工作区。不依赖团队文件拉片库。

## 5. 授权与开源

- 1 机买断激活为后续闸门  
- App 内 creative 运行时含 AGPL，须随包保留；对外品牌仅为「速影」

## 6. 开发者打一体包并写入载体

```bash
# 私下上门安装/受控交付包（ad-hoc；首次打开可能需要右键“打开”确认）
cd apps/desktop && npm run package:mac

# 可选：若未来改为陌生用户线上分发，再配置 Developer ID 与公证
cd apps/desktop
CODESIGN_IDENTITY="Developer ID Application: <组织名> (<TEAM_ID>)" \
NOTARY_PROFILE="suying-notary" \
npm run package:mac

# 将已验签、已公证的包写入产品套件/载体
cd ../..
BUILD_APP=0 PUBLISH_CARRIER=1 CARRIER_ROOT=~/Suying/carrier ./scripts/package-product.sh
```

私下安装交付不把 Developer ID / notarization 作为阻断项；ad-hoc 包仍会执行内嵌引擎 `/health=ok` 与 `codesign --verify`。配置 `NOTARY_PROFILE` 时才会等待 Apple 公证并 staple。自动更新要求新旧 App 的签名身份一致；ad-hoc 私下更新需由运维显式允许 `SUYING_ALLOW_ADHOC_UPDATE=1`。

## 7. 运维一键远程部署

客户 Mac 已开 SSH 时，从开发机执行：

```bash
./scripts/deploy-remote.sh \
  --host <SSH别名> \
  --customer-name "<客户名>" \
  --customer-config "configs/customers/<客户名>" \
  --build \
  --smoke-job
```

脚本使用单 ZIP 断点传输，并在远端自动完成 App 原子替换、standalone Python、签名、Ollama/FFmpeg、客户配置、极空间当前 T2S 绑定、同步/更新 Agent、Tauri CORS、health 和真实建任务验收。客户片库为空时 `--smoke-job` 会明确返回 `NEED_MEDIA`，不会伪报部署完成。

详见 [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md)、[`V8_STORAGE.md`](V8_STORAGE.md)、[`GSTAB_RUNBOOK.md`](GSTAB_RUNBOOK.md)。
