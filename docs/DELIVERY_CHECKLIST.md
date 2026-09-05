# 发版 / 客户机交付对照清单

> 冲突时：`DEV_LOCK` / `HARD_LOCKS` / `REMOTE_DEPLOY` > 本文。  
> 深度优化 P4：[`DEEP_OPTIMIZATION_PLAN.md`](DEEP_OPTIMIZATION_PLAN.md)。

## 本机发版前

- [ ] `python3 scripts/bump_app_version.py`（或确认 `engine/version.py` 与 package 一致）
- [ ] `./scripts/gate_package_regression.sh` 绿
- [ ] `GET /health` · `GET /ops/engine-supervisor` · `GET /readiness` 的 `engine_version` 一致
- [ ] 正式包走 `scripts/release-to-t2s.sh`（默认极空间 API）+ [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) + [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md)（禁止 scp 热补 `.py`；禁止默认 NFS 挂载推仓）
- [ ] [`packaging/release_notes.json`](../packaging/release_notes.json) 已登记本版更新说明
- [ ] 网页上传：`latest.json.sig` 先于 `latest.json`；ZIP 大小/SHA 已核对

## 客户机

- [ ] `python3 scripts/config_truth_diff.py --customer "<名>" --seed configs/customers/<名>`
- [ ] install-receipt：App CFBundle · engine_version · pause · health QPS
- [ ] 空闲 `health_qps_approx` ≤ 1
- [ ] 差分与回执归档 `~/Suying/logs/`（日期目录）

## 禁止

- 本机 smoke 绿写成客户机 DONE  
- 未重建一体包时宣称 UI 修复已交付
