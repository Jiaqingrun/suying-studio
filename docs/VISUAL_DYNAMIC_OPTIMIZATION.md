# 速影 · 视觉/动态优化方案（关账期立约）

> **效力：分锁与方案。** 2026-08-11 立约；**同日开闸 GVisualPack（阶段 1）**。  
> **不是** `DEV_LOCK` 进度表；完成态只写 [`DEV_LOCK.md`](DEV_LOCK.md) §E。  
> 冲突时：[`DEV_LOCK.md`](DEV_LOCK.md) / [`HARD_LOCKS.md`](HARD_LOCKS.md) > 分锁与领域合同 > 本文。权威索引见 [`README.md`](README.md)。  
> **日更差异执行闭环**（五层诊断 / 周槽位 / 可见成效对照）见 [`DAILY_DIVERSITY_PLAN.md`](DAILY_DIVERSITY_PLAN.md)；本文只管能力边界与包装禁令，禁止当并行进度表。

---

## 0. 一句话与开闸条件

**蒙太奇 × 速影**的观感与动态极致，优先走「镜头语义 + 片型节奏 + 滚动避重 + 已验收字标/声画」；包装动效只能 **默认关闭的 opt-in 层**，不得换切镜哲学、不得松 READY、不得增加日常运营工单。

| 条件 | 允许做什么 |
|------|------------|
| **现在** | **阶段 0** 配置；阶段 1 已交付（0.7.19）；**GVisualPack2 已开闸**（V4/V5，默认 off） |
| **引擎/包装代码** | GVisualPack + GVisualPack2 见 `DEV_LOCK` §D/`§E`；阶段 X 仍禁止夹带 |
| 未满足开闸（历史） | 关账期曾禁止；**已于 2026-08-11 开闸** |

与工程线 [`OVERALL_OPTIMIZATION_PLAN.md`](OVERALL_OPTIMIZATION_PLAN.md) 边界：

| 文档 | 管什么 |
|------|--------|
| OVERALL_OPT | 装得上、跑得稳、轮询预算、ResourceGate、config 真相 |
| **本文** | 成片观感、差异化、未来包装层边界、零运营负担 |
| 两者 | **禁止互相夹带实现**；互不为对方进度真相源 |

---

## 1. 设计原则（写死）

1. **默认关、opt-in** — 新包装字段默认 `none` / `false`；日更主路径在缺省规则下与现网行为一致。  
2. **先配置极致，后安全包装** — 阶段 0 无代码即可拉大日更差异；阶段 1 只动「叠在镜头上的装饰层」，不重定义切镜。  
3. **零新增日常工单** — 不新开必看页、必填日报、日/周特效配额；只复用规则实验室、行业包、关键词包、封面模板。  
4. **门禁只加严、不放松** — 触不可及：READY_GATE、QUALITY、ORIENTATION、旁白×字幕（含只烧一次）、成片>旁白（L15）、纸片滚动避重（L6）、L17–L20、证据/物品标几何。  
5. **产能护栏** — 未来包装仅允许精品 content_category 规则版本打开；全机 render 默认并发仍按 Install Profile（通常 1）；动效失败 fail-closed 重做，禁止降 READY 蒙混。  
6. **不写第二真相源** — 完成后项不得在本文标 ✅；开闸后只进 `DEV_LOCK` §E 对应 Gate 行。

---

## 2. 问题与成功标准

| 目标 | 可测标准 |
|------|----------|
| 日更吞吐不降 | 阶段 0 / 未来默认关时：近 20 条 ready 率 ≥ 现基线；渲染 wall-time 与现网同档可接受（未来 ON：p50 增幅 ≤15%，见 §7） |
| 观感差异可签字 | 同日抽 5 条 ready：hook/镜头/标题不「双胞胎」；字标/表情/旁白仍过现行门禁 |
| 运营负担 ≈ 0 | **不增加**每日必做步骤；仅客户要「精品样式」时，在规则实验室改存一版（一次性） |
| 合同不自残 | 无标题贴纸回潮、无二次烧字幕、无日周纸片满额、无 L17–L20 顺带改动 |

---

## 3. 能力分层

```text
阶段 0 配置极致（现在）
    → 阶段 1 安全包装层（未来 GVisualPack）
        → 阶段 2 品牌强化 / 多语言复用（未来可选）
            ✗ 阶段 X 明确不做（永久禁令于本方案）
```

| 阶段 | 内容 | 需要写代码？ | 运营动作 |
|------|------|--------------|----------|
| **0 配置极致** | 填满 hooks / title_pool / piece_type_rules / template_bindings；多规则色板与 tone / voice_pack；封面 A/B；strict 精品批可选；纸片滚动避重 **已存在、禁止改回满额** | **否** | 交付时一次性配置；按客户调包 |
| **1 安全包装** | 开场 ≤0.4s 安全闪框或微缩放；物品标微入场（仍证据门禁）；受控片尾名片（不挡字幕、守 L15） | 是（仅包装层） | 规则实验室 2–3 个 **默认 off** 枚举；精品类别才允许开 |
| **2 可选强化** | 极轻全片 LUT（`off`/`light`）；多语言表达层复用同一 MontagePlan（抬吞吐方向） | 是 | 仍默认 off；无新日常 |
| **X 明确不做（历史）** | Ken Burns **默认化**；BPM 节拍切；标题区浮动贴纸；数字人假脸；为特效放松 READY / 对齐 / 画质；装回 OpenMontage/Remotion | — | — |
| **X→GRuleVisualLab 开闸例外（2026-08-22）** | 受控 `xfade` 枚举（默认 none）；精品卡拉OK/跑马灯；非标题区装饰贴图；多层蒙版 | [`FX_ASSET_WHITELIST.md`](FX_ASSET_WHITELIST.md) · Gate GRuleVisualLab | 字段默认关；精品可开；不松门禁 |

### 3.1 何谓「动态感」（本方案口径）

| 来源 | 现状 | 本方案态度 |
|------|------|------------|
| 槽位节奏（fast-ship / stable-product / default-vertical） | 已落地 | 阶段 0 **用满** |
| 角色语义选片 hook/body/cta | 已落地 | 阶段 0 用满 piece_type_rules |
| 纸片近窗避重 | 已落地 | 禁止再做日周满额 |
| 标题 dual_chip / fade；字幕对齐 VO；行内 Twemoji | 已落地 | 不毁掉既有合同；fade 须保持 loop 渲染语义 |
| smart crop / 双画幅冻结 | 已落地 | 阶段 0 横+竖分任务配置即可 |
| 未来短包装 overlay | 未做 | 仅阶段 1，默认关 |
| 切镜哲学重写（BPM / 长伪运镜） | 未做 | **阶段 X** |

---

## 4. 运营零负担合同

### 4.1 做

- 新客户交付：按附录 A 跑完阶段 0 清单一次。  
- 需要「精品感」：在规则实验室为 **独立 content_category** 保存启用一版样式/音色/语义偏好；日更版不动。  
- 封面差异：维护多套 `05-品牌/封面模板/`，发布时选当前模板（已有能力）。  
- 可选观察（**不**强制日报）：ready 率、失败原因码、渲染耗时走既有运维/任务侧信息，不新设 KPI 会。

### 4.2 不做

| 禁止项 | 原因 |
|--------|------|
| 每天手动开/关包装特效 | 制造日常负担 |
| 每条成片人工点选动效 | 量产路径不可扩展 |
| 为包装新建一级「特效中心」页或日报 | 信息架构膨胀 |
| 为特效引入日/周次数配额熔断 | 与纸片合同相反；制造空转 |
| 要求客户机增配 GPU 专供滤镜 | 低配可运行原则 |
| 顺手改 LangCombobox / 主盘拓扑 / F5 试听 / 生活服务旁白 | HARD L17–L20 |

### 4.3 角色分工（交付时）

| 角色 | 动作 |
|------|------|
| 设计者 / 验收 | 确认阶段 0 抽检 5 条；未来开闸后人眼签 GVisualPack |
| AI/实现 | 阶段 0：只改客户配置（若被委托）；阶段 1+：开闸后按 §6 路径，默认 off |
| 运维 | 不新增日课；升级仍走 REMOTE_DEPLOY / release 一体包 |

---

## 5. 阶段 0 · 现在可执行清单（无代码）

> 目标：不写引擎也能把「日更差异 + 品牌观感」拉到当前技术栈上限。

### 5.1 行业包 `configs/samples/industry/<pack>/pack.json` 或客户等效

1. `hooks` 池足够（建议 ≥20 条有效短句），上 dual_chip 第一行仍可读。  
2. `theme_rules` / `scene_rules` / `objects` 与真实片库对齐。  
3. `template_bindings`：配送/装车 → `fast-ship`；产品/特写 → `stable-product`；门店/综合 → `default-vertical`。  
4. `piece_type_rules`：hook/body/cta 的 `prefer_scenes` / `prefer_objects` 与 `continuity` 写清；商品类排除仓配混镜（与现 stable-product 合同一致）。

### 5.2 关键词包 / 客户词池

1. `title_pool` 够量；禁用「画面可见 / 实拍记录」等已废套话。  
2. 开场 hook 近窗可借纸片 phrase 避重（现系统行为），**禁止**自行加日周次数硬拒。  
3. 生活服务类遵守 [`SERVICE_NARRATION_LOCK.md`](SERVICE_NARRATION_LOCK.md)（L20）：不改黄金底稿合同去「创新旁白」。

### 5.3 规则实验室（已有）

1. **日更版**默认：`title_effect=none`，`subtitle_effect` 按现状；`item_label_enabled` 仅证据策略需要时开。  
2. **精品版**（另一 content_category）：可调色板（标题/字幕颜色描边、安全区内 glyph 距）、`narration_tone`、`tts_*` / `voice_pack`、`pace`、`prefer/exclude` 语义、更高 `min_cliplet_quality`、可选 `strict_semantic_v1`。  
3. 双画幅：需要横版运营时用独立 `orientation=landscape` 规则版本与任务，遵守 [`ORIENTATION_LOCK.md`](ORIENTATION_LOCK.md)。  
4. `bgm_fade_out`：沿用 off/short/standard/long，勿为「更炫」无限拉长。

### 5.4 品牌与封面

1. `05-品牌/logo.*` 存在则可自动角标；缺则跳过（现行为）。  
2. 封面权威：`05-品牌/封面模板/`，每视频平台竖槽齐（抖音/快手 9:16 等以 `cover_templates` 规格为准）；**禁止**回退工作区全局混用 store。  
3. 多模板 = 信息流第一帧 A/B，无需重渲视频。

### 5.5 验收（阶段 0）

- 同日抽 5 条 ready：标题/钩子/主要镜头不全同。  
- READY 既有项全过；无新失败原因、无新人工步骤。  
- 配置 diff 可审计（客户 seed 目录；勿静默覆写 profile 绑定）。

---

## 6. 未来 GVisualPack · 改动面地图（只记录，不实现）

> **本章不得在未开闸时被执行。** 路径供 Gate 设计引用。

| 方面 | 白话 | 未来代码落点 | 冻结勿碰 |
|------|------|--------------|----------|
| 规则 | 多 2–3 个默认 off 字段 | [`engine/template/rule_schema.py`](../engine/template/rule_schema.py) 例如 `intro_punch` / `item_label_motion` / `end_card`（名可调整，语义须默认关） | `FORBIDDEN_KEYS` 不可放；不增纸片日 cap |
| 合成 | 第 0 槽短 enable overlay；物品标 PNG 轨轻微动 | [`engine/render/ffmpeg.py`](../engine/render/ffmpeg.py)；复用 title/item 已有 PNG 路径 | 不改 L18 默认路径/拓扑 |
| 门禁 | 包装不挡标题/字幕安全区；时长仍 > 旁白 | [`engine/qc/ready_gate.py`](../engine/qc/ready_gate.py) | H1–H11、H9 只烧一次、E 贴纸合同 |
| App | 规则实验室展示新枚举 | 既有 VideoRules UI | **L17** `LangCombobox` / `.lang-combo*` |
| 声音旁白 | 阶段 1 **零改旁白管线** | — | **L19** clone/试听 · **L20** 服务旁白 |
| 产能 | 仅精品 category 允许非默认 | Job 冻结规则 | Install Profile render 并发默认 1 |

### 6.1 阶段 1 三项（开闸后方可实现）

| ID | 能力 | 约束 |
|----|------|------|
| V1 | 开场 punch | 总时长 ≤0.4s；不得盖 dual_chip 标题安全区；失败则视为该开关未开或 job failed，无「半开」 |
| V2 | 物品标 motion | 仅 `item_label` 证据门禁已通过时；微入场 ≤0.25s 级；禁止侵入标题/字幕安全区；禁止自由横移（现几何锁） |
| V3 | 片尾 end card | 落在旁白结束后的 pad 区；必须仍满足成片 > 旁白（L15）；不得叠第二套旁白字幕 |

### 6.2 阶段 2 两项（阶段 1 验收后方可单独立项）

| ID | 能力 | 约束 |
|----|------|------|
| V4 | 轻 LUT `off`/`light` | 不得导致模糊门禁语义失效或系统性过曝；默认 off |
| V5 | 同 Plan 多语言表达复用 | 画面轨复用；标题/字幕/TTS 分叉；可 **抬** 吞吐；仍 mono 语言对齐与只烧一次 |

---

## 7. 未来 GVisualPack 验收草约

> **现在不写入** `DEV_LOCK` §D 新行。开闸时复制本节为 Gate 验收，并进 §E。

### 7.1 默认关回归

- 全量规则缺省 / 日更类别：`smoke_test` + 相关 `smoke_ready_gate` / 字幕 / 纸片 / hard_locks **绿**。  
- 黄金路径或 fixture：观感与现行一致（无未请求的 punch/end card）。  
- 主路径 diff：仅允许规则 schema 缺省字段，无强制新副作用。

### 7.2 精品 ON

- OFFLINE 至少 3 条 fixture：`intro_punch` / item motion / end card 按设计可见。  
- READY_GATE 全过；无挡字、无二次字幕、无时长违规。  
- 同一硬件档：渲染 duration **p50 相对默认关增幅 ≤15%**（样本 ≥10 job 或文档写明的 OFFLINE 基线）。  
- App 规则实验室：字段可保存、Job sidecar 冻结值等于生效值。

### 7.3 交付纪律

- 与 `.cursor/rules/app-change-selfcheck.mdc` 一致：tsc、health、专项 smoke、删死代码。  
- 发布：一体包 + REMOTE_DEPLOY；禁止客户机热补 py 当交付。  
- 文档：本文阶段状态**不**改成进度表；Gate 状态只写 DEV_LOCK §E。

---

## 8. 明确不做（阶段 X · 防需求回灌）

下列项 **不在本方案实施范围内**。若要坚持，必须：**关闭/结束关账逻辑允许开发新功能 + 用户当面开 Gate + 独立产能评估**；不得夹带进 GVisualPack「安全包装」默认范围。

| 项 | 原因 |
|----|------|
| Ken Burns / 默认推拉伪运镜 | 重编码、伤吞吐；非现节奏体系 |
| BPM / 节拍硬切 | 与 TTS 定槽声画同构冲突 |
| 逐字 karaoke 字幕 | 破坏句级 VO 对齐合同面过大 |
| 标题区浮动贴纸 / emoji 圆盘 | 已废止（EMOJI E4） |
| 复杂 flash/whip/glitch 转场库 | V8 后置；非日更默认 |
| 数字人口型假脸 | 产品非目标 |
| 绕检测矩阵 / 自动过验证码 | REACH 非目标 |
| 为特效放松 READY / 虚焦 / 对齐 / L15 | 硬锁 |

---

## 9. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 关账期夹带引擎实现 | 本文 §0；REVIEW 拒合；仅阶段 0 |
| 本文被当成第二进度表 | 禁止 ✅；索引已标明「只读方案」 |
| 与 OVERALL / CLOSEOUT 抢主线 | 索引写明非现行执行主线；执行权仍 CLOSEOUT + 已开 Gate 收口 |
| 未来默认开拖垮日更 | 默认 off；精品 category；p50≤15%；失败 fail-closed |
| 需求把 X 项塞进包装 | §8 表 + 要求新 Gate 与产能评估 |
| 顺手改冻结项 | HARD L17–L20 列入禁改；阶段 1 零旁白改 |

**回滚（未来代码开闸后）：**  
规则全客户改回默认 off → 重渲受影响批；字段可保留 schema 但无效副作用；紧急可用上一正式一体包（REMOTE_DEPLOY 路径）。

---

## 10. 与现有能力对照（避免重复造轮）

| 已落地 | 文档/落点 | 阶段 0 动作 |
|--------|-----------|-------------|
| 三节奏模板 | `engine/template/engine.py` | bindings 配满 |
| 纸片滚动 | [`PAPER_SLIP_LOCK.md`](PAPER_SLIP_LOCK.md) | 勿改 |
| 标题/字幕样式 | rule_schema + READY | 精品色板 |
| 表情行内 | [`EMOJI_STICKER_LOCK.md`](EMOJI_STICKER_LOCK.md) | 保持 |
| Logo 角标 | `engine/render/logo.py` | 放 logo 文件 |
| 物品竖排 | 证据 + 几何锁 | 按需开 |
| 封面槽 | `engine/reach/cover_templates.py` | 填满竖模板 |
| BGM 淡出 | `bgm_fade_out` | 选预设 |
| 声画同构 | Phase 3 / voice_subtitle | 不拆链路 |

---

## 11. 读者 10 分钟自检题

1. **现在能做什么？** — 附录阶段 0；无新动效代码。  
2. **关账后且开 Gate 才能做什么？** — V1–V3，再视情况 V4–V5。  
3. **永远不（在本方案）做什么？** — §8。  
4. **为什么不伤日更与运营？** — 默认关、精品 opt-in、无新日课、门禁不松、产能阈值写死。

---

## 12. 变更

| 日期 | 事件 |
|------|------|
| 2026-08-11 | 关账期立约：方案成文；仅阶段 0 可执行；GVisualPack 预置不写入 DEV_LOCK §D |
| 2026-08-11 | **开闸**：用户授权修改 DEV_LOCK · GVisualPack；阶段 1 字段默认 none；P3.5 logo/封面满槽用户要求暂跳过 |
| 2026-08-11 | **阶段 1 人眼**：用户确认精品 ON 3 条 ready 全过（#076–#078 · soft/fade/simple）；进度仅记 DEV_LOCK §E |
| 2026-08-11 | **GVP.SHIP**：正式 core **0.7.19** / T2S **seq=64** 含包装；本机覆盖；阶段 2 仍未授权 |
| 2026-08-11 | **GVisualPack2 开闸**：用户「视觉阶段 2（LUT / 多语言 Plan 等，另开锁）」→ V4/V5；进度仅记 DEV_LOCK §E GVP2.* |

---

## 附录 A · 阶段 0 检查表（可打印）

> 全行业增强表与周检/改前改后对照见 [`DAILY_DIVERSITY_PLAN.md`](DAILY_DIVERSITY_PLAN.md) 附录 A–C。

- [ ] 行业包 hooks ≥20 且无废词  
- [ ] piece_type + template_bindings 分流完整  
- [ ] title_pool / 配方可用且禁套话  
- [ ] 日更规则与精品规则分 category（若需要精品）  
- [ ] logo / 封面竖槽齐  
- [ ] 抽 5 条 ready：差异 + READY 全过  
- [ ] 未要求开发新动效、未改 L17–L20  
