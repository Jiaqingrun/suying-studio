# 模式卡（Pattern Cards）

从开放许可广告文案语料**离线分析**得到的可复用结构，供速影行业包 / 客户词池引用。

## 原则

- **学骨架，写新肉**：`structure` + `slots` + `examples` 为自写示例句，不是语料原文。
- **禁止**把语料整句复制进客户交付包。
- 生成时仍须走近窗去重与合规禁词。

## 文件

| 文件 | 说明 |
|------|------|
| `universal-ad-copy.json` | 全行业通用模式卡 |
| `universal-ad-copy-report.md` | 语料统计与接入说明 |

## 行业包

对应行业种子：`configs/samples/industry/universal-ad-copy/pack.json`

新客户可在 `profile.sample.json` 中设置：

```json
"industry_pack": "universal-ad-copy"
```

## 重新分析

语料路径默认：`/Volumes/TF/广告文案/中文广告语-全部.jsonl`

```bash
python3 scripts/ad_copy_analyze_patterns.py
```
