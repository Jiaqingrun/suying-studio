# 审计清理批次 · 2026-08-08

> 查漏补缺：死代码删除 + 测试对齐 + 全量自检 + core 出包双端推送。  
> **生产行为冻结**（不改门禁/发布/向量/L17–L19/`use_active_rule` 合同）。  
> 状态真相源仍为 [`DEV_LOCK.md`](DEV_LOCK.md) §E。

## G0 · 改前基线

| 检查 | 结果 |
|------|------|
| HEAD | `0376308` |
| `npx tsc --noEmit` | PASS |
| `npx vitest run` | 6 files / 11 tests PASS |
| `python3 scripts/smoke_test.py` | PASS |
| `pytest tests/ -q` | **10 failed**, 442 passed（账面） |
| `GET /health` | ok · engine 0.7.10 · ACTIVE · path_health ok |
| 删除二次确认 | `pipeline` / `NextAction` 无生产引用；`settingsAdvancedRequireUnlocked` 仅自身 |

### 已知 10 红（清理前）

1–3 `use_active_rule=False` 与 `queue.py` 现行合同冲突  
4–5 `rule_parser` 已改 `heavy_request`，测试仍 patch `rp.httpx`  
6–8 vision 测试仍 patch `httpx.Client`；timeout 抛 `TimeoutException`  
9 schedule mock 缺真实 `window_end` datetime  
10 voice clone live：f5 site-packages 与 Python 3.13 不兼容 → **ENV_KNOWN**

## G1 · 删除

| 路径 | 动作 |
|------|------|
| `engine/ingest/pipeline.py` | 删除 |
| `apps/desktop/src/shell/NextAction.tsx` | 删除 |
| `settingsAdvancedRequireUnlocked` | 仅删未使用导出；保留 settingsLock 模块 |

## G2 · 归档

| 路径 | 说明 |
|------|------|
| `docs/archive/code-snapshots/2026-08-08/` | 上述文件快照 + README |
| 本文件 | 全过程闸门留痕 |
| `docs/README.md` | §4 / §5 索引入口 |

## G3 · 测试对齐（仅 tests / 冒烟脚本；生产 0 语义）

| 区域 | 处置 |
|------|------|
| `test_job_customer_name_binding` / keyword freeze ×2 | `ensure_default_rule` + `use_active_rule=True` |
| `test_production_rules` parser ×2 | mock `heavy_request` |
| `test_semantic_ingest` vision ×3 | mock `heavy_request` / `VisionTimeoutError`；断言 gateway `trust_env=False` 字面 |
| `test_schedule_claim_idempotency` | 真实 `datetime` window_end/planned_at |
| `VoiceCloneLiveTests` | `skipUnless` 真实 F5TTS import 可跑（ENV_KNOWN ABI） |
| `scripts/smoke_ops.py` | 去掉已废的 `/reports/ops.quota` 字段门禁 |
| `scripts/smoke_zero_fork.py` | 拒绝标记字面 allowlist；创建规则后建 job |

## G4 · 全量自检

| 检查 | 结果 |
|------|------|
| tsc | PASS |
| vitest | 11 PASS |
| pytest | **451 passed, 1 skipped**（voice live ENV_KNOWN） |
| smoke_test | PASS |
| smoke_hard_locks | PASS |
| smoke_ops | PASS（对齐后） |
| smoke_ready_gate | PASS |
| smoke_system_events | PASS |
| smoke_zero_fork | PASS（对齐后） |
| closeout_selfcheck | PASS=14 FAIL=0 NEED_HUMAN=7 |
| freeze: external_required | False |
| live /health · /jobs/pipeline · vectorization | ok |

## G5 · 本机出包 + 安装

| 项 | 值 |
|----|-----|
| 命令 | `RUNTIME_FLAVOR=core ./scripts/release-to-t2s.sh` |
| 版本 | **0.7.11**（bump patch from 0.7.10） |
| T2S | `release_seq=55` · digest `5cb008f55a1bd090c86826b9f8e6d002a2c1401cda8422bdd8d55ead8d211899` |
| 套件 | `~/Suying/releases/速影-0.7.11-product-macos-arm64-core` |
| zip sha256 | `01bdd48b45fd3236449f0105b302d04579883f5cce485f8da31ffd3da9c51866` |
| 本机安装 | `安装到-应用程序.sh` OK · App CFBundle 0.7.11 |
| 本机 health | **0.7.11** · ok · ACTIVE |

## G6 · 双端推送

### T2S

已在 release-to-t2s 完成：`/nvme11/my/data/速影更新包/releases/0.7.11/20260807T194642Z`

### xlf-remote

```text
./scripts/deploy-remote.sh \
  --host xlf-remote \
  --customer-name "北京始峰伟业" \
  --customer-config configs/customers/北京始峰伟业 \
  --license …/xlf-remote-customer-1b0ae07cceee-seq2.suying-license \
  --offline-tools …/速影-offline-verify-python-pro \
  --skip-models
```

| 项 | 结果 |
|----|------|
| health gate | ok |
| outcome | **PARTIAL=`SMOKE_NOT_REQUESTED`**（与既有关账惯例一致；未请求 smoke-job） |
| 远端 App / health | **0.7.11** · ok · ACTIVE · ext_required=false |
| 词池 | 拒绝降级 revision 4→18（保留客户库更高版本，正确 fail-closed） |

## G7 · 关账

- 本文件填齐  
- DEV_LOCK §E 补批次行  
- **未**自动 git commit / push（待指令）  
- L17/L18/L19 与 `create_job` 强制 active rule **未改生产实现**
