# 事故记录 · 引导 settings 被测试临时目录污染（2026-07-27）


> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 现象

App / 引擎「资料没了」：客户片库、成片、表达设置等看起来空或指向错误路径。

## 根因

`save_settings()` 会把当前 `paths` **镜像写入** `~/Suying/data/settings.json`（引导指针）。

`tests/test_content_seo_geo.py::test_content_api_flow` 用 pytest `tmp_path` 调了 `save_settings`，但未隔离 `SUYING_DATA_ROOT`，于是把真实引导文件改写成：

```text
data_root = /var/folders/.../pytest-of-qr/.../test_content_api_flow0/data
```

引擎按引导指针启动 → 连上已消失的临时库 → 权威库上的真实资料「看不见」。

**权威数据从未删除**：仍在 `/Users/qr/QR-Volume/速影工作区/db/montage.db`。

软文表（`content_sources` 等）若只在临时库里写过，则不会自动回到权威库（本机权威库当前软文行为空属预期：从未正式写入权威库）。

## 处置（已做）

1. 污染引导备份为 `~/Suying/data/settings.json.polluted-pytest-*`
2. 从 `/Users/qr/QR-Volume/速影工作区/db/settings.json` 恢复引导
3. 重启引擎，确认 `data_root` 回到工作区权威路径
4. 代码：`save_settings` **禁止** ephemeral/`pytest`/`/var/folders`/`/tmp` 的 `data_root` 镜像到真实 `~/Suying/data`
5. 单测：显式 `SUYING_DATA_ROOT` 指向测试 boot 目录

## 以后发现同类问题

1. 看 `GET /health` → `paths.data_root` 是否含 `pytest`、`/var/folders`、`/tmp`
2. 看 `~/Suying/data/settings.json` 是否被改坏
3. 用工作区 `db/settings.json` 覆盖引导，再 `SUYING_FORCE=1 ./scripts/start-engine.sh`
4. **不要**把临时库里的东西当权威；以 QR-Volume 工作区库为准

## Gate

GStab / 工程止血相关；与 GContent 单测落地时引入。
