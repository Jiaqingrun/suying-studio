# 速影（Suying）

对外产品名 **速影**；代码仓目录仍为 `montage-studio`。本地智能混剪 — Python 引擎 + Tauri 桌面控制台。

> 注意：本仓库在 `~/QR/dev/montage-studio`，**不在** OpenMontage 工作区内。  
> 桌面 App：`~/Desktop/速影.app`；续跑说明：`~/Desktop/速影续跑说明.md`。

## 文档位置

权威索引：[`docs/README.md`](docs/README.md)。**先读** [`docs/DEV_LOCK.md`](docs/DEV_LOCK.md)。

| 文档 | 仓库内 | 说明 |
|------|--------|------|
| **开发锁（写死）** | [`docs/DEV_LOCK.md`](docs/DEV_LOCK.md) | Gate / 核对表 / 优先级 / 禁令 |
| **产品总图** | [`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md) | 做什么、分期、卖什么 |
| **开发标准（通用）** | [`docs/DEVELOPMENT_STANDARDS.md`](docs/DEVELOPMENT_STANDARDS.md) | App/引擎/多客户/配置铁律 |
| 速影 App | [`docs/V8_SUYING_APP.md`](docs/V8_SUYING_APP.md) | 控制台能力 |
| **客户安装（交付）** | [`docs/CUSTOMER_INSTALL.md`](docs/CUSTOMER_INSTALL.md) | 一体 macOS App + Ollama |
| 外置盘存储 | [`docs/V8_STORAGE.md`](docs/V8_STORAGE.md) | 同步区 vs 工作区 |
| 质量冲刺 | [`docs/V8_QUALITY.md`](docs/V8_QUALITY.md) | 冲刺范围（进度看 DEV_LOCK） |
| 黄金样片 | [`docs/GOLDEN_SAMPLES.md`](docs/GOLDEN_SAMPLES.md) | 质量标尺 |
| 验收 / 安装 / SOP | `docs/ACCEPTANCE.md` · `INSTALL.md` · `SOP.md` | 运维手册 |

> **定位：** 通用多客户工作室。始峰只是样板客户；新客户走 App 向导，不改引擎。

## 样板客户（fixture · 非产品默认）

路径示例见 `docs/V8_STORAGE.md`。样板脚本：

```bash
python3 scripts/fixtures/bootstrap_shifeng.py          # 需已挂载盘
python3 scripts/fixtures/convert_shifeng_keyword_pack.py
```

（`scripts/bootstrap_shifeng.py` 仅弃用跳转，会打 WARNING。）

## 快速开始

```bash
cd ~/QR/dev/montage-studio
python3 -m pip install -r requirements.txt
chmod +x scripts/*.sh
./scripts/start-engine.sh   # 终端 1
cd apps/desktop && npm install && npm run tauri dev   # 终端 2
```

或一键开发：

```bash
./scripts/dev.sh
```

## 冒烟测试

```bash
python3 scripts/smoke_test.py
```

## MVP 范围

已实现：多片库路径、摄入 Watcher、词包导入与冷却、模板约束抽样、Dry-run、FFmpeg 渲染烧字幕、质检门闩、任务队列/熔断、日志 CSV 导出、桌面控制台。

后置 / 进行中：以 [`docs/DEV_LOCK.md`](docs/DEV_LOCK.md) §E 为准。  
BGM：`~/Suying/music/` 或客户 `04-音乐/`。曲库调用与合规见 [`docs/MUSIC.md`](docs/MUSIC.md)（含 `bensound-*` + `km-*` 轻柔欢快）。
Pack/Reach 在 G1+G2 过关前禁止开工。
