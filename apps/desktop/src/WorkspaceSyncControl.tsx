import type { WorkspaceProbeView, WorkspaceSyncPrefs } from "./workspaceSync";
import { workspacePhaseLabel } from "./workspaceSync";

type Props = {
  phase: string;
  message?: string | null;
  error?: string | null;
  probe?: WorkspaceProbeView | null;
  prefs: WorkspaceSyncPrefs;
  busy: boolean;
  onToggleAuto: (enabled: boolean) => void;
  onSyncNow: () => void;
  onRefresh: () => void;
};

export function WorkspaceSyncControl({
  phase,
  message,
  error,
  probe,
  prefs,
  busy,
  onToggleAuto,
  onSyncNow,
  onRefresh,
}: Props) {
  const label = workspacePhaseLabel(phase, probe);
  const tone =
    phase === "error" || probe?.state === "mismatch"
      ? "err"
      : phase === "waiting_for_disk" || probe?.state === "missing"
        ? "warn"
        : phase === "ready" || probe?.ok || probe?.state === "local"
          ? "ok"
          : "info";
  const detail =
    error ||
    message ||
    probe?.reasons?.[0] ||
    (probe?.data_root
      ? `工作区 ${probe.data_root}`
      : "主盘工作区就绪；路径变化时可选自动重连（外置路径）");

  return (
    <div className="workspace-sync-control" title={detail}>
      <span className={`workspace-sync-chip tone-${tone}`} aria-live="polite">
        {label}
      </span>
      <label className="workspace-sync-auto" title="仅当库路径在外置卷时，挂载变化会自动重连">
        <input
          type="checkbox"
          checked={prefs.auto_reconnect_on_mount}
          disabled={busy}
          onChange={(e) => onToggleAuto(e.target.checked)}
        />
        路径变化时重连
      </label>
      <button type="button" className="primary" disabled={busy} onClick={onSyncNow}>
        {busy ? "刷新中…" : "刷新工作区"}
      </button>
      <button type="button" disabled={busy} onClick={onRefresh}>
        刷新
      </button>
    </div>
  );
}
