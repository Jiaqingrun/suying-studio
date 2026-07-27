# 速影 · 纸片规则（PAPER SLIP）

> **效力：写死。2026-07-26 用户明确：素材切片与词池短句每天最多使用 2 次，开始执行。**  
> **违反本文件 = 选片/用词超发，禁止进规划（禁止静默超发）。**  
> 总索引：[`HARD_LOCKS.md`](HARD_LOCKS.md) · 配置：`VIDEO_LOCK.paper_slip` · 代码：`engine/catalog/paper_slip.py`

---

## 1. 硬规则

| # | 规则 | 验收 |
|---|------|------|
| P1 | **切片（cliplet）本地日 ≤2 次** | `daily_usage` kind=`cliplet` key=`{cliplet_id}` |
| P2 | **词池/品牌短句本地日 ≤2 次** | kind=`phrase`；规范化 phrase key |
| P3 | **日界** | `Asia/Shanghai` 日历日 `YYYY-MM-DD`；跨日自然换 key |
| P4 | **仅 ready 计数** | failed / review **不** +1；成功入库 ready 后 `commit_paper_slip_for_ready` |
| P5 | **禁止静默超发** | 选片硬过滤；词句硬过滤；不够则换候选 / `block_reasons` 打回 |
| P6 | **阈值不可放宽** | `MAX_DAILY_USES=2`；`load_video_lock` 钳制 `max_daily_uses≤2` |

---

## 2. 规范化

- phrase key：去标点、去空白；多行用 `|` 连接（`normalize_phrase_key`）
- 标题记账：整句 key + 各行 key
- 覆盖：`title_pool` / hooks / keywords 选用路径

---

## 3. 落点

| 环节 | 行为 |
|------|------|
| 表 | `daily_usage`（catalog SQLite） |
| 选片 | `engine/template/engine._pick_from_cliplets`：`paper_slip_blocked` 硬拒 |
| 用词 | `pick_keyword` / `pick_title` / hooks 过滤 + `build_plan` |
| 记账 | `worker`：`final_state=="ready"` → `commit_paper_slip_for_ready` |
| 降级 | 无可用片 → 组装失败 / block；无可用词 → `纸片规则：…配额已满` |

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
| 2026-07-26 | 用户：纸片规则写死并开始执行（日≤2） |
