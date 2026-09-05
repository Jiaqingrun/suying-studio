# 项目约定 · 速影

> **分层、不混写**：全局个人规范见 `00-personal-standards.mdc`（勿在本文件重复 conda/Git/目录等全局条文）。
> 编辑：`qr project-standards --edit 速影` · 从对话修订：`qr project-standards-revise 速影`

## 用途

本机「剪辑 + 物料 + 辅助触达」工作室（速影 / montage-studio）：把客户自有实拍片库变成可发竖屏成片与发布物料，并在人在回路下辅助多平台触达。样板客户不是产品边界。

## 技术栈与结构

- 桌面端：`apps/desktop`（Tauri + React/TS）
- 引擎：`engine/`（Python FastAPI，默认端口 8766）
- 客户/行业配置：`configs/`
- 文档权威索引：`docs/README.md`
- QR 项目 ID：`速影`（路径 `~/QR/dev/速影`）
- 本仓是速影产品代码，**不是** OpenMontage 工作区

## 开发约定

- 改代码前读：`docs/README.md` → `docs/DEV_LOCK.md` → `docs/HARD_LOCKS.md`（及当前 Gate 分锁）
- **状态真相源唯一**：`docs/DEV_LOCK.md` §E；`docs/archive/**` 与 BACKLOG 快照不得指导现行开发
- 冒烟：`python3 scripts/smoke_test.py`（相关再跑专项 smoke）
- App 类型检查：`cd apps/desktop && npx tsc --noEmit`
- 引擎改动后按 `.cursor/rules/app-change-selfcheck.mdc` 自检并必要时重启引擎
- 远程部署只执行 `docs/REMOTE_DEPLOY.md`（HARD_LOCKS L12）
- **T2S 个人空间统一根**：`/nvme11/my/data/速影/`（更新包 / 主仓备份 / 行业包备份 / 辅助工具 / 客户暂存 / **离线交付**）；见 `docs/T2S_PERSONAL_ROOT.md`、`docs/T2S_OFFLINE_DEPLOY.md`；路径常量 `engine/ops/t2s_paths.py`；禁止在个人空间根再建并列「速影*」目录
- **QR 工作区备份（与其它项目相同）**：`qr t2s-backup -p dev/速影` → 极空间 `QR备份/dev/速影/`（`git bundle` + 元数据）；详见 `~/QR/dev/qr/docs/T2S_WORKSPACE_BACKUP.md`；**不含**运维 Keychain（仍走 `速影/主仓备份/`）
- **T2S App 更新推送**：默认极空间本地 API → `速影/更新包`（`docs/T2S_UPDATE_PUSH.md`）；禁止默认依赖 `~/QR/dev/T2s/ZSPACE` NFS 挂载
- 禁止：`if customer.name == "北京始峰伟业"`、往 `hooks.py` 堆客户文案、未授权 Gate 主开发、把 archive 当现行需求
- 默认主盘工作区（`~/Suying/data` + `~/Movies/速影工作区`）；外置盘可选；无真实客户片库时做 OFFLINE/fixture，禁止虚标媒体复检完成
- **HARD_LOCKS L17（冻结）**：`LangCombobox`（旁白/字幕语言）Portal+fixed 已验收；无授权不得再改该组件与 `.lang-combo*`；禁止卡片内 absolute 弹层与靠祖先 overflow「治裁切」；一体包须重建 `appsdesktop` 后再验
- **HARD_LOCKS L18（冻结 · Local-first 主盘工作区）**：权威库 `~/Suying/data`；媒体默认 `~/Movies/速影工作区`；`external_required` 默认 false；自动同步主盘；外置盘仅路径覆盖；外卷 fail-closed。**无用户当面授权禁止再改**该拓扑与默认同步语义
- **HARD_LOCKS L19（冻结 · 本地 F5 就绪与试听）**：clone 运行时（一体包 **clone flavor** / 内嵌 `f5-tts`）；设置页试听 `POST /voice/packs/preview` 单轨 `preview.wav` + macOS `afplay` 出声已验收；细则 [`docs/VOICE_CLONE.md`](docs/VOICE_CLONE.md)。**无用户当面授权禁止再改** `voice_clone` 就绪语义、预览接口、设置页音色试听相关实现

## AI 协作（本项目）

- MCP / `qr ask` 的 `project` 参数用 `速影`
- 冲突裁决：`DEV_LOCK` / `HARD_LOCKS` > 分锁 > `docs/README.md` > 运维/事故文 > `docs/archive/`
- 业务 Cursor 规则（勿删）：`suying-dev-lock.mdc`、`app-change-selfcheck.mdc`、`remote-deploy.mdc`、`product-package.mdc`
- 不主动 git commit / push，除非用户明确要求
