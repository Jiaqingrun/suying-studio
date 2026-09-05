# 速影 · 客户安装说明

> 产品对外名称仅为 **速影**。推荐交付路径：**T2S 载体 → 本机镜像 → 安装**。一体 macOS App 内含引擎与嵌入式 Python。不含客户片库、成片、词池或密钥。

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 0. 交付模型（必读）

| 内容 | 在哪里 |
|------|--------|
| 签名安装包 / 更新 / 离线工具与模型 | T2S「速影/更新包/」→ 只读同步到本机 `~/Suying/carrier/app` |
| 片库 / 成片 / 词池 | **仅本机**（客户自选路径） |
| 引擎 db / cache | 本机工作区，不上 T2S |

极空间官方账号在**客户电脑**上登录；速影只做引导与检测，不静默造号。

## 1. 你拿到的包里有什么

| 内容 | 说明 |
|------|------|
| `速影.app` / `速影-*.dmg` | 一体包（亦可从载体 `app/` 取得） |
| `安装到-应用程序.sh` | 拷到桌面 / Applications |
| `install-sync-service.sh` | 按当前 macOS 用户安装/卸载极空间载体同步服务 |
| `bootstrap/suying-bootstrap-<arch>` | 无 Python 前提下验签并物化离线 CAS；首次执行由运维核对已知 SHA256 |
| `安装说明.md` / `SHA256.txt` | 本说明与校验 |

**不会包含：** 客户片库视频、成片 mp4、本机许可证、设备密钥或发布私钥。Ollama、FFmpeg 与模型位于签名离线 CAS 仓，不要求客户机现场公网下载。

## 2. 安装流程（两段）

### 段 A · 本机检测 + 极空间 + T2S 载体

1. 打开「速影」→ 首启向导先检测芯片、统一内存、架构、磁盘与依赖，预览并应用 Install Profile v1
2. 安装计划自动选择模型；模型体积和未通过门禁必须在向导内可见。低于 16GB 默认只装向量模型，任何档位都不自动安装 27B
3. 安装 [极空间客户端](https://www.zspace.cn/)，用**对方账号**注册或登录
4. 添加/选中运维指定的 **T2S** 设备
5. 向导复查登录 → 绑定 `nas_id` = T2S → 确认本机出现载体镜像（`~/Suying/carrier` 或约定路径）
6. **只同步** T2S 上的 `速影载体/`（含 `app/` `seed/` `backups/`）；不要同步 `01-片库`

### 段 B · 速影本机工作区

1. 从载体或本包安装 `速影.app`  
2. 向导填写客户名、本机片库/成片/词池路径（可「标准本机目录布局」）
3. （可选）选择载体中的「客户配置种子」；先显示名称、说明和文件清单，确认后仅导入词池、品牌样式、视频规则。默认不导入、不覆盖已有文件，不含视频、数据库、账号或密钥
4. 首装生成「速影-许可请求.json」；运维离线签发单机永久许可证并导入
5. Rust 与 Python 双门禁验签通过后启动引擎；运维页确认 health、载体可见、签名 `latest.json`
6. 注册更新检查并安装极空间只读同步服务；从离线仓按机型安装 Ollama、FFmpeg 与模型

新电脑也可直接运行包内脚本（无需假设用户名是 `qr`）：

```bash
./install-sync-service.sh --nas-user "<极空间账号>" --nas-id "<设备 nas_id>" --nas-name "<显示名称>"

# 卸载服务；默认保留配置，便于重装
./install-sync-service.sh uninstall
```

脚本以当前 `$HOME` 生成所有路径，绑定依据是极空间账号与 `nas_id`，不依赖 NAS 型号名。私下更新传 `--update-remote-root /nvme11/my/data/速影/更新包`，只读同步到 `~/Suying/carrier/app`，客户机不得向发布仓反推。只有明确同时传入 `--enable-media-sync --volume-uuid <UUID>` 才启用客户媒体同步。

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

- 已启用单机永久授权；设备绑定由硬件 UUID + Keychain 随机密钥构成，许可证复制到另一台机器会失败
- 换机由运维核对台账后重新签发更高 `issue_seq`；不得复制旧机设备密钥
- App 内 creative 运行时含 AGPL，须随包保留；对外品牌仅为「速影」

## 6. 开发者打一体包并写入载体

```bash
# 私下上门安装/受控交付包（ad-hoc；首次打开可能需要右键“打开”确认）
cd apps/desktop && npm run package:mac

# 客户 VIDEO_LOCK.voice.provider 不是 clone 时，推荐小型 core flavor
cd apps/desktop && RUNTIME_FLAVOR=core npm run package:mac

# 可选：若未来改为陌生用户线上分发，再配置 Developer ID 与公证
cd apps/desktop
CODESIGN_IDENTITY="Developer ID Application: <组织名> (<TEAM_ID>)" \
NOTARY_PROFILE="suying-notary" \
npm run package:mac

# 组装产品套件；远程 ZIP 不含 DMG，人工安装 DMG 独立输出
# 该命令不会再写无签名 carrier latest.json
cd ../..
BUILD_APP=0 ./scripts/package-product.sh

# 可选：把运维端已校验的四档离线模型套件并入产品包
MODEL_BUNDLE_ROOT="$HOME/Desktop/速影-offline-models-macos-$(uname -m)" \
  BUILD_APP=0 ./scripts/package-product.sh
```

私下安装交付不把 Developer ID / notarization 作为阻断项；ad-hoc 只证明包结构未在签名后变化。发布者身份由内嵌 Ed25519 公钥验证的 release/runtime/许可证三条签名链建立，不再使用 `SUYING_ALLOW_ADHOC_UPDATE` 旁路。

## 7. 运维一键远程部署（唯一正式路径）

客户 Mac 已开 SSH 时，从开发机执行统一方案（默认离线，见 [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) §0）：

```bash
./scripts/deploy-remote.sh \
  --host <SSH别名> \
  --customer-name "<客户名>" \
  --customer-config "configs/customers/<客户名>" \
  --license ~/Suying/runtime/security/license-ledger/<已签发>.suying-license \
  --offline-tools "$HOME/Desktop/速影-offline-verify-python-<profile>" \
  --build
```

有片库素材再加 `--smoke-job`。脚本只读探测 Install Profile，Configure / Apply 只消费不可变 `plan_id`；LAN 断点传输离线模型与 offline-tools，默认禁止 `ollama pull` / Homebrew。随后单 ZIP 原子替换 App、standalone Python、签名验签、客户配置、T2S 绑定、health + Tauri CORS；（可选）真实建任务。片库空 + `--smoke-job` → `PARTIAL: NEED_MEDIA`；未传 → `PARTIAL: SMOKE_NOT_REQUESTED`。二者都不写成功回执。完整验收通过后，本次独立传输 staging 会立即清理；失败/PARTIAL 默认保留 24 小时供断点续传与排障，过期后在受限目录内清理。旧 App 仅保留最近 1 个回滚点。

`deploy-remote --build` 会读取客户 `brand/VIDEO_LOCK.json` 自动选择 `core` 或
`clone` flavor，并在传输前核对 `BUNDLE_FLAVOR`；客户不需要手动选择。
2026-08-01 本机 0.5.5 core 实测：App 330MiB、产品 ZIP 129MiB、独立 DMG
165MiB。

许可证必须以 GUI 会话 Keychain 中的真实 `device_key_id` 签发；SSH 下误生成的请求不得采用。权威库应落本机 APFS（`~/Suying/data`）；Chrome `user-data-dir` 固定为本机 APFS `~/Library/Application Support/com.qr.suying/chrome-profiles`，不得放 ExFAT/SMB/同步工作区。交付运行必须由 App 图形会话启动引擎，不得以 SSH / `nohup` 引擎作为完成状态。见 [`STORAGE_SYNC_SAFETY.md`](STORAGE_SYNC_SAFETY.md)。

只有真实任务验收完整通过后，`~/Suying/runtime/install/` 才原子写入 `0600` 脱敏回执。

**embed 排除陷阱：** 只排除 `scripts/publish_*.py` 等运维脚本；禁止 `**/publish_*.py`。出包后确认 `chrome_runtime` 含 LaunchServices 启动（禁止直接 Popen Chrome 二进制）。

**覆盖安装保留面：** 只替换 `速影.app`；不得删除本机 APFS Chrome profile 或其 DB 账号绑定；不得用 `profile.sample.json` 整表覆盖 `profile_json`。热补 `.py` 不算交付。

详见 [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md)、[`TRUSTED_OFFLINE_DELIVERY.md`](TRUSTED_OFFLINE_DELIVERY.md)。
打包规则（Agent）：[`.cursor/rules/product-package.mdc`](../.cursor/rules/product-package.mdc)。

## 8. 极空间正式更新仓库

正式发布统一走：**升版 + 暂存出包 + 极空间 API 直传到 T2S + 本机覆盖安装 + 立即清空本机暂存**（**不写桌面、不堆历史包**）。  
权威规范：[`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md)。

**2026-08-15 起默认 API 直传**：`release-to-t2s.sh` 默认 `PUBLISH=1`（须本机极空间客户端会话）。**2026-08-21 起**默认 `INSTALL_LOCAL=1`、`KEEP_LOCAL=0`：推送成功后覆盖本机 App，并删除 `~/Suying/releases` 内套件/ZIP/DMG（仅留 `LAST_PUBLISH.json`）。**禁止**默认依赖本机 NFS 挂载 `~/QR/dev/T2s/ZSPACE`。仅用户显式要求时才允许 `PUBLISH=0 WEB_STAGE=1`（网页暂存）、`PUBLISH=0 WEB_STAGE=0`（只打本地并保留产物）或 `KEEP_LOCAL=1`（调试留套件）。

**默认 flavor = core。** 本地 clone 旁白依赖 **App 外 F5 Runtime Kit**（见 [`VOICE_CLONE.md`](VOICE_CLONE.md)）：覆盖安装**只换 App**，**不得**删 `~/Suying/runtime/f5_site_packages`。Kit 推 T2S 后本机亦不保留历史包；勿绑进每次 core ZIP。

```bash
RUNTIME_FLAVOR=core ./scripts/release-to-t2s.sh
open -a "速影 Studio"
# 若本机要 clone：
./scripts/verify_f5_overlay_post_install.sh --require-overlay-source
```

等价底层步骤（API 直传；完成后须删除本机 `~/Suying/releases` 产物）：

```bash
python3 scripts/bump_app_version.py --bump patch
BUILD_APP=1 RUNTIME_FLAVOR=core ./scripts/package-product.sh
python3 scripts/publish_update_repo.py \
  --package ~/Suying/releases/速影-<ver>-product-macos-<arch>-<flavor>.zip \
  --version <ver> \
  --build-date <YYYYMMDDTHHMMSSZ> \
  --release-seq <严格递增整数> \
  --runtime-manifest ~/Suying/releases/速影-<ver>-product-macos-<arch>-<flavor>/速影\ Studio.app/Contents/Resources/runtime/RUNTIME_MANIFEST.json \
  --customer-ref <脱敏客户引用> \
  --delivery-id <该客户交付ID>
bash ~/Suying/releases/速影-<ver>-product-macos-<arch>-<flavor>/安装到-应用程序.sh
# 然后删除本机套件/ZIP/DMG（正式入口 release-to-t2s.sh 已默认完成）
```

备用网页暂存：在上述命令加 `--stage-dir ~/Suying/releases/t2s-web-stage/<ver>-<build>`，再按 `WEB_UPLOAD.txt` 上传。

目标为 T2S 个人空间 `M.2存储11/速影/更新包`（API：
`/nvme11/my/data/速影/更新包`）。版本包先写入
`releases/<版本>/<构建日期>/`；远端包回读并复算 SHA256 后，最后提交
`latest.json{,.sig}`（**`latest.json` 必须最后上传**）。每个版本目录另有可读 `更新说明.md`（后补说明**不得**改写
已签名的 `release.json` / ZIP）。客户端依次执行验签、水印、架构、防降级、包哈希和 runtime
完整性门禁。不得覆盖或删除 **T2S 仓**旧版本，禁止误传到共享区 `/nvme11/public`。
本机 `~/Suying/releases/` 仅出包暂存，推后清空；离线工具/模型：`~/Suying/offline/`。
发版前须在 [`packaging/release_notes.json`](../packaging/release_notes.json) 登记该版本说明；全文见 [`RELEASE_NOTES.md`](RELEASE_NOTES.md)。
