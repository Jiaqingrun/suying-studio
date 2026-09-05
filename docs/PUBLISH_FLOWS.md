# 速影 · 客户视频发布操作说明

> 客户日常只看本页最前面的三步。本文后半部分是维护说明，不需要客户理解。

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
---

## 客户每天只做三步

### 第一次使用：先登录一次

1. 打开「发布」页面。
2. 选择抖音、视频号、小红书或快手。
3. 点击「添加账号并去登录」。
4. 在打开的 Chrome 中登录本人的平台账号。

以后正常发布不需要重复创建账号。不要填写目录、配置名或文件路径。

### 马上发布

1. 如果有多个账号，勾选这次要用的账号；只有一个账号时不用选择。
2. 在「这次发几条」中填写数量。
3. 点击「开始发布」，确认后等待即可。

系统会自动完成：

- 从已经审核合格的视频中选片；
- 审核通过时自动生成发布所需的物料包；
- 跳过已经安排定时发布的视频；
- 准备标题、正文和封面；
- 把发布数量平均分给所选账号；
- 依次打开账号并发布。

### 什么时候需要客户回来

只有三种情况需要客户操作：

1. **需要登录或验证码：** 到已经打开的 Chrome 完成操作，系统随后自动继续。
2. **平台没有明确显示成功：** 先到平台作品列表查看，再点击「我确认已经发布」。
3. **页面提示发布已停止：** 不要重复点击发布，联系维护人员查看「日志」页面。

发布页会实时显示当前第几条、账号、平台，以及“切换账号 / 检查登录 / 上传 / 填文案 / 设封面 / 提交 / 核验”等阶段。某条在提交前卡住时，可以：

- **跳过，稍后手动补发：** 立即继续后面的条目，卡住项进入待补发列表；
- **跳过，稍后自动补发：** 立即继续后面的条目，发布槽空闲后自动补发；
- **结束本批次：** 安全停止剩余条目并释放发布窗口。

如果已经点击平台“发布”但结果不明确，系统只允许“确认已发布”或先到作品列表确认“未发布”后再补发，禁止直接重复上传。

### 每天定时发布

打开「自动任务」，可以建立多个任务。每个任务只需设置：

- 一个或多个本人账号；
- 每个账号发布几条；
- 哪几天；
- 精确到秒的最早、最晚时间；
- 是否开启「生产 → 自动审片 → 发布」循环。

点击「创建自动任务」即可。每次发布 occurrence 都会在窗口内使用独立随机 seed
生成并持久化随机秒；重启后沿用原时间，禁止重抽。开启闭环时，系统先使用
READY_GATE 与当前审片均通过、物料完整的 READY 成片；数量足够便跳过
生产，只有缺口才立刻补产。待发池无定时预留锁。

### 平时不要打开的区域

「高级设置与手动发布」用于特殊处理。普通客户不需要调整封面槽位、物料目录或队列状态；账号统一在设置中管理。

---

## 以下为维护人员规则

操作边界以 [`REACH_NON_GOALS.md`](REACH_NON_GOALS.md) + [`DEV_LOCK.md`](DEV_LOCK.md) 为准。软文发布见 [`CONTENT_NON_GOALS.md`](CONTENT_NON_GOALS.md)。

### 发布模式

| 模式 | UI 位置 | 谁点「发表」 | 一次发几条 | 典型用途 |
|------|---------|--------------|------------|----------|
| **A. 手动半自动（人点发布）** | 发布 → 触达队列 | **本人**在官方页点 | 1 | 默认、最稳、风控最低 |
| **B. 单条自动上传（G5.V）** | 队列项 / 历史 auto-upload | 程序代点（须 `accept_risk`） | 1 | 已登录本机账号，代填+上传当前一条 |
| **C. 即时批量自动发布（GImmediate）** | 「一键即时批量发布」 | 程序串行代点（须 `accept_risk`） | N（人工勾选后分配） | 一次勾选多账号/多次，马上发完 |
| **D. 窗口定时自动发布** | 「窗口随机排期」 | 到点后走同一套 runner | 按窗口/计划 | 每日窗口内随机时刻自动发 |

共性硬规则（四种都遵守）：

1. **本人账号、本机 Chrome 独立 `user-data-dir`**；不做 Cookie 池 / 并发群控 / 养号。  
2. **全机单槽**：任意时刻最多一个受管 Chrome / 一个 publish run 在跑。  
3. **登录 / 验证码 / 滑块 / 安全验证 → 必须人过**；停人后探测恢复或人工确认，禁止自动过验证。  
4. **不以绕开平台检测为卖点**；窗口内随机时刻仅用于排期审计，不是反检测。  
5. 自动发布必须由本人在自然语言确认框中确认风险，内部请求才写入 `accept_risk=true`。

---

## 1. 发布前必备条件

### 1.1 成片与物料

```text
渲染成片 → READY_GATE 全过 → state=ready
       → 强制自动审核通过（无需客户开关）
       → 服务端幂等生成四视频平台完整 publish_pack
       → 才可入队或进入即时/定时候选池
```

- 成品库门禁：[`READY_GATE.md`](READY_GATE.md)（fail-closed，任一项失败不成 `ready`）。  
- **审片自动过审（GReview.Auto）**：`qc_json.ready_gate.ok=true` 时（`state` 为 `ready` 或软信号留下的 `review`）必须自动通过并升为 `ready` 出包；硬失败自动拒绝；仅门禁缺失/未过或证据冲突进入 `uncertain` 人工队列。一致性偏低（`needs_review`）只告警，不挡自动过审。  
- 服务端出包必须覆盖抖音、视频号、小红书、快手四个平台；任一平台文案或视频合同不完整时成片转 `asset_blocked`，不得进入任何发布候选。  
- 即时、定时和自动闭环共用同一资产门禁，并要求当前审片决定为 `approved`。  
- **待发池共享**：`ready` 成片（无进行中发布组）可被立即或定时选用；禁止内容预留锁片；抢片靠执行时占用，不够立刻补产。

### 1.2 视频发布账号（Chrome）

1. 发布页「视频发布账号」：选平台（抖音 / 视频号 / 小红书 / 快手）→ 创建配置。  
2. 「打开官方页」用该配置登录**本人**账号（与软文账号目录隔离）。  
3. 可选「加入视频消息巡检」（消息只读，与发布队列无关）。  
4. 配置目录形态：`<工作区>/chrome-profiles/customer-{id}/video/<配置名>`。

### 1.3 发布前门禁（文案 + 封面）

权威见 `REACH_NON_GOALS`「发布前门禁」与 `engine/reach/publish_assets.py`。

| 项 | 规则 |
|----|------|
| 文案 | 平台正文/描述非空；视频号 = 短标题 + 描述 |
| **AI 披露（L14 已废止）** | 成片物料**禁止**出现「本作品由AI生成」「本视频由AI生成」等；出包/预填/门禁/CDP 一律剔除，不得再追加 |
| 封面槽位 | 所有视频平台固定 1 张竖版封面；比例按 App 对应平台提示 |
| 门禁失败 | **禁止点「发布/发表」**；返回需人工 / failed，写明缺文案或缺第几个封面 |
| 封面核验 | 抖音/视频号/快手优先上传并匹配 App 当前竖版封面；约 **20 秒**硬墙钟超时或核验失败则放行平台默认封面并继续点发；小红书封面可选（0 耗时跳过） |
| 小红书例外 | 网页封面编辑器常挂：跳过封面上传，仍自动点「发布」，记「封面待补」；文案门禁不变 |

未选用 App 封面模板、图片不是竖版时，非小红书平台仍 fail-closed（不回退 pack 的 `cover.jpg`）。封面设置超时约 20 秒或页面核验不匹配时：关闭封面弹层，用平台默认封面继续自动点发（记 soft pass 警告）。视频号封面：优先 DOM/文件注入，vision 最多 2 次点「编辑」，第二次 `modal_not_open` 立即 soft_pass。

#### 阶段耗时 evidence.timings

每条 `ReachPublishRunItem.evidence_json.timings`（并复制到 `publish_result.timings`）记录毫秒：

```text
login_ms, chrome_ready_ms, upload_inject_ms, wait_upload_ready_ms,
fill_ms, cover_ms, submit_ms, verify_ms, total_ms
```

批次结束时 `run.config_json.chrome_cleanup` 记录关 Chrome 结果（`elapsed_ms` / `killed`）。读 `GET /reach/publish/runs/{id}` 即可对照慢阶段；提速实验必须以 timings 为准。

#### 小红书自动上传与发布规则

1. 视频上传、标题/正文回读必须通过；真实短信验证码/滑块才停人，帮助文案里的「验证」不算。
2. **不强制上传封面**：跳过易坏的封面编辑器，使用平台默认帧，并记「封面待补」。
3. 表单就绪后必须自动点击红色「发布」按钮；封面失败不得阻断点击。
4. 点击后以成功提示/跳转/作品列表复核为准；结果不明进入待确认，禁止盲目重发。

#### 视频号自动上传与发表规则

1. 只读取 App「当前选用」模板的 `channels` 竖版封面；原图不改写，上传前在临时目录居中适配为视频号要求的 **6:7（1080×1260）**。
2. 暂停恢复时先确认页面仍有已上传视频；Chrome 重启导致表单丢失时必须重新上传视频、文案和封面，禁止把数据库检查点误当成网页现状。
3. 优先在约 **20 秒**硬墙钟内把模板封面核验进编辑器/表单缩略图；超时或仍为首帧/智能封面时软放行平台默认封面并继续发表。DOM/file 注入优先，vision「编辑」最多 2 次。
4. 短标题、视频描述回读通过后，最多等待/重试 3 次寻找已启用的底部「发表 / 发布」主按钮。
5. 点击使用真实鼠标事件，并正确换算同源 iframe 坐标；不点击页面标题、定时选项或普通文字。
6. 点击后只以成功提示、成功跳转或作品列表复核为准；短信验证码停人，结果不明进入待确认，禁止盲目重发。

### 1.4 账号 ↔ 平台匹配

自动批次创建前 `preflight_items`：Chrome 配置绑定平台必须与条目 `platform` 一致，否则 fail-closed，整批不启跑。

---

## 2. 模式 A · 手动半自动（人点发布）

产品默认路径。引擎只备料、开官方页、写粘贴卡；**发表按钮由人点**。

### 2.1 流程

```text
物料导出 publish_pack
    → 发布页填写 pack 路径 →「从物料包入队」
    → 队列项 status=queued（预填标题/文案/视频路径）
    → 选视频 Chrome 配置（可先「打开官方页」登录）
    → 队列项「打开官方入口」：打开创作者页 + 粘贴卡
    → 本人在官方页粘贴/上传/点发布（验证码人过）
    → 队列变 awaiting_human 后点「我已发布」→ published
```

### 2.2 队列状态机

| 状态 | 含义 |
|------|------|
| `queued` | 已入队，待打开官方页 |
| `awaiting_human` | 已预填/已开页，等人点发或确认 |
| `published` | 人确认已发（终态） |
| `cancelled` / `blocked` / `failed` | 取消 / 熔断阻塞 / 失败 |

合法迁移见 `engine/reach/queue.py`（例如 `queued → awaiting_human → published`）。

### 2.3 UI 与 API（摘要）

| 动作 | UI | API（示意） |
|------|-----|-------------|
| 入队 | 「从物料包入队」 | Reach enqueue from pack |
| 开官方页 | 队列「打开官方入口」 | `POST /reach/queue/{id}/open` |
| 确认已发 | 「我已发布」 | mark published |
| 登录配置 | 「打开官方页」 | `POST /reach/chrome-profiles/open` |

### 2.4 适用与注意

- 适合：首次登录、平台改版、验证码频繁、只发 1 条。  
- 不做：自动点发表、多号连发、无人值守。  
- 历史补记：[`HUMAN_PUBLISH_CONFIRM.md`](HUMAN_PUBLISH_CONFIRM.md)。

---

## 3. 模式 B · 单条自动上传（G5.V）

在**已授权风控自负**前提下，对**当前一条**队列项：打开受管 Chrome → 等登录 → CDP 代填视频/文案/封面 → 尝试点发布。

### 3.1 流程

```text
accept_risk=true
    → 打开绑定 Chrome 配置 + 平台上传 URL
    → phase=waiting_login（人登录若需要）
    → CDP 就绪后跳转上传页
    → 发布门禁（文案/封面）
    → 抖音/视频号/小红书/快手：CDP 注入并推进
    → 验证码/SMS → need_human（停）
    → CDP 不可用 → 写粘贴卡，退回人点（awaiting_confirm）
    → 成功则队列记 published；小红书封面 soft-fail 可放行并记「封面待补」
```

### 3.2 阶段（`auto_upload`）

| phase | 含义 |
|-------|------|
| `waiting_login` | 已开 Chrome，等人登录 |
| `uploading` | CDP 注入上传 |
| `need_human` | 验证/门禁/未点到发表 |
| `awaiting_confirm` | 已退回粘贴卡，等人完成 |
| `done` / `failed` / `cancelled` | 结束 |

### 3.3 规则摘要

- **一次一条**；不做多号连发。  
- 须 `accept_risk`。  
- 与即时批次共用受管 Chrome 资源时仍遵守单槽（lease）。  
- API：`POST /reach/auto-upload/start` · `GET .../status` · `POST .../cancel`。

---

## 4. 模式 C · 一键即时批量发布（GImmediate）

「现在就发完」：人工勾选本人账号与内容策略 → **预演冻结 seed** → 启动后**全机单槽串行**自动发完并通知。

### 4.1 UI 步骤（发布页 · PublishBatchPanel）

1. **选择本人账号**（多选已创建的视频 Chrome 配置）。  
2. **总发布 occurrence** + 分配方式：  
   - `auto_even`：总次数按账号自动平均（余数从前到后 +1）  
   - `manual`：逐账号填次数，和必须等于总次数  
3. **内容模式**：  
   - `random_unique`：从合格即时池随机不重复（数量 ≥ occurrence）  
   - `selected_cycle` / `single` 仅可在预演阶段表达选择意图；实际冻结时同一成片不得分配为重复平台/账号 occurrence。  
4. 「查看分配摘要」→ 冻结 seed、候选数、每条账号/内容分配（**不改定时 schedule/trigger/窗口**）。  
5. 「开始立即发布」→ 若已有进行中 run，则切到该批次，禁止并行第二槽。

### 4.2 内容候选硬过滤

即时池 `list_immediate_candidates` 只收：

- `RenderOutput.state == ready`  
- 有 publish_pack  
- READY_GATE 明确通过且当前审片决定为 `approved`  
- **未**进入其他发布组；已进入组的成片只能续跑原冻结目标  
- 对所选平台文案+封面门禁可通过  
- （2026-08-03 起）**不再**因定时 `reserved` 排除；内容预留已废止  

### 4.2a 发布组与退役

- 首个目标开始提交时，成片立即退出通用候选池；剩余目标只能在同一冻结发布组内续跑。
- 已成功的平台/账号目标形成不可变发布事实，永不重复上传。
- `outcome_unknown` 保持隔离；只有人工在作品列表确认“未发布”后，原目标才可恢复。
- 本次选定的全部目标核验成功后，成片先转 `retired_pending_archive`，再把视频、sidecar、封面、字幕、旁白和完整物料包非删除归档到 `retired/published/`。
- 归档失败只允许重试归档，成片不得回到 READY 或任何可上传片池。

### 4.3 执行状态机（`publish_runner`）

批次 run 状态（节选）：

| run.status | 含义 |
|------------|------|
| `queued` / `running` | 排队 / 串行执行中 |
| `paused_human` | 登录/验证等，等人；完成后自动探测续跑 |
| `outcome_unknown` | 已点发但结果不明 → **禁止盲目重发**，等人确认 |
| `stopping` | 正在安全结束当前批次并释放 Chrome |
| `completed` / `failed` / `cancelled` / `interrupted_system` | 终态；中断的安全条目可进入待补发 |

单条 item.phase 典型路径：

```text
queued → switching_profile → waiting_login
      → uploading → filling_copy → setting_cover → submitting
      → verifying → published
         ↘ paused_human（登录/验证/未点到发表）
         ↘ outcome_unknown（结果不明）
         ↘ deferred（待补发）/ awaiting_confirmation（待确认）
         ↘ failed / skipped / cancelled
```

### 4.4 停人、续跑与 pre-submit fail-forward

> **合同（2026-08-07）**：发布失败分双轨——**提交前 fail-forward**（有界宽限 → 跳号释槽 → 其它号继续）；**点击后 fail-closed**（`pub_clicked` / `outcome_unknown` 禁止盲补）。宽限超时 defer **≠** 自动过验证码。

| 场景 | 行为 |
|------|------|
| 登录 / 验证码 / 滑块 / 封面卡住（软） | 约 **15 秒**软跳过本条（`soft_skipped`），整轮结束后自动重试；满 **3 轮**仍失败 → App 弹窗+声音+human_alert 喊人。**禁止自动填写验证码或绕过平台** |
| **真登录墙**（扫码墙 / `true_login_wall`） | 进入有界 `waiting_login`（默认 **90s**，配置封顶 **120s**）；宽限内可 `resume_run` 续本号。超时：item → `deferred` + `retry_mode=auto`，同 profile 未发 sibling 一并 defer，**关 Chrome**，run 离开永久墙态（部分成功→`completed`/否则继续下项）；**多账号批次只跳过墙号，其它 profile 继续**。登录告警文案区分「宽限内扫码」与「已跳过并排队补发」 |
| 结果不明 | 先查作品列表；仍不明 → `outcome_unknown`，UI「人工确认已发布」；禁止质量补偿式自动重发；**不得** auto-defer 重发 |
| 连续 3 条失败 | 熔断，run=`failed`（「连续3条失败熔断」） |
| 系统睡眠等 | 可 `interrupted_system`（见 [`SYSTEM_EVENT_PAUSE.md`](SYSTEM_EVENT_PAUSE.md)） |
| 提交前卡住 | 可安全跳过；当前条进入手动/自动待补发，批次继续下一条 |
| 提交后结果不明 | 可先放入待确认并继续后续；确认未发布前不得进入补发 |
| 引擎重启 / worker 丢失 | `reconcile_stale_runs` 释放含 `waiting_login` / `paused_human` 的无 worker 僵尸；未提交→待补发（墙→auto），可能已提交→待确认 |
| 窗口过期 | trigger → `missed_human_confirm` + **human_alert**（`publish_window_missed`）；**禁止**按原 occurrence 静默自动再 materialize；人点「补发待补条目」仅走 deferred 安全补发 |
| 调度公平 | **已到点**（`planned_at<=now`）的 trigger 优先于 `deferred_auto`；仅未来计划时间不得制造空闲死区（到点前仍可有界补 deferred） |
| **物料不全自动修/补产** | 先 `force` 重出包（预算 2）；合格 READY 仍不足 → `create_job` 缺口补产；`缺文案` 可 auto heal，登录/未登录配置永不产/永不 auto |
| 自动接力 | run 终态释槽后立即尝试下一波 `deferred`+`retry_mode=auto`（最多 12 条/批，按平台/号聚合）；缺文案等永不 auto |
| **preparing 卡死（根治）** | 每 tick `heal_stuck_triggers`（约 90s 孤儿龄）：occ 已 `published` → trigger `completed`（**禁止**重开成功项）；occ 失败且窗内 → 至多 2 次 `pending` 重试；过窗 → `missed_human_confirm`+告警。发生源：`advance_occurrence` 发布终态必须 `_sync_linked_trigger`；materialize 异常不得滞留 `preparing` |

run 结束可写一次 `config_json.finish_summary`（soft_skip / deferred / 登录墙统计）；**不**为看板增加秒级全表 poll。

### 4.5 与定时的设置隔离（写死）

即时批次**不得**修改：定时计划、窗口、seed、trigger。  
内容与定时共享 ready 待发池（无内容预留）；执行时现挑，已被进行中发布组占用的成片不可再选。

### 4.6 API

| 动作 | 路径 |
|------|------|
| 预演 | `POST /reach/publish/immediate/preview` |
| 启动 | `POST /reach/publish/immediate/start`（`accept_risk`） |
| 状态 | `GET /reach/publish/runs/{run_id}` · active status |
| 确认结果 | confirm outcome（`published` / skip / failed） |
| 取消 | cancel run |
| 跳过当前 | skip current（`retry_mode=manual / auto / none`） |
| 待补发列表 / 手动补发 | `GET /reach/publish/deferred` · `POST .../deferred/retry` |
| 补发工作台 | `GET /reach/publish/makeup` · `POST .../makeup/retry`（人点 deferred；**不**重开过窗 trigger） |

---

## 5. 模式 D · 窗口随机排期（定时自动）

按自动任务的星期 + 日时段窗口，为**每个 occurrence 独立抽 seed 与最终随机秒**；
到点后物料化内容并启动与即时相同的 `publish_runner` 单槽执行。一个任务可以
包含多个账号和逐账号条数，但物理执行仍是全机单槽串行。

### 5.1 创建计划（UI）

| 字段 | 说明 |
|------|------|
| 计划名称 | 展示用 |
| 发布账号 | 单选或多选；每个账号分别填写发布条数 |
| 窗口 start/end | `HH:MM:SS`；不可相同；可跨日（end≤start 则 end+1 天） |
| 单账号发布次数 | 每个账号在该窗口内独立生成几个 occurrence |
| 最小间隔（分钟） | occurrence 之间最小间隔 |
| 星期 | 至少选一天 |
| 自动生产 | 关闭时仅用 READY；开启时 READY 优先、缺口补产 |

### 5.2 内容来源 `content_source`

| 值 | 行为 |
|----|------|
| `manual_ready` | 手选触达队列中 `queued` 项 |
| `eligible_ready_random` | 仅从成品库选择合格内容；不足则立刻补产（与下项缺口路径相同） |
| `generate_then_publish` | 先复用合格 READY；不足的 occurrence 按冻结规则生产、自动审片并出包 |

创建后可「查看 seed 与实际候选时间」：本地日、序号、最终时间、seed、冲突调整原因。

### 5.3 到点执行（概念）

```text
调度 / automation tick
    → 到期 trigger：materialize 时现挑 ready（禁止提前预留）
    → 够用 → 入队 / 建 ReachPublishRun
    → 不够 → 立刻 create_job（preparing），READY 后同一 occurrence 续跑
    → start_run（单槽；忙则记 publish_scheduled 等待）
    → 与模式 C 相同的上传/停人/核验/熔断
```

质量补偿：坏成片永久退出；替代成片过 READY_GATE 后可**越过原窗口**进高优先级单槽，但**不增加发布次数、不重复发布**（`bypass_window_reason=quality_recovery`）。

### 5.4 API（摘要）

- `GET/POST /reach/publish/schedules`  
- `GET .../schedules/{id}/preview`  
- `POST .../schedules/{id}/run-once`（手动触发一次）  
- 人机提醒：`/reach/publish/human-alerts`  

---

## 6. 对照总表：自动 vs 手动

| 维度 | 手动半自动 (A) | 单条自动 (B) | 即时批量 (C) | 窗口定时 (D) |
|------|----------------|--------------|--------------|--------------|
| 谁点发表 | 人 | 程序（可停人） | 程序串行 | 程序串行 |
| 条数 | 1 | 1 | N | 按计划 |
| 多账号 | 人换号 | 单配置 | 勾选后串行换号 | 计划绑一号 |
| 内容选取 | 入队 pack | 指定 queue | 随机/循环/单片 | 手选/随机/生成 |
| 与定时设置隔离 | — | — | 不改 schedule/窗口 | 独立窗口/seed |
| 内容占用 | — | — | 执行时现挑共享池 | 到点现挑；不够补产 |
| accept_risk | 不强制（人点） | 必须 | 必须 | 启动 run 时必须 |
| 停人后续 | 人点「我已发布」 | 状态 need_human | 自动探测续跑 / 确认结果 | 同 C |
| 结果不明 | 人确认 | 人确认 | 查列表 → 人工确认，禁盲重发 | 同 C |
| 单条卡住 | 人处理 | 取消当前条 | 提交前可跳过继续；可自动/手动补发 | 同 C |

---

## 7. 人机协作与通知

「需要你回来处理」面板只承接：登录、验证码、结果不明、熔断、错过窗口等。

- 通道：App 有声 · macOS 原生 · 可选 ntfy（三路去重审计）。  
- 升级：立即 → 5 → 15 → 30 分钟；点「我已知晓 / 开始处理」停止后续升级。  
- 深链形态：`suying://publish?run=...&item=...`。

更细的边界见 `REACH_NON_GOALS`「GAutoLoop 失败补偿与唤回」。

---

## 8. 推荐操作路径（实操）

### 8.1 第一次发某个平台（建议手动）

1. 创建该平台视频 Chrome 配置 → 打开官方页登录。  
2. 导出 publish_pack → 入队 → 打开官方入口。  
3. 本人点发布 → 「我已发布」。  

### 8.2 已登录、当天要连发多条

1. 确认 READY 成片 + 封面模板槽位齐。  
2. 即时批量：选账号 → 总次数 → 内容模式 → 预演 → 开始立即发布。  
3. 盯「需要你回来处理」；验证码在 Chrome 里过完即可自动续。  
4. 若出现「人工确认已发布」，先看作品列表再确认，勿重复点发。  

### 8.3 日常窗口自动发

1. 建窗口计划 + 内容来源。  
2. 预演 seed/时间，确认与其它号/窗口不冲突。  
3. 保持引擎在线、磁盘健康、账号已登录。  
4. 即时临时加发时用模式 C，与定时共享待发池；已被进行中发布组占用的成片不会再被选中。  

---

## 9. 明确不做（再强调）

1. 并发多 Chrome / 矩阵群控 / Cookie 池 / 养号。  
2. 自动过验证码、滑块、登录墙。  
3. 以反检测、指纹伪装为卖点。  
4. 结果不明时盲目重发或借「质量补偿」增加发布次数。  
5. 即时批次改写定时计划、窗口、seed 或 trigger。  
6. 自动回复私信（消息巡检只读 + 跳官方页人工回）。

---

## 10. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-28 | 初版：汇总手动半自动、单条 G5.V、即时批量、窗口定时的规则与流程；对齐 REACH_NON_GOALS / GImmediate |
| 2026-07-28 | G5.BATCH 恢复硬化：实时阶段、持久化跳过/结束、陈旧批次释放、自动/手动补发；结果不明仍禁止盲目重发 |
| 2026-08-03 | 废除定时内容预留：待发池共享；到点现挑；不够立刻补产；禁止 blocked_reservation |
| 2026-08-07 | pre-submit fail-forward：真登录墙 90s 宽限后 soft-defer 释槽；过窗 human_alert；makeup API；过窗仍禁止静默自动 materialize |
| 2026-08-07 | preparing 卡死根治：发布终态 mirror trigger；heal 每 tick；禁止成功后 reopen |
| 2026-08-05 | 发布加速：evidence.timings、封面 20s 硬墙钟、视频号 DOM 优先+vision≤2、登录快路径、Chrome 收尾硬超时、wait_upload 短 poll |
