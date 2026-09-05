# 关账后运营优化执行表（锚定臻享丽人）

> **效力：关账后运营执行权威表。** 2026-08-11 立约。  
> **不是** `DEV_LOCK` 进度表；**禁止**另立 ✅ / PARTIAL 完成态。  
> 冲突时：[`DEV_LOCK.md`](DEV_LOCK.md) / [`HARD_LOCKS.md`](HARD_LOCKS.md) > [`CLOSEOUT_PLAN.md`](CLOSEOUT_PLAN.md) > 本文 > OVERALL / DEEP / [`VISUAL_DYNAMIC_OPTIMIZATION.md`](VISUAL_DYNAMIC_OPTIMIZATION.md)。  
> 旁白：[`SERVICE_NARRATION_LOCK.md`](SERVICE_NARRATION_LOCK.md)（**L20**）。权威索引：[`README.md`](README.md)。

---

## 0. 一句话

关账后正式一体包已对齐 **0.7.21 / T2S release_seq=66**（含原生拖拽成片）；**GVisualPack2（阶段 2）已 SHIP**。**以臻享丽人为唯一主力客户**；**禁止**建材路径、**禁止**用仓库 seed 覆盖已适配生产规则、**禁止**阶段 X 夹带。

| 条件 | 允许 |
|------|------|
| 现在 | **日更稳态** + xlf 已升 **0.7.21**（生产规则未改）；本机/T2S **0.7.21 / seq=66** |
| 需你当面确认 | **D 开闸主题**；阶段 X 具体名 |
| 流水已批 | A→B→C 可连做过；B 含 xlf **0.7.20→0.7.21**（仅 App） |
| 未来新功能 | 当面改 DEV_LOCK 开新 Gate |

---

## 1. 主力客户：臻享丽人（写死）

| 维 | 约定 |
|----|------|
| 品牌 | 臻享丽人 · 养生美容护理 · 生活服务 |
| 禁止 | `building-supply`、仓配装车话术串入、客户名硬编码进引擎 |
| industry_pack seed | `life-service`（见 `configs/customers/臻享丽人/profile.sample.json`；勿用 building-supply） |
| 词池 | `configs/customers/臻享丽人/keyword-pack.json` |
| 规则种子（**仅参考，非 live 真相**） | `configs/customers/臻享丽人/brand/production_rule_store_intro.json` · `brand/README_TEMP_RULE.md` |
| VIDEO_LOCK 参考 | `configs/customers/臻享丽人/brand/VIDEO_LOCK.json` |
| 工作区品牌盘 | `~/Movies/速影工作区/速影客户/臻享丽人/05-品牌/` |
| 合规 | profile banned_terms · `风险词弱化对照.md` · `企业资料-合规弱化.md` |
| 旁白 | **L20** 黄金底稿 + 仅节奏润色；倾听用词 |

### 1.1 生产规则真相（用户 2026-08-11 · 写死）

| 允许 | 禁止 |
|------|------|
| xlf-remote **覆盖安装 App**（如 0.7.18-core，`--skip-models`） | 对臻享 **已激活 / Job 冻结生产规则** 做 seed 整表覆盖 |
| `config_truth_diff` **只读** | `config_truth_import` / 静默 rsync 把 `production_rule_store_intro.json` 或 sample 冲进 live |
| 规则实验室改 **该环境正在启用的规则** | deploy 后顺带降级词池 revision、冲 chrome/profile 业务绑定 |
| 差异对照后 **以现场 live 为准** | 用仓库 intro 覆盖本机/xlf 上已适配规则 |

升版后抽查：App 版本变了，**激活规则 revision / 气质应与升版前一致**（除非你故意在实验室改）。

---

## 2. 全局硬约束

1. 禁止静默新开 Gate / **阶段 X**；阶段 2 仅按已开 **GVisualPack2** 边界（V4/V5）。  
2. 禁止改 L17–L20、READY、纸片滚动、声画对齐、虚焦/方向、发布单槽、反检测卖点。  
3. 本机 ≠ 客户机；客户结论须部署回执 / install-receipt / 你本人确认。  
4. 零新日课（无特效日报、无新 KPI 页）。  
5. 正式交付：`release-to-t2s`（默认极空间 API）+ [`T2S_UPDATE_PUSH.md`](T2S_UPDATE_PUSH.md) + `REMOTE_DEPLOY`；热补不算版本。  
6. **xlf 可跑，规则不覆盖**（§1.1）。

---

## 3. 阶段与 DoD

### P0 · 版本与客户上下文

| # | 动作 | DoD |
|---|------|-----|
| P0.1 | 本机 App = **0.7.20** | `CFBundleShortVersionString` |
| P0.2 | `~/Suying/releases/LAST_PUBLISH.json` = **0.7.20** / **seq=65** | 与 CLOSEOUT §1.4 一致 |
| P0.3 | App 活动客户 = **臻享丽人** | 非始峰默认 |
| P0.4 | 心理口径：0.6.11/15 仅为历史开笔快照 | 不再当「当前」 |

**P0 2026-08-11 本机核对（GVP2.SHIP）：** App **0.7.20** · LAST_PUBLISH **0.7.20/seq=65** · 源码 version 0.7.20 · health `engine_version=0.7.20` · 臻享丽人 · 日更规则已回 id=61。
（同日早先 P0 曾核对 0.7.18/seq=63，已被本轮覆盖。）

### P1 · xlf 升 App（不灌规则）

| # | 动作 | DoD |
|---|------|-----|
| P1.1 | 按 [`REMOTE_DEPLOY.md`](REMOTE_DEPLOY.md) 对 **xlf-remote** 覆盖 **0.7.20-core** | 仅 App |
| P1.2 | 加 `--skip-models` | 不二次推模型 |
| P1.3 | 保留 F5 Kit、chrome-profiles、DB、**production rules** | 清单里无 rules import |
| P1.4 | health / readiness / engine_version = 0.7.20 | 回执或现场 |
| P1.5 | 可选 `config_truth_diff --customer 臻享丽人 --seed configs/customers/臻享丽人` | **只读**；不 import |
| P1.6 | 激活规则 revision 升版前后一致 | 抽查 |

**§8 ABCD 已批则 B 段执行 P1.1–P1.4；否则仍须当面 deploy 授权。**

### P2 · 臻享日更稳态

| # | 动作 | DoD |
|---|------|-----|
| P2.1 | 入口固定 **0.7.20** App（不以仓源码冒充交付） | 习惯入口 |
| P2.2 | 路径 ok → 扫臻享片库 → 出片 → 审 ready → 人在回路发布 | 可循环 |
| P2.3 | 盯气质：轻声带逛；无仓配串味；无疗效硬广；L20 | 人耳人眼 |
| P2.4 | 连续工作日不依赖 dev 引擎 | 自述 |

### P3 · 视觉阶段 0（主战场）

通用清单：[`VISUAL_DYNAMIC_OPTIMIZATION.md`](VISUAL_DYNAMIC_OPTIMIZATION.md) 附录 A。  
**臻享实例：**

| # | 动作 | DoD |
|---|------|-----|
| P3.1 | 审 `keyword-pack.json`：钩子够用、禁路过腔/疗效禁词 | 抽检可读 |
| P3.2 | industry 保持生活服务 / `_blank`；**永不** building-supply | 绑定可查 |
| P3.3 | **在 live 激活规则上**调（规则实验室）；intro seed **只对照** | 无 seed 覆盖 |
| P3.4 | 气质参考 warm/slow、邻里带逛、无正脸偏好（VIDEO_LOCK）；以 live 为准 | 出片可感 |
| P3.5 | logo + 各平台竖封面满槽 | `05-品牌` |
| P3.6 | 同日 ≥5 条 ready **你人眼签字** | 钩子/镜头差异 + 门禁过关 |
| P3.7 | 可选：去「临时」名 + 口径锁定句（配置层） | 非新 Gate |

**P3.6 必须你确认「能发」后才算过，Agent 不得代签。**

**本机只读盘点（2026-08-11 · 不改 live）**

| 项 | 现状 |
|----|------|
| keyword-pack | revision **17** · 主题含门店/护理/服务沟通；compliance 有 hard_deny/blocked；**未**扫到建材装车脏词 |
| live 规则 | id=**60**「服务介绍·尊贵连贯·v16」· pace=slow · tone=warm · 30s 竖版 · prefer 护理/门店类语义 · exclude 仓配装 |
| 封面槽 | 3 套满槽 · 当前选用 **tpl_ea482cc4**「臻享·日更·尊贵从容」· 抖/快/视频号/小红书竖槽齐（2026-08-11 ABCD.D2） |
| logo | **已安装** 透明 `05-品牌/logo.png` · profile `logo_enabled=true` · VIDEO_LOCK `logo.enabled=true`（2026-08-11） |

下一步仍受 §6：**G1–G3/GVP2 均已交付**；**P3.5 封面满槽 + logo 已装**；**ABCD 日更一圈（产→验→发）已闭环**。继续主路径 = 日更稳态；新 Gate 须当面点名。

**0.7.18 本机臻享真人测片批次（2026-08-11）**

| 批 | job | produced | 规则 | 成片目录 |
|----|-----|----------|------|----------|
| A | **373** | 3 ready | id=60 服务介绍·尊贵连贯·v16 | `02-成片/ready/2026-08-11/` |
| B | **374** | 2 ready | 同上 | 同上 |

| 序号 | 标题 | serial | 文件 |
|------|------|--------|------|
| #071 | 尊贵从容的到店体验 | `…20260811-000001` | `montage_373_1416601212.mp4` |
| #072 | 先听想法再安排项目 | `…20260811-000002` | `montage_373_1261408458.mp4` |
| #073 | 统一标准安心到店 | `…20260811-000003` | `montage_373_1700036220.mp4` |
| #074 | 到店先沟通再动手 | `…20260811-000004` | `montage_374_1052197973.mp4` |
| #075 | 进店好心情拉满 | `…20260811-000005` | `montage_374_589579669.mp4` |

**G2（用户 2026-08-11 当面）：**「这 5 条人眼通过。序号（#071–#075）」→ **P3.6 本批关闭**。  
**D2（2026-08-11）：** 平台竖封面 3 套已满槽并选用；**logo 仍空**（`enabled=false`，待正式 `logo.png`）。

### P4 · 其他客户

- 始峰等 **不**作为本计划默认优化对象。  
- xlf 上升 App **不得**误把他客户规则灌入臻享域。

### P5 · 明确不做

| 项 | 处理 |
|----|------|
| seed/intro 覆盖 live 生产规则 | 禁止 |
| 建材 pack / 串话 | 禁止 |
| 疗效保证、医美手术腔 | 禁词 |
| GVisualPack 代码 | 仅当面开闸 |
| 客户名硬编码 | 禁止 |
| 第二进度表 / 本文打完成 ✅ | 禁止 |

---

## 4. 每日最小节奏（零新负担）

1. 开 **0.7.20** App · 客户臻享丽人  
2. 路径/引擎 ok  
3. 扫新货 → 出片 → 审 ready  
4. 发布人在回路  
5. **不**每天开/关特效（日更规则 LUT/阶段2 默认 off；包装 soft/fade/simple 按 live）

---

## 5. 与既有文档分工

| 文档 | 角色 |
|------|------|
| **本文** | 你现在按什么顺序做 · 臻享锚点 · 规则不覆盖 |
| CLOSEOUT | 关账纪律 · 版本指针 §1.4 / §6.4 |
| VISUAL_DYNAMIC | 通用阶段 0 / 包装禁令 |
| SERVICE_NARRATION_LOCK | L20 |
| OVERALL / DEEP | 工程档案；不再开功能 Phase |

---

## 6. 需确认闸（到此 Agent 暂停）

| 闸 | 状态 | 你说什么才继续 |
|----|------|----------------|
| G1 deploy xlf | **已完成** | 含 ABCD.B 升至 **0.7.20**（2026-08-11） |
| **G2 五条 ready** | **已签字** #071–#075 | — |
| **G3 开 Gate 动效** | **GVP + GVP2 均 SHIP** · **0.7.20** / seq=**65** | 见 DEV_LOCK §E GVP.* / GVP2.* |
| **P3.5 logo/封面** | **封面满槽 + logo 已装** | 透明 logo.png · 角标 on · 见 abcd-20260811/logo-install.txt |

---

## 7. 变更

| 日期 | 事件 |
|------|------|
| 2026-08-11 | 立约：臻享主力；可 xlf 升 App；live 生产规则禁 seed 覆盖；文档执行表落盘 |
| 2026-08-11 | P0 本机版本核对 0.7.18/seq=63 通过；`/production-rules/active` 活动客户=臻享丽人 · live 规则 id=60「服务介绍·尊贵连贯·v16」（**非** seed intro 整表；禁止 import 冲掉） |
| 2026-08-11 | 文档落地：POST_CLOSEOUT_OPS_PLAN + README 序 10 + CLOSEOUT §6.4.5；**暂停于 §6 闸 G1/G2** |
| 2026-08-11 | **G1 完成**：xlf-remote `deploy-remote` 0.7.18-core `--skip-models`；exit 20=`SMOKE_NOT_REQUESTED`（未 smoke）；engine/App **0.7.18**；live 规则仍 **id=16「产品介绍」** cust=1；词池 **拒绝降级** 仍 revision **18**；未 config_truth_import |
| 2026-08-11 | **本机臻享测片**：job **373**/3 + **374**/2 ready → `ready/2026-08-11`；规则 id=60；词池 rev17 与 seed 同 SHA；logo/封面槽仍空 |
| 2026-08-11 | **G2 人眼签字**：用户确认 #071–#075 通过（job 373/374 · 序号见正文）；**P3.6 本批关闭**；非 G3 |
| 2026-08-11 | **G3 开闸**：用户要求暂跳过 P3.5 logo/封面，开始 GVisualPack；`DEV_LOCK` 写入 Gate + §E GVP.*；字段默认 none |
| 2026-08-11 | **GVP.H 人眼**：用户确认生产 3 条全部过关 = #076–#078（job 375/376/377 · soft/fade/simple）· 见 DEV_LOCK §E |
| 2026-08-11 | **GVP.SHIP**：0.7.19 core 包装推 T2S **seq=64** · 本机覆盖 · health ok · **未** xlf 再部署 · RECEIPT `~/Suying/logs/ship-0.7.19-20260811/` |
| 2026-08-11 | **GVisualPack2 开闸**：用户「视觉阶段 2（LUT / 多语言 Plan…）」· 主线 V4/V5 · 见 DEV_LOCK §E GVP2.* |
| 2026-08-11 | **GVP2.SHIP**：0.7.20 core 推 T2S **seq=65** · 本机覆盖 · health ok · schema 含 color_lut/plan_lang_reuse · 日更回 id=61 · **未** xlf · 回执 `~/Suying/logs/ship-0.7.20-gvp2-*.md` |
| 2026-08-11 | **ABCD 流水开立**：用户「创建计划，按 ABCD 一步步完成」→ 见 **§8** · 执行回执 `~/Suying/logs/abcd-20260811/` |
| 2026-08-11 | **ABCD 跑通**：A 机检绿 · B xlf **0.7.20**（exit20=SMOKE_NOT）· C 阶段0只读盘点 · D 候选表停闸 |
| 2026-08-11 | **D2 logo 暂收**：用户「暂时先这样」· 紫粉精修版保留 |
| 2026-08-11 | **logo 上片验证**：job **382** · 2 ready · meta.logo_applied=true · #日更角标验收条 · `LOGO_VERIFY_LIST.txt` |
| 2026-08-11 | **人眼 OK → 待发入队**：#085/#086 各 4 平台 `awaiting_human`（去重后 8 单）· 不自动发 · `PUBLISH_QUEUE.txt` |
| 2026-08-11 晚 | **发布成功**：用户「已发送完、验证码顺利」· queue 8 单标 published · 日更续产 **job 383**×3 |
| 2026-08-11 晚 | **第二波启动**：#087–#089 12 单 awaiting · 先开 **q#217 快手**（成片「到店就像串门子」）· run `WAVE2_PUBLISH_RUN.txt` |
| 2026-08-11 | **D2 logo**：用户上传品牌标（白底+AI水印）→ 透明 `05-品牌/logo.png` · profile/VIDEO_LOCK 启用 · seed 同步 · 回执 `logo-install.txt` |
| 2026-08-11 晚 | **0.7.21 SHIP**：原生拖拽成片（`start_file_drag`）· T2S **seq=66** · 本机覆盖 · 用户测拖拽+上传通过 |
| 2026-08-11 晚 | **第二波发布记账**：#087–#089 ×4 平台 · 12 单 published · `WAVE2_PUBLISH_DONE.txt` · 续产 **job 384**×3（385 重复已 pause） |
| 2026-08-12 早 | **第三波待发**：job **384** completed 3 · #090–#092 · logo_applied · 12 单 awaiting_human · `WAVE3_PUBLISH_RUN.txt` |
| 2026-08-12 早 | **第三波已发记账**：用户确认 · 12 单 published · `WAVE3_PUBLISH_DONE.txt` |
| 2026-08-12 早 | **xlf→0.7.21**：用户「对齐 0.7.21、生产规则不动」· skip-models · 规则 id=16 rev15 未变 · 词池拒降 rev18 · PARTIAL SMOKE_NOT · `B-summary-0.7.21.txt` |
| 2026-08-12 早 | **有余力全做+D1+30s循环**：job386 再产 · truth_diff 双客户 · xlf 轻烟测 · P3.7 口径锁定文档 · T2S seq66 · **D1 xlf 切换臻享运营**（始峰规则未动）· `SURPLUS_D1_BOARD.txt` |
| 2026-08-12 | **撤销 xlf 臻享空壳**：用户「对方完全不需要」· DB 删 id=4 + 工作区目录 · 活动客户回 始峰 · `D1-xlf-zhenxiang-removed.txt` |
| 2026-08-12 | **xlf 再删演示域**：演示客户 + 零分叉演示客户 · 始峰 id=1/规则16/rev15 未动 · `xlf-demo-customers-removed.txt` |
| 2026-08-13 | **0.7.29 审片封面三格+居中预览**：本机安装 + T2S seq73 + xlf `--skip-models` · 始峰规则未改 |

---

## 8. ABCD 流水（2026-08-11 开立 · 循序完成）

> **授权句：** 用户要求「创建计划，按照 ABCD 的流程开始一步步完成」。  
> **不是** `DEV_LOCK` Gate 进度表；**禁止**在本文用 Gate 号另标 ✅。冲突以 DEV_LOCK / HARD_LOCKS 为准。  
> **顺序硬约束：A 机检 → B → C → D 闸门**；B 为 xlf **仅升 App**；D **不得**静默写 DEV_LOCK 新 Gate 名。

| 段 | 对应 | 目标 | 主执行 | 停人点 |
|----|------|------|--------|--------|
| **A** | P2 日更稳态（本机 · 臻享） | 0.7.20 正式 App 入口 + 路径/客户/规则就绪；可扫→出→审→人发 | Agent 机检 + 你人在回路发布 | 你确认「可按正式 App 日更」 |
| **B** | P1 xlf 升 App | `xlf-remote` → **0.7.20-core**，`--skip-models`，**不** rules/import | Agent `deploy-remote` | 远端 health=0.7.20；规则 revision 不被 seed 冲掉 |
| **C** | P3 视觉阶段 0 | 附录 A / P3.1–P3.4 只读+配置核对；**P3.5 封面见 D2** | Agent 盘点；改 live 须你点名 | 无 logo 强推；P3.6 新人眼须你签 |
| **D** | 下一 Gate 候选 | 只列候选 + 取舍；**写入 DEV_LOCK 开闸须你第二句明确主题** | Agent 提案 | 你点名主题后才改 DEV_LOCK |

### 8.1 A · 本机日更稳态

| # | 动作 | DoD |
|---|------|-----|
| A.1 | 入口 = `/Applications` **0.7.20**（非仓库 dev 引擎冒充） | CFBundle + health `engine_version` |
| A.2 | LAST_PUBLISH **0.7.20 / seq=65** | 与 CLOSEOUT 指针一致 |
| A.3 | 活动客户 = **臻享丽人**；激活规则日更向（现 **id=61**，LUT 非 light） | API |
| A.4 | workspace / data_root / path 健康 | health.workspace ok |
| A.5 | 日更循环可执行：扫库→出片→审 ready→人在回路发布 | 可跑；**发布不代签** |
| A.6 | 气质：轻声带逛 · L20 · 无仓配串味 | 你人耳人眼抽听 |

### 8.2 B · xlf 升 App（已批准进流水）

| # | 动作 | DoD |
|---|------|-----|
| B.1 | 包：`~/Suying/releases/速影-0.7.20-product-macos-arm64-core` | 已有（GVP2.SHIP） |
| B.2 | `deploy-remote --host xlf-remote --skip-models`；客户名=远端 live（当前 **北京始峰伟业** · 机器客户域，**非**把臻享规则灌过去） | exit 可 PARTIAL smoke 未请求 |
| B.3 | 许可：已签发 `xlf-remote-customer-1b0ae07cceee-seq2` | 不新签除非失败 |
| B.4 | 健康：远端 `engine_version=0.7.20` + App 版本 | SSH/health |
| B.5 | 规则/词池：**拒绝降级属预期**；**禁止** config_truth_import | 日志可见 |
| B.6 | 回执：`~/Suying/logs/abcd-20260811/B-*.log` | 落盘 |

### 8.3 C · 视觉阶段 0（臻享配置盘点）

| # | 动作 | DoD |
|---|------|-----|
| C.1 | keyword-pack 抽检（禁建材脏词、钩子可用） | 只读 |
| C.2 | industry / pack = 生活服务 / `_blank` | 可查 |
| C.3 | live 规则 id=61：slow/warm/prefer 护理门店 · exclude 仓配 | 与 seed **对照不覆盖** |
| C.4 | VISUAL 附录 A：hooks/piece/title 项勾选结论表 | 文档/日志 |
| C.5 | **P3.5**：封面满槽已做（D2）；logo 仍待资产 | 不编造 logo |
| C.6 | 可选：再出一批 ready 供你人眼（非强制开新 G2） | 你若要再签再说 |

### 8.4 D · 下一 Gate 提案（停闸）

Agent **只提案、不开闸**。候选须你当面选 1：

| 候选 | 说明 | 风险 |
|------|------|------|
| D1 | xlf→臻享客户域切换 / 第二客户运营（非代码） | 配置与身份隔离 |
| D2 | logo + 平台竖封面满槽（解冻原 P3.5） | 要品牌资产 |
| D3 | 阶段 X 某一项（须 VISUAL §8 明确主题） | 高 · 须 DEV_LOCK |
| D4 | 其它你点名主题 | 须改 DEV_LOCK |

**D 完成定义：** 候选表已送达 + 你选定一项并口述开闸句后，才允许写 DEV_LOCK。

### 8.5 执行状态（流水账 · 非 Gate 表）

| 段 | 状态 | 证据 |
|----|------|------|
| A | **机检通过**（人发/听感待你） | `~/Suying/logs/abcd-20260811/A-local-dayops.txt` |
| B | **完成** · xlf **0.7.20** · exit20 SMOKE_NOT | `B-deploy-xlf-0.7.20-*.log` · `B-summary.txt` |
| C | **盘点完成**（P3.5 仍跳过） | `C-stage0-inventory.txt` |
| D | **闭环续跑** · 第二波 published · 0.7.21 拖拽修 · job384 续产 | WAVE2_PUBLISH_DONE · SHIP_0.7.21 · JOB384 |
