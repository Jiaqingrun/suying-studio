# INCIDENT · 覆盖升级后 job#510 假 running 占渲染槽

> 状态：已现场止血；根因待成片复现确认。  
> 时间：2026-08-02 · 客户机 `xlf-remote` / 北京始峰伟业 · App 0.6.2

## 现象

1. `deploy-remote.sh --skip-models` 覆盖升级到 0.6.2 后，health=ok，但 `#510` 长期 `running produced=0`。
2. `ops/runtime-health.resource_gate.render` 被 `job:510` 占用；无 ffmpeg/渲染子进程。
3. `POST /jobs/510/pause` 可将 DB 标为 `paused`，**不释放**内存中的 render 槽，新生产会被堵住。
4. 重启 App/引擎后槽位清空；`#510` 保持 `paused`。

## 时间线（客户机 DB / job_events）

| UTC | 事件 |
|-----|------|
| 19:06:22 | `#510` 开始处理任务 |
| 19:19:46 | 部署重启后被 `reap_stale` 重新入队并再次「开始处理任务」 |
| 之后 | 无任何后续 job_event；无成片 |

## 初步判断

- 后续同机 `#511` 复现后确认：**Ollama `/api/embeddings` 挂死**导致 `build_plan` 语义检索阻塞（见 [`INCIDENT_OLLAMA_EMBED_HANG.md`](INCIDENT_OLLAMA_EMBED_HANG.md)）。
- Worker 在 `_process_job` 开头 commit「开始处理任务」后阻塞，未进入后续阶段日志。
- `pause_job` 只改 DB 状态，不打断正在执行的 `_process_job`，故 `resource_gate` 要等进程退出才 `release_all`。
- `engine_version` 字符串在 0.6.2 App 内仍为 `0.6.1`（本机已改为 `0.6.2`，需下次 embed/部署才上客户机）。

## 现场处理

1. 暂停 `#510`
2. 重启 `速影 Studio.app`，确认 render 槽空闲
3. 新增 `./scripts/check-remote-status.sh --host xlf-remote` 便于盯任务/槽位

## 待复现后修

- [ ] 用户新开一条成片，观察是否再次卡在「开始处理任务」
- [ ] 若复现：给 `_process_job` 关键阶段补心跳事件，并对 pause 协作退出 / 超时回收持槽
- [ ] 同步修本机后重 embed 再 `--skip-models` 推客户机
