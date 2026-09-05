import { useEffect, useState } from "react";
import { api } from "./api";
import { settingsOperationToken } from "./settingsLock";
import type { NotifyFn } from "./pages/pageTypes";

export function BackupControl({ notify }: { notify: NotifyFn }) {
  const [status, setStatus] = useState<Record<string, unknown> | null>(null);
  const [backups, setBackups] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState("");
  const policy = (status?.policy || {}) as Record<string, unknown>;

  async function refresh() {
    const [next, list] = await Promise.all([api.backupStatus(), api.backupList()]);
    setStatus(next);
    setBackups(list.backups || []);
  }

  useEffect(() => {
    void refresh().catch(() => undefined);
  }, []);

  async function withToken(action: (token: string) => Promise<unknown>) {
    setBusy("action");
    try {
      const token = await settingsOperationToken();
      await action(token);
      await refresh();
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy("");
    }
  }

  const current = String(status?.status || "idle");
  return (
    <section className="panel-block backup-control" data-guide="ops-backups">
      <div className="panel-head">
        <div>
          <h3>数据保护</h3>
          <p className="hint">
            备份业务数据、词库、向量结果和登录资料。浏览器正在使用时会安全跳过登录资料，不会强制关闭。
          </p>
        </div>
        <span className={`badge-${current === "failed" ? "warn" : "ok"}`}>
          {current === "running"
            ? "正在备份"
            : current === "failed"
              ? "上次失败"
              : current === "partial"
                ? "部分完成"
                : current === "completed"
                  ? "已完成"
                  : "待使用"}
        </span>
      </div>
      {status?.error ? <p className="hint">{String(status.error)}</p> : null}
      {status?.path ? <p className="path">最近备份：{String(status.path)}</p> : null}
      <div className="actions-inline">
        <button
          type="button"
          className="primary"
          disabled={Boolean(busy) || current === "running"}
          onClick={() =>
            void withToken(async (token) => {
              const result = await api.backupStart(token);
              notify(result.ok === false ? "已有备份正在进行" : "备份已开始", result.ok === false ? "warn" : "ok");
            })
          }
        >
          {current === "running" ? "正在备份…" : "立即备份"}
        </button>
        <button type="button" onClick={() => void refresh()}>
          刷新
        </button>
        <button
          type="button"
          disabled={Boolean(busy) || backups.length <= Number(policy.retention_count || 4)}
          onClick={() =>
            void withToken(async (token) => {
              const preview = await api.backupPrune(token, {
                dry_run: true,
                retention_count: Number(policy.retention_count || 4),
              });
              const count = Array.isArray(preview.candidates) ? preview.candidates.length : 0;
              if (!count) {
                notify("没有需要清理的历史备份", "info");
                return;
              }
              if (!window.confirm(`将 ${count} 份较早备份移入安全回收区，继续？`)) return;
              await api.backupPrune(token, {
                dry_run: false,
                retention_count: Number(policy.retention_count || 4),
                confirm: true,
              });
              notify(`已将 ${count} 份较早备份移入安全回收区`, "ok");
            })
          }
        >
          清理较早备份
        </button>
      </div>
      <div className="grid3" style={{ marginTop: 12 }}>
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(policy.enabled)}
            onChange={(event) =>
              void withToken(async (token) => {
                const result = await api.backupPolicyUpdate(token, { enabled: event.target.checked });
                setStatus((previous) => ({ ...(previous || {}), policy: result.policy }));
                notify(event.target.checked ? "每周自动备份已开启" : "自动备份已关闭", "ok");
              })
            }
          />
          每周自动备份
        </label>
        <label>
          保留最近
          <select
            value={Number(policy.retention_count || 4)}
            onChange={(event) =>
              void withToken(async (token) => {
                await api.backupPolicyUpdate(token, { retention_count: Number(event.target.value) });
                notify("备份保留数量已保存", "ok");
              })
            }
          >
            {[3, 4, 8, 12].map((value) => (
              <option key={value} value={value}>
                {value} 份
              </option>
            ))}
          </select>
        </label>
        <div>
          <span className="hint">已有备份</span>
          <strong style={{ display: "block", marginTop: 6 }}>{backups.length} 份</strong>
        </div>
      </div>
    </section>
  );
}
