# 速影 · DB / 一体包续跑说明（GStab）

> 权威库唯一；空壳 + 坏 WAL 禁止当生产库。T2S 不承载 db。

> 冲突时：`DEV_LOCK.md` / `HARD_LOCKS.md` > 本文。权威索引见 [`README.md`](README.md)。
## 权威库

1. 工作区 `settings.json` 的 `paths.data_root` 指向唯一 `montage.db`。
2. 若发现 0 字节 db + `-wal`/`-shm`，先隔离：
   ```bash
   mv montage.db montage.db.empty-quarantine-$(date +%Y%m%d)
   mv montage.db-wal montage.db-wal.quarantine-$(date +%Y%m%d) 2>/dev/null || true
   ```
3. 从最近健康副本恢复（示例）：
   ```bash
   sqlite3 stale.db ".dump" | sqlite3 montage.db
   python3 -c "from engine.config.settings import load_settings; from engine.catalog.db import init_db; init_db(load_settings())"
   ```
4. `PRAGMA integrity_check;` 须为 `ok`；冒烟：
   ```bash
   python3 scripts/smoke_test.py
   python3 scripts/smoke_ops.py
   python3 scripts/smoke_zero_fork.py
   python3 scripts/smoke_carrier.py
   ```

## 一体包冷启动

1. 从 T2S 载体 `app/` 安装或打开 DMG → `/Applications/速影.app`
2. 极空间保持登录并绑 T2S；仅同步 `速影载体/`
3. App 内启动引擎；运维页确认 health + 载体可见 + `latest.json`
4. Ollama / FFmpeg 仍按 `CUSTOMER_INSTALL.md`

## 健康报警一句话

`GET /reports/ops` → `health_line` + `db_ok`；总览红灯：片库/成片不可写、db 空壳、音色违规。

## 陈旧生产任务 / clone 空转（运维标准）

假 `running`、旁白熔断、缺 `f5-tts` 时，**不要**在 worker 活跃时手写 SQL `UPDATE jobs`。优先：

```bash
# 运维机 → 客户机诊断并可选修复
python3 scripts/diag_fix_stalled_jobs.py --host <ssh-host>
python3 scripts/diag_fix_stalled_jobs.py --host <ssh-host> --fix --resume-jobs

# 客户机本机
python3 scripts/diag_fix_stalled_jobs.py --local --fix --resume-jobs

# 引擎活着时：API 回收无持有者的陈旧 running
curl -fsS -X POST 'http://127.0.0.1:8766/jobs/reap-stale?stale_after_sec=600'
```

clone 离线权重：

```bash
python3 scripts/materialize_clone_tts_cache.py --check
python3 scripts/materialize_clone_tts_cache.py --export /path/to/clone-hf-hub
# 客户机：--import-from …；~/Suying/runtime/local.env 设 HF_HUB_OFFLINE=1
```

出包须 `EMBED_CLONE_TTS=1`（默认）并视需要 `SUYING_CLONE_HF_CACHE=` 指向已物化 hub。

## 路径串用户名（/Users/xlf → 本机）

若 `settings.json` 或 `customers` 表残留其他机器用户前缀，引擎会 degraded / 无法 mkdir。批量替换为当前用户 home，并保证 `ensure_layout` 对不可写路径不崩（已 `_safe_mkdir`）。
