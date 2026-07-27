# 速影 · 语义切片与成片表达规格（优化版）

> **状态：2026-07-26 · P0–P5 完成**（P5：严格语义门禁 v1）
> **焦点：** 切片 + 描述 + 向量（理解层）优先；封面模板每客户固定目录；混剪规则 / 口播 / 多语言为后续消费层。  
> **约束：** 遵守 [`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md)（引擎零业务硬编码、客户边界、同步区≠工作区）。

### 已落地（P0）

- Cliplet：`scene` / `objects_json` / `actions_json` + 迁移  
- `compose_embed_text` → 向量；检索支持 `theme`/`scene` 硬过滤  
- Vision / 启发式描述提示强化；行业包 `scene_rules` 等  
- 封面权威路径：`05-品牌/封面模板/`（API + 发布门闸）；旧工作区店可一次性迁移  
- `ensure_customer_dirs` 创建 04/05/封面模板 + 目录说明

---

## 0. 一句话

入库时把实拍切成**可检索语义零件**（描述 + 场景/物品标签 + 向量）；组片按片型规则选零件；口播/字幕/语言在表达层按客户配置替换——**画面编排尽量语言无关**。

---

## 1. 三层架构（优化后）

```text
理解层  ingest → cliplet(切段) → 抽帧描述 → 标签(theme/scene/objects) → embedding
编排层  片型模板 + 标签硬过滤 ∩ 向量软排 → MontagePlan（可审计）
表达层  口播脚本 → TTS(voice_lang) → 字幕(subtitle_lang) → 烧录选项 → publish_pack
```

| 层 | 输入 | 输出 | 不做什么 |
|----|------|------|----------|
| 理解 | 源视频 | cliplet 行 + 帧缓存 | 不写客户文案硬编码 |
| 编排 | 任务主题/片型 | 镜头序列 | 不在此层做 TTS |
| 表达 | Plan + 客户语言偏好 | 成片音轨/字幕/物料包 | 不重跑全库视觉 |

---

## 2. 切片 + 描述 + 向量（主优化）

### 2.0 严格语义门禁 v1（P5）

新切片必须按以下顺序进入检索：

```text
画质门禁通过
  → 每段抽 3 个带时间戳的帧
  → Vision 返回 suying.cliplet.semantic.v1
  → schema / 可见事实 / 分类 / 置信度 / 帧间一致性门禁
  → 通过：status=usable → 组合结构化向量文本 → embedding
  → 失败：换抽帧位置重试（最多 3 次）
  → 仍失败：status=rejected_semantic + embedding=NULL（隔离、可审计）
```

有限重试是安全约束，不接受“直至成功”的无限循环：模型离线、持续输出坏
schema 或素材本身不可辨认时，无限重试只会重复消耗算力并制造队列饥饿。三次
尝试分别使用不同抽帧位置；每次抽帧路径/时间、失败原因、最终处置都写入
`semantic_gate_json`，`semantic_attempts` 记录次数。

真实样本收紧（2026-07-27）：

- 每次请求把实际成功抽取的 frame ID 列为唯一白名单；模型不得漏帧、改名或引用
  未提供 ID。后处理只会删除无效引用、去重及校准真实时间戳，绝不把引用映射到
  另一帧或补造事实。
- 单个目标时间点解码失败时，仅允许在前后 0.08 秒内做两次有界抽帧回退；不增加
  Vision 三次语义重试预算。
- `people.present=true` 与 `none_visible` 互斥；人物可见但身份无直接证据时使用中性
  `person_visible_unclassified`，不得被迫猜成员工/客户。冲突继续 fail-closed，
  不由后处理猜测哪一项正确。
- `visible_facts` 只写确定可见内容；无法辨认的信息进入 `unknowns`。身份、情绪、
  精神状态及未直接展示的用途禁止推测。
- 产品外观属性只允许颜色、形状、材质表观、包装和可见文字；不可见时由模型明确
  返回 `unknown`。后处理不会自动填充缺失属性，字段缺失仍由门禁拒绝。

稳定 schema 覆盖：

- `scenes`：配送、使用、装车、码放、库存齐全、公司形象，以及仓库、门店、生产、安装现场、办公、运输、产品特写、人物活动等通用枚举；允许多标签。
- `products`：可见名称、粗类别、画面直接展示的用途、关键外观属性、证据帧与置信度；看不清写未知，不凭行业猜产品。
- `interactions`：交付、安装、维修、使用、装卸、分拣、码放、演示、检查、咨询、协作或明确无人互动；允许多标签。
- `people`：是否有人、人数、可见精神/姿态描述、衣着，以及员工个人/集体、客户互动、工作肖像、团队形象等宣传分类。
- `frames`：每帧 ID、真实时间戳、至少两条可见事实和 `unknowns`；`consistency` 明示跨帧矛盾。

门禁要求：必须为 Vision 后端；2–4 帧且事实完整；描述 20–400 字、非空泛、
无推测措辞；场景与互动分类齐全；每个标签/产品有证据帧且置信度 ≥0.65；
总置信度 ≥0.72；人物有/无与分类一致；产品字段完整；帧间一致；
schema 版本完全匹配。任何一项失败均 fail-closed。

视觉调用约束（2026-07-27 · 分档级联）：

- 向量一律 `nomic-embed-text`。
- **16GB / standard 及以下**：默认视觉 `qwen3.5:9b`（约 6.6GB），**禁止**默认 `qwen3.5:27b-q4_K_M`；超时约 180s。
- **32–64GB / pro（含至 95GB）**：默认 9B 快筛，门禁失败 / `vision_timeout` / `schema_not_object` 可升级 27B；超时约 240s（升级 300s）。
- **≥96GB / max**：推荐同样「9B 快筛 → 27B 升级」以提速；也可主跑 27B（须显式配置）；超时约 300s。
- 级联消耗同一 `MAX_SEMANTIC_ATTEMPTS=3` 预算，不额外加次；门禁阈值 0.65 / 0.72 不放宽。
- Ollama `/api/chat` 的 `format` 必须传完整 JSON Schema（`semantic_response_json_schema()`），
  `options.temperature=0`；`gemma4` 仍为 `compat` 可选。

兼容策略：四个新增 SQLite 字段均为可空/带默认值的增量迁移，旧行不重写、
旧 embedding 不自动销毁；但旧行若要重新生成 embedding，必须先重新描述并
通过 v1 门禁。这样既不破坏生产库，也禁止新向量绕过门禁。

### 2.1 Cliplet 字段（目标）

| 字段 | 类型 | 说明 |
|------|------|------|
| `description` | text | 中文画面句（Vision 优先，启发式兜底） |
| `theme` | str | 业务主题：配送/仓配/门店/产品…（行业包） |
| `theme_score` | float | 主题置信 |
| `scene` | str | 场景码：仓内货架/装车卸货/门店门头/产品特写/工地现场/人物作业/default |
| `objects_json` | list[str] | 物品码列表（对齐词池品类，可多值） |
| `actions_json` | list[str] | 动作：装车/分拣/演示/堆放… |
| `score` | float | 画质分（暗糊过滤） |
| `embedding_json` | vector | 对「嵌入文本」而非仅 description |

### 2.2 入库流水线（增量）

```text
Asset ready
  → 场景切分 (2–8s)
  → 关键帧 1–3 张（段中偏前）
  → Vision 多帧结构化事实描述（禁止文件名/行业暗示造成臆测）
  → 多标签 scene/products/interactions/people + legacy 查询列映射
  → compose_embed_text → embed → 写入 embedding_json
```

**算力策略：** 关键帧为主，禁止默认全库逐帧细分类；回填任务可限速后台跑。

### 2.3 嵌入文本（优化点）

```text
场景分类={scenes}；产品={name/category/use/attributes}；互动={interactions}；
人物宣传={labels/appearance/clothing}；逐帧可见事实={facts}；
theme={theme}；scene={scene}；物品={objects}；动作={actions}；{description}
```

检索时：**标签硬过滤 ∩ 向量排序**。只靠向量会「像但不该用」。

### 2.4 行业包扩展（配置，不进引擎硬编码）

在 `configs/samples/industry/<id>/pack.json` 增加（可选）：

- `scene_rules` / `object_rules` / `action_rules`（同 theme_rules 结构）
- 缺省时从 `theme_rules` 关键词回退推导

### 2.5 验收（理解层）

- 新入库 cliplet：`description` 非空；`theme` 有值；`embedding_json` 非空  
- 语义搜「装车 货车」：Top10 中 ≥7 条含装车/配送相关 scene 或 theme  
- 组片 sidecar 能读出每槽命中的 theme/scene（后续编排层）

---

## 3. 封面模板 · 每客户固定目录

### 3.1 路径约定（同步区，可极空间同步）

```text
速影客户/<客户名>/05-品牌/
  logo.png | avatar.png | intro_card.jpg | …
  封面模板/                    ← 封面套装根（固定）
    index.json                 ← 套装索引（selected_id、槽位图路径）
    tpl_<id>/
      douyin_vertical.jpg
      douyin_horizontal.jpg
      xhs_feed.jpg
      …
```

| 侧 | 路径 |
|----|------|
| **权威存放（同步）** | `…/速影客户/<名>/05-品牌/封面模板/` |
| **配置种子（可选）** | `configs/customers/<名>/brand/封面模板/` |
| **废弃倾向** | 仅工作区 `速影工作区/db/cover_templates/`（全局混用，易串客户） |

### 3.2 产品行为

- 新建客户 / `ensure_customer_dirs`：自动创建 `05-品牌/封面模板/` + 占位说明  
- App/API 读写封面套：默认落在**当前激活客户**的上述目录  
- 若旧数据在工作区 `db/cover_templates/`，首次读取时可迁移到客户目录（不删源直至校验）  
- 成片抽帧封面仍在 `02-成片/..._covers/`；**模板套装**与**成片候选帧**分离

### 3.3 平台槽位

槽位规格仍以 `engine/reach/cover_templates.py` 的 `PLATFORM_COVER_SPECS` 为唯一真相（抖音竖/横、小红书 3:4 等）。

---

## 4. 编排层（规则混剪 · 摘要）

| 片型 | 结构意图 | 硬约束示例 |
|------|----------|------------|
| 配送承诺 | 缺料钩子→仓→装车→CTA | theme/scene 偏配送/装车 |
| 单品介绍 | 特写→细节→仓景→CTA | objects 同品类贯穿 |
| 门店实力 | 门头→展厅→仓深→装车 | scene 门店/仓配 |
| 综合日更 | 库存加权自动选题 | 现有 resolve_job_themes |

连贯性：邻接镜头同 scene **或** 同 object；单片 object 种类上限可配；冷却沿用 cliplet/asset。

---

## 5. 表达层 · 口播 / 字幕 / 多语言（摘要）

| 配置项 | 含义 |
|--------|------|
| `voice_lang` | 旁白语言（TTS）；可无旁白 |
| `subtitle_lang` | 字幕语言；可与旁白不同 |
| `subtitle_burn` | 外挂 / 单语烧录 / **双语烧录（可选）** |

原则：同一画面时间轴，换语言只重生脚本+TTS+srt；双语烧录是选项不是「多语言」定义本身。  
口播：**脚本 ← 镜头语义 + 词包事实**；声画同构（先 TTS 时长再微调取片）。

---

## 6. 明确不做

- 开放域无片型乱剪  
- 数字人假脸口播  
- 以绕过平台检测为卖点的全自动矩阵  
- 引擎内写死始峰文案/品类（必须进行业包/客户包）

---

## 7. 落地顺序

1. **P0** Cliplet `scene`/`objects`/`actions` + 嵌入文本 + Vision 提示优化 ✅  
2. **P0** 每客户 `05-品牌/封面模板/` + API 根路径切到客户目录 ✅  
3. **P1** 检索硬过滤 API（`theme`/`scene`/`object`）+ 组片消费 scene/objects 连贯性 ✅  
4. **P1** 片型规则 JSON（行业包 `piece_type_rules`） ✅  
5. **P2** `voice_lang`×`subtitle_lang` 产品化 + 双语选项 ✅（App/API/profile；`burn_*` 先交付 SRT + intent）  
6. **P3** `burn_mono` / `burn_dual` → ffmpeg 真烧录进 `publish_pack/video.burned.mp4` ✅（无 libass 时用 Pillow+overlay 回退）  
7. **P4** 第二客户零分叉验收 ✅（[`ZERO_FORK.md`](ZERO_FORK.md) · `scripts/smoke_zero_fork.py`）  
8. **P5** 多帧结构化分类 + 有限重试语义门禁 + 向量文本消费 ✅

---

## 8. 与现有文档关系

- 总路线：[`PRODUCT_PLAN.md`](PRODUCT_PLAN.md)  
- 开发铁律：[`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md)  
- 存储：[`V8_STORAGE.md`](V8_STORAGE.md)（同步区 05-品牌）  
- 质量：[`V8_QUALITY.md`](V8_QUALITY.md)
