# 安装检查清单

> **客户交付**请以 [`CUSTOMER_INSTALL.md`](CUSTOMER_INSTALL.md) 为准（产品套件 + Ollama）。下文偏开发机自检。

## 硬件
- [ ] Mac（建议 Apple Silicon，内存 ≥ 16GB，MVP 推荐 48GB）
- [ ] 内置 SSD 剩余 ≥ 20GB
- [ ] 外置资料盘（可选）固定接口与挂载路径

## 软件
- [ ] macOS 13+
- [ ] Python 3.11+
- [ ] FFmpeg（`ffmpeg -version`）
- [ ] Node.js 20+
- [ ] Rust / Cargo（Tauri 打包）

## 安装步骤
1. 克隆或复制 `速影` 到 `~/QR/dev/速影`
2. `python3 -m pip install -r requirements.txt`
3. `cd apps/desktop && npm install`
4. 启动引擎：`./scripts/start-engine.sh`
5. 启动桌面 App：`cd apps/desktop && npm run tauri dev`（开发）或 `npm run package:mac`（**一体包**：嵌入引擎 + Python）
6. 产品套件：`BUILD_APP=1 ./scripts/package-product.sh`（见 [`CUSTOMER_INSTALL.md`](CUSTOMER_INSTALL.md)）

> 一体包双击即可起引擎；Ollama / FFmpeg / 片库仍在本机。开发联调仍可用 `./scripts/start-engine.sh` + `tauri dev`。
> 默认 `package:mac` 是 ad-hoc 内测签名；客户外发必须按 `CUSTOMER_INSTALL.md` 配置 Developer ID Application 与 notarization。

## 路径配置（App → 系统）
- [ ] 资料库根目录已创建且可写
- [ ] 输出根目录已创建且可写
- [ ] （可选）若片库在外置路径，确认路径可写；默认不要求 `external_required`

## 防中断
- [ ] 接电源
- [ ] 系统设置 → 电池 → 防止自动睡眠（或使用 `caffeinate`）
- [ ] 资料库勿放在 iCloud/网盘同步目录

## 首次联调
- [ ] 导入词包 JSON/MD
- [ ] 投递 5–10 条测试视频到资料库并扫描
- [ ] Dry-run 通过后再创建正式任务

## 冒烟（推荐）

```bash
python3 scripts/smoke_test.py
python3 scripts/smoke_zero_fork.py   # 第二客户隔离 / 零分叉
python3 scripts/smoke_ops.py         # G6 运营：报表 / 无旁白筛选 / 批量重渲
```
