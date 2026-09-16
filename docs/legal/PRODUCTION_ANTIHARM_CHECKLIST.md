# 生产线改动 · 防误伤检查表（锁死）

> **来源**：生产线评估 **PL-20 / PL-21 / PL-22**（P0 防误伤）；Agent Store  
> `files/docs/suying-production-line-assessment.md`  
> **效力**：合规线锁定；性能/流程/验证 PR 合入前必勾。  
> **不是**解冻授权。无用户当面授权，下列「禁止」不可破。

仓内权威锁：`docs/HARD_LOCKS.md` · `docs/READY_GATE.md` · `docs/REACH_NON_GOALS.md` · `docs/APP_POLL_BUDGET.md`。

## PL-20 · UI / 壳层合同

- [ ] diff **无**无关改动：`.lang-combo*` / `LangCombobox`（L17）  
- [ ] **无**清玻璃 token / 主视觉叙事回潮到毛玻璃或已废弃皮肤  
- [ ] **无** FrozenTab 预挂载 / 离页轮询策略解冻（发布离页停 poll 维持 A）  
- [ ] 不为「省电」把 App 级消息未读预算停到永久不更新  

## PL-21 · 成片质量锁

- [ ] **不**放松 `READY_GATE` 项集合 / L15 / QUALITY_LOCK 阈值  
- [ ] **不**关闭 Twemoji 烧录路径（含 emoji 的字幕 cue）  
- [ ] **不**为降占用砍 VO 对齐 / 字幕只烧一次等质量合同  
- [ ] **不**回退 L20 生活服务旁白口径（无新授权）  
- [ ] 性能手段仅限：错峰、释槽、轮询合并、defer、进程数档位内  

## PL-22 · 入场 / 发布 / 消息

- [ ] IngestWatcher **默认仍启**；不关入场漏片  
- [ ] 消息巡检间隔仍 **1800s**；脱敏与「不自动回复」不变  
- [ ] 发布 **单槽**；不与消息合并权威 Chrome profile  
- [ ] 验证码 / 登录墙仍 **人过**；无自动过码 / Cookie 池 / 反检测卖点  
- [ ] 不静默 remap 历史绝对路径（须显式工具 + 确认）  

## 关联合规项（非 UI，同批 PR 自检）

- [ ] **PL-08**：不盲合 B3 词包（无「词包授权合」书面令）  
- [ ] **PL-19**：不建议/不实现「默认开全库 VLM」  
- [x] **PL-07**：方案 A 偏温和已落地（白名单 + localhost/`SUYING_OPS_DEV` 旁路）；收紧待真人确认 · 见 `OPS_AUTH_WHITELIST.md`  

## 验收一句

锁文件与上述路径无未授权 diff；验证线成片对照不降；发布单槽 + 消息 1800 仍成立。
