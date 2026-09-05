# 速影 App（V8）— 客户向控制台


> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
产品名：**速影**（Suying）。代码仓：`~/QR/dev/速影`。

## 能力

- App 内 **启动 / 停止引擎**（打开 App 自动拉起）
- 九页：总览 · 生产 · 规则 · 审片 · 发布 · 消息 · 数据 · 运维 · 设置（日志在运维内）
- 客户自选 **片库 / 输出 / 词池文件路径**
- **向量化默认关闭**，须在运维页手动开启后才自动补 cliplet/embedding
- 首次开启为 **增量模式**：已有向量保留；新客户新库会自然跑完全部缺口，老库只补缺失
- 片库监视 / Worker / 调度器 可独立启停
- 每日自动开跑开关 + 整点
- 竖屏 1080×1920 / 横屏 1920×1080 双画幅；素材、向量和规则按画幅隔离，默认竖屏
- 规则实验室统一管理标题、字幕、旁白语言、音色、语速、音量与画面质量；保存后冻结到新 Job
- 运维集中设备服务、本地 AI、向量、备份、同步、日志和高级功能；设置集中客户、品牌、账号、通知和视频保留

## 数据目录（Local-first · 主盘默认 · HARD_LOCKS **L18 冻结**）

- 权威库 / 设置：`~/Suying/data/`（本机 APFS；`montage.db` + `settings.json`）
- 媒体工作区：`~/Movies/速影工作区/`（片库 / 成片 / cache / render；不进 T2S）
- 载体镜像：`~/Suying/carrier/`（极空间只读载体）
- **外置盘可选**：仅当用户在设置中把路径改到 `/Volumes/…` 时生效；自动同步默认指向主盘工作区，不依赖插盘

详见 [`V8_STORAGE.md`](V8_STORAGE.md) · [`STORAGE_SYNC_SAFETY.md`](STORAGE_SYNC_SAFETY.md)。

## 客户首次流程

1. 打开「速影」App（自动启引擎）
2. 完成向导：客户名、片库、输出、词池文件
3. 选择「稍后开启向量化」或「完成并开启向量化」
4. 全量扫描入库 →（若已开向量化）补齐索引 → 日历/任务生产 → 审片

## 开发

```bash
cd ~/QR/dev/速影
./scripts/start-engine.sh          # 或仅用 App 启停
cd apps/desktop && npm run tauri dev
```

环境变量：`SUYING_ROOT` / `MONTAGE_ROOT` 指向仓库根；`SUYING_DATA_ROOT` / `MONTAGE_DATA_ROOT` 指向数据目录。
