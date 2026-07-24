# MVP 验收清单

## A. 环境与路径
- [x] 引擎 `/health` 返回 200
- [x] 资料库/输出路径可配置且持久化
- [x] 外置盘未挂载时（若启用 external_required）禁止任务并提示

## B. 词包与模板
- [x] 可导入样例或客户词包
- [x] Dry-run 返回 title + clips + warnings
- [x] 关键词冷却生效（近 7 天重复词权重降低）

## C. 摄入
- [x] 新视频入资料库后可扫描入库
- [x] assets 表可查分类、时长、分辨率

## D. 渲染与质检
- [x] 至少产出 1 条进入 `ready` 的成片（需真实素材）
- [x] 失败片进入 `failed` 且记录原因（路径已实现；当前库可无失败样本）
- [x] 同 seed Dry-run 计划结构可复现
- [x] 成片旁有 JSON sidecar（seed、clips、copywriting）

## E. 任务
- [x] 按条数任务可完成并停止
- [x] 连续失败触发熔断（circuit_open）（代码已实现）
- [x] 暂停/恢复可用

## F. 控制台
- [x] Tauri App 可连接引擎并操作上述功能
- [x] CSV 日志可导出

## G. 文档
- [x] INSTALL.md / SOP.md / 本验收清单可用（桌面副本已同步）

## 完成定义
在目标 Mac 上按 SOP 日更试跑，稳定产出 `ready` 成片，且**黄金样片**已由你方签字认可。

## 验收记录（2026-07-22）
- [x] **暂且通过**：功能项基本齐；导演质量与黄金样片未签字（见 [`MVP_PASSED.md`](MVP_PASSED.md)、[`BACKLOG.md`](BACKLOG.md)）。
- V1/V2 已继续推进：语义取片、日历、无人值守加固。
