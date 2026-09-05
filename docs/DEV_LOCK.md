# 速影 · 开发锁（DEV LOCK）

> **效力：写死。2026-07-24 起，本文件 > 口头习惯 > 临时灵感。**
> **违反本文件 = 不允许合并 / 不允许标 ✅。**
> 配套：[`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md) · [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) · [`V8_QUALITY.md`](V8_QUALITY.md) · **[`HARD_LOCKS.md`](HARD_LOCKS.md)**（2026-07-26 总锁）· [`SEMANTIC_OBJECT_VECTOR_LOCK.md`](SEMANTIC_OBJECT_VECTOR_LOCK.md)（GSemanticOps 专项锁）

---

## A. 你是谁、在造什么（锁死）

| 锁 | 内容 |
|----|------|
| 产品名 | 速影（Suying）；代码仓 `~/QR/dev/速影` |
| 产品形态 | **通用**本机「剪辑 + 物料 + 辅助触达」工作室 |
| 客户 | 多租户；始峰 = 样板 fixture，不是产品 |
| 三档 | Studio → Pack → Reach（见 PRODUCT_PLAN） |
| 不做 | 绕平台检测（卖点/对抗 SDK）、全自动矩阵养号、未授权数字人假脸 |
| Reach 例外 | **G5.R 半自动** + **G5.V 视觉全自动** 已授权（本人单账号 · 风控自负）→ [`REACH_NON_GOALS.md`](REACH_NON_GOALS.md) |

---

## B. 行为约束（每次开发前默念）

1. **先开哪扇门，再写代码**：未过 Gate 不得进入下一 Phase 主开发。
2. **一次只做一个 Gate 内的任务**；禁止平行开 Pack/Reach。
3. **无客户真实片库时只做 OFFLINE / fixture 队列**；需真实媒体复检的标 `NEED_MEDIA`（历史记号 `NEED_DISK` = 同义，**不再要求外置盘**）。
4. **禁止** `if customer.name == "北京始峰伟业"`、往 `hooks.py` 堆客户文案、把业务路径写死进 App。
5. **每完成一项**：更新本文 §E 核对表状态；冒烟绿；不写「差不多算完」。
6. **Agent/你自己**：改代码前读本文件 + DEVELOPMENT_STANDARDS；PR/提交说明写 Gate ID。

---

## C. 完整开发流程（强制顺序）

```text
0. 读锁
   DEV_LOCK → STANDARDS → 当前 Gate 的验收条
1. 选任务
   只从 §F 当前 Gate 的「未完成」里按优先级取 1 条
2. 设计（>30 分钟工作）
   写清：改哪些模块、是否客户域、是否配置化、如何冒烟
3. 实现
   遵守模块边界；差异进 industry/customer 配置
4. 自检 DoD（§G）
5. 冒烟
   python3 scripts/smoke_test.py
   （相关）python3 scripts/smoke_sprint_a.py
6. 更新核对表 §E
7. 停
   未过 Gate，不准开下一项 Phase
```

**主盘工作区默认（Local-first · 2026-08-07 · HARD_LOCKS L18 冻结）：**

```text
权威库 ~/Suying/data · 媒体 ~/Movies/速影工作区
→ 自动同步默认主盘；外置路径仅用户显式改路径时使用
→ 真媒体复检验客户片库是否在配置路径下可写（不要求外置盘）
→ 无用户当面授权：禁止再改上述默认拓扑与「插盘中心」回退
```

---

## D. Gate 定义（过关才算进入下一阶段）

| Gate | 名称 | 通过条件（全部） | 硬盘 |
|------|------|------------------|------|
| **G0** | 生产可用性 | 路径存在可写；引擎 health ok；权威库唯一；无双写混乱 | NEED_DISK |
| **G1** | Studio 质量收口 | L0 分数落盘；Sprint C 三项；B3 质量分非空转；整轮完成定义可测 | 部分 OFFLINE / 部分 NEED_DISK |
| **G2** | 通用化最小重构 | hooks/theme/bindings 数据化；无默认始峰；冒烟不依赖始峰盘 | OFFLINE |
| **G3** | Pack 物料 | publish_pack 导出；多平台文案；字幕；禁词扫描 | 可用临时目录 OFFLINE 开发，真客户验收 NEED_DISK |
| **G4** | 口播同构 | TTS 定时长→取片；口播+BGM；≥1 外语变体 | 同上 |
| **G5** | Reach | 半自动队列+人在回路；消息提醒；无反检测表述 | 同上 |
| **G5.R** | Safari 半自动 | 本人单账号；备料+剪贴板；人点上传/发布 | OFFLINE + 真机 NEED_DISK |
| **G5.V** | 视觉全自动 | 截图/快照驱动点击填表；验证停人；风控自负；无矩阵/无反检测卖点 | 同上 |
| **G6** | 日更运营闭环（Ops） | 旧片补旁白/字幕；运营看板；发布留痕；客户健康一句话；ops 冒烟 | 真客户 NEED_DISK；骨架 OFFLINE |
| **GStab** | 工程止血 | 权威库唯一；smoke_test/ops/zero_fork/carrier 绿；续跑说明 | OFFLINE |
| **GCarrier** | T2S 载体 | 载体目录；安装引导登录+绑 T2S；只同步载体；更新+配置备份 | OFFLINE + 真机绑 NAS |
| **GFleet** | 运营可见 | 已发归档；待发/已发/今日统计；词库量；本机路径健康；载体条 | OFFLINE |
| **GQual** | 质量硬化 | 抽检；Edge TTS；mock 不进 ready；主题冷却；黄金旁白注记 | 部分 NEED_DISK |
| **GShip** | 交付收口 | 口径纠偏；第二客户=载体安装；向导阻断；文档对齐 | OFFLINE |
| **G7** | 本机消息巡检 | 本人账号；受管 Chrome 串行只读未读摘要；官方页人工回复；通知去重 | OFFLINE + 真机登录验收 |
| **GUI** | App 整体 UI 重构 | 九页信息架构（独立规则；日志归运维）；消息独立侧栏；流程联动；数据中心；运维/设置分离；高级功能钥匙串密码；M 系列 Retina 自适应重排 | OFFLINE + 真机窗口验收 |
| **GContent** | SEO/GEO 软文中枢 | 客户自有官网/多域名资料库；主事实稿+平台变体；浏览器半自动发布；验证停人；消息回复草稿 | OFFLINE + 真机账号校准 |
| **GVideoRules** | 视频规则实验室 | 自然语言→结构化规则；人工确认；版本保存；约束 dry-run/任务；硬锁不可放松 | OFFLINE |
| **GSystemPause** | 系统事件暂停/恢复 | macOS 盒盖/睡眠/熄屏暂停；开盖/解锁继续；四开关；安全检查点；副作用不自动重放 | OFFLINE + 真机 sleep/wake |
| **GSemanticOps** | 语义证据、物品标注与向量运营 | coarse/strict 与目录/画面证据分层；日周双限额；标题/字幕/物品名绝对字形几何；向量最老优先队列与真实暂停状态 | OFFLINE + 真机成片验收 |
| **GVoiceClone** | 本地克隆旁白 | F5-TTS `provider=clone`；声色包 `configs/voice_packs/`；READY 允许 clone；默认仍 Edge | OFFLINE + 听感抽检 |
| **GCustomerUX** | 客户体验收口 | 通知中枢；统计刷新；许可无闪屏；全局序列号；标题24字；成片>旁白硬锁；发布软跳过×3轮；向量让路；规则实验室/消息/多语言 | OFFLINE + 真机发布验收 |
| **GVisualPack** | 安全包装动效（默认关）· 阶段 1 | 规则字段 `intro_punch` / `item_label_motion` / `end_card` 默认 `none`；日更缺省=现网；精品 ON 时 V1–V3 可见且 READY/L15 不松；p50 渲染增幅 ≤15%（OFFLINE 基线）；**零旁白管线改动**；不碰 L17–L20 / 纸片 / 二次字幕 | OFFLINE + 人眼 ON 样条 |
| **GVisualPack2** | 视觉阶段 2（默认关） | **V4** `color_lut`：`off`/`light` 轻 LUT；不得使虚焦/模糊门禁语义失效或系统性过曝 · **V5** 同 MontagePlan 多语言表达：画面轨复用；标题/字幕/TTS 分叉；mono 语言对齐 + 字幕只烧一次（H9）仍成立；默认 off；禁止阶段 X（Ken Burns/BPM/karaoke/贴纸等）；不碰 L17 组件实现 / L18–L20 / READY 放松 / 纸片日 cap | OFFLINE + 人眼 ON 样条 |
| **GRuleLabOpt** | 规则实验室深度优化 | schema 默认值同源；日更主路径瘦身；安全包装折叠；`premium` 跨类别克隆 draft；组件拆分；生产页闭环；**按任务规则轮换**（默认开、仅已保存、逐条开关）；**保持** production_rules v3 / L8；不碰 L17–L20 / seed 冲 live | OFFLINE |
| **GSceneTour** | 跟镜精品片型 | 对内 `scene_tour`；简报+画面要点+禁词自由写词；场景覆盖门槛；成片检查；客户隔离；与 L20 日更并列；App 文案全中文；V1 不做热点/看图写词 | OFFLINE + 试规划 |
| **GRuleVisualLab** | 规则实验室视觉大改 | 三栏画布拖字头；多层蒙版；Tier A 字体/贴图/xfade；竖排；卡拉OK/跑马灯；字段默认关、精品可开；白名单 [`FX_ASSET_WHITELIST.md`](FX_ASSET_WHITELIST.md)；**不**装 OpenMontage/Remotion；不松 READY/L15/L17–L20 | OFFLINE + 人眼精品样条 |

**当前主线：GRuleVisualLab（规则实验室视觉）+ GSceneTour 并列；GRuleLabOpt 已收口。** GVisualPack2 已交付；xlf 再部署另令。

**锁变更（2026-08-22 · GRuleVisualLab 开闸）：** 用户确认「规则实验室视觉大改」计划并指令实现。→ **允许 GRuleVisualLab**。权威：[`FX_ASSET_WHITELIST.md`](FX_ASSET_WHITELIST.md)。修订 VISUAL「阶段 X」中可自研项：受控 xfade 枚举、精品卡拉OK/跑马灯、装饰贴图（非标题区）允许在本 Gate 落地；仍禁止装回 OM/Remotion、数字人、放松 READY。日更默认关；精品可开；蒙版最多 8 层。  

**锁变更（2026-08-13 · GSceneTour 开闸）：** 用户确认「跟镜精品 · 产品化建议」并指令实现。→ **允许 GSceneTour**。权威：[`SCENE_TOUR_LOCK.md`](SCENE_TOUR_LOCK.md)。**不**改写 L20 黄金底稿；生产页可主按钮「生成跟镜精品」；日常日更为次要入口。禁止：词池金句当跟镜旁白、跨客户串味、检查不通过仍出片、App 英文裸奔。

**关账冻结修订（2026-08-11）：** 用户当面授权「修改 DEV_LOCK，开新 Gate GVisualPack」后阶段 1 收口；同日用户再授「视觉阶段 2（LUT / 多语言 Plan 等，另开锁）」→ **允许 GVisualPack2**。关账验收仍以 [`CLOSEOUT_PLAN.md`](CLOSEOUT_PLAN.md) 为历史收口权威；包装/阶段 2 进度只在本文件 §E。  
GVoiceClone / GSystemPause / GSemanticOps 真机项按验收剧本关闭；T2S = 安装/备份/更新载体；片库不上 T2S。

**锁变更（2026-08-07 · Local-first 主盘工作区 → HARD_LOCKS L18 冻结）：** 外置盘不再是生产前提。默认权威库 `~/Suying/data`、媒体 `~/Movies/速影工作区`；`external_required` 默认 false；自动同步目标为主盘；外置路径可选。用户明确要求「以后不再做任何变更」→ **写入 HARD_LOCKS L18**；无当面授权禁止再改该拓扑/默认路径/插盘中心语义。禁止把 `BLOCKED_DISK` 当作「没插盘就不能开发」。

**锁变更（2026-08-07 · HARD_LOCKS L19 · 本地 F5 就绪与试听冻结）：** 本机 clone 可选、设置页试听出声已验收。用户明确「这部分完成了，不要改动相关代码」→ **L19**；无当面授权禁止再改 `voice_clone` 就绪语义、`POST /voice/packs/preview`、设置页音色试听实现。权威：[`VOICE_CLONE.md`](VOICE_CLONE.md)、`HARD_LOCKS` L19。

**锁变更（2026-08-09 · HARD_LOCKS L20 · 生活服务旁白冻结）：** 用户确认生活服务旁白听感可接受（黄金底稿 + Ollama 仅润色节奏；倾听用词；禁空垫与「咱们/聊聊天」自由串改）。要求「写死，在我主动要求变更之前不要再改」→ **L20**。权威：[`SERVICE_NARRATION_LOCK.md`](SERVICE_NARRATION_LOCK.md)、`HARD_LOCKS` L20。无当面授权禁止再拧 `ollama_narration` base_lock、服务向 `narration_script` 开场/禁词、配方 hook 加权、已验收客户词池口径。

**锁变更（2026-08-07 · pre-submit fail-forward）：** 用户批准发布 fail-forward 升级方案。授权细化 GImmediate / GAutoLoop **提交前**语义：真登录墙有界宽限（默认 90s，封顶 120s）→ 超时 `deferred`+auto 释单槽、其它号继续；`reconcile` 收无 worker 的 `waiting_login` 僵尸；tick ACTIVE 与 `get_active_run` 对齐；过窗 `missed_human_confirm` 必须 human_alert；deferred 不得饿死 5min 内到点 trigger。**不**改变：单槽、不自动过验证码、结果不明/已点发 fail-closed、过窗禁止静默按原 occurrence 自动补发。见 [`PUBLISH_FLOWS.md`](PUBLISH_FLOWS.md) §4.4。

**锁变更（2026-08-07 · HARD_LOCKS L17 · 语言下拉冻结）：** 人验收「声音与语言」旁白/字幕下拉不被裁切。Portal+fixed 写死为唯一实现；禁止再改 `LangCombobox` / `.lang-combo*`（除非回归且用户明确授权）；禁止用祖先 overflow 当修法。见 `HARD_LOCKS` L17 与 `DEVELOPMENT_STANDARDS` §5.2.1。

**锁变更（2026-08-11 · GVisualPack 开闸）：** 用户明确：P3.5 logo/平台竖封面满槽**暂时跳过**；「开始 G3 GVisualPack（须当面改 DEV_LOCK）」。→ **允许 GVisualPack**；实现范围仅 [`VISUAL_DYNAMIC_OPTIMIZATION.md`](VISUAL_DYNAMIC_OPTIMIZATION.md) 阶段 1（V1 开场 punch · V2 物品标 micro motion · V3 片尾 end card）；字段默认 `none`；精品 content_category opt-in；禁止阶段 2/X 夹带；禁止改 L17–L20 / READY 放松 / 旁白黄金底稿。

**锁变更（2026-08-11 · GVisualPack2 开闸 · 阶段 2）：** 用户明确「视觉阶段 2（LUT / 多语言 Plan 等，另开锁）」。阶段 1 已人眼 + SHIP。→ **允许 GVisualPack2**；范围仅 VISUAL §6.2：**V4** 轻 LUT（`off`/`light`）· **V5** 同 Plan 多语言表达（画轨复用 / 文轨分叉）。默认 off；不碰 L17 实现 / L18–L20 / READY / 纸片；**禁止** §8 阶段 X。

**锁变更（2026-08-03 · CLOSEOUT）：** 用户接纳全面自检建议并授权关账循环：冻结新 Gate；GUI.V2 标 WONT（磁盘无 `apps/desktop-v2`）；过期队列归档；CDP 平台门面 + DOM 契约；deploy config_truth 默认提醒；升版推 T2S。真机人眼项保持 PARTIAL，关闭法见 [`CLOSEOUT_ACCEPTANCE.md`](CLOSEOUT_ACCEPTANCE.md)。

**锁变更（2026-08-03 · 取消定时内容预留）：** 用户明确要求「待发随时可发、不要预留、计划不够 ready 立刻生产」。废止 GImmediate / GUI.PUBLISH.EASY 中「即时必须排除定时预留 / 建 trigger 须先预留」合同。新合同：禁止新建内容 `reserved`；存量一次性释放；`ready` 且无进行中发布组即可被立即/定时选用（READY_GATE / approved / 物料门禁仍 fail-closed）；定时到点现挑，不够立刻补产，禁止 `blocked_reservation` 卡死；抢片=执行时占用。定时 schedule/trigger/seed/窗口设置仍不可被即时批次改写。关账期修复补丁，不开新 Gate。

**整体优化（2026-08-01 · P0 工程线）：** 权威方案 [`OVERALL_OPTIMIZATION_PLAN.md`](OVERALL_OPTIMIZATION_PLAN.md)；**0.5.2** 已 `deploy-remote` → `xlf-remote`（App/引擎对齐、health QPS≈0.4、pause ACTIVE、clone ok；PARTIAL=`SMOKE_NOT_REQUESTED`）；Phase1–4 工程项（冷启动延期/轮询预算/ResourceGate 可观测/config_truth/回执 v2/gate_package_regression）已进仓；**后续不新开功能 Phase，执行权转 CLOSEOUT_PLAN**。

**锁变更（2026-08-01 · 客户体验全量收口）：** 用户明确批准取消全部制作/发布日配额（含体验许可），保留失败熔断、发布单槽、内容占用与结果不明禁重发；日志并入运维，规则实验室成为独立一级页。用户进一步明确批准把原“标题黄/黑/220px、字幕白/黑/420px”精确样式锁改为**横屏/竖屏分别可配置**，但必须安全范围、人工保存、Job 冻结和 READY_GATE 按冻结规则验收。竖屏默认 1080×1920，横屏默认 1920×1080；素材、向量和规则按画幅隔离，默认仍竖屏。画质、字幕声画对齐/只烧一次、成片>旁白、纸片滚动避重、证据与发布门禁继续不可放松。

**G5 / G5.R / G5.V 均已授权（后两者风控自负）。禁止：反检测卖点 / 数字人假脸 / 矩阵养号 / 自动过验证码。**

**锁变更（2026-07-24）：** 用户确认人眼「能发」5/5 全过，并明确「修改 DEV_LOCK，允许 G3」。

**锁变更（2026-07-24 · 二）：** G3 全部 DONE 后，用户再次明确「修改 DEV_LOCK」→ **允许 G4**。

**锁变更（2026-07-24 · 三）：** G4 核心 DONE 后，用户再次明确「修改 DEV_LOCK」→ **允许 G5**。

**锁变更（2026-07-24 · 四）：** 「修改 DEV_LOCK / REACH_NON_GOALS」+ 风控自负 → **G5.R**。

**锁变更（2026-07-24 · 五）：** 用户明确「视觉全自动（风控自负）」→ **允许 G5.V**（技能 `vision-reach-publish`）。

**锁变更（2026-07-25）：** 用户明确「开新 Gate」并批准 G6 计划 → **允许 G6 · 日更运营闭环**。一次只做 G6 内任务；禁止平行开数字人/矩阵/换 embedding。

**锁变更（2026-07-26）：** 用户批准「速影整体提升计划」并指令实现 → **允许 GStab / GCarrier / GFleet / GQual / GShip**。顺序执行；T2S 仅载体。

**锁变更（2026-07-26 · G7）：** 用户明确批准「修改 DEV_LOCK，开启 G7」；后续明确将巡检硬锁为 **1800 秒**并授权正式 Chrome `--headless=new` 静默串行。只读发送者昵称、脱敏摘要与官方回复链接；验证码/登录停人；禁止自动回复、Cookie 导出/注入、绕过登录与反检测。

**锁变更（2026-08-02 · G7 历史摘要）：** 用户批准消息账号与发布账号独立，并将只读范围扩展为每账号最近 **30 天、最多 200 条**脱敏历史摘要。已读仅是速影本地待处理状态，不点击平台会话、不调用平台已读/回复接口；写入前脱敏，不保存完整会话。超过期限/上限的摘要清空但保留身份墓碑，已读项重复巡检不得复活。正式支持范围固定为视频四平台（抖音/视频号/小红书/快手）与软文四平台（百家号/头条号/知乎/微信公众号）；未完成真实登录和无副作用校准的平台必须显示 `login_required` / `readonly_unverified`，不得虚报完成。

**平台能力边界：** 快手公开官方网页当前只确认创作者通知中心，未确认可在网页回复私信；速影必须标记为“通知、无网页回复”，不得把通知入口冒充私信窗口。公众号消息子路由含登录态动态 token，须从官方后台导航解析，禁止把根页零结果当成“暂无消息”。

**锁变更（2026-07-27 · GUI）：** 用户明确授权「修改 DEV_LOCK 并开启 UI 重构 Gate」；运维按速影现有规范重组；高级设置密码使用 macOS 钥匙串。导航收敛为 **总览 / 生产 / 审片 / 发布 / 消息 / 数据中心 / 运维 / 设置**；消息回复为独立侧栏入口。自适应 = Retina 清晰度 + 密度分档重排，禁止整页等比缩放。

**锁变更（2026-07-27 · GContent）：** 用户批准「SEO/GEO 软文生成发布中枢」计划并指令实现 → **允许 GContent**。客户自有官网/多域名与本人官方账号；浏览器发布优先、验证停人；禁止站群/矩阵/绕检测/自动回复发送。边界：[`CONTENT_NON_GOALS.md`](CONTENT_NON_GOALS.md)。

**锁变更（2026-07-27 · GVideoRules）：** 用户批准「视频规则实验室」计划并授权修改 DEV_LOCK → **允许 GVideoRules**。生产页「规则实验室」；自然语言→本地 AI 结构化；人工确认后保存/启用；硬锁不可放松。

**锁变更（2026-07-27 · GSystemPause）：** 用户批准「系统事件暂停与恢复」计划并授权修改 DEV_LOCK → **允许 GSystemPause**。盒盖/睡眠与熄屏可分别暂停；开盖唤醒与解锁可分别继续；安全检查点；发布/更新等副作用任务中断后人工确认，禁止自动重放。边界：[`SYSTEM_EVENT_PAUSE.md`](SYSTEM_EVENT_PAUSE.md)。

**锁变更（2026-07-28 · GAutoLoop）：** 用户明确授权「自动闭环与八页信息架构重构」，并授权修改 DEV_LOCK：允许本人账号按人工设定的时间窗口为**每次发布 occurrence 独立生成并持久化可审计随机时间**；保存时区、窗口、seed、算法版本、最终时间与冲突调整。该能力仅用于内容排期，禁止扩展为反检测、鼠标抖动、指纹伪装或规避平台识别。视频质量门禁失败可按冻结规则有界补产，替代成片通过 READY_GATE 后立即补偿发布，不受原窗口约束且不得增加次数或重复扣配额。登录/验证码必须停人并通过 App、macOS、ntfy 三路升级提醒。

**锁变更（2026-07-28 · GImmediate / GLogs）：** 用户批准「即时批量发布与日志中心」计划并授权新增第九个一级「日志」Tab。允许当前客户本人账号按人工勾选后全机单槽串行自动发布：总发布 occurrence 可自动平均或手动分配；内容支持随机不重复、统一若干、单片复用。即时发布与定时发布设置、trigger、seed、预留内容和预留配额完全隔离；即时选择必须排除定时预留。READY_GATE、物料和账号平台匹配继续 fail-closed；仅登录/验证码/安全验证停人，完成后自动续跑；结果不明先查作品列表，仍不明禁止盲目重发。所有关键阶段写脱敏结构化日志。

**锁变更（2026-07-28 · GUI.PUBLISH.EASY）：** 用户批准「发布流程极简化」计划。客户默认路径收敛为「登录一次 → 填发布条数 → 开始发布」；系统自动选择 READY_GATE 明确通过、未定时预留且发布资产齐全的成片，并自动平均分配本人账号。路径、seed、occurrence、封面槽位和队列内部状态移入高级设置/日志；登录、验证码和结果不明仍停人，禁止降低发布门禁或盲目重发。

**锁变更（2026-07-28 · G5.V.COVER.VERTICAL）：** 用户要求所有视频平台只上传/设置竖版封面。每平台固定 1 个竖版槽位；发布优先使用 App「当前选用」模板的对应平台封面并通过页面匹配核验。禁止横封面、pack 封面回退。

**锁变更（2026-07-28 · G5.XHS.COVER.OPTIONAL）：** 用户明确：小红书封面编辑器不可靠时可跳过封面，但仍必须自动点击「发布」。真实短信/滑块才停人；帮助文案中的「验证」不得误报为人工验证。

**锁变更（2026-07-30 · G5.COVER.SOFT_TIMEOUT）：** 用户明确：封面设置卡住/失败约 **15 秒** 后不得停人；关闭封面弹层，用平台默认封面继续自动点发。模板设成功仍优先用模板；仅登录验证码/结果不明停人。

**锁变更（2026-08-05 · 发布加速）：** 封面硬墙钟改为约 **20 秒**；视频号 vision 点「编辑」最多 2 次；`evidence.timings` 为提速权威观测；批结束 Chrome 硬超时收尾（约 11s）。15 秒口径以此为准废止。
**锁变更（2026-07-28 · G5.CHANNELS.SUBMIT）：** 视频号自动发布固定为：App 当前竖封面临时适配 6:7 → 编辑器/表单匹配核验 → 标题正文回读 → 真实鼠标点击已启用的底部「发表/发布」按钮 → 成功提示/跳转/作品列表核验。验证码停人；结果不明禁盲目重发。

**锁变更（2026-07-29 · GLicense / GOfflineDepot）：** 用户批准少量客户阶段的私下离线交付与单机授权建设。许可固定为**单机永久、换机由运维人工迁移签发**；安装部署不得访问公网，运行时允许 Edge TTS 等既有联网能力。无 Developer ID 阶段以运维离线 Ed25519 发布密钥为产品信任根，ad-hoc 仅作包结构校验，不得冒充发布者身份。允许：Secure Enclave P-256 设备密钥（不可用时 ThisDeviceOnly Keychain 回退）、签名 release/runtime manifest、防降级、客户交付水印、极空间离线 CAS 仓、离线 Ollama/FFmpeg/模型安装和故障回滚。禁止：私钥进入 Git/App/T2S、环境变量生产旁路、静默联网下载、宣称可绝对对抗本机管理员、把公开模型加密包装成自有资产。

**锁变更（2026-08-04 · GTermLicense）：** 用户批准年期许可双仓升级实现：正式首装默认 `license_kind=term`（365 天）；客户无感直至到期；到期全屏硬锁「授权已到期，请联系运维人员」；运维中心台账紧急度排序与 T-7 待办、人工确认后远程再签仅导入；`perpetual` 祖父不随 App 升级改证；无公网心跳/kill-switch。权威：[`TERM_LICENSE_PLAN.md`](TERM_LICENSE_PLAN.md)。

**锁变更（2026-07-26 · 旁白字幕）：** 用户强制「旁白拟人可以，但话说完字幕还在必须修」→ **旁白×字幕对齐为硬规则**。权威：[`NARRATION_SUBTITLE_LOCK.md`](NARRATION_SUBTITLE_LOCK.md)。禁止把句间静音算进字幕时长；默认 `tail_trim_seconds=0.12`；出片前必须 `tighten_srt_to_voiceover`。
**锁变更（2026-07-29 · H9）：** 成片已烧旁白字幕时 publish_pack **禁止二次烧录**（事故：字幕堆叠）；复用成片 SRT/VO，`video.burned.mp4` 仅拷贝。
**锁变更（2026-07-30 · G2.KW.V2）：** 每客户词池收敛为唯一 `03-词池/keyword-pack.json`；revision+SHA256 校验、原子提升、archive/回滚、Job 冻结版本。文案必须由最终切片 strict semantics 内容指纹路由，并在 TTS/READY/publish_pack 前通过全字段事实与平台合规门禁。

**历史锁变更（2026-07-30 · GSemanticOps）：** 当时固定 1080×1920 标题 220px/字幕 420px 与日周双限额；日周限额已于 2026-07-31 被滚动避重取代，固定样式值又于 2026-08-01 被“双画幅安全范围 + Job 冻结值验收”取代。证据分层、向量最老优先/只入队/真实状态/暂停保留已完成继续有效。权威：[`SEMANTIC_OBJECT_VECTOR_LOCK.md`](SEMANTIC_OBJECT_VECTOR_LOCK.md)。

**锁变更（2026-07-31 · GVoiceClone）：** 用户批准将授权的阿姨音色接入本地 TTS。允许可选 `provider=clone`（F5-TTS 声色包）；默认仍 Edge；`VIDEO_LOCK` 可锁 clone；READY_GATE 接受 edge|clone；禁止 mock/say 冒充；禁止客户名硬编码。权威：[`VOICE_CLONE.md`](VOICE_CLONE.md)。

**冲突清理（2026-07-24）：** 引擎默认客户名中性化；行业包默认 `_blank`；样板脚本迁 `scripts/fixtures/`；V1–V7 标历史归档；过时「字号64 / 仍-an / 需重启」等干扰已删或废止。权威索引见 [`docs/README.md`](README.md)。

---

## E. 总核对表（进度真相源）

状态：`DONE` / `PARTIAL` / `TODO` / `BLOCKED_DISK` / `WONT`

### E0 · 底座（历史）

| ID | 项 | 状态 | 备注 |
|----|-----|------|------|
| E0.1 | MVP 能出片 | DONE | |
| E0.2 | V1 cliplet/向量 | DONE | |
| E0.3 | V2 日历/调度骨架 | DONE | auto_daily 默认关 |
| E0.4 | V3 冷却/一致性/标题区 | DONE | |
| E0.5 | V5 proxy | DONE | |
| E0.6 | V6 多客户字段 | DONE | 引擎默认客户名已中性化 |
| E0.7 | V8 App 五区 | DONE | 物料 + 触达 Tab 已建 |

| E0.8 | 外置盘布局文档 | DONE | QR-Volume 已挂载；权威库 `速影工作区/db`（jobs=28） |
| E0.G0 | Gate0 生产可用性 | DONE | 路径可写；health ok；陈旧 `~/Suying/data/montage.db` 已隔离；引擎指向卷上库 |
| E0.PATH_W | 外置路径可写门禁 | DONE | write probe + assert_production_ready force；Worker EROFS→paused 不空烧熔断 |

### E1 · 质量冲刺

| ID | 项 | 状态 | 硬盘 | 备注 |
|----|-----|------|------|------|
| E1.L0 | 黄金样片分数落盘 | DONE | — | G1/G2 已入 `golden_samples` + `golden_scores.json`；文件在盘可 ffprobe |
| E1.A1 | 有声+loudnorm+BGM | DONE | — | 代码+曾实机 |
| E1.A2 | 字幕收紧 | DONE | — | 文档已与代码对齐（96/6 dual_chip） |
| E1.A3 | 质量熔断 | DONE | — | |
| E1.B1 | Hook 池扩容 | DONE | — | 已迁入 building-supply pack |
| E1.B2 | 节奏模板+主题绑定 | DONE | — | bindings 在 pack.json |
| E1.B3 | 视觉质量过滤实效 | DONE | — | 712 条已回填；score=1.0 哨兵清零；API `POST /cliplets/quality/backfill` |
| E1.C1 | Logo 角标 | DONE | — | 实机 job29 `logo_applied=true`；`05-品牌/logo.png` |
| E1.C2 | 打回原因码→降权→可重渲 | DONE | — | 实机 output#38 dark_blur 降权 6 条 cliplet |
| E1.C3 | 质量日报 | DONE | — | `/reports/quality` 实机 ready_rate=1.0 |
| E1.DEF | 整轮完成定义 | DONE | — | 机器试跑 + 人眼能发 5/5 全过（`completion_ship_sample.json`） |

### E2 · 通用化（G2）

| ID | 项 | 状态 | 硬盘 | 备注 |
|----|-----|------|------|------|
| E2.1 | 去掉 DEFAULT_CUSTOMER=始峰 | DONE | OFFLINE | |
| E2.2 | industry pack hooks | DONE | OFFLINE | 合入 pack.json |
| E2.3 | industry pack theme_rules | DONE | OFFLINE | 合入 pack.json |
| E2.4 | template_bindings | DONE | OFFLINE | 合入 pack.json |
| E2.5 | 加载器 + fallback | DONE | OFFLINE | `industry_pack.py` |
| E2.6 | 冒烟不依赖始峰盘 | DONE | OFFLINE | smoke 强制临时目录 + 禁 QR-Volume/始峰路径泄漏 |
| E2.7 | 样板脚本标为 fixtures | DONE | OFFLINE | `scripts/fixtures/` |
| E2.8 | 单一词池 schema v2 + 语义文案路由 | DONE | OFFLINE | canonical 生命周期、Job 冻结、内容指纹、组合组件、统一合规门禁；始峰 revision 6 已迁移，旧文件入 archive |

### E3 · Pack（G3 · 已完成）

| ID | 项 | 状态 | 备注 |
|----|-----|------|------|
| E3.1 | `engine/pack`：从 ready 导出 publish_pack | DONE | video+封面+manifest |
| E3.2 | 多平台文案 `copy.zh.json` | DONE | 抖音/视频号/小红书/公众号 |
| E3.3 | 字幕 `subtitle.zh.srt` | DONE | 开场双行标题 |
| E3.4 | 禁词扫描 | DONE | profile + pack → compliance.json |
| E3.5 | API + 冒烟 | DONE | `POST /outputs/{id}/publish-pack` |
| E3.6 | App「物料」入口（最小） | DONE | 桌面 Tab「物料」→ 导出物料包 |

### E4 · 口播同构（G4 · 已完成）

| ID | 项 | 状态 | 备注 |
|----|-----|------|------|
| E4.1 | 脚本 → TTS → 得真实分段时长 | DONE | `engine/pack/tts.py`；provider=mock/say |
| E4.2 | 按时长预算按段取片混剪 | DONE | `template_with_duration_budget` + `build_plan(duration_budget=)` |
| E4.3 | 成片音轨：口播主 + BGM 床 | DONE | `render_plan(narration_path=)`；`bgm_bed_gain` |
| E4.4 | ≥1 门外语变体（字幕+旁白）可出包 | DONE | publish_pack：`copy.en`/`subtitle.en`/`voiceover.en.wav` |
| E4.5 | API + 冒烟覆盖口播路径 | DONE | `POST /pack/narration/preview`；smoke 覆盖 |
| E4.6 | 抽检「不像机翻旁白」≥4/5 | DONE | 用户 2026-07-24 确认通过（`narration_ship_sample.json`） |


### E5 · Reach（G5 · 已完成）

| ID | 项 | 状态 | 备注 |
|----|-----|------|------|
| E5.1 | `engine/reach` 发布队列骨架（选平台→预填→人点发） | DONE | 队列 API + `from-pack` 预填；人在回路 |
| E5.2 | 历史日配额 + 失败熔断 + 操作日志 | SUPERSEDED | 2026-08-01 日配额与 `/reach/quota` 已移除；仅保留连续失败熔断与结构化日志 |
| E5.3 | 打开已登录浏览器配置 / 官方入口（半自动） | DONE | `POST /reach/queue/{id}/open`；粘贴卡；dry_run |
| E5.4 | 发布待办：未读摘要 + 深链（不做自动回复；不读平台私信） | DONE | `GET /reach/inbox`；仅本机队列提醒 |
| E5.R | Safari 半自动技能 | DONE | `safari-reach-assist` |
| E5.V | 视觉全自动技能（风控自负） | DONE | `.cursor/skills/vision-reach-publish/` |
| E5.5 | API + 冒烟（临时目录） | DONE | queue/from-pack/open/quota/inbox/platforms 均入 smoke |
| E5.6 | App「触达」最小入口 | DONE | 桌面 Tab「触达」 |
| E5.7 | 书面非目标声明（文档/UI） | DONE | `REACH_NON_GOALS.md` 已含 G5.R；触达 Tab 口径已同步 |
---

## F. 任务队列（严格优先级）

### 现在就做（无硬盘）— P0 剩余

无（离线 P0 已空）。等插盘后做下方 P1。

已完成的 P0（勿重复做）：DEV_LOCK/Rule、去默认始峰、行业包数据化、打回降权、质量日报 API、A2 文档纠偏、fixtures 迁移、冲突清理、Logo 角标、打回一键重渲。

### 插盘后 — P1

| 序 | 任务 | Gate | 状态 |
|----|------|------|------|
| 9 | G0：挂载、权威库、引擎、路径健康 | G0 | DONE |
| 10 | L0 表补分 + 确认 G1/G2 文件 | G1 | DONE |
| 11 | B3 存量 quality score 回填任务 | G1 | DONE |
| 12 | Logo/降权/日报实机出片验证 | G1 | DONE |
| 13 | 整轮完成定义试跑 | G1 | DONE |

**P1 已全部完成。** 报告：`~/QR-Volume/速影工作区/db/completion_trial.json`。

### 收尾（已完成）

| 序 | 任务 | 状态 |
|----|------|------|
| W1 | E2.6 冒烟不依赖始峰盘 | DONE |
| W2 | 人眼「能发」抽 5 条 | DONE · **5/5 全过**（用户 2026-07-24） |

### 已完成 — G3 Pack

| 序 | 任务 | Gate | 状态 |
|----|------|------|------|
| 20 | publish_pack 目录骨架 + 拷贝成片/封面 | G3 | DONE |
| 21 | 多平台文案引擎 copy.zh.json | G3 | DONE |
| 22 | subtitle.zh.srt | G3 | DONE |
| 23 | 禁词扫描接入导出 | G3 | DONE |
| 24 | API + smoke_test 覆盖 | G3 | DONE |
| 25 | App「物料」最小入口（可选） | G3 | DONE |

### 已完成 — G4 口播同构

| 序 | 任务 | Gate | 状态 |
|----|------|------|------|
| 30 | TTS 适配层（脚本→分段音频+真实时长） | G4 | DONE |
| 31 | 按时长预算按段取片混剪 | G4 | DONE |
| 32 | 口播主轨 + BGM 床混音 | G4 | DONE |
| 33 | ≥1 外语变体进 publish_pack | G4 | DONE |
| 34 | API + smoke（可 mock TTS） | G4 | DONE |
| 35 | 抽检「不像机翻」≥4/5（可选后置） | G4 | DONE |



### 现在就做 — G5 Reach

| 序 | 任务 | Gate | 状态 |
|----|------|------|------|
| 40 | `engine/reach` 队列骨架（入队/列表/状态） | G5 | DONE |
| 41 | 从 publish_pack 预填标题/文案/视频路径 | G5 | DONE |
| 42 | 历史日配额 + 失败熔断 + 操作日志 | G5 | SUPERSEDED · 日配额已移除，熔断/日志保留 |
| 43 | 半自动打开浏览器配置/官方入口（人点发） | G5 | DONE |
| 44 | 发布待办最小（未读摘要+深链） | G5 | DONE |
| 45 | API + smoke | G5 | DONE |
| 46 | App「触达」最小入口 | G5 | DONE |
| 47 | 非目标声明（文档+UI 文案） | G5 | DONE |

**G5 核心项（40–47）已完成。** G4 序 35 人耳抽检已通过（2026-07-24）。

### 现在就做 — 出货试用

| 序 | 任务 | 状态 |
|----|------|------|
| S1 | 真客户链路试跑（出片→物料→触达入队→打开入口；人点发布） | **DONE**（人点发布已确认 2026-07-25 · 抖音 · montage_35_963246320.mp4） |
| S2 | 真实 TTS（macOS say）+ 可听样本 | **DONE** |
| S3 | 工程收口（PRODUCT_PLAN 对齐、引擎启动、commit 不 push） | **DONE** |

**出货试用 S1–S3 全部完成。** 证据：
- S1：`docs/HUMAN_PUBLISH_CONFIRM.md`（人点发布已确认 2026-07-25）
- S2：`db/ship_trial_s2.json` + `db/ship_trial_s2_samples/`
- S3：`PRODUCT_PLAN` Phase 0–4 勾选对齐；`scripts/start-engine.sh` 端口占用提示；本仓库已 commit、**未 push**

### 现在就做 — G6 日更运营闭环（已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 50 | E6.1 | 无旁白/无字幕筛选 + `POST /outputs/batch-rerender`（≤20）+ 审片 UI | G6 | DONE |
| 51 | E6.2 | 总览运营条：ready/失败/旁白覆盖/配额 | G6 | DONE |
| 52 | E6.3 | 发布留痕：reach published → 成片侧平台/时间 | G6 | DONE |
| 53 | E6.4 | 客户健康一句话（盘/路径/引擎） | G6 | DONE |
| 54 | E6.5 | `scripts/smoke_ops.py` + 文档勾选收口 | G6 | DONE |

**G6 核心项（50–54）已完成（2026-07-25）。** 证据：`scripts/smoke_ops.py` SMOKE_OPS OK；`GET /reports/ops`；审片批量补旁白。

### 已完成 — GStab / GCarrier / GFleet / GQual / GShip（2026-07-26 开闸）

| 序 | ID | 任务 | 状态 |
|----|-----|------|------|
| 60 | GStab | 权威库恢复、三条冒烟 + `smoke_carrier`、一体包续跑说明 | **DONE** · `docs/GSTAB_RUNBOOK.md` |
| 61 | GCarrier | T2S 载体目录、安装向导极空间+绑定、只同步载体、更新服务、配置备份 | **DONE** · `engine/ops/carrier.py` / `app_update.py` / `CarrierInstallWizard` |
| 62 | GFleet | 已发归档 + 总览统计、词库空挡日更、本机路径健康、载体状态条 | **DONE** |
| 63 | GQual | `quality_sample.py`、Edge TTS 默认、mock→review、主题冷却、黄金旁白注记 | **DONE** |
| 64 | GShip | 口径纠偏（发布待办）、第二客户=载体安装、文档对齐 CUSTOMER_INSTALL / V8_STORAGE | **DONE** |
| 65 | Ollama旁白 | 运维点选：视觉描述→口播文案+主题表情包→烧录；TTS 仍 Edge | **DONE** · `engine/pack/ollama_narration.py` |
| 66 | 下一波收口 | 视觉 gemma4 对齐；旁白 qwen2.5:32b 实机 preview；更新 Agent + seed + 恢复 UI | **DONE**（2026-07-26） |
| 67 | NS.Align | 旁白×字幕对齐硬锁（话说完字幕即灭） | **DONE 代码** · Job85 抽检 · `NARRATION_SUBTITLE_LOCK.md` |
| 68 | NS.Emoji | 表情贴纸：旁白不读 + Twemoji 可见 | **DONE 代码** · Job86/87 核验 · `EMOJI_STICKER_LOCK.md` |
| 69 | NS.Margin | 字幕禁止贴边裁切（side_margin+CJK 像素换行） | **DONE 代码** · Job87 · H7 |
| 70 | NS.Breath | 旁白短句呼吸 ≤18 字 + 强制 rate=-8% | **DONE 代码** · Job89 · H8 |
| 70b | NS.OnceBurn | 旁白字幕只烧一次：publish_pack 禁二次叠烧 | **DONE 代码** · 2026-07-29 · H9 · `test_export_reuses_burned_captions_without_second_burn` |
| 71 | B10.QA | 成品 10 条严格循环（对齐/边距/呼吸/Ollama表情） | **DONE** · Jobs92–101 ✅；Job91 暴露 Ollama SyntaxError 已修 |
| 72 | Q.Blur | 虚焦模糊不入库不向量化（Laplacian 门禁 + 存量 purge） | **DONE 代码** · `QUALITY_LOCK.md` |
| 73 | HARD.ALL | 用户「以上规则全部写死」→ `HARD_LOCKS.md` + VIDEO_LOCK.quality 钳制 + smoke | **DONE** |
| 74 | TITLE.YB | 历史标题黄字黑描边写死 | **SUPERSEDED · 2026-08-01 改为分画幅规则安全范围** |
| 75 | READY.GATE | 进成品库总门禁 `READY_GATE.md` + worker `evaluate_ready_gate` | **DONE** · 2026-07-26 |
| 76 | TITLE.Y120 | 历史标题整体下移 `offset_y_px=120` | **DONE（历史）** · 2026-07-26；2026-07-30 已被 GSO.4 的“最终字形顶边距顶 220px”取代 |
| 77 | PAPER.SLIP | 纸片滚动避重：近窗 exclude + 渐进放宽；ready 记账；禁配额熔断 | **DONE** · 2026-07-26 日起满额写死；2026-07-30 日/周双限额；**2026-07-31 用户废止满额**→滚动避重（20/15 近窗） |
| 78 | SELFCHECK | 全量自检收口：READY_GATE fail-closed、选片拒糊、纸片 DB 封顶、客户域/CORS/路径白名单、安全更新、产品核心去客户及建材默认、内嵌 health+验签阻断、产物排除 QA/旧同步脚本 | **DONE 代码+全套冒烟+App/DMG/产品套件/载体** · 2026-07-26；当前私下受控安装采用 ad-hoc，Developer ID + notarization 仅为未来线上分发可选项 |
| 79 | G2.SEED.SYNC | 可选版本化客户种子；T2S 载体角色收紧；同步按当前 `$HOME`、`nas_id`、可选卷 UUID；一键安装/卸载脚本入 App 与交付套件（补充：媒体同步 v3 客户文件夹隔离 fail-closed + cliplet 候选只看 ready） | **DONE** · `smoke_carrier.py` + `smoke_sync_portable.py` + `smoke_zero_fork.py` + `smoke_test.py` + 内嵌 `/health`/相关 API + ad-hoc `codesign --verify` · 2026-07-27 |
| 80 | GShip.REMOTE | 一键远程部署：单 ZIP 断点传输、App 原子替换/回滚、standalone Python、离线 Ollama/FFmpeg、正式客户配置、T2S 绑定、同步/更新 Agent、health+CORS+建任务门禁 | **DONE · 统一离线方案 · HARD_LOCKS L12** · `REMOTE_DEPLOY.md` §0 为唯一正式路径；**2026-07-31**：`deploy-remote.sh` 推送一体包 **0.3.0**（`速影 Studio.app`）→ `xlf-remote`；`appsdesktop` SHA=`b1e14f3e…d85d2` 与桌面套件一致；health ok / engine 0.3.0 / 片库挂载 / Chrome 12 账号保留；exit 20=`SMOKE_NOT_REQUESTED`（未加 `--smoke-job`，未写成功回执） |
| 80F | GShip.FLEET | 批量安装 Install Profile v1：实测机型→版本化计划→设置/模型→App 首装向导→远程部署→脱敏回执 | **DONE** · 后端/API/向导/远程脚本/0600 回执、安装锁、ZIP SHA、失败回滚、smoke 去重与只读 health；运维端 pro 离线 manifest/blob 套件、LAN 断点传输、SHA256 原子导入；`xlf@192.168.124.15` 真机验收：App 0.2.0、`offline_bundle` 导入 qwen3.5:9b+nomic-embed、`install-receipt.json`（`~/Suying/runtime/install/`）、vision/embed ready；片库空时勿用 `--smoke-job` · 2026-07-28 |
| 80G | GStab.WORKSPACE | 插盘工作区自动/手动重连：缺盘 fail-closed 不建空库；卷身份探测；总览「立即同步」= 重连权威工作区并 refreshAll（不复制/合并 DB）；macOS mount 边沿自动同步 | **DONE 代码** · `/workspace/*` + Tauri `workspace_events` + Overview 控件；`tests/test_workspace_reconnect.py` 7 绿；真机拔插验收待开 · 2026-07-28 |
| 81 | GLicense | 私下交付单机授权底座：Secure Enclave/ThisDeviceOnly 设备密钥、离线签发、Rust 双门禁、人工换机迁移 | **ACTIVE · M2 Keychain MVP DONE**；**SE 加固另议**；关账口径：不宣称对抗本机管理员 · `TRUSTED_OFFLINE_DELIVERY.md` · 2026-08-03 |
| 81T | GTermLicense | 年期 term 默认正式许可：365 天、客户无感硬锁、运维台账 T-7/紧急度、远程再签仅导入；perpetual 祖父 | **DONE 代码+单测+smoke** · 主仓 term/entitlement/LicenseGate/import-remote-license；运维 `license_binding`+续签流水线 · [`TERM_LICENSE_PLAN.md`](TERM_LICENSE_PLAN.md) · 2026-08-04 |
| 81E | ENGINE.SUPER | 引擎掉线根治：LaunchAgent 权威常驻、`boot_state` 先绑定、状态三分层、App kickstart 优先、失败分因 UI · 0.6.39 | **DONE 代码** · [`ENGINE_SUPERVISOR.md`](ENGINE_SUPERVISOR.md) · `check-engine-supervisor.sh` · 交付须 agent（缓存已有时 fail-closed） · 2026-08-07 |
| 82 | GOfflineDepot | 极空间完全离线安装仓：Ed25519 release/runtime 签名、防降级、CAS 工具/模型、原子安装回滚 | **UPLOADED_VERIFIED**：legal-complete depot seq=4（8 组件/67 对象，7,217,085,815 bytes）已上传至 T2S `T0210023G0UWV` 个人空间 `/nvme11/my/data/速影更新包/depot`；67 上传、0 跳过，全对象远端回读 SHA256、元数据回读验签与 CAS 集合/大小闭包均通过。客户特定 App `release/latest` 因 `delivery_id/customer_ref` 尚未签发仍待，未上传产品 ZIP、未改 `latest.json`、未覆盖 App · 2026-07-29 |

**硬边界：** T2S 无片库同步；极空间账号引导+检测；`media_sync_enabled` 默认 false。
**硬边界总锁：** [`HARD_LOCKS.md`](HARD_LOCKS.md)（画质 / 字幕 / 旁白 / 表情 / Ollama / **READY_GATE**）。
**硬边界（字幕）：** cue end ≤ speech end；句间静音空屏；左右 margin≥48 不得贴边；**只烧一次（H9）**；见 `NARRATION_SUBTITLE_LOCK.md`。
**硬边界（旁白）：** 先断句再去标点；每呼吸 ≤18 字；Edge `rate=-8%`；见同文档 H8。
**硬边界（表情）：** 旁白禁 emoji；贴纸必须可见（Twemoji）；见 `EMOJI_STICKER_LOCK.md`。
**硬边界（Ollama）：** `engine.pack.ollama_narration` 必须可 import；失败须写入 `ollama_narration_error`，不得静默跳过。
**硬边界（画质）：** 虚焦/模糊 `rejected_blur`；阈值只可加严；见 `QUALITY_LOCK.md`。
**硬边界（标题）：** 竖屏默认黄字黑描边/顶部 220px，横屏默认顶部 120px；颜色、描边和位置可在规则实验室安全范围内按画幅配置。最终字形必须与 Job 冻结规则完全一致，不得越界、漂移或用模板 offset 冒充验收。
**硬边界（成品库）：** 未过 [`READY_GATE.md`](READY_GATE.md) 不得标 ready。
**硬边界（纸片）：** 滚动避重（近窗优先、渐进放宽）；仅 ready 计入近窗；禁止日/周满额熔断；见 [`PAPER_SLIP_LOCK.md`](PAPER_SLIP_LOCK.md)。
**硬边界（语义/物品/向量）：** coarse 不冒充 strict；目录证据不冒充画面事实；字幕默认竖屏底部 420px/横屏 180px，可在分画幅安全范围内配置并按冻结规则验收；物品中文名单列竖排可左/右且不得侵入标题/字幕安全区；向量按横/竖画幅分别最老优先、只入队、状态如实，暂停放弃当前请求并保留已完成；见 [`SEMANTIC_OBJECT_VECTOR_LOCK.md`](SEMANTIC_OBJECT_VECTOR_LOCK.md)。

### 现在就做 — G5.V 视觉全自动（已授权 · 风控自负）

| 序 | 任务 | 状态 |
|----|------|------|
| V1 | 技能：快照/截图循环 → 点击填表 → 验证停人 → 可点发布 | **DONE** |
| V2 | 物料桥：`safari_reach_assist.py` 导出 JSON 供视觉流程消费 | **DONE** |
| V3 | 文档/App 口径对齐 REACH_NON_GOALS（含 G5.V） | **DONE** |
| V4 | 发布前门禁：文案非空 + 当前 App 模板单张竖版封面上传并匹配，否则禁止点发 | **DONE · 2026-07-28 竖版统一** |
| V5 | App 封面模板套（多套、按平台槽位、本机缓存、选用） | **DONE** |

### 现在就做 — G5.BATCH 串行发布批次（已授权 · 风控自负）

| 序 | ID | 任务 | 状态 |
|----|-----|------|------|
| B1 | G5.BATCH.1 | 持久化批次/条目状态机；全机单槽严格串行；验证码/登录停人 | **DONE** 代码 |
| B2 | G5.BATCH.2 | 严格封面门禁 + 发布成功核验；结果不明先作品列表复核 | **DONE** 代码 |
| B3 | G5.BATCH.3 | 受管 Chrome 动态 CDP 端口；可见发布统一 lifecycle | **DONE** 代码 |
| B4 | G5.BATCH.4 | 批次 API + App 可恢复观察；定时计划（明确时间点） | **DONE** 代码 |
| B5 | G5.BATCH.5 | 三种内容来源 + 阶段有限重试/跳过/连续3条熔断 | **DONE** · 2026-07-28 补强：提交前可跳过继续、实时阶段、陈旧 run 释放、自动/手动补发；结果不明禁盲重发 |
| B6 | G5.BATCH.6 | 第三方云代理人工故障接管 | **WONT · 商业离线版已移除** |
| B7 | G5.BATCH.SHIP | 单测 + smoke + 双账号真机小规模验收 | **DONE · 2026-07-28**（抖音两个本人账号全机单槽串行发布并核验成功） |
| B8 | GAutoLoop.1 | 生产→READY_GATE→出包→窗口随机排期→发布→消息通知持久化闭环 | **DONE · 2026-07-28**（持久化检查点、API、调度独立 tick 与恢复测试通过） |
| B9 | GAutoLoop.2 | 每条 occurrence 独立 seed；质量补偿立即发布；登录/验证码三路升级提醒 | **DONE · 2026-07-28**（独立 seed、补偿封顶、提醒幂等测试通过） |
| B10 | GUI.IA2 | 八页相关功能归位、唯一区块导航、明显视觉分区、软文账号显式创建 | **DONE · 2026-07-28**（五档视口八页无横向溢出；真机业务 Gate 仍按 SHIP 项验收） |
| B11 | GImmediate.1 | 多账号 occurrence 自动/手动分配 + 随机/统一若干/单片内容模式 | **DONE · 2026-07-28** |
| B12 | GImmediate.2 | 即时与定时内容/配额预留隔离；登录验证后自动续跑；完成三路通知 | **DONE · 2026-07-29**（初版曾允许多账号并行保活；**已由 B19 硬锁纠正**） |
| B13 | GLogs.1 | 历史第九个一级日志 Tab + 客户域脱敏结构化日志中心 | **SUPERSEDED · 2026-08-01 日志整体迁入运维** |
| B14 | GImmediate.SHIP | 单测、smoke、九页五档视口与多账号真机验收 | **DONE · 2026-07-28**（118 tests、tsc、cargo、smoke 通过；两个本人账号自动/手动分配及三种内容模式真机发布成功；完成通知与日志可追溯） |
| B15 | GUI.PUBLISH.EASY | 客户发布三步主路径；审片通过时自动准备发布物料；账号一键创建并登录；四平台任一物料不完整即阻断该成片；技术字段收进高级区 | **DONE · 2026-07-29 纠偏** |
| B16 | GReview.Auto | READY_GATE 合格（含软 consistency 留下的 review）强制自动审片通过并出包；硬失败自动拒绝；仅门禁未过/证据冲突进人工；审片页「一键审核通过」+「同步自动审片补录」UI | **DONE · 2026-07-30 纠偏**（gate_ok 一律过审，needs_review 不挡） |
| B17 | GUI.PUBLISH.ACCOUNTS | 「立即发布」账号清单与配置库总量同步；登录仅认同一受管 Chrome DOM 实时探针，待检测/退出账号禁选 | **DONE · 2026-07-29 纠偏** |
| B18 | G5.CHANNELS.LOGIN.WUJIE | 视频号登录检测穿透 wujie 子帧；缺 OpenCV 时视觉定位软失败并 DOM 兜底；上传未完成不进封面；补装 opencv-headless | **DONE · 2026-07-29**（误报未登录已修；客户机 `36dd4e16a5d146bd` 视频号 1 条 published） |
| B19 | G5.CHROME.SINGLE_SLOT | 账号切换硬隔离：关闭旧 Chrome（CDP 落盘）→ 再切配置 → 打开新账号；同客户同业务域同时只允许一个 user-data-dir；打开 A 不得触碰 B 目录；客户 video 配置库已清空待重建四平台测试账号 | **DONE · 2026-07-29** |
| B20 | GImmediate.ACCOUNT.CONTRACT | 立即发布账号契约：先按用户所选账号分配，再只删除缺平台资产的账号槽位，禁止把其次数静默转给其他账号；确认框显示实际队列；每个账号轮到时在同一 Chrome 原进程完成真实登录探针并直接上传，未登录停在该窗口自动续跑 | **DONE · 2026-07-29**（修复四账号选择被错误生成为 xhs×2/douyin/channels，以及“磁盘状态可选但实际 401”） |
| B21 | G5.CHROME.SESSION.HANDOFF | 禁止“登录预检成功 → 关闭 Chrome → 重新打开发布”两段式流程；账号检查与上传复用同一 PID/CDP；跨账号必须正常关闭并最多等待 20 秒落盘，启动带 `--restore-last-session` 恢复 session cookie | **DONE · 2026-07-29**（修复用户刚登录、点发布又回登录页） |
| B22 | G5.UPLOAD.STATE.CLASSIFIER | 上传完成判定不得用裸文本“上传中”（平台完成页含固定帮助文案）；只认取消上传、进度、转码/处理等动态信号。注入成功后若因此暂停，恢复时必须复用现有表单并先验证表单存在，禁止重复上传 | **DONE · 2026-07-29**（修复抖音上传完成被误暂停，并建立无重复上传恢复检查点） |
| B23 | G5.CHROME.LOCAL.PROFILE | Chrome `user-data-dir` 必须位于本机 APFS 的 `~/Library/Application Support/com.qr.suying/chrome-profiles`；禁止放入 ExFAT/SMB/同步工作区。Chrome 必须由 App 图形会话内的引擎启动，禁止以 SSH/nohup 作为交付运行方式（否则 macOS Keychain 不可用、加密 Cookie 不落盘）。旧工作区配置在无运行锁时一次性迁移到本机目录 | **DONE · 2026-07-29**（客户盘确认为 ExFAT；迁移并由 App 重启后 `sessionid/sessionid_ss` 加密落盘，连续关闭重开无登录页） |
| B24 | G5.CDP.TOP_LEVEL.PAGE | 发布标签选择只允许 CDP `type=page` 顶层目标；禁止因 iframe 查询参数含平台域名而选中聊天/客服子帧，否则会误报 `no_file_input`。僵尸 Chrome PID（`ps stat=Z`）必须视为已退出，避免阻塞同配置重开 | **DONE · 2026-07-29**（修复抖音 summon.bytedance iframe 被误选；第 3 条续跑 published） |
| B25 | G5.CHROME.LAUNCHSERVICES | macOS 必须用 `open -na "Google Chrome.app" --args …`（LaunchServices）启动受管 Chrome；禁止引擎 `Popen` 直接 exec `Contents/MacOS/Google Chrome`。直接 exec 时 Keychain「Chrome Safe Storage」不可用，`Default/Cookies` 行数恒为 0，切号必回登录页 | **DONE · 2026-07-29**（客户机复现：内存有 Cookie、磁盘 0 行；`open -na` 后落盘正常） |
| B25 | G5.KUAISHOU.TOPIC.LIMIT | 快手发布文案最多 4 个话题标签；`publish_pack` 生成、旧包预填、发布资产门禁与 CDP 写入均须截断正文内 `#话题`，不得只截 `hashtags` 数组；其他平台不受影响 | **DONE · 2026-07-29**（5 条专项回归、34 条发布回归、主 smoke 通过） |
| B26 | G3.PACK.CONTRACT | READY+approved 服务端幂等生成抖音/视频号/小红书/快手完整物料；出包失败 `asset_blocked`，即时/定时/自动闭环统一 fail-closed | **DONE · 2026-07-29** |
| B27 | G5.PUBLICATION.RETIRE | 首个目标提交即隔离；本次冻结目标全部成功后整套成片与物料非删除归档；unknown/归档失败绝不回流上传池 | **DONE · 2026-07-29** |
| B28 | GFleet.REPORT.TRUTH | 报表只认当前审片决定与去重发布事实；Asia/Shanghai 业务日；文件仅作 reconciliation；GET 报表无写副作用 | **DONE · 2026-07-29** |
| B29 | G3.AI.DISCLOSURE | ~~视频四平台末行强制「本作品由AI生成」（L14）~~ → **2026-08-13 用户废止**：成片物料禁止再出现该披露及别名；出包/预填/门禁一律剔除 | **REVOKED · 2026-08-13** |
| B30 | G5.CHANNELS.SESSION.STABLE | 视频号登录态治理：发布租约优先于消息巡检（`publish_priority`）；login.html 真墙立即 `waiting_login` 停人、禁止 3 轮空转；Cookie 在时 25s 恢复导航；channels 仅 form 可快通过；同 profile 连发复用、跨号加长 flush；证据 `login_kind` + App 区分扫码 vs 浏览器占用 | **DONE · 2026-08-06**（单元 19 项；tsc/smoke 自检） |

**G5.BATCH / GAutoLoop 硬边界：** 当前客户所有本人账号可自动切换，但全机始终单任务串行；禁止并发群控与自动过验证码；允许的是每次 occurrence 可审计的窗口随机排期，禁止反检测行为；「已点击发布」≠「已核验成功」；结果不明不得自动重发；系统暂停后禁止自动补发错过的定时计划；故障接管由人工按结构化日志处理，不依赖第三方云代理。质量补偿只替代原 occurrence，必须 READY_GATE 全过、次数有界、配额不重复。快手每条正文最多保留 4 个 `#话题`，生成与发布前必须双重钳制。

### 现在就做 — G7 本机消息巡检（已授权）

| 序 | ID | 任务 | 状态 |
|----|----|------|------|
| 81 | G7.1 | 受管 Chrome runtime：单 profile、PID/端口/锁、不得误杀普通 Chrome | DONE |
| 82 | G7.2 | 客户域账号/消息/扫描记录 + 只读平台适配器 | DONE 代码 |
| 83 | G7.3 | **1800 秒硬锁**调度 + 到期账号静默串行扫描 + API | DONE · API/SQLite trigger/调度三层锁定，2026-07-27 整合复检 |
| 84 | G7.4 | App 有声 + macOS 有声 + 可选 ntfy；脱敏去重审计；官方页人工回复 | DONE 代码 · SSRF 地址钉扎、密钥不回显、客户隔离已复检；真实 ntfy 与 macOS 权限仍待人工验收 |
| 85 | G7.5 | 离线冒烟 + 八平台逐账号真机验收 | **DONE 真机 · 2026-08-10** · 用户确认 CLOSEOUT_ACCEPTANCE §D 全过（未校准平台不虚报 completed） |
| 86 | G7.SHIP | 一体包包含消息 runtime；App 启动不写签名包；打包冒烟无残留引擎 | DONE · 2026-07-27 standalone 重打包并安全替换 `/Applications/速影.app`；内嵌 `/health`/消息 API/CORS/验签通过 |
| 87 | SEM.P5 | 多帧语义分类 + 三次有限重试 + fail-closed + 旧 SQLite 增量迁移 | **DONE / 已被 SEM.P6 替代** · P5 的拒绝清向量策略仅作历史审计 |
| 88 | SEM.P5.H | 真实素材模型分类准确率与抽帧质量人工验收 | **DONE 真机 · 2026-08-10** · 用户确认 CLOSEOUT_ACCEPTANCE §F（抽 30 条人眼、未扩全库 VLM） |
| 89 | SEM.P5.R | 严格语义存量安全续跑：客户域资格过滤、SQLite 原子 claim、断点/进度 API | **DONE / 已被 SEM.P6 替代** · 严格验证失败改为保留 coarse 行与向量、写终态审计，禁止重复后台领取 |
| 90 | SEM.P5.Q | 真实失败驱动的提示/schema 硬化：实际 frame ID 白名单、事实/unknown 分离、人物互斥中性类、有界抽帧回退 | DONE 代码 · 修复前验证批 IDs 21–30：1 passed / 9 rejected，拒绝率 90% 熔断；remaining=763；不得扩大，后续需更换/调优视觉模型后另取新批 · 2026-07-27 |
| 91 | SEM.P5.M | 历史视觉模型分档级联 | **DONE / 已被 SEM.P6 替代** · 原 9B→27B 级联实现保留历史审计；运行默认不再启用 |
| 92 | SEM.RENDER.STRICT | 生产任务可启用 `strict_semantic_v1`：检索只取 quality+embedding+semantic v1 passed cliplet；不足 fail-closed，禁止整片资产回退；READY_GATE 审计每段来源 | **DONE** · Job134 / Output203 ready；cliplets 48/82/74/111/110 全部通过；`test_strict_semantic_planner` + READY_GATE + smoke 绿 · 2026-07-27 |
| 93 | SEM.P6 | 低配 Mac 分层按需语义：全库 VLM 回填默认停用；新素材 coarse 快速索引；Dry-run 候选 9B 单次验证；不自动安装/调用 27B；coarse 不得冒充 strict v1 | **DONE** · `/index/captions` 默认 409 且 `use_vision=false`；后台 reconcile 禁用 VLM；`/index/captions/verify` ≤10；strict 必须持久化 `ollama/nomic-embed-text` provenance；普通生产保留 coarse 向量 · 2026-07-28 |

**G7 硬边界：** 仅本人账号；固定 1800 秒（API/DB/调度不可配置缩短）；正式 Chrome headless 静默巡检，不弹浏览器；真实摘要与本机发布待办分开展示；不保存完整会话；通知只含脱敏摘要和官方链接；不自动输入/发送回复；不处理验证码；不读取非官方域名；不得误杀用户 Chrome。

### 现在就做 — GUI App 整体 UI 重构（已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 100 | GUI.IA | 九页导航：总览/生产/规则/审片/发布/消息/数据中心/运维/设置；日志在运维内；主线联动 | GUI | **DONE · 2026-08-01 客户向重排** |
| 101 | GUI.SHELL | 暖宣纸主题 + AppShell + Retina 密度分档（紧凑/标准/宽屏） | GUI | **DONE** |
| 102 | GUI.PAGES | 拆分页面组件；素材并入生产；物料+发布台+触达并入发布 | GUI | **DONE** |
| 103 | GUI.DATA | 数据中心只读聚合；总览动态摘要；上下文下一步 | GUI | **DONE** |
| 104 | GUI.OPSSET | 运维机器级 / 设置客户级分离；高级区钥匙串密码 | GUI | **DONE** |
| 105 | GUI.SHIP | tsc + cargo check + smoke + 五档视口验收 | GUI | **DONE 真机 · 2026-08-10** · 离线 tsc/cargo/smoke 既有 PASS；用户确认双账号发布 / 三路通知业务验收通过 |
| 106 | GUI.POLISH | 消息独立侧栏；账号/消息双栏高密度布局；暖宣纸配色与顶栏按钮尺寸统一 | GUI | **DONE · 2026-07-27** |

**GUI 硬边界：** 保留全部既有生产/审片/发布/触达/运维能力，不得因改 UI 砍功能；高级密码只保护 App 高级入口，不宣称对抗本机管理员；禁止全局 `zoom/transform: scale()` 整页缩放。

### GUI.V2 · 已废止（WONT · 2026-08-03 · CLOSEOUT）

> **WONT：** 磁盘无 `apps/desktop-v2`；DEV_LOCK 历史「DONE」为纸面状态，作废。正式 UI 仅 `apps/desktop`。禁止平行重写。

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 200–207 | GUI.V2.* | 玻璃舱并行 App | GUI.V2 | **WONT · 2026-08-03** · 关账冻结；勿再建 |

### 现在就做 — CLOSEOUT 关账（2026-08-03）

| 序 | ID | 任务 | 状态 |
|----|-----|------|------|
| 180 | CLOSE.PLAN | CLOSEOUT_PLAN + ACCEPTANCE + 索引 | **DONE · 2026-08-03** |
| 181 | CLOSE.ARCHIVE | APP_OPT / CONTINUOUS 归档 stub | **DONE · 2026-08-03** |
| 182 | CLOSE.V2WONT | GUI.V2 → WONT | **DONE · 2026-08-03** |
| 183 | CLOSE.CDP | cdp_platforms + upload_state_classify + DOM 契约 | **DONE · 2026-08-03** |
| 184 | CLOSE.TRUTH | deploy config_truth 提醒 + REMOTE_DEPLOY | **DONE · 2026-08-03** |
| 185 | CLOSE.CHECK | `scripts/closeout_selfcheck.py` 无 FAIL | **DONE · 2026-08-03** · PASS=12 FAIL=0 NEED_HUMAN=6 |
| 186 | CLOSE.SHIP | 升版打包 / 本机覆盖 / 推 T2S | **DONE · 2026-08-11** · 最新 **0.7.19 core**（GVisualPack）→ T2S **`release_seq=64`** · build `20260811T060835Z` · zip sha256 `5097fd71…270d` · 本机已覆盖 · **本轮未要求 xlf deploy** · 证据 `~/Suying/logs/ship-0.7.19-20260811/RECEIPT.txt` + `LAST_PUBLISH.json` · **历史**：同日 **0.7.18** / seq=**63**；2026-08-10 **0.7.17** / seq=**62** 等 |
| 186a | CLOSE.CLEANUP.20260808 | 死代码删除（pipeline/NextAction）+ 测试/冒烟对齐 + 全量自检绿 + 0.7.11 双端 | **DONE · 2026-08-08** · 生产行为冻结；voice live ENV_KNOWN skip · 见 [`AUDIT_CLEANUP_2026-08-08.md`](AUDIT_CLEANUP_2026-08-08.md) |
| 198 | OPT.P0 | 版本单源 `engine/version.py` + supervisor 修复 + VERSION_SOURCE + bump/gate | DEEP | **DONE · 2026-08-10** · 见 [`DEEP_OPTIMIZATION_PLAN.md`](DEEP_OPTIMIZATION_PLAN.md) / [`VERSION_SOURCE.md`](VERSION_SOURCE.md) |
| 199 | OPT.P1 | ResourceGate/pause/publish 回归 + readiness 可观测 | DEEP | **DONE · 2026-08-10** · 租约/同token/host_pressure 单测；readiness 回显 engine_version；shutdown 结构化 log |
| 200 | OPT.P2 | app.py 域路由 + CDP/publish_runner 分层（零语义） | DEEP | **DONE · 2026-08-10** · app.py ~2k；job/catalog/reach_console/library_review；`publish_fail_forward`/`publish_prep_heal` 表面；平台 fill facade |
| 201 | OPT.P3 | poll 预算审计 + App/api 拆分 | DEEP | **DONE · 2026-08-10** · `api/` 包；`useVisibleInterval`/`useEngineHealthPoll`；Production poll 对齐 |
| 202 | OPT.P4 | 仓卫生 + 交付模板 | DEEP | **DONE · 2026-08-10** · `.gitignore` promo/chrome-for-testing；[`DELIVERY_CHECKLIST.md`](DELIVERY_CHECKLIST.md) |
| 203 | OPT.P5 | CLOSEOUT 真机剧本执行 | DEEP | **DONE · 2026-08-10** · 用户确认 [`CLOSEOUT_ACCEPTANCE.md`](CLOSEOUT_ACCEPTANCE.md) **A→F 全部通过**（GSP.6/GSO.6/CUX.DUAL_FRAME/G7.5/GVC.4/SEM.P5.H） |
| 187 | CUX.NARR_IDLE | 旁白冷启超时空转：keep_alive/早停/基建熔断 | GCustomerUX | **DONE 代码+单测 · 2026-08-03** · [`INCIDENT_OLLAMA_NARRATION_IDLE.md`](INCIDENT_OLLAMA_NARRATION_IDLE.md) · 随 **0.6.16** 交付 |
| 188 | CLOSE.NO_RESERVE | 废除定时内容预留；即时共享 ready 池；定时缺片立刻补产 | CLOSEOUT / GImmediate | **DONE · 2026-08-03** · 待发随时可发；存量 reserved 释放；无 blocked_reservation · 随 **0.6.17** / `release_seq=21` 双端覆盖 |
| 189 | CUX.SLOT_LEASE | 旁白假死占槽：租约/墙钟/TTS后置；续修：Ollama原子安装、可取消网关、机器熔断、调度幂等 | GCustomerUX | **DONE 代码+单测+0.6.19交付 · 2026-08-04** · T2S `release_seq=23`；`xlf-remote` 覆盖：原子安装+chat探针OK、无新增签名崩溃、槽归零；READY 单条仍受内容 breath 质检约束（非基建）· [`INCIDENT_OLLAMA_HEAVY_ORPHAN_SLOT.md`](INCIDENT_OLLAMA_HEAVY_ORPHAN_SLOT.md) |
| 190 | CLOSE.W2.BASELINE | W2 可离线基线：smoke + tsc + cargo + closeout_selfcheck + config_truth 跑通 + xlf 只读探针证据归档 | CLOSEOUT | **DONE · 2026-08-04** · 证据 `~/Suying/logs/closeout-w2-20260804/`；**不**销 NEED_HUMAN 真机项 |
| 191 | CUX.TASK_CREATE | 自动任务创建误报「无法连接引擎」：occurrence_key 竞态 + 前端 create/refresh 拆分 + 多账号 list N+1 | GCustomerUX / GImmediate | **DONE · 2026-08-07** · 代码+单测 · 一体包 **0.6.37** · T2S `release_seq=40` · `xlf-remote` health/CORS/create·delete · 客户三班任务保留 |
| 192 | CUX.PUB_FAIL_FORWARD | 发布 pre-submit fail-forward：登录墙有界 defer、僵尸 waiting_login reconcile、过窗告警、到点优先 deferred、makeup 人控 | GCustomerUX / GImmediate | **DONE 代码+单测 · 2026-08-07** · 见 PUBLISH_FLOWS §4.4 · 随 **0.7.6**；续 **0.7.7** auto 接力/死区修 |
| 193 | CUX.PUB_AUTO_CHAIN | 补发空转根治：due-only 让位、deferred 修复 auto、run 终态 kick 下一批、按 profile 聚合最多 12 条 | GCustomerUX / GImmediate | **DONE 代码+单测 · 2026-08-07** · 随 **0.7.7** |
| 194 | CUX.PUB_PREP_HEAL | preparing 卡死：occ 终态 mirror + 每 tick heal（成功→completed/失败窗内 reopen/孤儿 90s）；materialize 异常 reopen | GCustomerUX / GImmediate | **DONE 代码+单测 · 2026-08-07** · PUBLISH_FLOWS §4.4 · 随 **0.7.8** |
| 195 | CUX.ASSET_HEAL | 物料不全：force 重出包 + 缺数 gap 补产；缺文案可 auto heal；登录墙永不产 | GCustomerUX / GImmediate | **DONE 代码+单测 · 2026-08-07** · 随 **0.7.10**/**0.7.10** |
| 196 | QUAL.NARR_DIV | 旁白拟人去同质：画面优先装配、稀疏不注水、行业 narration_lines、近 N 条开场去重、Ollama 禁套话/画面命中 | GQual 续 | **DONE 代码+单测 · 2026-08-08** · 阶段 B 口碑规则见 NARRATION 原则 11–12 |
| 197 | QUAL.NARR_B | 阶段B：禁口水词、默认正式、商品商业「更卖」收尾、仓配连贯过程口播（#179 向）、禁包装/装车错配；Edge 晓晓不动 VIDEO_LOCK | GQual 续 | **DONE 真机 · 2026-08-10** · 用户确认 ready≥1 条人耳抽验通过 |
| 204 | CLOSE.CONFIG_TRUTH | 客户机 config_truth 只读归档 | CLOSEOUT | **DONE · 2026-08-10** · `xlf-remote` 证据 `~/Suying/logs/closeout-opt-20260810/config_truth_xlf-remote.{json,txt}` · 词池 rev=4 对齐 · **接受** `video_lock` live=null（见备注：真源多为工作区 `05-品牌/VIDEO_LOCK.json`，diff 探针只看 `profile_json.video_lock`）· **未** config_truth_import |
| 205 | CLOSE.W4 | W4 全量自检 + 关账决策笔记 | CLOSEOUT | **DONE · 2026-08-10** · 证据 `~/Suying/logs/closeout-w4-20260810/` · 本机 version 0.7.17 对齐 · closeout_selfcheck FAIL=0 / NEED_HUMAN=0 · smoke/tsc/vitest/cargo 全绿 · **人眼 PARTIAL 已销**（A→F + GUI.SHIP + QUAL.NARR_B + config_truth） |
| 206 | CLOSE.SHIP.0.7.17 | 0.7.17 一体包重打 + T2S + xlf-remote 覆盖 | CLOSEOUT | **DONE · 2026-08-10** · 用户授权 · 见 **CLOSE.SHIP** 序 186 历史段 · `release_seq=62` · 客户机词池拒绝 seed rev4 降级（仍 rev18）属预期 |
| 207 | CLOSE.SHIP.0.7.18 | 0.7.18 升 patch + T2S + 本机覆盖 | CLOSEOUT | **DONE · 2026-08-11** · 用户授权打包覆盖推 T2S · `release_seq=63` · 见序 186 · 极空间曾断连后重推成功；**未**本轮 deploy-remote |

真机 PARTIAL 关闭法一律见 [`CLOSEOUT_ACCEPTANCE.md`](CLOSEOUT_ACCEPTANCE.md)；**A→F + GUI.SHIP + QUAL.NARR_B + config_truth 归档 + W4 本机自检** 均已于 **2026-08-10** 闭环（config_truth `video_lock` 探针 diff 已判定可接受 · 见 CLOSE.CONFIG_TRUTH）。关账 W4 收口后禁止再挂人眼 NEED_HUMAN 进度行。

### 现在就做 — GVisualPack 安全包装（2026-08-11 已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 210 | GVP.1 | DEV_LOCK / VISUAL 开闸；§D 验收草约落地 | GVisualPack | **DONE · 2026-08-11** |
| 211 | GVP.2 | rule_schema：`intro_punch` / `item_label_motion` / `end_card` 默认 `none` + clamp | GVisualPack | **DONE · 2026-08-11** · `rule_schema` + 单测 |
| 212 | GVP.3 | ffmpeg 包装层：开场 soft fade ≤0.35s；物品标 fade≤0.25s；片尾 end card 在 pad、守 L15 | GVisualPack | **DONE 代码 · 2026-08-11** · `render_end_card_png` + `_video_chain` |
| 213 | GVP.4 | 规则实验室 UI 枚举（不碰 LangCombobox / L17） | GVisualPack | **DONE · 2026-08-11** · `VideoRuleWorkbench` |
| 214 | GVP.5 | 默认关回归 smoke + 单测；OFFLINE ON 可见性抽检 | GVisualPack | **DONE · 2026-08-11** · smoke/tsc/单测绿；ON 成片 meta 含 soft/fade/simple |
| 215 | GVP.H | 人眼：精品 ON ≥3 条；日更缺省观感无差 | GVisualPack | **DONE 真机 · 2026-08-11** · 用户确认 #076–#078 全部过关（job 375/376/377 · soft+fade+simple） |
| 216 | GVP.SHIP | 阶段 1 正式交付：升 patch 一体包 + T2S + 本机覆盖（含包装引擎/UI） | GVisualPack | **DONE · 2026-08-11** · **0.7.19** core → T2S **`release_seq=64`** · 本机覆盖 · health `engine_version=0.7.19` · schema 含 packaging 字段 · 首推 T2S 时 vuex 曾在 Z2S 拒传后切 T2S 重推成功 · 见 `~/Suying/logs/ship-0.7.19-20260811/RECEIPT.txt` · **未** xlf deploy |

**GVisualPack 硬边界（阶段 1 · 已交付）：** 默认关；仅装饰层；不改切镜哲学、不改旁白、不松 READY/L15/纸片。

**阶段 1 人眼收口（2026-08-11）：** #076–#078 · soft/fade/simple · `ready/2026-08-11/`。

### 现在就做 — GVisualPack2 视觉阶段 2（2026-08-11 已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 220 | GVP2.1 | DEV_LOCK / VISUAL 阶段 2 开闸；§D 验收草约 | GVisualPack2 | **DONE · 2026-08-11** · 用户开锁句成立 |
| 221 | GVP2.2 | rule_schema：`color_lut` 默认 `off`；枚举 `off`/`light` + clamp | GVisualPack2 | **DONE · 2026-08-11** · + `plan_lang_reuse` |
| 222 | GVP2.3 | ffmpeg：light LUT 链（弱曲线）；fail-closed；不伤虚焦/过曝语义 | GVisualPack2 | **DONE · 2026-08-11** · `eq` 弱增益只上画面轨 |
| 223 | GVP2.4 | V5：同 MontagePlan 多语言 — 画轨复用 + 标题/字幕/TTS 分叉 API/Job 语义 | GVisualPack2 | **DONE 代码 · 2026-08-11** · `plan_from_dict` + `reuse_montage_plan` worker + `POST /outputs/{id}/expression-variant` |
| 224 | GVP2.5 | 规则实验室 UI：LUT 枚举（**不碰** LangCombobox / L17）；多语言入口不破坏 mono 合同 | GVisualPack2 | **DONE · 2026-08-11** · 包装卡增 color_lut / plan_lang_reuse |
| 225 | GVP2.6 | 默认关 smoke + 单测；READY/L15/H9 只烧一次仍绿 | GVisualPack2 | **DONE · 2026-08-11** · 单测 gvisualpack + smoke_test 绿；tsc 无新增错 |
| 226 | GVP2.H | 人眼：V4 light ≥2 条不糊不过曝；V5 同 Plan 双语 ≥1 组画轨一致文轨分叉 | GVisualPack2 | **DONE 真机 · 2026-08-11** · 用户确认 #079–#082 + 修后 #083（job 378/379/381 · light + en 画轨复用）通过 |
| 227 | GVP2.SHIP | 升 patch 一体包 + T2S + 本机覆盖（阶段 2 入正式包） | GVisualPack2 | **DONE · 2026-08-11** · **0.7.20** · T2S **seq=65** · 本机覆盖 · health ok · schema 含 `color_lut`/`plan_lang_reuse` · 日更规则已回 **id=61** · 回执 `~/Suying/logs/ship-0.7.20-gvp2-*.md` |

**GVisualPack2 硬边界：** 默认关；仅 V4+V5；**禁止** VISUAL §8 阶段 X；不改切镜哲学；不松 READY/L15/纸片；L17 仅消费语言能力不得改组件；L18–L20 冻结；不改生活服务旁白黄金底稿。

**人眼收口（2026-08-11）：** #079–#082 中文 light 包装 · #083 英文字幕半字裁切已修后复验 · 清单 `~/Suying/logs/gvp2-human-20260811/READY_LIST.txt`。

**SHIP 收口（2026-08-11）：** `RUNTIME_FLAVOR=core` → 0.7.20 · T2S seq=65 · kit `~/Suying/releases/速影-0.7.20-product-macos-arm64-core` · zip sha256 `400e8f989c86680171a12ea9e4bcf8eb418bbeef03f0a9f0eaeddec36b9d8e86` · `/Applications` 安装 · 引擎 `engine_version=0.7.20` · 含 stage2 + Latin 字幕字高修复。

**下一步：** **0.7.26** 风格硬切已出包（T2S seq=71，本机已覆盖，xlf PARTIAL）；始峰试看三条在桌面 `速影试看-xlf-风格-20260812`；人眼验规则轮换开关；运营流水 ABCD 见 [`POST_CLOSEOUT_OPS_PLAN.md`](POST_CLOSEOUT_OPS_PLAN.md)。

### 现在就做 — GRuleLabOpt 规则实验室深度优化（2026-08-12 已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 230 | GRL.0 | DEV_LOCK / RULE_LAB_OPT 开闸；索引；禁 seed 冲 live | GRuleLabOpt | **DONE · 2026-08-12** |
| 231 | GRL.1 | schema orientation_defaults + fields min/max；前端 defaults 同源 | GRuleLabOpt | **DONE · 2026-08-12** |
| 232 | GRL.2 | 日更主路径瘦身；包装折叠；单主 CTA；Edge 文案 | GRuleLabOpt | **DONE · 2026-08-12** |
| 233 | GRL.3 | copy API 目标 content_category；克隆为精品稿 | GRuleLabOpt | **DONE · 2026-08-12** |
| 234 | GRL.4 | ruleLab/ 组件拆分 + useRuleLabState | GRuleLabOpt | **DONE · 2026-08-12** |
| 235 | GRL.5 | 主路径预览选片 + 生产页启用规则摘要 | GRuleLabOpt | **DONE · 2026-08-12** |
| 236 | GRL.6 | tsc + production_rules 测 + smoke；§E 收口 | GRuleLabOpt | **DONE · 2026-08-12** · tsc 绿；GRL schema/copy 测绿；smoke 绿 |
| 237 | GRL.7 | 按任务规则轮换：日更默认开；仅已保存；逐条开关；池空回退启用槽 | GRuleLabOpt | **DONE · 2026-08-12** · tsc 绿；rotation 测绿；smoke 绿 |
| 238 | GRL.SHIP | 0.7.24 一体包 + T2S + 本机覆盖 | GRuleLabOpt | **DONE · 2026-08-12** · **0.7.24 core** · T2S **seq=69** · zip sha256 `aa30c280…2293` · 本机已覆盖 · **未** xlf deploy |
| 239 | GRL.8 | 规则绑定词池内容面；从词池生成草稿；臻享丽人客情/创始人分类文案 | GRuleLabOpt | **DONE · 2026-08-12** · facet fail-closed；drafts-from-pack；词池 **rev21**（可发短句，店规/传记不上屏）；规则 71/72/73；试产 396/397/398「角落收拾得很干净」/「好好生活好好美」/「过程细态度也在线」 |
| 240 | GRL.8.SHIP | 0.7.25 一体包 + T2S + 本机覆盖 + xlf 覆盖（不改客户音色） | GRuleLabOpt | **DONE · 2026-08-12** · **0.7.25 core** · T2S **seq=70** · zip sha256 `a001e8e0…187a` · 本机 health `0.7.25` 臻享丽人 · xlf **PARTIAL SMOKE_NOT_REQUESTED** · App/engine `0.7.25` · 始峰规则 16 仍 **clone/aunt_slow rev15** · 词池拒绝降级仍 rev18 · 未 config_truth_import / 未 PUT TTS |
| 241 | STYLE.HARD | 0.7.26 风格硬切选片 + T2S + 本机/xlf 覆盖；始峰装车/店面/产品各 1 条试看 | GRuleLabOpt | **DONE · 2026-08-12** · **0.7.26 core** · T2S **seq=71** · zip sha256 `ede85ecb…5e41` · 未 `--customer-config` · 规则 16 仍 clone/aunt_slow · 词池仍 rev18 · 试产 942/943/944 画面分别为 transport / 门头+店招 / product_closeup · 自动发布与 auto_daily 已停 |

**GRuleLabOpt 硬边界：** 不改 v3 真相/Job 冻结；包装默认关；精品克隆只产 draft；不碰 L17 实现 / L18–L20；禁止 seed 覆盖 live；一体包重建后再验 UI。  
**分锁：** [`RULE_LAB_OPT.md`](RULE_LAB_OPT.md)

### 现在就做 — GSceneTour 跟镜精品（2026-08-13 已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 250 | GST.0 | SCENE_TOUR_LOCK + DEV_LOCK 开闸；与 L20 并存 | GSceneTour | **DONE · 2026-08-13** |
| 251 | GST.1 | 客户隔离简报/骨架/选片 fail-closed | GSceneTour | **DONE · 2026-08-13** |
| 252 | GST.2 | 分桶冷启动 + 覆盖门槛 + 冷却对齐 | GSceneTour | **DONE · 2026-08-13** |
| 253 | GST.3 | 本地自由写词 + 标题小集 + 成片检查 | GSceneTour | **DONE · 2026-08-13** |
| 254 | GST.4 | worker `scene_tour` 分支；跳过词池金句 | GSceneTour | **DONE · 2026-08-13** |
| 255 | GST.5 | 生产页主按钮 + 规则实验室中文类别 + 审片镜句对照 | GSceneTour | **DONE · 2026-08-13** |

### 现在就做 — GRuleVisualLab 规则实验室视觉大改（2026-08-22 已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 260 | GRVL.0 | FX 白名单文档 + manifest + licenses + DEV_LOCK 开闸 | GRuleVisualLab | **DONE · 2026-08-22** |
| 261 | GRVL.A | 三栏画布 + 标题/字幕安全区拖字头 | GRuleVisualLab | **DONE · 2026-08-22** |
| 262 | GRVL.B | Tier A 字体枚举 + 本机扫描 API | GRuleVisualLab | **DONE · 2026-08-22** |
| 263 | GRVL.C | 多层蒙版 schema + 渲染 overlay + UI | GRuleVisualLab | **DONE · 2026-08-22** |
| 264 | GRVL.D | 装饰贴图层（非标题区）+ Twemoji 合同 | GRuleVisualLab | **DONE · 2026-08-22** |
| 265 | GRVL.E | 字幕竖排布局 | GRuleVisualLab | **DONE · 2026-08-22** |
| 266 | GRVL.F | clip_transition xfade 枚举（默认 none） | GRuleVisualLab | **DONE · 2026-08-22** |
| 267 | GRVL.G | narration_text_effect karaoke/marquee | GRuleVisualLab | **DONE · 2026-08-22** |

| 256 | GST.6 | 热点第二版占位；评测/冒烟；界面无英文裸奔 | GSceneTour | **DONE · 2026-08-13** |
| 257 | GST.7 | 声画对齐：TTS后拉长镜长 / tempo-fit / 混音前贴齐；`freeze_pad>0.5` 打回；L14 废止剔除 AI 披露 | GSceneTour | **DONE · 2026-08-13** · 单元测绿；#405 离线贴齐样片 `*_av_fixed`；新任务受 clone TTS 墙钟影响待人听验收 |

**GSceneTour 硬边界：** 不改 L20；不把热点原文写进旁白（V1）；不把 `premium` 精品样式冒充跟镜；跨客户不串味；缺分类/检查失败 → 拒片且中文说明；**禁止片尾冻帧旁白还在说**。  
**分锁：** [`SCENE_TOUR_LOCK.md`](SCENE_TOUR_LOCK.md)

### 现在就做 — GContent SEO/GEO 软文中枢（已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 110 | GC.1 | DEV_LOCK / PRODUCT_PLAN / CONTENT_NON_GOALS 开闸 | GContent | DONE |
| 111 | GC.2 | 资料/事实/多域名/平台注册表 + SQLite 表 | GContent | **DONE** |
| 112 | GC.3 | 主事实稿、平台变体、合规审核、App「软文」分区 | GContent | **DONE** |
| 113 | GC.4 | 浏览器发布状态机（headless + 可见接管 + 核验） | GContent | **DONE** |
| 114 | GC.5 | 官网/百家号/头条号/公众号适配器骨架 + 冒烟 | GContent | **DONE** |
| 115 | GC.6 | 消息回复草稿工作台（不自动发送） | GContent | **DONE** |
| 116 | GC.7 | 多客户隔离 + smoke + tsc | GContent | **DONE** · 单测/smoke/tsc 绿 · 2026-07-27 |
| 117 | GC.BOOT | 事故：内容单测 `save_settings` 污染 `~/Suying/data` 引导 → 引擎连临时库「资料消失」；已恢复权威库指针 + 禁止 ephemeral 镜像引导 | GContent / GStab | **DONE** · 2026-07-27 · [`INCIDENT_SETTINGS_BOOTSTRAP_POLLUTE.md`](INCIDENT_SETTINGS_BOOTSTRAP_POLLUTE.md) |
| 118 | GC.SCOPE | 软文/视频业务域隔离：Chrome 目录、登录态、消息账号、未读红点、回复草稿；消息 Tab 双标签；软文预览即已读 | GContent / G7 | **DONE** · 2026-07-27 |
| 119 | GC.ACCOUNTS | 视频/软文 Chrome 账号分区；软文完整增改删（含自定义官方入口）；视频单删/批删；安全删除同步选择与消息绑定 | GContent / G7 | **DONE** · 登录仅认同一受管 Chrome DOM 实时探针；Cookie 不冒充已登录；删除受运行任务/计划/预留门禁 · 2026-07-29 |

**GContent 硬边界：** 仅客户自有站点与本人账号；事实约束生成；人工审核后发布；验证码停人；回复仅草稿+官方页；禁止站群/矩阵/绕检测/自动发送回复；密钥不进 SQLite；**视频与软文浏览器配置/消息不得共用登录态**。
**工程硬边界（引导）：** 测试/冒烟的临时 `data_root` 不得覆盖真实 `~/Suying/data/settings.json`；权威库 = 工作区 `速影工作区/db`。

### 现在就做 — GVideoRules 视频规则实验室（已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 120 | GVR.1 | DEV_LOCK / PRODUCT_PLAN 开闸 + 可调/硬锁分层 | GVideoRules | **DONE** |
| 121 | GVR.2 | 规则 schema / 版本表 / 本地 AI 严格解析 | GVideoRules | **DONE** |
| 122 | GVR.3 | API + dry-run/Job 快照冻结 + 生成链路消费 | GVideoRules | **DONE** |
| 123 | GVR.4 | 生产页「规则实验室」人工确认启用 | GVideoRules | **DONE** |
| 124 | GVR.5 | 隔离/硬锁/解析测试 + smoke + tsc | GVideoRules | **DONE** · 回归 8 绿；AI 强制补齐全部结构化字段，人工确认与当前字段原子保存；规则旁白语气实际消费、逐条 seed/画面旁白差异化；smoke/tsc 绿 · 2026-07-27 |

**GVideoRules 硬边界：** 本地 AI 仅为可选草稿捷径；手动预设无需 AI。保存并启用须校验且 Job 冻结 revision/画幅/完整 effective rules/音色版本。标题、字幕、旁白、语言、音色可按横/竖画幅配置，但只能在安全范围内；禁止放松 READY_GATE、画质、声画对齐/只烧一次、成片>旁白、纸片滚动避重和证据门禁。

### 现在就做 — GSystemPause 系统事件暂停/恢复（已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 130 | GSP.1 | DEV_LOCK / HARD_LOCKS / SYSTEM_EVENT_PAUSE 开闸 | GSystemPause | **DONE** |
| 131 | GSP.2 | Tauri macOS NSWorkspace 事件桥 + 四开关 + 系统 token | GSystemPause | **DONE** · NSWorkspace 睡眠/唤醒/关机/熄屏 + NSDistributed 锁屏/解锁；token 单源持久化、0600；出队以引擎 HTTP 确认（含 duplicate/acknowledged）；**2026-08-03** 修审计阻塞+3s超时+duplicate 卡死 outbox（见 INCIDENT_SYSTEM_EVENT_OUTBOX_TIMEOUT） |
| 132 | GSP.3 | 引擎 pause_coordinator + 423 门禁 + /system/* API | GSystemPause | **DONE** · 原因 owner/generation 精确释放；健康检查异常 fail-closed；恢复竞态取消；状态原子落盘；**2026-07-31** 修迟到 quiesce 空原因 `PAUSED_BLOCKED`；同日续修 `power_off` 冷启动/人工 resume 假恢复（见 INCIDENT_PAUSE_ORPHAN_BLOCK）；**2026-08-03** 修 quiesce 超时+唤醒 `pending_resume` 卡 `PAUSED_BLOCKED`（`message_sync` 停机）导致一键发布 423 |
| 133 | GSP.4 | 生产/扫描/索引/调度/消息安全暂停；发布/同步/更新人工确认 | GSystemPause | **DONE** · quiesce 超时阻断；模型拉取可终止；发布/上传/副作用任务不自动重放 |
| 134 | GSP.5 | 设置页四开关 + 顶栏/运维运行态 | GSystemPause | **DONE** |
| 135 | GSP.6 | 单测 + smoke_system_events + tsc/cargo；真机 sleep/wake | GSystemPause | **DONE 真机 · 2026-08-10** · 自动验收绿 + 用户确认 CLOSEOUT_ACCEPTANCE §A（合盖/熄屏/冷启动） |

**GSystemPause 硬边界：** 自动恢复只清系统暂停原因；人工暂停/熔断/验证码停人/路径异常不得解除；发布与更新等副作用任务禁止自动重放；事件日志在本机 runtime，不依赖外置盘。见 [`SYSTEM_EVENT_PAUSE.md`](SYSTEM_EVENT_PAUSE.md)。

### 现在就做 — GSemanticOps（已授权 · 离线实现完成）

| 序 | ID | 任务 | Gate | 状态 |
|----|----|------|------|------|
| 140 | GSO.1 | DEV_LOCK / HARD_LOCKS / 专项锁开闸并消除旧口径冲突 | GSemanticOps | **DONE** |
| 141 | GSO.2 | coarse/strict 与官方目录证据/画面事实分层 schema、迁移、API、App | GSemanticOps | **DONE · 代码/迁移/API/App/专项测试** |
| 142 | GSO.3 | 纸片：原日/周双限额已于 2026-07-31 废止，改为滚动避重（见 PAPER.SLIP / L6） | GSemanticOps | **SUPERSEDED → 滚动避重** |
| 143 | GSO.4 | 标题/字幕/物品中文名最终字形像素几何渲染与验收 | GSemanticOps | **DONE · 代码 + 1080×1920 像素黄金测试** |
| 144 | GSO.5 | 向量最老优先持久队列、只入队 API、真实状态、暂停放弃当前请求并保留已完成 | GSemanticOps | **DONE · 持久队列/单槽/≤2s 暂停/总览真实状态** |
| 145 | GSO.6 | 多客户隔离、幂等恢复、暂停/崩溃回归、主冒烟与真机成片验收 | GSemanticOps | **DONE 真机 · 2026-08-10** · 自动验收绿 + 用户确认 CLOSEOUT_ACCEPTANCE §B（新成片 5 条人眼） |

**GSemanticOps 状态：离线 + 真机新成片人眼（§B）完成 · 2026-08-10。** 专项硬边界见 [`SEMANTIC_OBJECT_VECTOR_LOCK.md`](SEMANTIC_OBJECT_VECTOR_LOCK.md)。

### 现在就做 — GVoiceClone 本地克隆旁白（已授权）

| 序 | ID | 任务 | Gate | 状态 |
|----|----|------|------|------|
| 150 | GVC.1 | DEV_LOCK / VOICE_CLONE 开闸 | GVoiceClone | **DONE** |
| 151 | GVC.2 | `voice_clone` 声色包 + `tts provider=clone` + READY/worker 门禁 | GVoiceClone | **DONE** · 2026-07-31；**同日 xlf-remote**：同步 `aunt_slow`→`05-品牌/voice` + VIDEO_LOCK `provider=clone`；一体包内嵌 `f5-tts`+`configs/voice_packs` 并重签；`/voice/tts` → `effective_provider=clone` / `clone_available=true` |
| 152 | GVC.3 | 捆绑 `aunt_slow` 包；客户 VIDEO_LOCK 可锁 clone | GVoiceClone | **DONE** |
| 153 | GVC.4 | 单测 + smoke；听感抽检 | GVoiceClone | **DONE 本机 · 2026-08-07** + **关账 §E 真机 · 2026-08-10** · 用户确认 CLOSEOUT_ACCEPTANCE §E（3 条 clone 听感）；L19 冻结相关链路 |
| 154 | GVC.5 | **F5 Runtime Kit 合同**（core App + App 外 overlay；package/install/verify/T2S 分轨；deploy-remote core+Kit） | GVoiceClone | **DONE · 2026-08-07** · 见 [`VOICE_CLONE.md`](VOICE_CLONE.md)；`f5_kit.py` + 安装/发布脚本 |

**GVoiceClone 硬边界：** 默认 Edge；clone 为可选；参考音须授权；F5 模型 CC-BY-NC 商用清权另议；慢速包禁止混入快语速产品口播参考；**L19：无授权勿再改就绪/试听链路**；**交付主路径 core+App 外 Kit**（禁常态 merge 进 App）；见 [`VOICE_CLONE.md`](VOICE_CLONE.md)。

### 现在就做 — GCustomerUX 客户体验收口续（2026-08-01 开执行）

| 序 | ID | 任务 | Gate | 状态 |
|----|-----|------|------|------|
| 160 | CUX.NOTIFY | App 内 toast（右上/15s/可点跳转/无按钮）+ Mario 合成音；业务路径停用 macOS 系统通知 | GCustomerUX | **DONE · 2026-08-01** |
| 161 | CUX.DISPLAY_NO | 成片 sole UID `#001…`（`display_no`）；客户 UI 隐藏库内 #id | GCustomerUX | **DONE · 2026-08-01**（API/worker/审片；存量回填） |
| 162 | CUX.TTS_FP | edge/clone 成片不因事后改锁判音色违规 | GCustomerUX | **DONE · 2026-08-01** |
| 163 | CUX.REJECT | 打回释放纸片+词池；默认重渲；满 2 次转人工 | GCustomerUX | **DONE · 2026-08-01** |
| 164 | CUX.REVIEW_UI | 审片操作坞右移；「打开文件位置」 | GCustomerUX | **DONE · 2026-08-01** |
| 165 | CUX.TIER_GATE | `gate_profile.v1` 按档写入 install-plan/settings；回执带版本 | GCustomerUX | **DONE · 2026-08-01**（INSTALL_PROFILE 已改口径） |
| 166 | CUX.P1 | 发布同步/语义开关/日志/首页刚完成/任务看板/自定义克隆音色 | GCustomerUX | **DONE · 2026-08-01**（安装 App：语义 off 返回 409 且恢复 on_demand；视频账号取得 `verified_logged_in`，软文账号取得 `logged_out`，均来自受管 Chrome DOM） |
| 167 | CUX.P2 | 运维·设置 IA / 高级密码锁死 / 账号统一 | GCustomerUX | **DONE · 2026-08-01**（App 内 create/change/clear 命令面已移除；真实 Keychain verifier 格式、进程内解锁/重启复锁及视频/软文同构测试通过） |
| 168 | CUX.TRIAL | 三日体验期：签名合同、时间硬锁、会话解锁；无制作/发布日配额 | GCustomerUX / GLicense | **DONE · 2026-08-01** · schema v2 无配额；旧 v1 仅兼容验签 · [`TRIAL_LICENSE_PLAN.md`](TRIAL_LICENSE_PLAN.md) |
| 169 | CUX.NO_QUOTA | Reach、体验许可、软文伪配额与额度表/API/UI 全量移除；失败熔断/单槽/内容占用保留 | GCustomerUX | **DONE 代码+专项测试 · 2026-08-01** |
| 170 | CUX.LOG_NOTIFY | 完成通知真实编号+15 秒绝对计时+精确深链；生产/向量/语义/消息/系统/备份/清理统一日志 | GCustomerUX | **DONE 代码+专项测试 · 2026-08-01** |
| 171 | CUX.SYSTEM_HEAL | 系统事件持久 FIFO、唤醒竞态安全边界、发布原子系统中断禁重放 | GSystemPause / GCustomerUX | **DONE 代码+专项测试 · 2026-08-01** · 真机 sleep/wake 人眼 **GSP.6 DONE · 2026-08-10** · CLOSEOUT_ACCEPTANCE §A |
| 172 | CUX.DATA_PROTECT | SQLite 一致快照、词库/Chrome 备份、历史备份安全回收、已发布视频保留策略 | GCustomerUX | **DONE 代码+专项测试 · 2026-08-01** · LaunchAgent 现场确认归 W3 运维点验（不另虚报 DONE） |
| 173 | CUX.RULES_IA | 独立规则页；标题/字幕/语言/音色统一；日志归运维、备份归运维、账号归设置；客户向文案 | GVideoRules / GUI | **DONE 代码+App 类型/组件测试 · 2026-08-01** |
| 174 | CUX.DUAL_FRAME | 横屏/竖屏素材分类、向量/规则/生产隔离；1080×1920 与 1920×1080 双合同 | GVideoRules / GSemanticOps | **DONE 代码+专项测试 · 2026-08-01** · **DONE 真机 · 2026-08-10** · 用户确认 CLOSEOUT_ACCEPTANCE §C（≥1 条 1920×1080） |
| 175 | CUX.TITLE_SUB_FIX | 标题 fade 单帧不可见 + burn_mono 字幕≠旁白（H11） | GCustomerUX | **DONE 代码+单测 · 2026-08-02** · [`INCIDENT_TITLE_SUBTITLE_MISMATCH.md`](INCIDENT_TITLE_SUBTITLE_MISMATCH.md) · 包 **0.6.6 core** |
| 176 | CUX.RELEASE_T2S | 一体包升版直推 T2S；产物改 ~/Suying/releases；桌面禁落交付物；**打包默认闭环=升 patch + 推 T2S + 本机覆盖** | GCustomerUX / GCarrier | **DONE · 2026-08-02** · `scripts/release-to-t2s.sh` · **完成时快照** T2S `0.6.11` / `release_seq=15`（勿当现行最新；现行见序 186 / `LAST_PUBLISH.json`） |

### 禁止现在做

数字人假脸、反检测卖点/对抗 SDK、全自动矩阵养号、自动过验证码/滑块、自动回复私信、Cookie 导出/注入、片库默认同步进 T2S、软文站群。
（**允许** G5.R / G5.V 本人单账号路径；**当前主线 = GCustomerUX**。）

---

## G. Definition of Done（写死）

- [ ] 无客户名硬编码分支
- [ ] 读写带 customer 域
- [ ] 差异在配置/profile
- [ ] `smoke_test.py` 通过
- [ ] §E 状态已改
- [ ] 不把 NEED_DISK 项标 DONE

---

## H. 文档权威顺序

**唯一权威索引**：[`docs/README.md`](README.md)。本节不得另立矛盾表。

冲突裁决（从上到下）：

1. `DEV_LOCK.md`（本文件）+ [`HARD_LOCKS.md`](HARD_LOCKS.md) — 做什么、何时做、硬规则
2. 分锁与领域合同（READY_GATE / 旁白字幕 / 语义对象向量锁等，见 README §1–2）
3. `DEVELOPMENT_STANDARDS.md` / `PRODUCT_PLAN.md` — 工程标准与产品总图
4. 运维与交付（`REMOTE_DEPLOY.md` 等）→ 事故复盘 / 优化方案
5. [`archive/`](archive/) — 历史归档，仅考古；V1–V7 / BACKLOG 不得指导现行开发

---

## I. 变更本锁

只有你本人明确说「修改 DEV_LOCK」才允许改 Gate/优先级。
Agent 不得擅自把 **未授权** 的下一 Gate 标为当前可做。
**已授权：** G3；G4；G5；**G5.R**（四）；**G5.V 视觉全自动**（五 · 风控自负）；**G6 日更运营闭环**（2026-07-25）；**GStab / GCarrier / GFleet / GQual / GShip**（2026-07-26 · 整体提升计划实现）；**G7 本机消息巡检**（2026-07-26 · 本人账号只读摘要）；**GUI App 整体 UI 重构**（2026-07-27）；**GContent SEO/GEO 软文中枢**（2026-07-27 · 计划批准并实现）；**GVideoRules 视频规则实验室**（2026-07-27 · 计划批准并实现）；**GSystemPause 系统事件暂停与恢复**（2026-07-27 · 计划批准并实现）；**GAutoLoop 自动闭环 + GUI.IA2 八页重构**（2026-07-28 · 每条窗口随机、质量补偿、三路提醒）；**GImmediate 即时批量发布 + GLogs 日志中心**（2026-07-28 · 即时/定时隔离、九页导航）；**GSemanticOps 语义证据、物品标注与向量运营**（2026-07-30 离线 + **2026-08-10 真机 §B**）；**GVisualPack 安全包装动效**（2026-08-11 · 默认关 · 阶段 1 仅 V1–V3 已交付）；**GVisualPack2 视觉阶段 2**（2026-08-11 · 默认关 · V4 轻 LUT + V5 同 Plan 多语言）；**GRuleLabOpt 规则实验室深度优化**（2026-08-12 · 计划批准并实现）。
