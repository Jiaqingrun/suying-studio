# 年期许可（term）合同

> Gate：**GTermLicense**。冲突时：`DEV_LOCK` / `HARD_LOCKS` / [`TRUSTED_OFFLINE_DELIVERY.md`](TRUSTED_OFFLINE_DELIVERY.md) > 本文。
> 与体验期共用时间锁求值；差异见本文与 [`TRIAL_LICENSE_PLAN.md`](TRIAL_LICENSE_PLAN.md)。

## 目标

- 正式客户机默认 **单机年期**授权（`license_kind=term`，`term_days=365`）。
- 客户 **无感直至到期**：不展示剩余天数、不弹即将到期。
- 到期或时钟回拨：全屏硬锁 + 业务 `423 LICENSE_LOCKED`；文案固定「授权已到期，请联系运维人员」。
- 运维中心以台账 `expires_at` 为提醒真相；远程再签并仅导入许可证后自动恢复。
- 不设公网心跳 / 远程 kill-switch。

## Envelope 字段

| 字段 | term 合同 |
|------|-----------|
| `license_kind` | `term` |
| `term_days` | 固定 `365`（生产签发拒绝其他值） |
| `perpetual` | `false` |
| `expires_at` / `clock_anchor` | 签名 UTC 截止与签发锚点；Keychain `max_seen_utc` 防回拨 |
| `lock_mode` | `hard_all` |
| `ops_unlock_allowed` | `true`（仅运维现场会话救急；**客户 UI 不展示**） |
| `issue_seq` | 单调；同机续签必须更高 |

保留：`trial`（3×24h，可显示剩余）；`perpetual`（祖父证/特批）。

## 续签

```text
新 expires_at = 本次签发 UTC + 365 天
issue_seq    = 旧证 + 1（同 device_key_id）
```

从**再签时刻**起算，不按旧截止顺延「攒年」。App 覆盖升级 **不**自动改证。

## 客户 / 运维分流

| 侧 | 行为 |
|----|------|
| 客户 term 有效期 | 零许可噪声 |
| 客户到期 | 全屏「授权已到期，请联系运维人员」；无导入/ops 主按钮 |
| 运维中心 | 剩余天、紧急度排序、T-7 任务、人工确认签发、远程仅导入 |

## 不做

联网激活服务器、客户自助续期页、续签重推模型、批量静默把 perpetual 转 term、宣称对抗本机管理员。
