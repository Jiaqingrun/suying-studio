import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { outputDisplayLabel } from "./displayId";
import type { AskConfirmFn, NotifyFn } from "./pages/pageTypes";
import { settingsOperationToken } from "./settingsLock";

type Props = {
  notify: NotifyFn;
  askConfirm: AskConfirmFn;
};

export function PublishedCleanupControl({ notify, askConfirm }: Props) {
  const [policy, setPolicy] = useState<Record<string, unknown>>({});
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const days = Number(policy.retention_days || 30);

  async function refresh(nextDays = days) {
    const [policyResult, preview] = await Promise.all([
      api.publishedCleanupPolicy(),
      api.publishedCleanupPreview(nextDays),
    ]);
    setPolicy(policyResult.policy || {});
    setItems(preview.items || []);
    setSelected((previous) => {
      const allowed = new Set((preview.items || []).map((item) => Number(item.output_id)));
      return new Set([...previous].filter((id) => allowed.has(id)));
    });
  }

  useEffect(() => {
    void refresh().catch(() => undefined);
  }, []);

  const selectedRows = useMemo(
    () => items.filter((item) => selected.has(Number(item.output_id))),
    [items, selected],
  );
  const selectedBytes = selectedRows.reduce((sum, item) => sum + Number(item.size || 0), 0);

  async function updatePolicy(patch: Record<string, unknown>) {
    setBusy(true);
    try {
      const token = await settingsOperationToken();
      const result = await api.publishedCleanupPolicyUpdate(token, patch);
      setPolicy(result.policy);
      notify("视频保留设置已保存", "ok");
      await refresh(Number(result.policy.retention_days || days));
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  async function removeSelected() {
    if (!selectedRows.length) {
      notify("请先选择要清理的已发布视频", "warn");
      return;
    }
    const labels = selectedRows
      .slice(0, 5)
      .map((item) => outputDisplayLabel(item) || "成片")
      .join("、");
    const ok = await askConfirm({
      title: "清理已发布视频",
      body:
        `将清理 ${selectedRows.length} 个已发布视频（${(selectedBytes / 1024 / 1024).toFixed(1)} MB）：${labels}` +
        "\n\n发布记录、编号和物料会保留，视频先进入安全回收区。",
      confirmLabel: "确认清理",
      cancelLabel: "取消",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const token = await settingsOperationToken();
      const result = await api.publishedCleanupRun(token, {
        output_ids: selectedRows.map((item) => Number(item.output_id)),
        confirm: true,
      });
      notify(`已清理 ${result.count} 个已发布视频`, "ok");
      setSelected(new Set());
      await refresh();
    } catch (error) {
      notify(String(error), "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel-block published-cleanup" data-guide="settings-published-cleanup">
      <h3>已发布视频保留</h3>
      <p className="hint">
        只处理已经全部发布并完成归档的视频。发布记录、真实编号和物料始终保留，不会重新进入待发列表。
      </p>
      <div className="grid3">
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(policy.enabled)}
            disabled={busy}
            onChange={(event) => void updatePolicy({ enabled: event.target.checked })}
          />
          自动清理较早视频
        </label>
        <label>
          保留时间
          <select
            value={days}
            disabled={busy}
            onChange={(event) => void updatePolicy({ retention_days: Number(event.target.value) })}
          >
            {[7, 15, 30, 60, 90, 180].map((value) => (
              <option key={value} value={value}>
                {value} 天
              </option>
            ))}
          </select>
        </label>
        <div>
          <span className="hint">当前可清理</span>
          <strong style={{ display: "block", marginTop: 6 }}>{items.length} 个</strong>
        </div>
      </div>
      {items.length ? (
        <div className="review-list" style={{ marginTop: 12 }}>
          {items.map((item) => {
            const id = Number(item.output_id);
            return (
              <label key={id} className="review-card check">
                <input
                  type="checkbox"
                  checked={selected.has(id)}
                  onChange={(event) =>
                    setSelected((previous) => {
                      const next = new Set(previous);
                      if (event.target.checked) next.add(id);
                      else next.delete(id);
                      return next;
                    })
                  }
                />
                <span>
                  <strong>{outputDisplayLabel(item) || "成片"}</strong>
                  <span className="hint" style={{ display: "block" }}>
                    {(Number(item.size || 0) / 1024 / 1024).toFixed(1)} MB
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      ) : (
        <p className="hint" style={{ marginTop: 12 }}>当前没有超过保留时间的已发布视频。</p>
      )}
      <div className="actions-inline" style={{ marginTop: 12 }}>
        <button type="button" onClick={() => void refresh()} disabled={busy}>
          刷新列表
        </button>
        <button
          type="button"
          className="danger"
          disabled={busy || !selectedRows.length}
          onClick={() => void removeSelected()}
        >
          {busy ? "处理中…" : `清理已选（${selectedRows.length}）`}
        </button>
      </div>
    </section>
  );
}
