# 速影 · 本机持久化与同步安全


> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 可复用原则

1. **硬隔离后再切换**：有状态运行时必须先正常关闭、释放锁并确认进程退出，再切换到另一份独立配置。Chrome 登录态按客户、业务域和账号使用独立 `user-data-dir`。
2. **SQLite/WAL 只放本机 APFS**：`montage.db`、Chrome Cookies、缓存索引等依赖文件锁与 WAL 的状态不得放在 ExFAT、SMB 或同步目录。
3. **异常时 fail-closed**：身份、挂载点、映射、目的地或页面状态不明确时停止，不创建空库、不扩大同步范围、不盲目重试副作用。
4. **硬规则全链路钳制**：平台约束要同时覆盖生成、旧包读取、资产门禁和最终写入；例如快手正文最多四个话题标签。
5. **只信顶层、动态和可核验证据**：浏览器控制只选择顶层页面；静态帮助文案不作为上传进度；僵尸进程不视为有效运行实例。

## Local-first（HARD_LOCKS **L18 冻结**）

默认权威库与媒体工作区在本机 APFS（`~/Suying/data` + `~/Movies/速影工作区`）。外置盘仅用户改路径后可选；不得把插盘当作启动自动同步的前提。
**无用户当面授权禁止再改本拓扑与默认同步目标。**

## 存储边界

```text
~/Suying/data/             montage.db + settings.json（本机 APFS）
~/Suying/cache/            抽帧、代理和临时缓存（不同步）
~/Suying/render/           渲染中间物（不同步）
~/Suying/runtime/          安装与运行状态（不同步）
~/Suying/carrier/          极空间载体只读镜像
<同步根>/速影客户/...       显式映射的客户片库/交付物
```

极空间同步进程不得写入 `data`、`cache`、`render`、`runtime`、Chrome profile 或 App Support 状态目录。每条媒体规则只能写入自己的显式 `local_target`。

## 并行同步合同

- 不同规则仅在本地目的地目录互不重叠时允许并行。
- 相同、父子或别名解析后重叠的目的地必须互斥并报错。
- 每个进程持有带 PID 与目的地集合的 advisory lease；崩溃遗留 lease 由下一次启动在确认锁未持有后清理。
- `--only-media-customer` 可重复指定，只加载对应客户的媒体映射；未命中任何规则时拒绝运行。
- 下载先写 `.partial`，校验大小后原子替换；远端删除仍不删除本机文件。

## 数据库迁移合同

- 迁移前必须停止引擎，禁止复制活跃 WAL。
- 迁移使用 SQLite backup API 生成一致快照，并对源、目标执行 `PRAGMA integrity_check`。
- 若目标已有不同数据库，先备份目标，绝不静默覆盖。
- 目标校验通过后才原子更新本机 bootstrap `settings.json` 的 `paths.data_root`。
- 旧库保留为只读回滚源，不自动删除。
