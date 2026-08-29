> **历史归档**：质量冻结期问题快照，不得指导现行开发。
> **现行任务/Gate 状态唯一真相源**：[`../DEV_LOCK.md`](../DEV_LOCK.md) §E。 冲突句作废。

# 成片问题 Backlog

记录时间：2026-07-22（2026-07-23 更新质量冻结）  
来源：始峰伟业实拍抽检反馈

## 问题清单

| ID | 问题 | 状态 | 落地 |
|----|------|------|------|
| Q1 | 同一片段复用过高 | ✅ V3 | cliplet 冷却 + 单片同素材限 1 |
| Q2 | 片段与主题不符 | ✅ V3 基础 | 一致性阈值 0.35，最多重抽 3 次，仍低进 `review` |
| Q3 | 配文字色/位置过随机 | ✅ V3 | 固定顶栏深色底 + 白字 |
| Q4 | 黑场/静音/响度漏检 | ✅ V7 | `qc/gates` blackdetect + silence/volume |
| Q5 | 开场/收尾语义雷同 | ✅ V7 | hook/body/cta 差异化查询 |
| Q8 | 同任务标题素材刷同款 | ✅ V7 | 标题/钩子冷却 + job 内素材降权 |
| A1 | 成片无音轨 | ✅ Sprint A | 环境音压低 + BGM/占位垫乐 + loudnorm；job13 实机有声 |
| A2 | 字幕偏「机打」 | ✅ Sprint A | dual_chip：每行≤6 字、字号 96、渲染截断 |
| A3 | 废片无人值守刷不停 | ✅ Sprint A | `consecutive_non_ready` → 质量熔断 |
| L0 | 无黄金样片标尺 | ✅ 已冻结 | [`GOLDEN_SAMPLES.md`](../GOLDEN_SAMPLES.md) G1/G2 |
| B1 | 开场同质 | ✅ Sprint B | hook 池扩容 + 冷却加严 |
| B2 | 节奏单一 | ✅ Sprint B | `fast-ship` / `stable-product` + 日历绑定 |
| B3 | 暗糊素材入选 | ✅ Sprint B | 入库质量分 + 抽样过滤 |

详见 [`V3.md`](V3.md)–[`V7.md`](V7.md)。质量冻结路径见 [`V8_QUALITY.md`](../V8_QUALITY.md)。

## 明确仍后置

- Logo / 打回降权 / 质量日报 → Sprint C（[`V8_QUALITY.md`](../V8_QUALITY.md)）  
- 多平台文案包、口播字幕、半自动发布、消息提醒 → [`PRODUCT_PLAN.md`](../PRODUCT_PLAN.md) Phase 2–4  
- 数字人；「绕检测」全自动矩阵（永不作为卖点）  
