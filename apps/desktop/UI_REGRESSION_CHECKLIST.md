# 速影桌面端 — UI 回归清单（九页 IA · 高科技沉浸）

每完成一大阶段全量勾选；未通过不得宣称交付。  
对照：`docs/HARD_LOCKS.md`（尤其 L17 LangCombobox、L7/L8/L18/L19）。  
**禁止假数**：数据中心 / 总览 KPI / 消息红点一律以引擎实数据为准。

## A. 功能冻结（九页 + 壳）

### 壳与门禁

- [ ] 9 页签可进：总览 / 生产 / 规则 / 审片 / 发布 / 消息 / 数据 / 运维 / 设置（`TABS` id 不变；hash `#tab=&section=`）
- [ ] 左轨按八大心智组**视觉分段**（开工台 / 做片子 / 过片子 / 发出去 / 回话 / 看数 / 养机器 / 客户与盘），仍九键
- [ ] LicenseGate：校验中中性 splash「正在启动…」，**不闪未授权卡**；term 到期仅联系运维；可导入路径保留
- [ ] 引擎启停三态（启动中/在线/离线）+ 离线条闸停生产/审片提示
- [ ] 健康点：引擎 / watcher / worker / scheduler / 向量化
- [ ] 客户切换（含 busy/dirty 守卫）
- [ ] Flash（ok/err/info/warn）+ 关闭；可带动作跳转
- [ ] 首次向导不因轮询误弹（`wizardDismissedRef`）
- [ ] ⌘K 命令面板 / 下一步胶囊 / 脉冲条 / 密度档 / `?` 快捷键图例
- [ ] AppDialog 替换 confirm/prompt
- [ ] **L17**：LangCombobox Portal→body + fixed；`.lang-combo*` 未改实现

### 01 总览 · 开工台

- [ ] 一屏看清：泳道 + 下一步 + 人告警/同步；无装饰假 KPI
- [ ] Pipeline + `path_health` 横幅闸停
- [ ] 「刚完成」跳转审片/发布；工作区同步条
- [ ] KPI/ops 仅绑 `report` / `opsReport` / `health`（空则「—」）

### 02 生产 · 做片子

- [ ] 分区：`tasks` \| `assets`（任务 / 素材）
- [ ] Dry-run / 创建任务 / 快速创建 / 日历 CRUD（删日确认）
- [ ] 画幅横/竖、主题/类别、目标条数；规则轮换
- [ ] `path_health` 闸停生产；语义进度条；品牌 Logo 即存

### 03 规则 · 做片子

- [ ] 规则实验室：状态头 / 主编辑 / 高级；画幅 `portrait` \| `landscape`
- [ ] LangCombobox（L17）旁白/字幕语言
- [ ] 保存启用 → Job 冻结 revision（L8）；不得放松 READY_GATE

### 04 审片 · 过片子

- [ ] 筛选（all / missing_voice / missing_sub / tts_bad）+ localStorage
- [ ] 影院模式 + J/K/A/R/F；通过默认留下；toast 可「去发布」
- [ ] 批量旁白/TTS；封面灯箱；mediaEpoch 刷新预览；拖拽访达

### 05 发布 · 发出去

- [ ] 域：视频 \| 软文；视频：`pack` \| `reach` \| `desk`；软文：`articles`
- [ ] 话术对齐：准备视频 / 快速发布 / 手动发布台 / 软文
- [ ] 即时批次预览合同确认后入队；封面/Chrome/自动上传风险声明
- [ ] reachBusy 拆分：chrome / cover / queue / autoUpload

### 06 消息 · 回话

- [ ] scope：`video` \| `content` + 通知通道（macOS / ntfy）
- [ ] 扫描/已读/草稿；红点无假填；软文需显式 preview 清红点
- [ ] L7：脱敏摘要、profile 隔离语义服从合同

### 07 数据 · 看数

- [ ] 只读 KPI；无核心数据 → EmptyState「不会展示虚构数字」
- [ ] 语义进度 / 词库摘要 / 事件导出；空则空态

### 08 运维 · 养机器

- [ ] 七区：`ai` \| `services` \| `backup` \| `carrier` \| `logs` \| `health` \| `advanced`
- [ ] 引擎启停 / Ollama / 备份 / 载体同步 / 日志 / 磁盘 / 高级
- [ ] 系统事件暂停偏好（L9）；旧 hash `tab=logs` → ops.logs

### 09 设置 · 客户与盘

- [ ] 侧栏：客户 / 品牌 / 账号 / 偏好 / 保留 / 路径；（notify 过滤；advanced 密码门控）
- [ ] 路径 L18：local-first；禁止默认 `external_required=true`
- [ ] 音色试听 L19：真 wav；**SoundBed 静音不得拦截试听**
- [ ] SoundBed 一键静音（设置偏好，默认开）
- [ ] settings+advanced hash → ops.advanced

## B. 隐性契约

- [ ] `formSyncPaused`：dirty 时跳过表达/路径/调度回写
- [ ] 5s 轻量 health；客户/词池约 30s；总览 `reportOps` 轮询预算
- [ ] `path_health`：Pipeline 阻塞 + 生产 disabled
- [ ] 审片通过默认留下；mediaEpoch 跟预览
- [ ] 切客户 busy/dirty 拦截；表达 dirty 导出确认
- [ ] reachBusy 拆分；自动上传风险声明常驻
- [ ] 配额分平台合计 ≤ 日总额；`mergeReachPlatforms` 不收缩
- [ ] `prefers-reduced-motion`：动效瞬时降级；切页 `.workspace` `scrollTop = 0`
- [ ] 长列表 `content-visibility: auto`

## C. Busy 键粒度

| 键 | 用途 |
|----|------|
| `actionBusy` | 生产/路径/日历/切客户等 |
| `reviewBusyId` | 单条审片 |
| `batchBusy` | 批量旁白/TTS |
| `packBusyId` | 导出物料包 |
| `exprSaveBusy` | 保存表达 |
| `brandBusy` | Logo 即存 |
| `quotaBusy` | 配额保存 |
| `chromeBusy` / `coverBusy` / `queueBusy` / `autoUploadBusy` | 触达分区 |
| `engineBusy` | 引擎启停 |
| `dialog` | AppDialog |

## D. 滚动 / 焦点

- 切页：`.workspace` `scrollTop = 0`
- 影院：J/K 聚焦；备注全页共享
- ⌘K / AppDialog：拦截底层快捷键

## E. 构建与测试

- [ ] `npx tsc --noEmit`（apps/desktop）
- [ ] `npm test`（vitest）全绿
- [ ] 可选：`scripts/smoke_hard_locks.py` 等（有引擎时）
- [ ] 真机/预览：壳 / 总览 / 生产主链 / 消息 / 发布 + SoundBed

## F. 记录

| 部分 | 日期 | 结果 | 备注 |
|------|------|------|------|
| 清单重写对齐九页 | 2026-09-14 | 进行中 | 沉浸 UI 分支 |
| Bright Control Deck 历史 | 2026-07-25 | 通过 | 旧 Tab 命名已废止于本清单 |
