# 广告文案语料分析报告 · 全行业通用

生成时间：2026-08-29 09:01

## 语料规模

- 去重中文句：**41718** 条
- 来源：`/Volumes/TF/广告文案/中文广告语-全部.jsonl`

## 长度分布

| 桶 | 条数 | 用途 |
|----|------|------|
| title (≤12字) | 23147 | 片上标题 |
| hook (13–24字) | 4152 | 开场/短钩子 |
| vo_line (25–48字) | 2540 | 单句旁白 |
| long_form (>48字) | 11879 | 只抽结构，不当口播 |

## 模式标签命中（可重叠）

- `plain`: 20897
- `cta`: 12987
- `trust`: 8395
- `benefit`: 6935
- `scene`: 6914
- `price`: 3746
- `contrast`: 2654
- `question`: 828

## 产出物

1. 模式卡：`configs/pattern_cards/universal-ad-copy.json`（23 张）
2. 行业包：`configs/samples/industry/universal-ad-copy/pack.json`
   - hooks: 72 条
   - title_candidates: 24 条
   - narration_lines 主题: 开场, 利益, 信任, 行动, default

## 接入速影

1. 新客户 `profile.sample.json` 设置 `"industry_pack": "universal-ad-copy"`
2. 或从本包复制 hooks / narration_lines 到客户 `keyword-pack.json`
3. 生成时走「模式卡槽位 + 近窗去重」，禁止整句背语料

## 说明

- 写入行业包的句子为**模式卡自写示例句**，不是语料原文复制。
- 长邮件/产品描述（long_form）未整句入库，仅用于统计。
