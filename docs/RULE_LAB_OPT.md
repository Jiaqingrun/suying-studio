# 规则实验室深度优化（GRuleLabOpt）

> **领域合同。** 状态只写 [`DEV_LOCK.md`](DEV_LOCK.md) §E。  
> 冲突时：`DEV_LOCK` / `HARD_LOCKS` / `GVideoRules` L8 > 本文。  
> **日更去同质化**（周槽位如何消费 `content_facet` / `rule_rotation`、可见成效 A/B 档）见 [`DAILY_DIVERSITY_PLAN.md`](DAILY_DIVERSITY_PLAN.md)；本文只定能力语义，不另立运营进度 ✅。

## 目标

在 **不改 production_rules v3 真相模型** 的前提下：日更主路径短、精品可克隆、默认值与引擎同源、与生产页闭环清晰。

## 信息架构

| 区 | 内容 |
|----|------|
| 日更主路径 | 画幅 · 状态条 · 类别 · 预览 · 三档气质 · 标题 · 字幕 · 声音 · 主 CTA |
| 折叠·安全包装 | intro / motion / end_card / lut / plan_lang（默认关） |
| 折叠·更多设置 | 版本 · AI 草稿 · 语义/时长/模板 · 物品标 · 质量保护只读 |

**主 CTA 唯一 `primary`：保存并启用。** 预览选片为次 CTA（须先保存）。高级区不得再设第二主色「保存并启用」。

## 类别

| 键 | 含义 |
|----|------|
| `default` | 日更；包装保持关 |
| `premium` | 精品；可开包装/加严画质 |

仍允许自由 `content_category` 字符串。「克隆为精品稿」只产 **draft**，绝不自动 activate。

## 默认值契约

- 前端消费 `GET /production-rules/schema` 的 `empty_rules` + `orientation_defaults` + `fields`（min/max）
- 禁止组件内第二套 `EMPTY_RULES` 字面量

## 按任务规则轮换（GRL.7）

- **粒度**：一个 Job 抽一套规则并冻结；`target_count=N` 的 N 条成片共用这一套。
- **日更默认开**：客户 `profile_json.rule_rotation_enabled` 缺省为 True。
- **池子**：同客户、同画幅、`status==approved`、`rotation_enabled==True`。草稿/归档永不入池。可跨 `content_category`。
- **逐条开关**：已保存规则可「参加轮换」；新建保存启用后默认打开。
- **池空**：回退当前类别启用槽（仍须 approved）。
- **客户端**：正式建任务仍禁止指定 `rule_profile_id`，也禁止 `use_active_rule=false`。

## 规则绑定词池内容面（GRL.8）

- **一份词池、多条规则**：规则字段 `content_facet` 钉死 `keyword_pool.themes` 的分类键（如 `门店形象`、`品牌故事`）。
- **空 / default**：不钉死，行为与现网一致（任务 theme / 自动选题）。
- **非 default**：规划时 `pack_theme` 与标题池 `strict_theme` 只用该面；禁止混入默认标题池。缺面或该面无标题 → 批准/建任务/预演 **409 fail-closed**。
- **改文案只改词池对应区块**；规则不复制标题原文。`POST /production-rules/drafts-from-pack` 按面生成草稿，绝不自动启用。
- **不改** L17 / L19 / L20、`vo_style_lock`、canonical `keyword_pack_path`。

## 禁止

- 客户名硬编码分支
- seed / `config_truth_import` 冲 live 启用规则
- 放宽 L8 / L15 / L17–L20、READY_GATE、纸片、成片>旁白
- 重做 schema v4
- 改 LangCombobox 实现 / `.lang-combo*`（L17 仅调用）
- 改 F5 试听协议（L19）、生活服务旁白黄金底稿（L20）

## live 臻享

优化 **App/引擎能力**。升版后激活规则 revision/气质 **不得因本 Gate 自动变化**；改 live 只走规则实验室人工确认。

## DoD

见 DEV_LOCK §E `GRuleLabOpt` 行与 § 验收清单。
