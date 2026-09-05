# 速影 · 关账计划（CLOSEOUT PLAN）

> **效力：权威执行计划。2026-08-03 起生效。**
> **冲突裁决：本文件不改变、不覆盖 [`DEV_LOCK.md`](DEV_LOCK.md) / [`HARD_LOCKS.md`](HARD_LOCKS.md) 的裁决顺序；三者不一致时，仍以 `DEV_LOCK` / `HARD_LOCKS` 为准，本文件只负责把「怎么收口」讲清楚。**
> 权威索引：[`README.md`](README.md)。本文件是「关账」这一阶段的唯一执行计划，问题清单/运维文不得另立并行的关账进度表。

---

## 0. 为什么现在关账

速影从 G0 一路开到 GCustomerUX，Gate 数量已足以支撑「成片能发」的产品目标；继续开新 Gate 会让 `docs/DEV_LOCK.md` §E/§F 越滚越长、真相越来越难核对。关账不是停止开发，而是：

1. 把「代码已写但没人眼验证」的 PARTIAL 项排期收口，而不是无限期挂着；
2. 把文档真相收敛到少数权威文件，历史队列物理隔离；
3. 把发布/交付动作固定成唯一默认路径，减少「临时改法」；
4. 给下一阶段（若要开新功能）一个干净的起点。

---

## 1. 目标（本计划要达成的 5 件事）

### 1.1 冻结新 Gate

- 2026-08-03 起，**不再开任何新 Gate**（不新增 `docs/DEV_LOCK.md` §D 表格行）。
- 已授权 Gate 的**遗留 PARTIAL/真机项**可以继续做，但只能收口，不能借关账期夹带新功能。
- 唯一并行例外：**GCustomerUX 真机收口**（见 §D 当前主线），因为它已经开闸，只是没跑完真机验收，不算「新开」。
- 若确有新功能需求，必须走《00-personal-standards》§四「先方案后动手」，由用户明确说「修改 DEV_LOCK，开新 Gate」——关账期间默认不受理。

### 1.2 关 PARTIAL

- `docs/DEV_LOCK.md` §E/§F 中所有 `PARTIAL` 状态项，逐条归入两类：
  - **可离线收口**：靠补测试/补文档/补代码路径即可关闭 → 排入 W1–W2。
  - **必须真机人眼**：需要真实客户机、真实账号登录、真实设备（Secure Enclave/受管 Chrome/真实素材）→ 排入验收剧本（§4），**不得在没有真机证据前标 DONE**。
- 收口标准：状态从 `PARTIAL` 改为 `DONE` 时，必须在同一行写清楚证据来源（job id / 测试文件 / 用户确认时间），不得只写「应该没问题」。

### 1.3 本机≠客户机

- 明确承认：本机（开发机）状态不等于任何一台客户机的真实状态。
- 关账期间禁止把「本机 smoke/tsc/cargo 绿」直接写成「客户机已验证」；凡涉及客户机结论，必须有该客户机的 `install-receipt.json` / 部署回执 / 用户现场确认之一作为证据。
- `scripts/config_truth_diff.py` 是本机↔客户机配置对比的唯一只读工具；关账期间任何「客户机应该和本机一致」的判断都必须先跑一次 diff，不得凭记忆断言。

### 1.4 发布巨石起步

- **关账开笔时的历史基线**（勿再当「当前最新」）：2026-08-02 一体包 `0.6.11` / T2S `release_seq=15`（见 `docs/DEV_LOCK.md` 序 176 `CUX.RELEASE_T2S` 完成快照）。
- **当前正式一体包指针**（真相 = `~/Suying/releases/LAST_PUBLISH.json` + `DEV_LOCK` 最新 `GVP2.SHIP`）：**0.7.20 core**（含 GVisualPack + GVisualPack2）/ T2S **`release_seq=65`**（build `20260811T073353Z`；本机 `/Applications/速影 Studio.app` 已覆盖）。更旧的 0.7.19 / seq=64、0.7.18 / seq=63 为前序正式出海。
- 关账期间的发布只允许两类：
  1. **收口/修复补丁**：修 bug、补真机验收留下的问题、文档对齐；
  2. **本计划要求的工程收口**（如回执增强、config_truth 落地）。
- 一律走既定路径：`scripts/release-to-t2s.sh`（打包默认闭环 = 升 patch + 推 T2S + 本机覆盖）→ 客户机另走 `docs/REMOTE_DEPLOY.md`。**禁止**为求快另开发布脚本或手动 scp 热补当正式发布。推 T2S 前须极空间客户端在线（本机 `vuex.json` localPort API 可通）；连接失败不得把 `release_seq` 静默当成从 0 起算。
- 关账期间不新开发布通道、不新增平台、不新增账号矩阵能力。

### 1.5 交付默认动作

关账期间，任何「App/引擎改动后」的收尾，默认按以下顺序执行（与 `.cursor/rules/app-change-selfcheck.mdc` 一致，此处仅重申为关账纪律）：

1. `cd apps/desktop && npx tsc --noEmit`
2. 引擎相关路由冒烟（至少 `GET /health` + 改动相关 API）
3. `python3 scripts/smoke_test.py`（相关再跑专项 smoke）
4. 删除死代码 / 无效 import / 被替换的旧提示路径
5. 更新 `docs/DEV_LOCK.md` §E/§F 对应状态行
6. 需要真机的，登记进本文件 §4 验收剧本，而不是自行标 DONE

### 1.6 禁止项（关账期叠加，不替代 DEV_LOCK §B/§I 既有禁令）

- 禁止开新 Gate（§1.1 例外除外）。
- 禁止平行 UI 路径：`apps/desktop-v2` 磁盘不存在，**正式路径仅 `apps/desktop`**；GUI.V2 全表标 `WONT`（见 `DEV_LOCK.md` 序 200–207）。
- 禁止把 `docs/archive/**`、已归档队列（`APP_OPT_QUEUE.md` / `CONTINUOUS_QUEUE.md`）当现行需求或并行进度表。
- 禁止把本机验证结果包装成客户机验证结果（§1.3）。
- 禁止用「历史队列」「优化方案」新开一张与 `DEV_LOCK` §E 冲突的状态表。
- 禁止 License / Secure Enclave 相关文案宣称「可对抗本机管理员」；口径只能是「不对抗本机管理员，SE 硬件加固另议」（见 `DEV_LOCK.md` GLicense 行）。
- 禁止为关账进度好看而虚报真机项为 DONE；PARTIAL 保留即诚实，不是失败。

---

## 2. 30 天四阶段（W1–W4）

| 阶段 | 周期 | 主题 | 产出 |
|------|------|------|------|
| **W1** | 第 1 周（2026-08-03 起） | 文档真相清理 | 本文件 + README/DEV_LOCK 索引对齐；过期队列归档；GUI.V2 标 WONT；PARTIAL 项逐条打上「关闭法」批注（本轮已落地，见 §5） |
| **W2** | 第 2 周 | 可离线 PARTIAL 收口 | 工具/冒烟/文档/口径。**2026-08-04 已完成离线基线**（见 `CLOSE.W2.BASELINE`）；余下 PARTIAL 全是人眼，归 W3 |
| **W3** | 第 3 周 | 真机验收剧本执行 | 按 §4 + `CLOSEOUT_ACCEPTANCE.md` 执行（GSP/GSO/G7/GVC/SEM/横屏/GUI.SHIP） |
| **W4** | 第 4 周 | 交付总验收与关账收尾 | 全量 smoke/tsc/cargo；§E 终态；`qr log --type decision` |

四个阶段按周顺序执行，若某阶段因缺客户机/缺硬盘（`NEED_DISK` / `BLOCKED_DISK`）无法完成，允许顺延，但不得跳过直接标完成。

---

## 3. 可离线完成 vs 必须真机人眼

### 3.1 可离线完成（W1–W2 排期，代码/测试/文档即可关闭）

- 文档真相清理：本文件、README、DEV_LOCK、OVERALL_OPTIMIZATION_PLAN 索引对齐（本轮已落地）
- 过期队列归档：`APP_OPT_QUEUE.md` / `CONTINUOUS_QUEUE.md` → `docs/archive/`
- GUI.V2 状态纠偏为 `WONT`（磁盘无 `apps/desktop-v2`，不存在需要真机验证的产物）
- License SE 口径纠偏（文案问题，非功能问题）
- 现有单测/smoke 补漏（不涉及真实客户机、真实账号、真实设备的部分）
- `config_truth_diff.py` 工具本身的可用性验证（脚本能跑、能出 diff 报告；不等于「已核对某台客户机」）

### 3.2 必须真机人眼（不得标 DONE，只能排入验收剧本 §4）

| 关联项 | 位置 | 真机要求 |
|--------|------|----------|
| G7.5 | `DEV_LOCK.md` 序 85 | 八平台逐账号真机登录巡检（抖音/公众号动态 token 等仍待） |
| GSO.6 | `DEV_LOCK.md` 序 145 | 新成片真机人眼验收（语义/物品标注/向量运营） |
| GSP.6 | `DEV_LOCK.md` 序 135 | 真机 `sleep`/`wake` 人眼验收 |
| GVC.4 | `DEV_LOCK.md` 序 153 | 本地克隆旁白听感抽检（依赖本机 F5-TTS 运行时） |
| SEM.P5.H | `DEV_LOCK.md` 序 88 | 真实素材模型分类准确率人工验收（不得用 schema/单测替代） |
| CUX.SYSTEM_HEAL / CUX.DATA_PROTECT / CUX.DUAL_FRAME | `DEV_LOCK.md` 序 171/172/174 | 真机系统事件竞态、LaunchAgent 备份、真实横屏成片人眼 |
| GUI.SHIP | `DEV_LOCK.md` 序 105 | 双账号发布、软文新账号、三路通知真机业务验收 |
| GLicense（Secure Enclave 原生键） | `DEV_LOCK.md` 序 81 | 需要真实 Secure Enclave 硬件加固路径，非纯代码问题 |
| 本机≠客户机 diff | 本计划 §1.3 | 需要实际连接/访问某台客户机才能产出 diff 报告 |
| 发布全链路真机验收 | `REMOTE_DEPLOY.md` | 需要客户机部署 + 真实平台账号点发布 |

---

## 4. 验收剧本（真机项统一走这一套，避免各写各的）

对 §3.2 每一项，验收记录必须包含：

1. **前置条件**：客户机/本机身份、版本号（App + 引擎）、是否已按 `REMOTE_DEPLOY.md` 部署到位
2. **操作步骤**：可复现的操作序列（哪个页面、点了什么、等待多久）
3. **观测结果**：截图或日志摘录 + 关键指标（如 health QPS、job id、发布结果）
4. **人工确认**：用户本人确认通过的原话或时间戳（不得由 Agent 单方面代签）
5. **写回**：`docs/DEV_LOCK.md` 对应行状态从 `PARTIAL` 改 `DONE`，备注写清证据来源；无法通过则保持 `PARTIAL` 或改 `BLOCKED_DISK`/`NEED_DISK`，并写清阻塞原因

---

## 5. 自检清单（本轮 + 后续通用）

- [x] `docs/CLOSEOUT_PLAN.md` 已建
- [x] `docs/README.md` 已加入 CLOSEOUT_PLAN 索引，标注 `APP_OPT_QUEUE.md` / `CONTINUOUS_QUEUE.md` 为历史
- [x] `APP_OPT_QUEUE.md` / `CONTINUOUS_QUEUE.md` 已迁移至 `docs/archive/`，原路径留 stub
- [x] `docs/DEV_LOCK.md` §D 已加「关账冻结」声明
- [x] `docs/DEV_LOCK.md` GUI.V2（序 200–207）状态改 `WONT · 2026-08-03`
- [x] `docs/DEV_LOCK.md` §F 已加 CLOSEOUT 核对表（指向本文件，本轮离线项先占位 TODO/IN_PROGRESS）
- [x] G7.5 / GSO.6 / GSP.6 / GVC.4 / SEM.P5.H / CUX 真机 PARTIAL 行已批注「关闭法见 CLOSEOUT_PLAN 验收剧本」
- [x] License SE 口径已改为「不对抗本机管理员；SE 加固另议」
- [x] `docs/OVERALL_OPTIMIZATION_PLAN.md` 文末已加「Phase 后续 = 关账执行」小节
- [x] **W2 离线基线（2026-08-04）**：smoke + tsc + cargo + closeout_selfcheck 无 FAIL；config_truth 可跑；xlf 只读探针 → `~/Suying/logs/closeout-w2-20260804/` · `CLOSE.W2.BASELINE`
- [x] W2 判定：无可再纯离线销号的 Gate 行；余下 PARTIAL = NEED_HUMAN → **转 W3**
- [x] W3：真机验收剧本 A→F 用户确认通过 · **2026-08-10** · 见 [`CLOSEOUT_ACCEPTANCE.md`](CLOSEOUT_ACCEPTANCE.md) · `DEV_LOCK` / [`CLOSEOUT_HUMAN_BOARD.md`](CLOSEOUT_HUMAN_BOARD.md)
- [x] W4：全量自检 + `qr log --type decision` 关账结论记录 · **DONE · 2026-08-10** · 证据 `~/Suying/logs/closeout-w4-20260810/` · `CLOSE.W4`

---

## 6. 本轮工程交付范围

### 6.1 W1（2026-08-03 · 文档）

CLOSEOUT_PLAN / README 索引 / 队列归档 / DEV_LOCK 冻结与 V2 WONT / OVERALL 收尾指引。

### 6.2 W2（2026-08-04 · 离线基线）

1. 本机：`smoke_test.py` PASS、`npx tsc --noEmit` PASS、`cargo check` PASS
2. `closeout_selfcheck.py` PASS=14 FAIL=0 NEED_HUMAN=7
3. `config_truth_diff.py` 对始峰 seed 可跑
4. xlf-remote：引擎 **0.6.19** · health + path_health · `GET /system/pause-state` → ACTIVE
5. 纠偏验收文：pause 查询须用 `/system/pause-state`
6. 澄清 GUI.SHIP / CUX「代码 DONE / 真机 PARTIAL」口径；**未**把人眼项标 DONE

### 6.3 W4（2026-08-10 · 关账收尾）

1. 本机：`ENGINE_VERSION`/`package.json` **0.7.17** 对齐；`closeout_selfcheck` **PASS=23 FAIL=0 NEED_HUMAN=0**（人眼门按 `DEV_LOCK` DONE 判定）；`smoke_test` / `smoke_hard_locks` / `smoke_system_events` PASS；`tsc` + vitest 11/11；`cargo check` PASS
2. 证据：`~/Suying/logs/closeout-w4-20260810/`
3. 人眼 PARTIAL 已于 W3 销号；**当日**未把 0.7.18 记入本小节
4. 决策笔记：`qr log --type decision` · project `速影`

### 6.4 发版指针修订（2026-08-11）

1. 升 **0.7.18** patch + 打 core 一体包 + 本机 `/Applications/速影 Studio.app` 覆盖 + 推 T2S **`release_seq=63`**
2. 指针：`~/Suying/releases/LAST_PUBLISH.json`；证据 `~/Suying/logs/ship-0.7.18-20260811/RECEIPT.txt`
3. **修正** §1.4：勿再把 0.6.11 / seq=15 写成「当前已发布」；历史开笔基线与现行指针分列
4. 本轮**未** `deploy-remote` 客户机（仅 T2S + 本机）；xlf 升 App 另见后续
5. **关账后运营执行**（舰队升版 · 臻享丽人日更与阶段 0 · **xlf 只动 App 不覆盖生产规则**）：一律跟 [`POST_CLOSEOUT_OPS_PLAN.md`](POST_CLOSEOUT_OPS_PLAN.md)；**不**改写本文件 W1–W4 历史段落，**不**开新 Gate

### 6.4.1 GVP.SHIP（2026-08-11 · 0.7.19 / GVisualPack）

1. 升 **0.7.19** patch + core 一体包 + 本机覆盖 + 推 T2S **`release_seq=64`**（含 `intro_punch` / `item_label_motion` / `end_card`）
2. 指针：`~/Suying/releases/LAST_PUBLISH.json`；证据 `~/Suying/logs/ship-0.7.19-20260811/RECEIPT.txt`

### 6.4.2 GVP2.SHIP（2026-08-11 · 0.7.20 / GVisualPack2）

1. 升 **0.7.20** patch + core 一体包 + 本机覆盖 + 推 T2S **`release_seq=65`**（含 `color_lut` / `plan_lang_reuse` + Latin 字幕字高修复；默认仍 off）
2. 指针：`~/Suying/releases/LAST_PUBLISH.json`；证据 `~/Suying/logs/ship-0.7.20-gvp2-*.md`
3. 首轮 publish 因极空间在 Z2S 拒传；切 T2S 后重推成功（包未重打）
4. 本轮**未** xlf `deploy-remote`；视觉阶段 2 **未**授权
