# 可信离线交付与单机授权合同

> 状态：`GLicense / GOfflineDepot` 已于 2026-07-29 获用户批准。

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 1. 固定产品边界

- 许可：默认 **单机年期**（`term` / 365 天）；`perpetual` 仅祖父证或运维特批；`trial` 为三日体验。换机必须由运维重新签发，旧设备私钥不迁移。年期细则见 [`TERM_LICENSE_PLAN.md`](TERM_LICENSE_PLAN.md)。
- 网络：安装和更新全过程不得依赖公网；运行后的 Edge TTS 等既有联网能力不在离线约束内。不设客户机联网心跳或远程 kill-switch。
- 首期架构：正式交付 Apple Silicon。Intel 必须另行构建、签名和验证，禁止混用 ARM 制品。
- 信任根：无 Developer ID 阶段使用运维离线 Ed25519 发布公钥/私钥体系。ad-hoc codesign 只校验 Bundle 结构，不能证明发布者身份。
- 威胁边界：阻止误复制、跨机复用、仓库篡改、降级和普通程序修改，并提高逆向成本；不承诺对抗持有客户机管理员权限且可替换整个 App 的专业攻击者。

## 2. 密钥职责

| 密钥 | 算法 | 私钥位置 | 用途 |
| --- | --- | --- | --- |
| 发布密钥 | Ed25519 | 仅运维机 Keychain；**换机**：所有者授权可将明文导出到个人 T2S「速影/主仓备份/keys/」（见 [`REPO_BACKUP_ZSPACE.md`](REPO_BACKUP_ZSPACE.md)）；仍禁止进入 Git、App、**速影/更新包**、团队空间、日志 | 签名 release、runtime manifest、许可证 |
| 设备密钥 | Secure Enclave P-256 | 客户机 Secure Enclave | 设备身份和许可请求 |
| 设备回退密钥 | P-256 | 客户机 ThisDeviceOnly Keychain | 无 Secure Enclave 时的兼容回退 |

发布私钥严禁进入 Git、App、**速影/更新包**、日志、安装回执和测试 fixture。测试必须使用独立、公开标记的测试密钥。运维机换机备份见 [`REPO_BACKUP_ZSPACE.md`](REPO_BACKUP_ZSPACE.md)（个人主仓 `keys/`，明文由所有者授权）。

## 3. 签名发布合同

每个不可变 release manifest 至少包含：

- `schema_version`、`product_id`、`release_seq`、`version`、`arch`
- artifact 的相对路径、字节数和 SHA256
- runtime manifest SHA256
- `delivery_id`、脱敏 `customer_ref`
- `created_at`、可选 `expires_at`、`key_id`

客户端验证顺序固定为：

1. 验证 Ed25519 签名和 manifest 自身摘要；
2. 验证 schema、产品、架构、交付水印；
3. 拒绝更低 `release_seq`，并拒绝同序号不同摘要；
4. 验证 artifact 大小和 SHA256；
5. 验证 App、runtime manifest 和许可；
6. 原子切换，启动健康检查失败则回滚。

`force` 不得绕过防降级。首期紧急回滚只允许人工 SSH；自动回滚授权另行设计。

## 4. 单机许可合同（默认年期）

许可 envelope 至少包含：

- `schema_version`、`product_id`、`license_id`
- `device_key_id`、设备公钥摘要
- `delivery_id`、脱敏 `customer_ref`
- 功能集合、`issued_at`、`issue_seq`、`key_id`
- `license_kind`：`term`（默认正式）| `trial` | `perpetual`（特批/祖父）
- term：`term_days=365`、`expires_at`、`clock_anchor`、`lock_mode=hard_all`、`perpetual=false`

React 的 `LicenseGate` 负责展示；Tauri `engine_start` 与打包引擎中间件是强制门禁。
term 客户无感直至到期；到期硬锁见 [`TERM_LICENSE_PLAN.md`](TERM_LICENSE_PLAN.md)。
未授权安装必须以 `PARTIAL: LICENSE_REQUIRED` 结束，不得伪报完整成功。
App 覆盖升级 **不**自动改证。

换机流程固定为：新机生成新设备密钥和请求 → 运维核对台账 → 签发更高 `issue_seq`
的新许可 → 记录旧机状态。Secure Enclave 私钥不得导出或复制。
续签：同机 `issue_seq+1`，`expires_at = 再签 UTC + 365d`（不旧截止顺延）。

## 5. 极空间离线仓合同

仓库由签名元数据和内容寻址对象组成，至少包括：

- 速影 App 和内嵌 Python；
- Apple Silicon Ollama 离线安装器；
- Apple Silicon FFmpeg/ffprobe；
- 通用种子、Ollama manifests、去重模型 blobs；
- `lite/standard/pro/max` profile 引用；
- 许可证 envelope 和安装回执目录。
- `legal` 组件：机器可读 `THIRD_PARTY_MANIFEST.json`、中文交付说明、全部
  LICENSE/COPYING/NOTICE，以及 GPL/LGPL 对应源码材料。

Bootstrap 必须先完成全量哈希与空间预算，再安装。离线模式严禁调用
`brew`、`pip`、`curl`、`ollama pull` 或任何隐式公网下载路径。

外部分发固定 fail-closed：`legal` 组件缺失、未覆盖全部非 legal 组件、
`distribution_status != ready`、存在 gap、许可证正文引用不闭合，或任一
`source_required` 条目缺少实际 CAS 源码对象时，构建、验证、bootstrap 和上传
均必须拒绝。仅记录来源 URL 或书面 source offer 不等于已提供对应源码。

## 6. 验收

- 断网干净机完成安装，网络调用为零；
- 复制许可证到另一台机器后启动失败；
- manifest、包、runtime、模型任一字节篡改均 fail closed；
- release 降级和同序号换包均被拒绝；
- App、工具、模型、设置任一提交失败都恢复旧状态；
- 每阶段继续执行 `scripts/smoke_test.py` 和相关 TypeScript、Cargo、Python 测试。
