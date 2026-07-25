# 速影 · 开发锁（DEV LOCK）

> **效力：写死。2026-07-24 起，本文件 > 口头习惯 > 临时灵感。**  
> **违反本文件 = 不允许合并 / 不允许标 ✅。**  
> 配套：[`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md) · [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) · [`V8_QUALITY.md`](V8_QUALITY.md)

---

## A. 你是谁、在造什么（锁死）

| 锁 | 内容 |
|----|------|
| 产品名 | 速影（Suying）；仓名可叫 montage-studio |
| 产品形态 | **通用**本机「剪辑 + 物料 + 辅助触达」工作室 |
| 客户 | 多租户；始峰 = 样板 fixture，不是产品 |
| 三档 | Studio → Pack → Reach（见 PRODUCT_PLAN） |
| 不做 | 绕平台检测（卖点/对抗 SDK）、全自动矩阵养号、未授权数字人假脸 |
| Reach 例外 | **G5.R 半自动** + **G5.V 视觉全自动** 已授权（本人单账号 · 风控自负）→ [`REACH_NON_GOALS.md`](REACH_NON_GOALS.md) |

---

## B. 行为约束（每次开发前默念）

1. **先开哪扇门，再写代码**：未过 Gate 不得进入下一 Phase 主开发。  
2. **一次只做一个 Gate 内的任务**；禁止平行开 Pack/Reach。  
3. **无硬盘日只做 OFFLINE 队列**；需片库/成片复检的标 `NEED_DISK`，明天插盘再做。  
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

**插盘日附加流程（明天）：**

```text
挂载 QR-Volume → 确认工作区 db 为权威库
→ 跑 §F Gate0 全部勾选
→ 复检 G1/G2 / job28 路径
→ 再动 NEED_DISK 项
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

**当前主线：G6 · 日更运营闭环 — 已完成（2026-07-25）。** 下一 Gate 须你明确开闸。

**G5 / G5.R / G5.V 均已授权（后两者风控自负）。禁止：反检测卖点 / 数字人假脸 / 矩阵养号 / 自动过验证码。**

**锁变更（2026-07-24）：** 用户确认人眼「能发」5/5 全过，并明确「修改 DEV_LOCK，允许 G3」。

**锁变更（2026-07-24 · 二）：** G3 全部 DONE 后，用户再次明确「修改 DEV_LOCK」→ **允许 G4**。

**锁变更（2026-07-24 · 三）：** G4 核心 DONE 后，用户再次明确「修改 DEV_LOCK」→ **允许 G5**。

**锁变更（2026-07-24 · 四）：** 「修改 DEV_LOCK / REACH_NON_GOALS」+ 风控自负 → **G5.R**。

**锁变更（2026-07-24 · 五）：** 用户明确「视觉全自动（风控自负）」→ **允许 G5.V**（技能 `vision-reach-publish`）。

**锁变更（2026-07-25）：** 用户明确「开新 Gate」并批准 G6 计划 → **允许 G6 · 日更运营闭环**。一次只做 G6 内任务；禁止平行开数字人/矩阵/换 embedding。

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
| E5.2 | 日配额 + 失败熔断 + 操作日志 | DONE | `limits.py`；`GET /reach/quota`；入队前校验 |
| E5.3 | 打开已登录浏览器配置 / 官方入口（半自动） | DONE | `POST /reach/queue/{id}/open`；粘贴卡；dry_run |
| E5.4 | 消息中枢：未读摘要 + 深链（不做自动回复） | DONE | `GET /reach/inbox`；仅本机队列提醒 |
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
| 42 | 日配额 + 失败熔断 + 操作日志 | G5 | DONE |
| 43 | 半自动打开浏览器配置/官方入口（人点发） | G5 | DONE |
| 44 | 消息中枢最小（未读摘要+深链） | G5 | DONE |
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

### 现在就做 — G5.V 视觉全自动（已授权 · 风控自负）

| 序 | 任务 | 状态 |
|----|------|------|
| V1 | 技能：快照/截图循环 → 点击填表 → 验证停人 → 可点发布 | **DONE** |
| V2 | 物料桥：`safari_reach_assist.py` 导出 JSON 供视觉流程消费 | **DONE** |
| V3 | 文档/App 口径对齐 REACH_NON_GOALS（含 G5.V） | **DONE** |
| V4 | 发布前门禁：文案非空 + 封面槽位齐套，否则禁止点发 | **DONE** |
| V5 | App 封面模板套（多套、按平台槽位、本机缓存、选用） | **DONE** |

### 禁止现在做

数字人假脸、反检测卖点/对抗 SDK、全自动矩阵养号、自动过验证码/滑块、自动回复私信。  
（**允许** G5.R / G5.V 本人单账号路径；**当前主开发 = G6 Ops**。）

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

1. `DEV_LOCK.md`（本文件）— 做什么、何时做  
2. `DEVELOPMENT_STANDARDS.md` — 怎么写代码  
3. `PRODUCT_PLAN.md` — 产品是什么  
4. `V8_QUALITY.md` — 质量冲刺细节  
5. 其它 V*.md — 历史，冲突时以上为准  

---

## I. 变更本锁

只有你本人明确说「修改 DEV_LOCK」才允许改 Gate/优先级。  
Agent 不得擅自把 **未授权** 的下一 Gate 标为当前可做。  
**已授权：** G3；G4；G5；**G5.R**（四）；**G5.V 视觉全自动**（五 · 风控自负）；**G6 日更运营闭环**（2026-07-25）。
