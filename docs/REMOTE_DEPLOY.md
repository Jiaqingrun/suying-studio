# 速影 · 统一远程部署 Runbook（权威）

> Gate：GShip / GCarrier / GLicense / GOfflineDepot。
> **唯一正式路径**：运维 Mac 一条 `deploy-remote.sh` → 客户 Mac 离线验收。
> 热补客户机 `.py`、现场 `brew` / `ollama pull` **不算交付**。

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。

## 0. 真机成功方案（已固化 · 2026-07-29 `xlf-remote`）

下列步骤已在客户机验收通过，此后所有远程部署按此执行，不得另开旁路：

| 步骤 | 成功做法 | 禁止 |
|------|----------|------|
| 出包 | 源码修复后 **重 embed / 重打产品套件**，再部署 | 把 scp 热补当正式版本 |
| 传输 | 产品套件 **单 ZIP**（不含 DMG / offline-models）+ 首装时档位模型 LAN rsync；覆盖升级 `--skip-models` | 客户机公网拉模型；逐文件传 App；更新时重复推已有模型 / DMG |
| 工具 | `--offline-tools`（depot / `offline_bootstrap` 物化的 ollama+ffmpeg） | 默认走 Homebrew；正式路径调用 `brew`/`pip`/`curl` 下载 |
| 许可 | GUI 会话读 Keychain 的真实 `device_key_id` 签发；正式默认 **term 365 天**；`--license` 传入；纯续签用轻量 `import-remote-license.sh` | 信任 SSH 下误生成的设备请求；把许可证拷到别的机器；续签默认重推模型 |
| App 启动 | `open` 图形会话拉起 App，由 App 图形会话内引擎运行，让 Rust/Python 门禁能读钥匙串 | 以 SSH / `nohup` 引擎作为交付运行方式，或仅 SSH 控制台起引擎却宣称授权完成 |
| 覆盖安装 | 只替换 `速影.app`；保留已登录 Chrome 配置与 DB `profile_json` 绑定 | `profile.sample.json` 整表 PATCH；删登录目录 |
| 本机数据 | `paths.data_root` 落本机 APFS（如 `~/Suying/data`） | 把 `montage.db` 长期放在外置/同步盘当权威库 |
| Chrome | `user-data-dir` 固定在本机 APFS `~/Library/Application Support/com.qr.suying/chrome-profiles`；受管 Chrome 必须 `open -na "Google Chrome.app" --args …`（LaunchServices） | profile 放 ExFAT/SMB/同步工作区；直接 `Popen` Chrome 二进制（Cookie 不落盘） |
| 验收 | health + Tauri CORS +（有素材时）真实任务；写 `0600` 回执 | 只看 health；片库空仍宣称「部署完成」 |

参考验收：`xlf-remote` App 0.2.0、许可证 `issue_seq=2`（`1b0ae07c…`）、权威库 `/Users/xlf/Suying/data`、Ollama `qwen3.5:9b`+`nomic-embed-text`、`install-receipt.json` 已落盘。

## 1. 历史问题复盘（门禁原因）

1. **Python 绑打包机路径**：`venv --copies` 仍依赖打包机 Python home。
2. **Homebrew 不在 App PATH**：GUI App 默认 PATH 不含 `/opt/homebrew/bin`。
3. **Tauri 2 CORS**：GET health 成功 ≠ JSON POST 的 OPTIONS 预检成功。
4. **逐文件传 App 太慢**：必须单包 + 断点续传。
5. **Homebrew 安装易卡住**：正式路径改为离线工具，不再依赖 brew。
6. **通用种子不是正式客户配置**：正式词池/品牌锁由运维显式 `--customer-config` 传入。
7. **只看 health 会误判完成**：须覆盖签名、可迁移 Python、依赖、CORS、许可、词池、同步绑定与真实任务。
8. **客户机公网拉模型不可控**：运维端预备并校验；LAN 传输；默认禁止 `ollama pull`。
9. **误排除生产模块**：禁止 embed 用 `**/publish_*.py`；源码指纹与排除规则一致。
10. **覆盖安装冲掉登录态**：不得删 `chrome-profiles/`，不得样例整表覆盖 `profile_json`。
11. **热补当交付**：正式版本必须重打一体包再 `deploy-remote`。
12. **SHA 与包同仓不可信**：须 Ed25519 验签。
13. **首装无许可证**：`PARTIAL: LICENSE_REQUIRED`，签发导入后再继续。
14. **离线部署禁止隐式联网**：正式路径不得 brew / pip / curl 下载 / `ollama pull`。
15. **SSH 许可请求漂移**：SSH 可能读不到 Keychain，生成错误 `device_key_id`；必须以 GUI 解锁后的真实设备密钥为准。
16. **Chrome Cookie 不落盘**：直接 Popen 二进制时 Keychain 加密不可用；必须 LaunchServices 启动。
17. **App `/health` 轮询洪水**：前端 `notify` 身份不稳定导致 boot effect 重挂（见 [`INCIDENT_HEALTH_POLL_FLOOD.md`](INCIDENT_HEALTH_POLL_FLOOD.md)）。
18. **系统暂停假死**：空原因 `PAUSED_BLOCKED` / `power_off` 无法 resume（见 [`INCIDENT_PAUSE_ORPHAN_BLOCK.md`](INCIDENT_PAUSE_ORPHAN_BLOCK.md)）。
19. **冷启动跨卷 Chrome merge**：工作区 ExFAT `chrome-profiles`（含 trash）在 lifespan 同步拷到 APFS，拖垮 health 门禁导致整包回滚；须跳过 trash、异卷且本地已有 profile 时延期，部署期可 `SUYING_SKIP_WORKSPACE_CHROME_MIGRATE=1`。
20. **能力随包、状态不随包**：规则实验室/词池/日历在客户库，不会因装 App 自动等于本机 DB。

完整归类与稳妥流程：[`REMOTE_DEPLOY_POSTMORTEM.md`](REMOTE_DEPLOY_POSTMORTEM.md)。

## 2. 运维端预备

**默认**：离线模型 / verify-tools 真相源在 T2S `速影/离线交付/`；本机仅暂存后 LAN 推客户机。见 [`T2S_OFFLINE_DEPLOY.md`](T2S_OFFLINE_DEPLOY.md)。

```bash
# 一次性：从本机 Ollama 生成并推送到极空间
python3 scripts/ollama_model_bundle.py create \
  --profile lite --profile standard --profile pro --profile max \
  --output-root "$HOME/Suying/offline/速影-offline-models-macos-$(uname -m)"
python3 scripts/publish_offline_deploy_to_zspace.py
python3 scripts/publish_offline_deploy_to_zspace.py --prune-local   # 推送后清本机副本

# 签名 CAS depot → 速影/更新包/depot（若重建）
# python3 scripts/publish_offline_depot.py --depot ~/Suying/offline/速影-offline-depot-arm64-ready
```

`deploy-remote.sh` 在本机无套件时 **默认** 从极空间暂存到 `~/Suying/incoming/zspace-offline/`；`--no-offline-zspace` 关闭。

`lite` 只含 `nomic-embed-text`；`standard/pro/max` 含 `nomic-embed-text + qwen3.5:9b`。旁白模型默认不捆绑。

## 3. 统一单命令入口

```bash
./scripts/deploy-remote.sh \
  --host <SSH别名> \
  --customer-name "<客户名>" \
  --customer-config "configs/customers/<客户名>" \
  --license ~/Suying/runtime/security/license-ledger/<已签发>.suying-license \
  --offline-tools "$HOME/Suying/offline/速影-offline-verify-python-<profile>"
```

有片库素材、需要写成功回执时再加 `--smoke-job`。片库为空不要加，否则得 `PARTIAL: NEED_MEDIA`。

**覆盖升级（客户机已有模型）**加 `--skip-models`：不传离线模型套件，远程 ZIP 也不含 `offline-models` / DMG；安装门禁仍核验本机已装模型。首装不要加此开关。

```bash
./scripts/deploy-remote.sh \
  --host <SSH别名> \
  --customer-name "<客户名>" \
  --customer-config "configs/customers/<客户名>" \
  --license ~/Suying/runtime/security/license-ledger/<已签发>.suying-license \
  --offline-tools "$HOME/Suying/offline/速影-offline-verify-python-<profile>" \
  --skip-models
```

`deploy-remote.sh` **默认离线**：不传 `--install-deps`；自动按探测档位选模型套件（`--skip-models` 时跳过）；若未显式 `--offline-tools`，会尝试本机 depot 物化目录。绑定极空间默认开启（`--no-zspace` 关闭）。更新仓默认 `/nvme11/my/data/速影/更新包`（统一根见 [`T2S_PERSONAL_ROOT.md`](T2S_PERSONAL_ROOT.md)）。

不同 M 系列机型会按实测内存自动选择 Install Profile。跨 arm64/x86_64 部署允许使用
预构建并签名的目标架构套件；`--build` 只能构建运维机本身架构，跨架构时必须显式
提供目标架构 App、offline-tools 与模型组件，最终 import/health 在客户机验证。

部署流水线：

```text
核对套件签名与可迁移 Python
→ 机型/Profile 探测 → 选离线模型档位（或 --skip-models）
→（可选）传输 offline-tools
→ 单 ZIP（不含 DMG / offline-models）解包 / 原子替换 App
→ 导入许可证或 PARTIAL: LICENSE_REQUIRED
→ 离线 Ollama+FFmpeg →（首装）模型原子导入 /（--skip-models）仅核验已有模型
→ Install Profile configure/apply（只消费 plan_id）
→ GUI open App → health + CORS OPTIONS +（可选）POST /jobs
→ 脱敏 install-receipt.json（仅完整验收）
→ 验收成功后删除本次独立传输 staging；失败/PARTIAL 保留 24h 供续传与排障
```

## 4. 退出码与人工边界

- `0`：全部门禁通过，并已写 `0600` 成功回执。
- `20`：主体已落地但验收不完整（`LICENSE_REQUIRED` / `SMOKE_NOT_REQUESTED` / `NEED_MEDIA`）；不写成功回执。
- 非 `0`：失败阶段可幂等重跑。
- 同机安装锁：拒绝并发安装。
- 极空间未登录、验证码、macOS 图形确认：停并提示，不伪造。
- `--install-deps` 仅为旧兼容联网路径；正式交付禁止依赖它。
- 未传 `--smoke-job` → `SMOKE_NOT_REQUESTED`；传入但片库空 → `NEED_MEDIA`。

## 5. 交付门禁

1. App 内无指向打包机的 `pyvenv.cfg`；换路径可 `import encodings, fastapi, uvicorn`。
2. `codesign --verify --deep --strict` 通过。
3. `RUNTIME_MANIFEST.json{,.sig}` 验签与逐文件 SHA256 通过。
4. Rust + Python 许可证双门禁；跨机复制许可证必须失败。
5. `/health.status=ok`；FFmpeg / Ollama 可用。
6. `OPTIONS /jobs` 对 `Origin: http://tauri.localhost` 允许源站。
7. 活跃客户、路径、正式词池与品牌锁符合参数。
8. 若已登录极空间：`username + nas_id` 匹配，`sync_mode=carrier_only`；**不得**把 `update-remote-root` 写入异 NAS 客户的 `carrier_remote_root`。
9. （若 `--smoke-job`）`POST /jobs` 且 `completed + produced_count>=1`，或同 `plan_id+customer_ref` 可复核。
10. `install-plan.json` 与 `install-receipt.json` 的 `plan_id` 一致。
11. runtime 源码指纹与当前仓库一致；`chrome_runtime` 含 LaunchServices 启动路径。
12. 抽查本机 APFS `~/Library/Application Support/com.qr.suying/chrome-profiles` 体积未异常缩小；reach 账号仍绑定带平台前缀的已登录配置；引擎由 App 图形会话启动，不以 SSH / `nohup` 运行作为交付状态。
13. 若客户 `VIDEO_LOCK` 锁定 `provider=clone`：部署 **core App + F5 Runtime Kit**（`--f5-kit` 或 `~/Suying/releases/f5-runtime/kits`）；`GET /jobs/pipeline` 的 `clone_runtime.ok_for_clone_lock` 为真（`f5_source=overlay` 或应急 bundle + 权重）；否则部署失败。**禁止**仅传 core、无 Kit 带病交付。`FORCE_CLONE_FLAVOR_APP=1` 才用 fat clone 一体包。详见 [`VOICE_CLONE.md`](VOICE_CLONE.md)。
14. 成功部署后，本次 `$HOME/Suying/incoming/remote-deploy/<deployment_id>` 必须清空；失败或 PARTIAL 写 `STAGING-RETENTION.txt`，默认 24 小时，后续部署会在受限根下清理过期 staging。
15. 产品/远程 ZIP 禁止包含 DMG 与 `offline-models/`；人工安装 DMG、模型套件独立交付。覆盖升级用 `--skip-models`，不得把 App 与内含同一 App 的 DMG / 已装模型重复塞入传输包。

### Phase0 DoD（0.5.2+ 止血包，叠加上表）

16. 包内含 Chrome workspace migrate：**跳过 trash**、**默认延期全部异卷 merge**、`SUYING_SKIP_WORKSPACE_CHROME_MIGRATE`；`remote-install` 写入 `local.env` skip 且 health 等待加长。
17. 部署后抽查：`pause`/runtime **ACTIVE 且接单**（`GET /ops/runtime-health.accepts_new_work`）、空闲 **health QPS ≤1**、本机 APFS chrome-profiles 体积未因跨卷拷异常暴涨。
18. 成功回执 `install-receipt.json`（v2）含 `engine_version`、`runtime_source_sha256`、pause/QPS/chrome 根、clone 门禁采样。
19. 权威说明见 [`OVERALL_OPTIMIZATION_PLAN.md`](OVERALL_OPTIMIZATION_PLAN.md) Phase0；复盘 [`REMOTE_DEPLOY_POSTMORTEM.md`](REMOTE_DEPLOY_POSTMORTEM.md)。
19b. **引擎常驻（0.6.39+）**：`com.qr.suying.engine` LaunchAgent 须已安装；有许可证缓存时 agent 安装失败 **fail-closed**；验收可加跑 `scripts/check-engine-supervisor.sh`。见 [`ENGINE_SUPERVISOR.md`](ENGINE_SUPERVISOR.md)。

### 关账默认运维动作（2026-08-03 · CLOSEOUT）

20. **本机 ≠ 客户机**：规则实验室 / 词池 / 日历在客户库，不会因装 App 自动等于本机。每次 `deploy-remote` 默认打印 `config_truth_diff` 提醒；可加 `--config-truth` 对本机 seed 只读对照。显式导入用 `config_truth_import.py`，**禁止静默覆盖** `profile_json` / chrome 绑定。见 [`CLOSEOUT_PLAN.md`](CLOSEOUT_PLAN.md)。

20b. **发布班次窗口运维提示**：客户常用窗约 09–11 / 14–16 / 21–23 本地时间，全机单 publish 槽。**勿在班次峰值连打覆盖部署**（会杀 worker、放大 in-flight 批次风险）；非紧急变更改在窗间空闲。紧急修复需先确认无 ACTIVE 发布批次或人已放槽。见 [`PUBLISH_FLOWS.md`](PUBLISH_FLOWS.md)。

21. **Ollama 原子替换（硬门禁 · 2026-08-04）**：禁止对正在运行的受管二进制 `cp` 覆盖。正式路径必须：
    - 物化到独立临时文件 → depot SHA / 架构 / `codesign --verify --strict`；
    - 检测受管 LaunchAgent 与监听 PID；工具变化时先停受管服务，再 `rename` 原子替换；保留 `ollama.prev` 单次回滚点；
    - 启动后核对新 PID、可执行路径、SHA/CDHash、`/api/version`；不得因旧 `/api/tags` 仍返回就跳过重启；
    - 安装门禁升级为：所有权正确 + **精确模型 tag 可见** + 最小 chat/embed 功能探针；
    - 模型目录分叉（`~/Suying/runtime/ollama/models` 与 `~/.ollama/models` 双实体）时 **阻断安装**；
    - 成功回执须含 Ollama SHA/version、模型 exact digest、功能探针层（service/model/inference/circuit）与 LaunchAgent 结果。
    实现：[`scripts/install-ollama-atomic.sh`](../scripts/install-ollama-atomic.sh)、[`scripts/install-ollama-agent.sh`](../scripts/install-ollama-agent.sh)。事故见 [`INCIDENT_OLLAMA_HEAVY_ORPHAN_SLOT.md`](INCIDENT_OLLAMA_HEAVY_ORPHAN_SLOT.md)。

## 6. 回滚

远端替换前旧 App 移到：

```text
~/Suying/backups/apps/速影.app.<UTC时间>
```

其后任一步失败自动恢复本次备份。完整验收成功后仅保留最近 1 个 App 回滚点，
避免每次升级累积约 2GiB 旧包。客户数据、数据库、片库与本机 APFS
**`~/Library/Application Support/com.qr.suying/chrome-profiles` 登录目录** 不随 App 删除。

## 7. 与 App 向导的关系

远程部署与首装向导共用 [`INSTALL_PROFILE.md`](INSTALL_PROFILE.md)、[`TRUSTED_OFFLINE_DELIVERY.md`](TRUSTED_OFFLINE_DELIVERY.md)、[`STORAGE_SYNC_SAFETY.md`](STORAGE_SYNC_SAFETY.md)。运维入口说明见 [`CUSTOMER_INSTALL.md`](CUSTOMER_INSTALL.md)。
