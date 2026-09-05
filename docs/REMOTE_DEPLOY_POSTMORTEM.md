# 速影 · 远程部署问题归类与稳妥方案（2026-08-01）

> 依据：`REMOTE_DEPLOY.md` §0–§1、三份 INCIDENT、`xlf-remote` 真机（含 0.5.0/0.5.1）、出包/热补纪律。  
> 效力：运维与研发共用；冲突时 `DEV_LOCK` / `HARD_LOCKS` L12 > 本文。  
> 分期总方案：[`OVERALL_OPTIMIZATION_PLAN.md`](OVERALL_OPTIMIZATION_PLAN.md)。

---

## 1. 为什么「本机好好的，远程就出事」

本机与客户机不是同一运行形态：

| 维度 | 本机（开发） | 客户机（交付） |
|------|--------------|----------------|
| 代码来源 | 源码树 / 热更 / 可混用 anaconda | **密封一体包** + runtime 验签；改 `.py` 会破完整性 |
| 路径 | 常全在 APFS；`$HOME`、卷 UUID 熟悉 | ExFAT/T2S 片库 + 本机 APFS 权威库 **混用** |
| 会话 | 长期 GUI，Keychain 已解锁 | SSH 部署 ≠ GUI；钥匙串/许可易漂移 |
| 冷启动 | 引擎常驻，迁移早已跑完 | 每次换包冷启动：DB migrate、Chrome merge、clone/torch |
| 数据 | 本机 DB/规则/词池 | **另一套** `montage.db`；能力随包，内容不随包 |
| 超时 | 人眼等几分钟也行 | 安装脚本 health 门禁原先约 **40s**，超时即回滚 |

结论：远程问题多半不是「功能没写」，而是 **密封包 + 冷启动副作用 + 存储拓扑 + 会话/许可门禁 + 短超时** 叠在一起。

---

## 2. 问题归类（历史 + 本轮）

### A. 出包 / 运行时形态（打错包或打丢模块）

| ID | 现象 | 根因 | 防护 |
|----|------|------|------|
| A1 | 客户机缺 uvicorn / 绑死打包机 Python | 非 standalone / host venv | 正式包必须可迁移 CPython |
| A2 | 发布相关模块 import 失败 | embed `**/publish_*.py` 误排除 | 只排除 `scripts/publish_*.py` |
| A3 | 热补后引擎拒启 | runtime manifest 验签失败 | **热补不算交付**；须重出包 |
| A4 | GUI 找不到 brew ffmpeg/ollama | App PATH 无 Homebrew | 离线 tools 物化进部署 |

### B. 许可 / Keychain / 启动会话

| ID | 现象 | 根因 | 防护 |
|----|------|------|------|
| B1 | LICENSE_REQUIRED / 许可失效 | SSH 读不到 Keychain，错误 `device_key_id` | 只认 GUI 会话签发 |
| B2 | SSH `nohup` 引擎「看似成功」 | 非图形会话，门禁绕开或半残 | 交付必须以 App `open` 起引擎 |
| B3 | Cookie 每次要登 | `Popen` Chrome 二进制，加密 Cookie 不落盘 | LaunchServices `open -na` |

### C. 存储拓扑（权威库 / Chrome / 外置盘）

| ID | 现象 | 根因 | 防护 |
|----|------|------|------|
| C1 | 双库/错库、设置漂移 | `data_root` 指到卷上或旧 `~/Suying/db` | 权威库固定本机 APFS；旧库只读归档 |
| C2 | 登录态被冲掉 | 覆盖装删 profile / sample 整表 PATCH | 只换 App；保留 APFS chrome-profiles |
| C3 | **0.5.1 deploy 回滚** | lifespan 跨卷 merge **30G** 工作区 chrome-profiles（含 trash），health 超时 | 见 §3 代码+安装脚本；卷上树隔离 |

### D. 引擎生命周期 / 暂停

| ID | 现象 | 根因 | 防护 |
|----|------|------|------|
| D1 | `PAUSED_BLOCKED` 空原因 | 迟到 quiesce 无 generation | pause_coordinator 自愈（0.5.0+） |
| D2 | `power_off` 假恢复 | 冷启动无 wake；manual resume owner 不匹配 | `_drop_stale_power_off` + did_wake 清 power_off |

### E. App / 控制面负载

| ID | 现象 | 根因 | 防护 |
|----|------|------|------|
| E1 | 卡顿、health ≈6/s | `notify` 每帧新 identity → boot effect 重挂 | 0.5.1：稳定 notify + boot/poll 拆分 + 单次 health |
| E2 | 只看 health 误报完成 | 未验 CORS/许可/词池/任务 | 门禁清单 + 有素材才 `--smoke-job` |

### F. 配置 / 数据不同步（常被误认为「部署丢了规则」）

| ID | 现象 | 根因 | 说明 |
|----|------|------|------|
| F1 | 本机规则实验室 ≠ 客户机 | DB 不随 App 同步 | 能力在包；规则/词池/日历在客户库 |
| F2 | 序列号「本机有客户无」 | 旧成片无 serial 列值 | 机制在 0.5.x；新成片才有 `SY-…` |
| F3 | 词池假 empty | 统计未读 v4 `keyword_pool.themes` | 已修代码；须进包 |

---

## 3. 本轮已做修正（仓内）

1. **Health 洪水**：`AppNotifyHub` / `App.tsx` / Rust `engine_reach_and_healthy`（已进 **0.5.1**）。
2. **暂停假死**：`pause_coordinator` power_off / orphan blocked（0.5.0+）。
3. **Chrome 跨卷 merge（本轮补强）**  
   - 跳过 `.trash*` / 隐藏目录  
   - 本机 APFS 已有 `customer-*` 且源在异卷 → **`cross_volume_deferred_local_ready`**，不阻塞 lifespan  
   - `SUYING_SKIP_WORKSPACE_CHROME_MIGRATE=1`：部署期可强制跳过  
   - `remote-install.sh`：写入 `local.env`、拉长 health 等待、fallback 引擎带 skip  
4. **事故文档**：`INCIDENT_HEALTH_POLL_FLOOD` / `INCIDENT_PAUSE_ORPHAN_BLOCK`；索引见 `docs/README.md`。

> 3 中「Chrome merge 补强」与 `remote-install` 改动需进 **下一版一体包** 才随正式 `deploy-remote` 生效；客户机 0.5.1 现场已靠隔离卷上 chrome-profiles + 手工装包稳住。

---

## 4. 稳妥解决方案（推荐固定流程）

### 4.1 原则（写死）

1. **唯一路径**：`BUILD_APP=1` 出产品套件 →（可选）`publish_update_repo` → `deploy-remote.sh`。禁止 scp 热补冒充版本。  
2. **三分离**：  
   - **能力** = App 包  
   - **客户配置种子** = `--customer-config`  
   - **业务状态** = 客户机 APFS `~/Suying/data` + APFS chrome-profiles（永不随包覆盖）  
3. **会话**：许可与引擎交付只认 **GUI**。  
4. **存储**：DB / Chrome 只在 APFS；片库可在外置；禁止把工作区 chrome-profiles 再当权威。

### 4.2 部署前检查清单（运维机）

- [ ] 版本已 bump；`tsc` / 相关单测 / `smoke_test` 按 Gate  
- [ ] `EMBED=1` + clone HF（若 VIDEO_LOCK=clone）  
- [ ] 包内可 import：`publish_runner`、`chrome_runtime`（LaunchServices）、`pause_coordinator` 关键修复  
- [ ] 许可证：GUI 真实 `device_key_id`  
- [ ] 离线 tools + 档位模型就绪  
- [ ] 确认客户权威库路径（如 `/Users/xlf/Suying/data`）与卷上旧库已隔离  

### 4.3 部署中（客户机）

- [ ] 只原子替换 App；备份自动回滚  
- [ ] 不碰 chrome-profiles、不整表 PATCH `profile_json`  
- [ ] 若仍存在「工作区 chrome-profiles」大树：部署前确认 APFS 已有登录态，或整树隔离（本机已有先例）  
- [ ] App `open` 后等 health；失败看 `~/Suying/logs/remote-install-engine.log` 是否卡在 copy/migrate  

### 4.4 部署后验收（不得只看 health）

1. App 版本 + `engine_version`  
2. `pause-state` = ACTIVE（或可解释的人工暂停）  
3. 空闲 `GET /health` 频率 ≈ **≤1/s**（防 E1 复发）  
4. CORS `OPTIONS /jobs`  
5. 词池 / 活跃规则 / 路径 health  
6. Chrome 绑定目录仍在 APFS且体积未异常缩小  
7. 有素材时 `--smoke-job`；否则明确 `PARTIAL`  

### 4.5 研发侧长期项（降低「本机≠客户机」）

| 优先级 | 项 |
|--------|-----|
| P0 | lifespan **禁止**同步做多 GB 跨卷 I/O；迁移异步或显式运维命令 |
| P0 | 安装脚本 health 等待与 skip 策略（已开做） |
| P1 | 「交付差异清单」：包版本 / 指纹 / 关键锁特性表，部署回执附带 |
| P1 | 规则/词池：**显式导入工具**，禁止幻想「装 App = 同步本机 DB」 |
| P2 | 客户机只跑正式包，开发机 Desktop 旧版勿混用同一端口 |

---

## 5. 一句话总结

远程部署翻车，本质是把 **开发态长驻环境** 当成了 **密封冷启动交付**。  
稳妥解法：**重出包走正式脚本、APFS 权威数据不动、GUI 许可与引擎、禁止启动期跨卷大拷贝、验收按门禁而不是「本机感觉能跑」。**

配套：[`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) · [`STORAGE_SYNC_SAFETY.md`](STORAGE_SYNC_SAFETY.md) · [`INCIDENT_HEALTH_POLL_FLOOD.md`](INCIDENT_HEALTH_POLL_FLOOD.md) · [`INCIDENT_PAUSE_ORPHAN_BLOCK.md`](INCIDENT_PAUSE_ORPHAN_BLOCK.md)
