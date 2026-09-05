# 版本单源合同（VERSION SOURCE）

> 冲突时：`DEV_LOCK` / `HARD_LOCKS` > 本文。索引见 [`README.md`](README.md)。  
> 深度优化 P0：[`DEEP_OPTIMIZATION_PLAN.md`](DEEP_OPTIMIZATION_PLAN.md)。

## 唯一真相

| 符号 | 路径 |
|------|------|
| 引擎/产品语义版本 | [`engine/version.py`](../engine/version.py) → `ENGINE_VERSION` |
| App 包装版本 | `apps/desktop/package.json` · `src-tauri/tauri.conf.json` · `Cargo.toml` |

**升版命令**：`python3 scripts/bump_app_version.py --bump patch`（或 `--set x.y.z`）。

## 必须同源的出口

- `engine.__version__`
- FastAPI `app.version`
- `GET /health` → `engine_version`
- `GET /readiness`（若回显版本）
- `GET /ops/engine-supervisor` → `engine_version`
- install-receipt / remote-install 回执中读取的 health 字段

## 禁止

- 在 `engine/api/**` 路由内手写 `"0.x.y"` 字面量
- 只改 `package.json` 不跑 bump
- 用 OpenAPI 0.1.0 或其它陈旧串冒充交付版本

## 门禁

[`scripts/gate_package_regression.sh`](../scripts/gate_package_regression.sh) 断言 package / tauri / cargo / `ENGINE_VERSION` 全部相等。
