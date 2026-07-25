# 安装检查清单

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
1. 克隆或复制 `montage-studio` 到本机
2. `python3 -m pip install -r requirements.txt`
3. `cd apps/desktop && npm install`
4. 启动引擎：`./scripts/start-engine.sh`
5. 启动桌面 App：`cd apps/desktop && npm run tauri dev`（开发）或 `npm run tauri build`（打包）

## 路径配置（App → 系统）
- [ ] 资料库根目录已创建且可写
- [ ] 输出根目录已创建且可写
- [ ] 外置盘必选时，勾选「external_required」并确认挂载

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
