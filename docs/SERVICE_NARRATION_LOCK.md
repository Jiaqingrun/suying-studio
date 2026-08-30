# 生活服务旁白合同 · L20（强制冻结）

> **效力：写死。2026-08-09 用户确认「旁白现在我能接受了，请将规则写死，在我主动要求变更之前不要再做出改动」。**  
> **无用户当面授权 = 禁止改本文件所列引擎行为、默认语感规则与验收金句口径。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 旁白×字幕时间锁仍见 [`NARRATION_SUBTITLE_LOCK.md`](NARRATION_SUBTITLE_LOCK.md)（H1–H11 不放松）。

---

## 1. 冻结范围

适用于 **生活服务 / 店介绍 / default 主题** 成片口播（`product_display=false` 且 `loading_ops=false`）。

商品桌面与装车/仓配路径可继续自由改写（不受本锁「黄金底稿」约束），但不得改动下列公共锚点（共享模块上的服务向分支）。

| 区域 | 冻结内容 |
|------|----------|
| **装配底稿** | `narration_script_zh` 生活服务路径：词库 hook 优先作开场；配方 `hook`↔`hooks` 别名；加权抽金句；warm 开场称「您」、禁「咱们/聊聊天」路线 |
| **Ollama** | **L20 v2（2026-08-29 用户授权）**：生活服务关闭 `use_golden_base_lock` 硬锁，允许受控多样改写；仍禁「咱们/聊聊天」等漂移（`_address_drift_blocked`）；近窗碰撞见 `copy_diversity_gate` |
| **用语** | 「倾听」；禁「请听/先请听完」；禁「安排到店服务」；禁空洞「会提前和您讲清楚 / 当面把服务与安排讲清楚 / 沟通清楚安排合理让您对每一步都放心」；禁「路过、聊两句」等已否口径 |
| **语音交付** | 仍服从 L2 H8：`rate=-8%`、短句呼吸；服务向规则常用 Edge 晓晓 + pitch/volume 以 **Job 冻结的 production rule** 为准，禁止默默改 TTS 合同键 |
| **客户词池/规则种子** | 臻享丽人服务介绍词池（`meta.revision`≥16）与 `production_rule_store_intro` 尊贵连贯口径为已验收配置；**无授权禁止再拧金句权重/禁词/开场池「优化」** |

---

## 2. 黄金底稿语义（验收线）

一条可接受的服务介绍旁白，语义上应能落到下列链路之一（可轮换组合，但不得回退到已禁空话）：

1. **开场**：来到 / 欢迎来到 · 先感受我们怎样为您服务 · 或服务包您满意等已验收 hook  
2. **中段（可组合）**：有预约 → 提前安排好；**先倾听完您的想法 → 再安排适合项目**；统一标准 / 休息茶点 / 培训态度等 **价值句**  
3. **收尾**：来店体验详聊 / 欢迎来店（**单收尾**，禁止引擎再叠第二 CTA）

**已否（写死禁回潮）**

- 「安排到店服务」（已到店却绕口）  
- 「来之前需要怎么/怎样安排」  
- 「会提前和您讲清楚」空垫  
- 「请听完」代替「倾听完」  
- Ollama 自由改成「咱们 / 聊聊天 / 这会儿到店」冲掉金句  

参考成片（听感已接受，非必须字字相同，但口径不得放松）：

- `montage_368_884010424`（倾听修正）  
- `montage_370_1951365684` / `montage_371_442788402`（Ollama 锁底稿）  
- 多样本：`montage_372_*`（#67–#70 文案轮换）  

---

## 3. 代码锚点（无授权勿改）

| 模块 | 路径 |
|------|------|
| Ollama 黄金锁 / 回退 | `engine/pack/ollama_narration.py`：`use_golden_base_lock`、`_address_drift_blocked`、`_base_retention_ratio`、`_finalize_ok(..., base_lock=)`、服务向 prompt「黄金底稿·节奏润色」 |
| 生活服务 VO 装配 | `engine/pack/narration_script.py`：warm openers/closers、`STOCK_BANNED_PHRASES` 服务禁词、pack opener 不双「来到」、服务长句 `to_spoken_line`、warm 少注水 |
| 配方槽位 | `engine/content/content_fingerprint.py`：`hook`/`hooks` 别名、`_weighted_pick` |
| 落盘旁白 | `engine/render/voice_subtitle.py`（Ollama 调用与底稿接管） |
| 分锁本文件 | `docs/SERVICE_NARRATION_LOCK.md` |

相关单测（回归不得无故删）：`tests/test_ollama_narration_idle.py`、`tests/test_narration_diversity.py` 中 Ollama / stock 路径。

---

## 4. Agent 禁令

1. **禁止**「再优化一下旁白」「顺手改改 Ollama 温度/提示/保留率」等无授权改动。  
2. **禁止**无授权把 warm 改回「咱们/聊」模板或删掉 `_address_drift_blocked`。  
3. **禁止**再把客户文案硬编码进引擎通用默认（继续走词池 / production rule）。  
4. L20 v2 已授权：允许关 base_lock + `copy_diversity_gate`；臻享等已验收客户词池勿整包替换。  
5. 仅当用户**当面写明**要改旁白合同，才可改 L20 及上表锚点，并同批更新本文件 + `HARD_LOCKS` 变更栏。

---

## 5. 变更记录

| 日期 | 事件 |
|------|------|
| 2026-08-09 | 用户听感验收通过（含 Ollama 锁底稿 + 倾听用词 + 金句轮换 #67–#70）→ **L20 冻结** |
| 2026-08-29 | 用户授权 L20 v2：关 `use_golden_base_lock`、接入 `copy_diversity_gate`（近窗 50）；保留倾听禁词与 H1–H11 |
