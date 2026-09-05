# 速影文档索引（唯一权威索引）

> **冲突裁决（写死，从上到下）**：`DEV_LOCK` / `HARD_LOCKS` > 分锁与领域合同 > 本索引说明 > 运维/交付 Runbook > 事故复盘与优化方案 > [`archive/`](archive/)（仅考古）。
> **状态真相源唯一**：[`DEV_LOCK.md`](DEV_LOCK.md) §E；问题清单/方案文不得并行标 ✅。
> 治理通则见 QR 全局规范 §四「多文档项目治理」。

## 1. 核心锁（先读）

| 序 | 文档 | 用途 |
|----|------|------|
| 1 | [`DEV_LOCK.md`](DEV_LOCK.md) | 写死流程 / Gate / 核对表 / 禁令 |
| 1a | [`HARD_LOCKS.md`](HARD_LOCKS.md) | 硬规则总索引（L0–L20；含 L18 主盘、L19 本地 F5/试听、**L20 生活服务旁白**） |
| 1a2 | [`READY_GATE.md`](READY_GATE.md) | 进成品库总门禁 |
| 1a3 | [`PAPER_SLIP_LOCK.md`](PAPER_SLIP_LOCK.md) | 纸片滚动避重 |
| 1b | [`NARRATION_SUBTITLE_LOCK.md`](NARRATION_SUBTITLE_LOCK.md) | 旁白×字幕对齐 |
| 1b2 | [`SERVICE_NARRATION_LOCK.md`](SERVICE_NARRATION_LOCK.md) | **L20**：生活服务黄金底稿 + Ollama 仅节奏润色（已冻结） |
| 1b3 | [`SCENE_TOUR_LOCK.md`](SCENE_TOUR_LOCK.md) | **跟镜精品**：`scene_tour` 片型合同（与 L20 日更并列；不改黄金底稿） |
| 1c | [`EMOJI_STICKER_LOCK.md`](EMOJI_STICKER_LOCK.md) | 表情贴纸硬规则 |
| 1d | [`QUALITY_LOCK.md`](QUALITY_LOCK.md) | 虚焦/模糊不入库 |
| 1d2 | [`ORIENTATION_LOCK.md`](ORIENTATION_LOCK.md) | 横竖屏显示尺寸双源硬审核 |
| 1e | [`SEMANTIC_OBJECT_VECTOR_LOCK.md`](SEMANTIC_OBJECT_VECTOR_LOCK.md) | 语义证据 / 物品 / 向量运营（GSemanticOps） |
| 1f | [`VOICE_CLONE.md`](VOICE_CLONE.md) | 可选本地克隆 TTS（**L19**：就绪+试听冻结；**core + App 外 F5 Kit** 交付） |

## 2. 产品与工程合同

| 序 | 文档 | 用途 |
|----|------|------|
| 2 | [`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md) | 通用多客户工程标准 |
| 3 | [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) | 产品是什么、三档、分期 |
| 3b | [`REACH_NON_GOALS.md`](REACH_NON_GOALS.md) | Reach 触达非目标 |
| 3b1 | [`PUBLISH_FLOWS.md`](PUBLISH_FLOWS.md) | 视频发布四流 |
| 3b2 | [`CONTENT_NON_GOALS.md`](CONTENT_NON_GOALS.md) | SEO/GEO 软文边界 |
| 3b3 | [`SYSTEM_EVENT_PAUSE.md`](SYSTEM_EVENT_PAUSE.md) | 睡眠/熄屏暂停与恢复 |
| 3c | [`SEMANTIC_PIPELINE.md`](SEMANTIC_PIPELINE.md) | 语义切片 / 表达层落地顺序 |
| 3d | [`ZERO_FORK.md`](ZERO_FORK.md) | 第二客户零分叉验收 |
| 3e | [`CONTINUOUS_QUEUE.md`](CONTINUOUS_QUEUE.md) | **历史归档 stub** → [`archive/CONTINUOUS_QUEUE.md`](archive/CONTINUOUS_QUEUE.md)；不得指导现行开发 |
| 3f | [`HUMAN_PUBLISH_CONFIRM.md`](HUMAN_PUBLISH_CONFIRM.md) | 人点发布补记 |
| 3g | [`APP_OPT_QUEUE.md`](APP_OPT_QUEUE.md) | **历史归档 stub** → [`archive/APP_OPT_QUEUE.md`](archive/APP_OPT_QUEUE.md)；现行关账见 CLOSEOUT_PLAN |
| 3g2 | [`CLOSEOUT_PLAN.md`](CLOSEOUT_PLAN.md) | **关账执行计划**（冻结新 Gate；PARTIAL 关闭；交付默认动作） |
| 3g3 | [`CLOSEOUT_ACCEPTANCE.md`](CLOSEOUT_ACCEPTANCE.md) | 关账真机/人眼验收剧本 |
| 3g4 | [`CLOSEOUT_HUMAN_BOARD.md`](CLOSEOUT_HUMAN_BOARD.md) | 真机执行看板（OPT.P5 · NEED_HUMAN） |
| 4 | [`V8_QUALITY.md`](V8_QUALITY.md) | 质量冲刺执行细节 |
| 5 | [`GOLDEN_SAMPLES.md`](GOLDEN_SAMPLES.md) | L0 标尺 |
| 6 | [`V8_STORAGE.md`](V8_STORAGE.md) | 同步区 vs 工作区 |
| 7 | [`V8_SUYING_APP.md`](V8_SUYING_APP.md) | App 能力 |
| 8 | [`MUSIC.md`](MUSIC.md) | BGM 曲库与合规 |
| 9 | [`VISUAL_DYNAMIC_OPTIMIZATION.md`](VISUAL_DYNAMIC_OPTIMIZATION.md) | **视觉/动态优化**：阶段 0；阶段 1–2 均已交付（**0.7.20**；V4 LUT / V5 多语言 Plan，默认关）。**禁止当并行进度表**；状态只进 `DEV_LOCK` §E |
| 9a | [`DAILY_DIVERSITY_PLAN.md`](DAILY_DIVERSITY_PLAN.md) | **日更去同质化（全行业）**：五层诊断、周槽位、facet/轮换、可见成效 A/B 档；**禁止另立 ✅**；B 档须点名 live 客户与改前/改后对照 |
| 9b | [`RULE_LAB_OPT.md`](RULE_LAB_OPT.md) | **GRuleLabOpt**：规则实验室 IA/默认值同源/日更·精品克隆/按任务规则轮换/规则绑定词池内容面（不改 v3 真相模型） |
| 9c | [`FX_ASSET_WHITELIST.md`](FX_ASSET_WHITELIST.md) | **GRuleVisualLab**：免费可商用特效/字体 Tier A 白名单；多层蒙版；字段默认关、精品可开；非法律意见 |
| 10 | [`POST_CLOSEOUT_OPS_PLAN.md`](POST_CLOSEOUT_OPS_PLAN.md) | **关账后运营优化执行表**：主力 **臻享丽人**；0.7.18 舰队；**xlf 可升 App、生产规则禁止 seed 覆盖**；阶段 0 配置。**禁止另立 ✅**；非新 Gate |

## 3. 运维与交付

| 序 | 文档 | 用途 |
|----|------|------|
| 3h | [`INSTALL_PROFILE.md`](INSTALL_PROFILE.md) | 批量安装机型检测与回执 |
| 3i | [`TRUSTED_OFFLINE_DELIVERY.md`](TRUSTED_OFFLINE_DELIVERY.md) | 私下签名更新 / 单机许可 / 离线仓 |
| 3i1 | [`TRIAL_LICENSE_PLAN.md`](TRIAL_LICENSE_PLAN.md) | 三日体验期签名合同与时间硬锁（schema v2 无日配额） |
| 3i1b | [`TERM_LICENSE_PLAN.md`](TERM_LICENSE_PLAN.md) | 年期 term（365 天）默认正式许可；客户无感；运维台账续签 |
| 3i2 | [`OFFLINE_DEPOT_LEGAL_DELIVERY.zh-CN.md`](OFFLINE_DEPOT_LEGAL_DELIVERY.zh-CN.md) | 离线仓法务交付说明 |
| 3i3 | [`legal/SALES_CONTRACT_速影Studio.zh-CN.md`](legal/SALES_CONTRACT_速影Studio.zh-CN.md) | 客户向许可及技术服务合同模板 **v2.0**（高风险功能确认 / 广告法与深度合成 / 许可种类须与装机一致） |
| 3j | [`STORAGE_SYNC_SAFETY.md`](STORAGE_SYNC_SAFETY.md) | 本机 APFS 权威库与同步沙盒 |
| 3j2 | [`DISK_CLEANUP_LOCK.md`](DISK_CLEANUP_LOCK.md) | 磁盘清理允许/禁止范围锁（白名单 + facade） |
| 3k | [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) | **客户机远程部署唯一正式路径** |
| 3k0 | [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) | **T2S App 更新推送规范**（默认极空间 API；禁 NFS 挂载默认通道） |
| 3k0a | [`T2S_PERSONAL_ROOT.md`](T2S_PERSONAL_ROOT.md) | **T2S 个人空间统一根**（`速影/`） |
| 3k0d | [`T2S_OFFLINE_DEPLOY.md`](T2S_OFFLINE_DEPLOY.md) | **T2S 离线交付**（模型/verify-tools；deploy 经运维机中转） |
| 3k0b | [`REPO_BACKUP_ZSPACE.md`](REPO_BACKUP_ZSPACE.md) | **主仓备份**（docs + git bundle → T2S「速影/主仓备份」；LaunchAgent 自动增量） |
| 3k0c | [`INDUSTRY_PACK_BACKUP_ZSPACE.md`](INDUSTRY_PACK_BACKUP_ZSPACE.md) | **行业包备份**（industry + seeds + 客户覆写 → T2S「速影/行业包备份」；current + releases 基线） |
| 3k1 | [`REMOTE_DEPLOY_POSTMORTEM.md`](REMOTE_DEPLOY_POSTMORTEM.md) | 远程部署复盘 |
| 3k2 | [`OVERALL_OPTIMIZATION_PLAN.md`](OVERALL_OPTIMIZATION_PLAN.md) | 整体优化分期方案（Phase0–4；后续 → CLOSEOUT / DEEP） |
| 3k2b | [`DEEP_OPTIMIZATION_PLAN.md`](DEEP_OPTIMIZATION_PLAN.md) | **深度优化 P0–P5**（版本真相/防回归/巨石拆分/控制面/交付/真机）；进度 `DEV_LOCK` OPT.* |
| 3k2c | [`VERSION_SOURCE.md`](VERSION_SOURCE.md) | 版本单源合同（`engine/version.py`） |
| 3k2d | [`DELIVERY_CHECKLIST.md`](DELIVERY_CHECKLIST.md) | 发版 / 客户机交付对照清单（DEEP P4） |
| 3k2e | [`RELEASE_NOTES.md`](RELEASE_NOTES.md) | T2S 历史版本包更新说明（与 `packaging/release_notes.json` 同源） |
| 3k3 | [`ENGINE_SUPERVISOR.md`](ENGINE_SUPERVISOR.md) | 引擎 LaunchAgent 常驻 / 三分态探针 / 掉线根治 |
| 3l | [`GSTAB_RUNBOOK.md`](GSTAB_RUNBOOK.md) | GStab 续跑 / 权威库恢复 |
| — | [`CUSTOMER_INSTALL.md`](CUSTOMER_INSTALL.md) · [`INSTALL.md`](INSTALL.md) · [`SOP.md`](SOP.md) · [`ACCEPTANCE.md`](ACCEPTANCE.md) | 安装与验收手册 |

远程部署只执行 [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) 统一标准（HARD_LOCKS L12）。

## 4. 事故与控制面预算

| 文档 | 用途 |
|------|------|
| [`INCIDENT_PAUSE_ORPHAN_BLOCK.md`](INCIDENT_PAUSE_ORPHAN_BLOCK.md) | 空原因 PAUSED_BLOCKED 卡死 |
| [`INCIDENT_HEALTH_POLL_FLOOD.md`](INCIDENT_HEALTH_POLL_FLOOD.md) | App `/health` 轮询洪水 |
| [`INCIDENT_SETTINGS_BOOTSTRAP_POLLUTE.md`](INCIDENT_SETTINGS_BOOTSTRAP_POLLUTE.md) | 设置引导污染权威库指针 |
| [`INCIDENT_STUCK_JOB_RENDER_SLOT.md`](INCIDENT_STUCK_JOB_RENDER_SLOT.md) | 假 running 占 render 槽；pause 不释槽 |
| [`INCIDENT_OLLAMA_EMBED_HANG.md`](INCIDENT_OLLAMA_EMBED_HANG.md) | Ollama embeddings 挂死阻塞选片；字幕空字形 |
| [`INCIDENT_TITLE_SUBTITLE_MISMATCH.md`](INCIDENT_TITLE_SUBTITLE_MISMATCH.md) | 标题 fade 单帧不可见；burn_mono 字幕≠旁白 |
| [`INCIDENT_SYSTEM_EVENT_OUTBOX_TIMEOUT.md`](INCIDENT_SYSTEM_EVENT_OUTBOX_TIMEOUT.md) | 系统事件审计阻塞 + 3s 超时 + duplicate 卡死 outbox |
| [`INCIDENT_OLLAMA_NARRATION_IDLE.md`](INCIDENT_OLLAMA_NARRATION_IDLE.md) | 旁白 keep_alive=0 冷启超时后整轮重渲空转 |
| [`INCIDENT_OLLAMA_HEAVY_ORPHAN_SLOT.md`](INCIDENT_OLLAMA_HEAVY_ORPHAN_SLOT.md) | 旁白假死占 ollama_heavy/TTS/render；租约+墙钟 |
| [`APP_POLL_BUDGET.md`](APP_POLL_BUDGET.md) | App 控制面轮询预算表 |
| [`AUDIT_CLEANUP_2026-08-08.md`](AUDIT_CLEANUP_2026-08-08.md) | 死代码清理 / 测试对齐 / 出包双端推送审计留痕（只读；不改合同） |

## 5. 历史归档（勿当现行需求）

已物理隔离至 [`archive/`](archive/)。若与 DEV_LOCK / PRODUCT_PLAN / 本索引冲突，**一律作废冲突句**。

| 文档 | 说明 |
|------|------|
| [`archive/MVP_PASSED.md`](archive/MVP_PASSED.md) · [`archive/V1.md`](archive/V1.md) … [`archive/V7.md`](archive/V7.md) | 历史里程碑 |
| [`archive/AUTO_INGEST.md`](archive/AUTO_INGEST.md) | 摄入说明（细节以代码为准） |
| [`archive/BACKLOG.md`](archive/BACKLOG.md) | 质量冻结快照；**现行状态以 DEV_LOCK §E 为准** |
| [`archive/APP_OPT_QUEUE.md`](archive/APP_OPT_QUEUE.md) · [`archive/CONTINUOUS_QUEUE.md`](archive/CONTINUOUS_QUEUE.md) | 旧优化/循环队列；现行见 CLOSEOUT_PLAN |
| [`archive/code-snapshots/2026-08-08/`](archive/code-snapshots/2026-08-08/) | 2026-08-08 删除死代码前的文件快照；**不得指导开发** |

原路径（如 `docs/V3.md`）仅保留短 stub 重定向。
