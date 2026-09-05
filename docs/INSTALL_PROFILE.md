# 速影 · 批量安装配置合同

> Gate：GShip.FLEET / GCarrier。目标：同一套机型检测、模型选择和验收规则同时服务 App 首装向导与远程部署。

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 1. 唯一流程

```text
Probe（只读检测）
→ Plan（生成 install-plan.json）
→ Approve（用户/运维确认）
→ Configure（应用机器级设置）
→ Install（依赖与模型）
→ Customer（客户配置与路径）
→ Verify（全门禁）
→ Receipt（install-receipt.json）
```

禁止安装入口自行按机型名称猜配置。`MacBook Air / Pro / mini` 不是档位真相；统一内存、架构、磁盘和依赖探测结果才是。

## 2. Install Profile v1

权威 schema：`suying.install.plan.v1`，实现：`engine/ops/install_profile.py`。

计划必须包含：

- `plan_id`：同一机器、版本、档位和模型选择保持稳定；
- `app_version`、脱敏 `host.fingerprint`；
- 实测芯片、架构、统一内存、可用磁盘；
- 机器档位与原因；
- 精确模型 tag、是否必需、是否选中、预计体积；
- 机器级 `settings_patch` 白名单；
- 平台、架构、Python、FFmpeg、Ollama、磁盘门禁；
- 需要人工处理的 Homebrew、macOS 图形确认、极空间登录与验证码。

计划保存于本机：

```text
~/Suying/runtime/install/install-plan.json
```

不得进入 T2S 同步树，不含客户片库、密钥、Cookie 或完整硬件序列号。
计划与回执必须以 `0600` 权限原子落盘；同机远程安装必须持有安装锁。

## 3. 档位规则

当前档位沿用 `engine/catalog/host_profile.py`：

- `lite`：低于 16GB；默认只安装向量模型，视觉模型可选；
- `standard`：16–31GB；向量 + 9B 候选按需视觉；
- `pro`：32–95GB；向量 + 9B 候选按需视觉；
- `max`：96GB 及以上；向量 + 9B 候选按需视觉。

旁白文案模型独立推荐：

- lite：`qwen2.5:3b`
- standard：`qwen2.5:7b`
- pro：`qwen2.5:14b`
- max：`qwen2.5:32b`

旁白模型默认可选；旁白语气属于客户表达设置，不由机器档位决定。

## 4. 写死边界

### 绝对硬锁（全档统一，禁止放松）

- 成片时长 > 旁白、旁白×字幕对齐、成片物料禁止 AI 披露行、禁 mock TTS 进 ready、纸片滚动避重；
- `semantic_full_backfill_enabled=false`；禁止自动安装或级联 27B；
- **发布全机单槽**与 **TTS（F5）单槽**不可放宽；
- coarse 不得冒充 strict v1。

### 产能门禁（`gate_profile.v1`，按档位写入 settings_patch）

远程部署 Configure 一次写入；覆盖升级按白名单合并，不整表覆盖客户绑定。

| 项 | lite（&lt;16GB） | standard（16–31） | pro（32–95） | max（≥96） |
|----|-----------------|-------------------|--------------|------------|
| `semantic_analysis_mode` | `off`（顶栏开关禁用） | `on_demand` | `on_demand` | `on_demand` |
| 视觉包默认 | 不装 | 装 9B | 装 9B | 装 9B |
| clone / 自定义导入音色 | 禁止 | 允许（运行时齐） | 允许 | 允许 |
| 选片 quality 软阈 | 0.25 | 0.30 | 0.35 | 0.35 |
| `max_render_concurrency` | **1** | **1** | **1** | **2** |

`max_render_concurrency` 再由 `engine/runtime/resource_gate.py` 实测占用：TTS/publish 仍为 1。max 档允许「1 条发布中 + 1 条 ffmpeg 渲染中」重叠。

轻量档不装视觉模型时仍可 coarse 入库和普通生产；严格语义生产必须先安装视觉模型并完成候选验证。回执须含 `gate_profile_version`。

### 4.1 吞吐压测（max / pro）

在闸门就绪后、同素材集上对比：

1. 基线：concurrency=1，记成片/小时与峰值内存。
2. max：`max_render_concurrency=2`，同时跑一条 publish（验证码可人工停）+ 一条 render；确认无 OOM、无双 F5、纸片/READY 冒烟绿。
3. pro：保持 1；若试 ffmpeg 重叠须经 ResourceGate 且 TTS=1，无 OOM 才可写进该机 install-plan。

目标：max 档有效吞吐相对 concurrency=1 基线提升（同素材集 ≥1.5× 成片/小时为验收参考，非强制写死客户机）。

## 5. API

- `GET /setup/install-plan`：只读预览；
- `POST /setup/install-plan`：批准并持久化；
- `POST /setup/install-plan/configure`：请求只传已批准的 `plan_id`，加载已持久化不可变计划并应用设置，不下载；
- `POST /setup/install-plan/apply`：请求只传已批准的 `plan_id`，应用同一计划并启动计划内模型下载；
- `GET /setup/install-state`：读取当前计划与安装回执。

模型下载属于系统副作用：系统暂停期间返回 423，不自动重放。批量远程部署默认不调用该在线下载路径；运维端按档位生成精确 manifest/blob 离线套件，经 LAN SSH 传输、SHA256 校验并原子导入。只有显式 `--allow-online-model-pull` 才允许公网兜底。

## 6. 安装回执

权威 schema：`suying.install.receipt.v1`，保存于：

```text
~/Suying/runtime/install/install-receipt.json
```

回执记录：

- `plan_id`、App 版本、档位、脱敏硬件指纹；
- 已安装模型的精确名称、Ollama digest、大小、修改时间和来源（`offline_bundle` / `online_fallback`）；
- health、CORS、客户激活、真实任务等门禁结果；
- 客户只保存不可逆短哈希 `customer_ref`。

只有真实任务达到 `completed + produced_count>=1`，或同一 `plan_id + customer_ref` 的既有完成态验收可复核时，才允许原子写入 `0600` 回执。未请求真实任务标记 `SMOKE_NOT_REQUESTED`，片库为空标记 `NEED_MEDIA`，其余未闭环状态标记 `PARTIAL`；这些状态都不得写成功回执、成功标志或“安装完成”文案。没有回执或回执门禁不完整，不得对批量部署报告“安装完成”。

`plan_id` 由 App 版本、脱敏主机指纹、档位、精确模型选择和设置白名单共同计算。Configure / Apply 不得在请求内重新携带选择项并现场生成替代计划；持久化计划内容与 `plan_id` 不一致时必须拒绝。

## 7. 人工边界

以下步骤必须停人并给出明确引导：

- Homebrew 首装；
- macOS 权限、隔离或图形确认；
- Ollama 首次启动；
- 极空间登录、T2S 选择与验证码；
- 客户片库为空导致的真实任务验收。

安装器不得静默造号、绕过验证码或把 `NEED_MEDIA` 伪报成功。
