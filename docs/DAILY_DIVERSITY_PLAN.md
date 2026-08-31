# 速影 · 日更去同质化（全行业执行分锁）

> **效力：运营/配置分锁。** 2026-08-21 立约。  
> **不是** [`DEV_LOCK.md`](DEV_LOCK.md) 进度表；**禁止**在本文另立 ✅ / PARTIAL 完成态。  
> 冲突时：[`DEV_LOCK.md`](DEV_LOCK.md) / [`HARD_LOCKS.md`](HARD_LOCKS.md) > 本文 > [`VISUAL_DYNAMIC_OPTIMIZATION.md`](VISUAL_DYNAMIC_OPTIMIZATION.md) 阶段 0 清单 > [`POST_CLOSEOUT_OPS_PLAN.md`](POST_CLOSEOUT_OPS_PLAN.md)（臻享舰队执行表）。  
> 权威索引：[`README.md`](README.md)。

---

## 0. 一句话与边界

把日更从「同一条流水线狂出」改成「多槽位轮换 + 配置极致 + 近窗避重 + 可测抽检」；**方法对所有行业客户通用**，样板客户（始峰 / 臻享）不是产品边界。

| 项 | 约定 |
|----|------|
| 适用范围 | 产品层全行业；差异只在 `configs/` 与规则实验室 live |
| 与 VISUAL | 能力边界与包装禁令仍以 VISUAL 为准；**日更差异执行闭环以本文为准** |
| 与 RULE_LAB | `content_facet` / `rule_rotation` / 日更·精品双轨语义见 [`RULE_LAB_OPT.md`](RULE_LAB_OPT.md) |
| 与 POST_CLOSEOUT | 臻享 live 升版与「禁止 seed 覆盖生产规则」仍以 POST_CLOSEOUT 为准 |
| 明确不做 | 新 Gate / 阶段 X 特效 / 放松 READY / 改 L17–L20 / 软文站群同质灌站（[`CONTENT_NON_GOALS.md`](CONTENT_NON_GOALS.md)）/ seed 覆盖 live 生产规则 |

---

## 1. 五层双胞胎诊断

同质化按层诊断，禁止只改文案假装治好。

```text
日更像双胞胎
  ├─ L1 片型槽位单一     → 周日历 + rule_rotation + 日更/精品双轨
  ├─ L2 词池与 hook 过薄 → hooks≥20、title_pool、themes + content_facet（生活服务守 L20）
  ├─ L3 选片语义未分流   → piece_type_rules + template_bindings
  ├─ L4 片库景别不足     → 覆盖矩阵 + 补库 + 质量分（禁止拧旁白掩盖）
  └─ L5 封面第一眼同款   → 05-品牌/封面模板/ 多套竖槽
```

| 层 | 可观测信号 | 主杠杆（现网能力） |
|----|------------|-------------------|
| L1 片型 | 一周内几乎同一 `content_category` / 同一节奏模板 | 周槽位日历 + [`RULE_LAB_OPT.md`](RULE_LAB_OPT.md) `rule_rotation` + default/premium |
| L2 文案 | hook/标题近窗撞车；服务向「家族相似」 | 行业包 hooks≥20、`title_pool`、词池 `themes` + 规则 `content_facet`；L20 只轮换金句 |
| L3 选片 | hook/body/cta 镜头可互换 | `piece_type_rules` + `template_bindings`（`fast-ship` / `stable-product` / `default-vertical`） |
| L4 片库 | 再轮换仍同景 | 场景/物体覆盖矩阵；`min_cliplet_quality`；缺则补库 |
| L5 封面 | 信息流缩略图双胞胎 | `05-品牌/封面模板/` 多套竖槽 |

---

## 2. 成功标准（全行业同一把尺）

1. **差异签字**：同客户同日抽 5 条 `ready`，标题 / 开场 hook / 主镜头段 **不全同**（允许品牌语气家族相似）。
2. **吞吐不降**：近 20 条 ready 率 ≥ 该客户改前基线；不因避重引入日/周满额硬拒空转。
3. **零新日课**：不新增必看页、特效日报、每日点选动效。
4. **零分叉**：只改配置与 live 规则；[`ZERO_FORK.md`](ZERO_FORK.md) 仍成立。
5. **门禁不松**：READY / 纸片滚动避重 / 旁白×字幕 / L17–L20 全部保持。

### 2.1 可见成效合同

| 档 | 做完有什么 | 成片上是否可见 |
|----|------------|----------------|
| **A · 文档 + 行业包种子** | 检查表、基线 hooks、全行业方法 | **几乎看不见**现网日变更样；只改善新客户/新 seed 起点 |
| **B · 点名客户 live 执行 D0–D3** | 词池/facet/轮换池/封面/（必要时）补库后 **新产 ready** | **能看见**：同日 5 条不全同；封面可 A/B |

**B 档时效：** 当天～2 天可做改前/改后 5 条对照；约 1 周后 facet 轮换更明显；每周抽检，连续 2 次双胞胎 → 回 D0（先查 L4/L2）。

**你会看见（B 达标）：** 标题/hook 少连撞；主镜少「同走廊剪三次」；封面可换皮；建材装车 vs 产品节奏可分；生活服务主题面轮换。

**你不会看见（写死）：** 生活服务旁白每日换风格（L20）；空库靠特效假装多样；精品包装关着却「更炫」；旧已发帖自动变新。

**硬口径：** 未完成「改前 5 条 vs 改后 5 条」对照 = **不得对外说同质化已解决**。

### 2.2 反模式清单

| 禁止 | 原因 |
|------|------|
| 放开生活服务 Ollama / 关 base_lock「换花样」 | **2026-08-29 已授权 L20 v2**；须配合 `copy_diversity_gate` 与模式卡，禁止语料原文复制 |
| 每天手选特效 / 新建特效中心 | 制造日课；与 VISUAL 零负担合同相反 |
| 日/周纸片满额硬拒 | 空转；与 [`PAPER_SLIP_LOCK.md`](PAPER_SLIP_LOCK.md) 相反 |
| Ken Burns / BPM / karaoke / 数字人等阶段 X | VISUAL §8 |
| seed / `config_truth_import` 冲 live 启用规则 | POST_CLOSEOUT §1.1；RULE_LAB 禁令 |
| 引擎内 `if customer.name == …` | 零分叉 / 开发标准铁律 |

---

## 3. 现网能力对照

| 能力 | 落点 | 本分锁要求 |
|------|------|------------|
| 行业包 hooks / themes / bindings | `configs/samples/industry/*/pack.json` | 正式行业包过附录 A 基线 |
| 客户词池与合规 | `configs/customers/<名>/keyword-pack.json` 等 | 主题面齐全；禁废套话 |
| 规则轮换 | `profile_json.rule_rotation_enabled`（缺省 True） | 日更池 ≥2 条 `approved` 且 `rotation_enabled` |
| 内容面钉死 | 规则 `content_facet` ↔ 词池 `themes` | 每周至少 3 个不同 facet 出现 |
| 日更 vs 精品 | `content_category` default / premium | 包装默认关；精品 opt-in |
| 跟镜精品 | [`SCENE_TOUR_LOCK.md`](SCENE_TOUR_LOCK.md) | 周槽位之一；不替代日更主路径 |
| 纸片近窗避重 | [`PAPER_SLIP_LOCK.md`](PAPER_SLIP_LOCK.md) | 渐进放宽；禁止日周 cap |
| 安全包装 / LUT | GVisualPack / GVP2 | **仅精品**；日更缺省 off |

---

## 4. 行业周槽位骨架

| 行业包 | 代表形态 | 周槽位（示例） | 硬约束 |
|--------|----------|----------------|--------|
| `building-supply` | 仓配 / 门店 / 产品 | 配送装车 · 仓内配货 · 产品特写 · 门店综合 ·（可选）跟镜 | 商品与仓配混镜由 `piece_type` 排除 |
| `life-service` | 门店护理等 | 门店形象 · 护肤护理 · 身体与形体 · 服务沟通 · 品牌故事 ·（可选）跟镜 | **L20**；禁止建材仓配话术串入 |
| `_blank` | 新行业起步 | 先建 `content_themes` 再开 facet；**hooks 少于 20 不得宣称阶段 0 完成** | 禁止用样板客户名当默认 |

配置差异、方法同一。≥2 客户共用的主题/钩子再抽进行业包；单客户差异只进客户目录（[`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md)）。

---

## 5. Phase D0–D5

### D0 · 诊断（每客户首次 / 每季）

- 抽最近 ready 5–10 条，填附录 B「五层双胞胎表」。
- 输出主死层（L1–L5）与是否片库瓶颈。
- live 规则改动须点名授权；臻享禁止 seed 覆盖。

### D1 · 片库覆盖

- 按行业 `theme_rules` / `objects` 做覆盖矩阵。
- **下限（写死）：** 每个正式 `content_themes` 键（不含仅作回退的 `default`）至少 **8** 条可用 cliplet，且视觉质量分满足该客户日更规则的 `min_cliplet_quality`（缺省按现网）。
- 缺景别 → 补拍/补入库 + 质量回填；禁止拧旁白或开特效掩盖空库。

### D2 · 配置极致（阶段 0 增强）

行业包 + 点名 live 客户核对附录 A。要点：

1. hooks ≥20；title_pool / 主题标题够量；废套话清除  
2. `template_bindings` + `piece_type_rules` 完整  
3. 词池 themes 与规则 `content_facet` 可钉  
4. 日更包装 off；精品独立 category（克隆草稿不自动 activate）  
5. 封面竖槽 ≥3 套  
6. `rule_rotation` 池非空  

### D3 · 周内容操作系统

- 固定周槽位表：每天目标 facet / 片型意图（不是每天选手动特效）。
- 建任务走现网轮换：Job 抽一套规则并冻结；同 Job `target_count` 共用（GRL.7）。
- 生活服务：差异 = facet + 镜头 + 封面；旁白仅在 L20 金句链内轮换。

### D4 · 度量与复发

- 周检：抽 5 条按成功标准签字。  
- 连续 2 次双胞胎 → 强制 D0；优先 L4 / L2。  
- 禁止「再优化 Ollama / 放开 base_lock」当对策。

### D5 · 多客户复制

- 行业包 seed → 客户目录覆写 → 规则实验室调 live → D0–D3。  
- 验收引用 [`ZERO_FORK.md`](ZERO_FORK.md)。

---

## 6. 角色与改动面

| 角色 | 可做 | 不可做 |
|------|------|--------|
| 设计者 / 验收 | 定周槽位、签双胞胎、授权 live 客户与规则 | 要求阶段 X / 松门禁 |
| AI 实现 | 维护本文、抬行业包基线、按授权改客户配置 | 无授权改引擎旁白 / L17–L20；deploy 冲规则 |
| 运维 | 升 App（可 skip-models）；核对规则 revision 未漂 | 把仓库 intro 当 live 真相覆盖 |

**默认改动面：** 文档 + `configs/samples/industry/**` + **点名授权**的客户配置。**默认不开**新引擎/App Gate。

---

## 7. 与既有文档边界

| 文档 | 管什么 | 本文关系 |
|------|--------|----------|
| VISUAL_DYNAMIC | 观感能力分层、包装默认关、阶段 X 禁令 | 能力边界；执行闭环见本文 |
| RULE_LAB_OPT | facet / 轮换 / 日更·精品 IA | 能力语义；周槽位用法见本文 |
| SCENE_TOUR_LOCK | 跟镜精品片型 | 可选周槽；不改 L20 日更旁白 |
| SERVICE_NARRATION_LOCK (L20) | 生活服务黄金底稿 | 本文不得指示松锁 |
| POST_CLOSEOUT_OPS_PLAN | 臻享舰队与升版 | 臻享 B 档执行须遵守其 §1.1 |
| CONTENT_NON_GOALS | 软文边界 | 软文同质灌站不在本文范围 |
| DEV_LOCK §E | 唯一状态真相源 | 本文不写 Gate ✅ |

---

## 8. 变更记录

| 日期 | 事件 |
|------|------|
| 2026-08-21 | 立约：全行业日更去同质化分锁成文；A 档含行业种子抬升；B 档须点名 live 客户与改前/改后对照 |
| 2026-08-21 | **B 档·北京始峰伟业（结构进度）**：D0/D2/D3 与改前改后对照见 `~/Suying/logs/daily-diversity-shifeng-20260821/`；轮换池收敛为规则 **38+90**；改后抽样含产品稳镜 + 配送装车景别；**未**宣称同质化已彻底解决 |
| 2026-08-30 | **臻享丽人停留 A 档**：D0 改前基线见 `~/Suying/logs/daily-diversity-zhenxiang-20260830/`；rush 试产 job#487 仍 `circuit_open`（零产出）；C2 改后栏未填；**不得**宣称同质化已解决。引擎侧 `bedfd99`（模式卡 + 近窗门禁 + L20 v2）已入库，臻享 live 须先打通选片产出再跑 B 档对照 |
| 2026-09-01 | **臻享 B 档·结构进度**：`0ce0faa` 修复 life-service `scene_aliases`（须重启引擎）；改后 5 条见 `~/Suying/logs/daily-diversity-zhenxiang-20260830/C2-after-sample.md`（out#663/665/666/670/672）；标题与配方主段已分化；hook 仍 L20 家族相似；连跑 rush 仍可能熔断；**未**宣称同质化已彻底解决 |

---

## 附录 A · 阶段 0 / 客户交付检查表（可打印）

扩展 [`VISUAL_DYNAMIC_OPTIMIZATION.md`](VISUAL_DYNAMIC_OPTIMIZATION.md) 附录 A，不删除原表。

- [ ] 行业包 hooks ≥20 且 dual_chip 第一行仍可读、无废词  
- [ ] `piece_type_rules` + `template_bindings` 分流完整  
- [ ] `title_pool` / 配方可用且禁套话（如「画面可见 / 实拍记录」）  
- [ ] 词池 `themes` 与拟用 `content_facet` 一一可钉  
- [ ] 日更规则与精品规则分 `content_category`（若需要精品）  
- [ ] 日更包装字段保持缺省 off；精品 opt-in  
- [ ] `rule_rotation`：同画幅 approved 且参加轮换 ≥2  
- [ ] logo / 封面竖槽 ≥3 套（平台规格按现 cover_templates）  
- [ ] D1：每正式 theme ≥8 可用 cliplet（质量分过门）  
- [ ] 抽 5 条 ready：差异 + READY 全过  
- [ ] 未要求阶段 X；未改 L17–L20；未 seed 覆盖 live 规则  

`_blank`：未满 hooks≥20 与 themes 骨架前，不得勾选「阶段 0 完成」。

---

## 附录 B · 五层双胞胎诊断表（D0）

客户：__________ 日期：__________ 抽样 ready 数：__________

| # | 成片/任务 ID | 标题 | hook | 主镜/景别 | 节奏模板 | facet | 封面模板 |
|---|--------------|------|------|-----------|----------|-------|----------|
| 1 | | | | | | | |
| 2 | | | | | | | |
| 3 | | | | | | | |
| 4 | | | | | | | |
| 5 | | | | | | | |

主死层（可多选）：L1 / L2 / L3 / L4 / L5  
片库是否瓶颈：是 / 否  
下一步：D1 / D2 / D3（圈选）

---

## 附录 C · 周检与改前/改后对照（B 档成效证据）

### C1 周检

日期：__________ 客户：__________ 签字：__________

- [ ] 5 条标题不全同  
- [ ] 5 条 hook 不全同  
- [ ] 5 条主镜头段不全同  
- [ ] READY 全过  
- [ ] 本周出现 ≥3 个不同 facet（若已启用 content_facet）  

不过 → 记复发；连续 2 次 → 强制 D0。

### C2 改前 vs 改后（宣称「已解决」前必填）

| | 改前抽样日 | 改后抽样日 |
|--|------------|------------|
| 日期 | | |
| 双胞胎？ | 是 / 否 | 是 / 否 |
| 主死层 | | |
| 已执行项 | （D0–D3 勾选） | |

未填 C2 = 不得对外宣称同质化已解决。

---

## 附录 D · 复发处置

1. 连续 2 次周检双胞胎 → 停「再拧旁白」念头。  
2. 重跑 D0；优先核对 L4 覆盖矩阵与 L2 hooks/title/facet 厚度。  
3. 生活服务：差异 = facet + 镜头 + 封面 + **L20 v2 受控改写**；旁白在近窗 50 内 opener/CTA/相似度门禁；禁止语料整句复制。  
4. 仍不过 → 补库或加封面/facet 规则，不新开特效 Gate。
