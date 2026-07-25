# 速影桌面端 — 功能冻结 + 隐性契约自检底稿

每完成一部分（Phase 或 Phase C 一页）全量勾选；未通过不得进入下一部分。

## A. 功能冻结（九页 + 壳）

### 壳

- [x] 9 页签可进：总览 / 生产 / 素材 / 审片 / 物料 / 发布 / 触达 / 运维 / 助手（代码：TABS + StageMotion）
- [x] 引擎启停三态（启动中/在线/离线）
- [x] 健康点：引擎 / watcher / worker / scheduler / 向量化
- [x] 客户切换（含 busy/dirty 守卫）
- [x] Flash 横幅（ok/err/info/warn）+ 关闭；可带「去发布」动作按钮
- [x] 首次向导不因 5s 轮询误弹（wizardDismissedRef 保留）
- [x] ⌘K 命令面板 / 下一步胶囊 / 脉冲条 / 密度档 / `?` 快捷键图例

### 总览

- [x] KPI / ops 健康（亮底可读）/ 今日日历 / 快捷跳转
- [x] Pipeline 作战泳道 + path_health 横幅

### 生产

- [x] Dry-run 结构化卡 / 创建任务 / 暂停恢复 / 日历 CRUD（删日确认）

### 素材

- [x] 全量扫描 / 路径配置 / 品牌 Logo（即点即存）/ 词池摘要

### 审片

- [x] 筛选（localStorage 记忆）/ 批量补旁白 / 批量 TTS / 预览 / 访达 / 通过打回重渲
- [x] 影院模式 + J/K/A/R/F；通过后默认留下；toast 可「去发布」
- [x] 全局备注语义提示（应用于下一次操作）

### 物料

- [x] 表达设置 + LangCombobox 区域分组 / 保存 dirty / 导出物料包（脏确认）

### 发布

- [x] PublishDesk 过审优先 / 复制 fallback / 竞态清卡 / mediaEpoch 预览刷新 / 访达 / 去触达

### 触达

- [x] 配额校验 / Chrome / 入队 / 自动上传风险声明 / 封面状态机文案
- [x] chromeBusy / coverBusy / queueBusy / autoUploadBusy 分区；长轮询可取消

### 运维

- [x] 向量化开/关确认 / Cursor Key / 极空间 / 路径设置（AppDialog）

### 助手

- [x] 输入框 / 流式区 / 缺 Key 导运维 / Stop→cancel API / 全高亮色壳

## B. 隐性契约（§9.4）

- [x] formSyncPaused：dirty 时跳过表达/路径/调度回写（**真机已确认不回滚**）
- [x] 5s 轻量 health；客户/词池约 30s 同步（脉冲不打满库）
- [x] path_health：Pipeline 阻塞 + 生产 disabled
- [x] 审片通过默认留下
- [x] media_ok / 访达 / 拖拽 best-effort
- [x] mediaEpoch：重渲/批量/通过/切客户后 +1；发布台预览跟 epoch
- [x] 助手全高；AppDialog 替换 confirm/prompt
- [x] 切客户 busy/dirty 拦截
- [x] 表达 dirty 导出确认
- [x] reachBusy 拆分；自动上传风险声明常驻
- [x] 平台目录 mergeReachPlatforms 不收缩
- [x] 配额分平台合计 ≤ 日总额
- [x] 引擎离线条 + 窗口亮底 `#F4F6F8`
- [x] prefers-reduced-motion；切页滚顶

## C. Busy 键粒度表

| 键 | 用途 |
|----|------|
| `actionBusy` | 生产/路径/日历/切客户等通用操作 |
| `reviewBusyId` | 单条审片通过/打回/重渲 |
| `batchBusy` | 批量补旁白/TTS |
| `packBusyId` | 单条导出物料包 |
| `exprSaveBusy` | 保存表达设置 |
| `brandBusy` | Logo 即存 |
| `quotaBusy` | 触达配额保存 |
| `chromeBusy` | Chrome 配置相关 |
| `coverBusy` | 封面模板相关 |
| `queueBusy` | 入队/打开官方入口 |
| `autoUploadBusy` | 自动上传长轮询 |
| `engineBusy` | 引擎启停 |
| `dialog` | AppDialog 模态 |

## D. 滚动 / 焦点策略

- 切页：`.workspace` `scrollTop = 0`
- 影院模式：J/K 聚焦卡片；备注为全页共享（下一次操作生效）
- ⌘K / AppDialog：打开时拦截底层快捷键
- 长列表：审片卡 / 发布列表 `content-visibility: auto`

## E. 构建

- [x] `npm run build`（apps/desktop）通过
- [x] `tauri build` → `速影.app`（已同步到桌面）

## F. 记录

| 部分 | 日期 | 结果 | 备注 |
|------|------|------|------|
| Phase A–E | 2026-07-25 | 通过 | Bright Control Deck |
| formSync 真机 | 2026-07-25 | **通过** | 用户确认无回滚 |
| loop1–6 | 2026-07-25 | 通过 | 对比度/reachBusy/降载/确认框/语言分组等 |
| 收尾完成 | 2026-07-25 | **通过** | §9 代码项收齐；`tauri build` 成功并更新桌面 `速影.app` |
