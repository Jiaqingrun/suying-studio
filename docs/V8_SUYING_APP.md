# 速影 App（V8）— 客户向控制台

产品名：**速影**（Suying）。代码仓仍可在 `montage-studio` 目录。

## 能力

- App 内 **启动 / 停止引擎**（打开 App 自动拉起）
- 五区：总览 · 生产 · 素材 · 审片 · 运维
- 客户自选 **片库 / 输出 / 词池文件路径**
- **向量化默认关闭**，须在运维页手动开启后才自动补 cliplet/embedding
- 首次开启为 **增量模式**：已有向量保留；新客户新库会自然跑完全部缺口，老库只补缺失
- 片库监视 / Worker / 调度器 可独立启停
- 每日自动开跑开关 + 整点

## 数据目录

- 本机引导：`~/Suying/data/settings.json`（仅指针）
- 外置盘工作区（不同步）：`~/QR-Volume/速影工作区/{db,cache,render,logs}`
- 外置盘同步区：`~/QR-Volume/极空间团队文件同步/速影客户/<客户>/{01-片库,02-成片,03-词池}`

详见 [`V8_STORAGE.md`](V8_STORAGE.md)。

## 客户首次流程

1. 打开「速影」App（自动启引擎）
2. 完成向导：客户名、片库、输出、词池文件
3. 选择「稍后开启向量化」或「完成并开启向量化」
4. 全量扫描入库 →（若已开向量化）补齐索引 → 日历/任务生产 → 审片

## 开发

```bash
cd ~/QR/dev/montage-studio
./scripts/start-engine.sh          # 或仅用 App 启停
cd apps/desktop && npm run tauri dev
```

环境变量：`SUYING_ROOT` / `MONTAGE_ROOT` 指向仓库根；`SUYING_DATA_ROOT` / `MONTAGE_DATA_ROOT` 指向数据目录。
