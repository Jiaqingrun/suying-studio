# 体验期功能锁 / 时间锁

> 状态：**0.5.5 已实现并纳入发布门禁**。冲突时：`DEV_LOCK` / `HARD_LOCKS` / [`TRUSTED_OFFLINE_DELIVERY.md`](TRUSTED_OFFLINE_DELIVERY.md) > 本文。

## 目标

提供离线、设备绑定的三日全功能体验；到期立即锁定全部业务功能。**时间锁求值与年期 term 共用** [`engine/security/entitlement.py`](../engine/security/entitlement.py)；term 转正默认签发年期见 [`TERM_LICENSE_PLAN.md`](TERM_LICENSE_PLAN.md)。不设联网心跳、远程 kill-switch 或只读宽限。

## Envelope 扩展

| 字段 | 说明 |
|------|------|
| `license_kind` | `trial` \| `perpetual` \| `term`；兼容旧 `perpetual: true` 许可证 |
| `trial_days` | trial 固定 `3`（3×24h） |
| `lock_mode` | trial / term 固定 `hard_all` |
| `ops_unlock_allowed` | trial / term 为 `true`；高级设置密码仅解锁当前 App 会话（term 客户 UI 不展示该入口） |
| `expires_at` + `clock_anchor` | 签名 UTC 截止与签发/激活锚点；Keychain 持久 `max_seen_utc` 防回拨；早于锚点、显著回拨或达到截止一律锁定 |
| `features[]` | 体验期为全功能；正式许可证仍按签发 features 生效 |

## 行为

1. 运维签发 `trial`，或更高 `issue_seq` 的 `term`（默认转正）/ `perpetual`（特批）。
2. 试用期间全功能，不设每日制作或发布额度。连续失败熔断、发布单槽、READY_GATE、内容占用和结果不明禁重发仍照常生效。trial **可显示剩余时间**；term **不显示**（无感直至到期）。
3. 到期或时钟回拨时 UI 定时复检并全屏锁、停止业务引擎；包内引擎中间件仅放行 health/license，其余业务 API 返回 `423 LICENSE_LOCKED`，没有只读宽限。
4. `ops_unlock_allowed=true` 时复用高级设置 Keychain 盐化 SHA-256 验证器，只允许本次进程会话继续；正式解锁仍须导入有效许可证。
5. 复制许可证跨机仍失败（设备键绑定不变）。

## 兼容

- 新签发许可证使用 schema v2，不包含制作/发布日额度字段。
- 已签发 schema v1 的 `daily_produce_cap=10` / `daily_upload_cap=10` 仅用于验证旧签名合同，运行时完全忽略，不展示、不计数、不阻断。
- `license_daily_usage` 与 `license_quota_reservations` 已从权威库迁移删除；发布事实继续以 publication group/target 为准。

## 不做

联网激活服务器、远程 kill-switch、明文运维密码、到期只读宽限、环境变量生产旁路。
