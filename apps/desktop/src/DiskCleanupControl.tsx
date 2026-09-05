import { useEffect, useState } from "react";
import { api } from "./api";
import type { AskConfirmFn, NotifyFn } from "./pages/pageTypes";
import { settingsOperationToken } from "./settingsLock";

type Props = {
  notify: NotifyFn;
  askConfirm?: AskConfirmFn;
};

function fmtMb(bytes: unknown): string {
  const n = Number(bytes || 0);
  if (!Number.isFinite(n) || n <= 0) return "0 MB";
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function catBytes(
  cat: Record<string, unknown> | undefined,
  stale = true,
): string {
  if (!cat) return "—";
  const key = stale ? "stale_bytes" : "bytes";
  if (cat[key] != null) return fmtMb(cat[key]);
  return fmtMb(cat.bytes);
}

export function DiskCleanupControl({ notify, askConfirm }: Props) {
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState("");
  const policy = (report?.policy || {}) as Record<string, Record<string, unknown>>;
  const categories = (report?.categories || {}) as Record<
    string,
    Record<string, unknown>
  >;
  const published = (report?.published_eligible || {}) as {
    count?: number;
    bytes?: number;
  };
  const failedPol = policy.failed || {};
  const workPol = policy.work_cache || {};
  const pubPol = policy.published || {};

  async function refresh() {
    const next = await api.diskCleanupReport();
    setReport(next as unknown as Record<string, unknown>);
  }

  useEffect(() => {
    void refresh().catch(() => undefined);
  }, []);

  async function withToken(action: (token: string) => Promise<void>) {
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

  async function updatePolicy(patch: Record<string, unknown>) {
    await withToken(async (token) => {
      const result = await api.diskCleanupPolicyUpdate(token, patch);
      setReport((prev) =>
        prev
          ? { ...prev, policy: result.policy }
          : { policy: result.policy },
      );
      notify("磁盘清理策略已保存", "ok");
    });
  }

  async function runTiers(tiers: string[], title: string, body: string) {
    if (askConfirm) {
      const ok = await askConfirm({
        title,
        body,
        confirmLabel: "确认清理",
        cancelLabel: "取消",
        danger: true,
      });
      if (!ok) return;
    }
    await withToken(async (token) => {
      const result = await api.diskCleanupRun(token, {
        tiers,
        confirm: true,
      });
      const parts = (result.parts || {}) as Record<string, unknown>;
      const labels = tiers.join("、");
      notify(`已执行清理：${labels}`, "ok");
      void parts;
    });
  }

  return (
    <section className="panel-block disk-cleanup" data-guide="ops-disk-cleanup">
      <div className="panel-head">
        <div>
          <h3>磁盘清理</h3>
          <p className="hint">
            仅清理允许范围内的失败品、临时渲染缓存与（可选）抽帧/代理、已发布成片。
            <strong>待审成片、片库、数据库、cache/library 不会被本页删除。</strong>
          </p>
        </div>
        <button type="button" disabled={Boolean(busy)} onClick={() => void refresh()}>
          刷新报告
        </button>
      </div>

      <div className="grid3" style={{ marginTop: 12 }}>
        <div>
          <span className="hint">可清 temp</span>
          <strong style={{ display: "block" }}>
            {catBytes(categories.temp)} / {Number(categories.temp?.files ?? 0)} 文件
          </strong>
        </div>
        <div>
          <span className="hint">可清 render</span>
          <strong style={{ display: "block" }}>
            {catBytes(categories.render)}
          </strong>
        </div>
        <div>
          <span className="hint">frames+proxies（过期）</span>
          <strong style={{ display: "block" }}>
            {fmtMb(
              Number(categories.frames?.stale_bytes || 0) +
                Number(categories.proxies?.stale_bytes || 0),
            )}
          </strong>
        </div>
        <div>
          <span className="hint">failed 树过期</span>
          <strong style={{ display: "block" }}>
            {catBytes(categories.failed_tree)}
          </strong>
        </div>
        <div>
          <span className="hint">可清已发布成片</span>
          <strong style={{ display: "block" }}>
            {published.count ?? 0} 个 · {fmtMb(published.bytes)}
          </strong>
        </div>
        <div>
          <span className="hint">发布回收区</span>
          <strong style={{ display: "block" }}>
            {fmtMb(categories.published_trash?.bytes)}
          </strong>
        </div>
      </div>

      <h4 style={{ marginTop: 16 }}>策略</h4>
      <div className="grid3">
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(failedPol.enabled)}
            disabled={Boolean(busy)}
            onChange={(event) =>
              void updatePolicy({
                failed: { enabled: event.target.checked },
              })
            }
          />
          自动清失败品（{Number(failedPol.retention_hours || 72)} 小时）
        </label>
        <label>
          失败/temp 保留小时
          <select
            value={Number(failedPol.retention_hours || 72)}
            disabled={Boolean(busy)}
            onChange={(event) =>
              void updatePolicy({
                failed: { retention_hours: Number(event.target.value) },
              })
            }
          >
            {[24, 48, 72, 168].map((h) => (
              <option key={h} value={h}>
                {h} 小时
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(workPol.frames_proxies_enabled)}
            disabled={Boolean(busy)}
            onChange={(event) =>
              void updatePolicy({
                work_cache: { frames_proxies_enabled: event.target.checked },
              })
            }
          />
          自动清 frames/proxies
        </label>
        <label>
          frames/proxies 天数
          <select
            value={Number(workPol.frames_proxies_days || 14)}
            disabled={Boolean(busy)}
            onChange={(event) =>
              void updatePolicy({
                work_cache: { frames_proxies_days: Number(event.target.value) },
              })
            }
          >
            {[7, 14, 30, 60].map((d) => (
              <option key={d} value={d}>
                {d} 天
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(pubPol.enabled)}
            disabled={Boolean(busy)}
            onChange={(event) =>
              void updatePolicy({
                published: { enabled: event.target.checked },
              })
            }
          />
          自动清较早已发布视频
        </label>
        <label>
          已发布保留天数
          <select
            value={Number(pubPol.retention_days || 30)}
            disabled={Boolean(busy)}
            onChange={(event) =>
              void updatePolicy({
                published: { retention_days: Number(event.target.value) },
              })
            }
          >
            {[7, 15, 30, 60, 90, 180].map((d) => (
              <option key={d} value={d}>
                {d} 天
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="actions-inline" style={{ marginTop: 16 }}>
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() =>
            void runTiers(
              ["work_cache"],
              "清理工作缓存",
              "将删除过期的 temp 与 render 中间文件。不触碰片库、待审成片与 library。",
            )
          }
        >
          清理工作缓存
        </button>
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() =>
            void runTiers(
              ["rebuild_cache"],
              "清理可重建缓存",
              "将删除过期的 frames 与 proxies。下次分析/代理会重新生成，不删 cache/library。",
            )
          }
        >
          清理 frames/proxies
        </button>
        <button
          type="button"
          disabled={Boolean(busy)}
          onClick={() =>
            void runTiers(
              ["failed"],
              "清理失败残留",
              "将清理过期 failed 成片媒体与 failed/ 孤儿文件。数据库审计记录保留。",
            )
          }
        >
          清理失败残留
        </button>
        <button
          type="button"
          className="danger"
          disabled={Boolean(busy) || !(published.count || 0)}
          onClick={() =>
            void runTiers(
              ["published"],
              "清理已发布视频",
              `将清理 ${published.count || 0} 个超过保留期的已发布视频（${fmtMb(published.bytes)}）。发布记录与编号保留，视频进安全回收区。`,
            )
          }
        >
          清理可清已发布
        </button>
      </div>
    </section>
  );
}
