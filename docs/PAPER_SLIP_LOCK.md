# 速影 · 纸片规则（PAPER SLIP）· 滚动避重

> **效力：写死。2026-07-31 用户明确废止日/周满额硬拒，改为滚动避重。**  
> **历史：** 2026-07-26 日≤2；2026-07-30 日/周双限额 — **均已废止，不得再按满额熔断。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 代码：`engine/catalog/paper_slip.py` + `engine/template/engine.py`

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
---

## 1. 硬规则（滚动避重）

| # | 规则 | 验收 |
|---|------|------|
| P1 | **优先排除近窗已用切片** | 默认排除最近 **20** 次 ready 切片用量（`cliplet_usage`） |
| P2 | **优先排除近窗已用短句/标题** | 默认排除最近 **15** 条 ready 提交的 phrase key |
| P3 | **渐进放宽，禁止空转** | cliplet：20→10→0；phrase：15→8→0；仅同任务去重后仍无可用素材才 `blocked` |
| P4 | **禁止配额熔断** | 不得因「用过几次 / 日周满额」将任务标 `circuit_open` |
| P5 | **仅 ready 计入避重账本** | failed / review **不**进入近窗；ready 后记账 |
| P6 | **同任务内硬去重** | 一条成片内不复用同一 `asset_uuid` / 已选 cliplet |
| P7 | **不够则换候选，不可静默硬塞满额旧逻辑** | 放宽阶梯写死；不得恢复日/周≤2 硬拒 |
| P8 | **窗口常量不可配成「再满额」** | `ROLLING_CLIPLET_WINDOW=20`、`ROLLING_PHRASE_WINDOW=15`；禁止再引入日/周次数上限配置后门 |

---

## 2. 规范化

- phrase key：去标点、去空白；多行用 `|` 连接（`normalize_phrase_key`）
- 标题记账：整句 key + 各行 key（供近窗与分析）
- 覆盖：选片 / hooks / title_pool / 路由文案 `select_recipe_components`
- 近窗真相源：cliplet → `cliplet_usage`；phrase → ready 提交账本（committed 预留或等价 usage 行）
- **废止：** 上海自然日/周「同一 key ≤2」硬拒；满额 trigger 不得再拦截 bump

---

## 3. 落点

| 环节 | 行为 |
|------|------|
| 选片 | `_pick_from_cliplets` + `recently_used_cliplet_ids`；不足则放宽近窗 |
| 用词 | `pick_*` / hooks / recipe 排除 `recently_used_phrase_keys`；不足则放宽 |
| 记账 | worker：`final_state==ready` → 记账供下次避重（无次数上限） |
| 预留 | 可保留并发租约语义；**不得**因日/周满额拒绝预留 |
| 降级 | 真无可用片 → `blocked`；文案不足 → 警告并尽量组装，不熔断配额 |

---

## 4. 冒烟

```bash
python3 scripts/smoke_paper_slip.py
python3 scripts/smoke_hard_locks.py
```

---

## 5. 变更

| 日期 | 事件 |
|------|------|
| 2026-07-26 | 用户：纸片规则写死（日≤2） |
| 2026-07-30 | 用户：加严为自然日/周双限额 |
| 2026-07-31 | 用户：废止满额硬拒；改为滚动避重 + 渐进放宽；禁止配额熔断 |
