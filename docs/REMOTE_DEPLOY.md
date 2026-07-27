# 速影 · 快速远程部署 Runbook

> Gate：GShip / GCarrier。目标是从运维 Mac 一条命令部署到客户 Mac，并在退出前证明「能启动、能连 API、能建任务」。

## 1. 本次问题复盘

远程安装必须同时防住以下问题，任何一项失败都不得宣称完成：

1. **Python 绑打包机路径**：`venv --copies` 仍依赖打包机 Python home，换用户名/换机器后会缺 `encodings`。
2. **Homebrew 不在 App PATH**：GUI App 默认 PATH 不含 `/opt/homebrew/bin`，已安装的 Ollama / FFmpeg 会被误判缺失。
3. **Tauri 2 CORS**：WebView 源站是 `http://tauri.localhost`；GET health 可成功不代表 JSON POST 的 OPTIONS 预检能成功。
4. **逐文件传 App 太慢**：App 内数千小文件应压成单包传输，支持断点续传。
5. **Homebrew 安装易卡住**：自动更新、SSH 超时与残留 `.incomplete` 锁会让 FFmpeg 安装半途而废。
6. **通用种子不是正式客户配置**：通用种子只用于起步；已知客户的正式词池/品牌锁必须由运维显式传入，不能打进通用产品包。
7. **只看 health 会误判完成**：最终门禁必须覆盖签名、可迁移 Python、依赖检测、Tauri CORS、客户激活、词池数量、同步绑定，以及真实 `POST /jobs`。

## 2. 单命令入口

```bash
./scripts/deploy-remote.sh \
  --host xlf-remote \
  --customer-name "北京始峰伟业" \
  --customer-config "configs/customers/北京始峰伟业" \
  --install-deps \
  --bind-active-zspace \
  --smoke-job
```

默认读取最新产品套件 `~/Desktop/速影-*-product-macos-$(uname -m)`。部署内容：

```text
本机预检 → 单 ZIP 断点传输 → 远端解包/原子替换 App
→ standalone Python / codesign → Ollama+FFmpeg
→ 启动内嵌引擎 → 客户目录/配置/词池
→ 极空间当前 T2S 绑定 → 同步与更新 Agent
→ health + CORS OPTIONS + POST /jobs
```

## 3. 退出码与人工边界

- `0`：全部自动门禁通过。
- 非 `0`：输出失败阶段；可直接重跑，传输和安装步骤幂等。
- 极空间未登录、验证码、macOS 图形确认属于人工边界；脚本停止并给出明确提示，不伪造账号或绕过系统确认。
- `--install-deps` 仅使用客户机已有 Homebrew；不静默安装 Homebrew。
- `--smoke-job` 仅在当前客户片库已有可用素材时创建 1 条任务；片库为空则明确标记 `NEED_MEDIA`。

## 4. 交付门禁

1. App 内无指向打包机的 `pyvenv.cfg`，从另一绝对路径执行 `import encodings, fastapi, uvicorn` 成功。
2. `codesign --verify --deep --strict` 通过。
3. `/health.status=ok`，`host.ffmpeg_ok=true`，Ollama API 可达。
4. `OPTIONS /jobs` 对 `Origin: http://tauri.localhost` 返回允许源站。
5. 活跃客户、路径、正式词池与品牌锁符合部署参数。
6. 若已登录极空间：绑定 `username + nas_id` 匹配，且 `sync_mode=carrier_only`。
7. `POST /jobs` 返回任务 ID；Worker 能接单。是否进入 ready 仍服从 READY_GATE。

## 5. 回滚

远端替换前将旧 App 移到：

```text
~/Suying/backups/apps/速影.app.<UTC时间>
```

安装失败时恢复最近备份；客户数据、数据库和片库不随 App 替换删除。
