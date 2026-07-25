# 速影 · 语义切片与成片表达规格（优化版）

> **状态：2026-07-25 · P0–P4 完成**（含第二客户零分叉）  
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
  → Vision 一句描述（行业中立提示 + 行业包词表暗示）
  → 规则标注 theme/scene/objects/actions（词典优先）
  → compose_embed_text → embed → 写入 embedding_json
```

**算力策略：** 关键帧为主，禁止默认全库逐帧细分类；回填任务可限速后台跑。

### 2.3 嵌入文本（优化点）

```text
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

---

## 8. 与现有文档关系

- 总路线：[`PRODUCT_PLAN.md`](PRODUCT_PLAN.md)  
- 开发铁律：[`DEVELOPMENT_STANDARDS.md`](DEVELOPMENT_STANDARDS.md)  
- 存储：[`V8_STORAGE.md`](V8_STORAGE.md)（同步区 05-品牌）  
- 质量：[`V8_QUALITY.md`](V8_QUALITY.md)
