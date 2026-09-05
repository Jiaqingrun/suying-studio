/** GStab.WORKSPACE: mount-aware workspace reconnect (reattach + refresh, never copy DB). */

export type WorkspaceSyncPrefs = {
  auto_reconnect_on_mount: boolean;
  mount_settle_ms: number;
};

export type WorkspaceProbeView = {
  ok: boolean;
  state: string;
  data_root?: string | null;
  reasons: string[];
  workspace_id?: string | null;
  volume_uuid?: string | null;
  engine_status?: string | null;
  engine_healthy: boolean;
};

export type WorkspacePhase =
  | "idle"
  | "waiting_for_disk"
  | "disk_settling"
  | "checking"
  | "reconnecting"
  | "needs_engine_restart"
  | "refreshing"
  | "ready"
  | "error";

export type WorkspaceStateSnapshot = {
  last_kind?: string | null;
  last_at?: string | null;
  prefs: WorkspaceSyncPrefs;
  supported: boolean;
  probe?: WorkspaceProbeView | null;
  phase: string;
  message?: string | null;
  error?: string | null;
};

// Local-first: mount auto-reconnect only useful for optional external paths.
const DEFAULT_PREFS: WorkspaceSyncPrefs = {
  auto_reconnect_on_mount: false,
  mount_settle_ms: 1500,
};

function isTauri(): boolean {
  return (
    typeof window !== "undefined" &&
    !!(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
  );
}

export async function getWorkspaceSyncPrefs(): Promise<WorkspaceSyncPrefs> {
  if (!isTauri()) return { ...DEFAULT_PREFS };
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return (await invoke("workspace_sync_get_prefs")) as WorkspaceSyncPrefs;
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export async function setWorkspaceSyncPrefs(prefs: WorkspaceSyncPrefs): Promise<WorkspaceSyncPrefs> {
  if (!isTauri()) return prefs;
  const { invoke } = await import("@tauri-apps/api/core");
  return (await invoke("workspace_sync_set_prefs", { prefs })) as WorkspaceSyncPrefs;
}

export async function getWorkspaceSnapshot(): Promise<WorkspaceStateSnapshot> {
  if (!isTauri()) {
    return {
      prefs: { ...DEFAULT_PREFS },
      supported: false,
      phase: "idle",
      probe: null,
    };
  }
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return (await invoke("workspace_events_snapshot")) as WorkspaceStateSnapshot;
  } catch {
    return {
      prefs: { ...DEFAULT_PREFS },
      supported: false,
      phase: "idle",
      probe: null,
    };
  }
}

export async function reconnectWorkspaceNow(): Promise<WorkspaceStateSnapshot> {
  if (!isTauri()) {
    // Browser preview: hit engine HTTP directly.
    const res = await fetch("http://127.0.0.1:8766/workspace/reconnect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        typeof body?.detail === "object"
          ? body.detail.message || JSON.stringify(body.detail)
          : body?.detail || res.statusText,
      );
    }
    return {
      prefs: { ...DEFAULT_PREFS },
      supported: false,
      phase: body.ok ? "ready" : "error",
      message: body.ok ? "工作区已同步" : "工作区同步失败",
      probe: body.workspace
        ? {
            ok: Boolean(body.ok),
            state: String(body.state || ""),
            data_root: body.workspace.data_root,
            reasons: body.workspace.reasons || [],
            workspace_id: body.workspace.workspace_id,
            volume_uuid: body.workspace.volume_uuid,
            engine_healthy: Boolean(body.ok),
          }
        : null,
    };
  }
  const { invoke } = await import("@tauri-apps/api/core");
  return (await invoke("workspace_reconnect_now")) as WorkspaceStateSnapshot;
}

export async function listenWorkspaceState(
  onEvent: (snap: WorkspaceStateSnapshot) => void,
): Promise<() => void> {
  if (!isTauri()) return () => undefined;
  try {
    const { listen } = await import("@tauri-apps/api/event");
    const un = await listen<WorkspaceStateSnapshot>("suying://workspace-state", (e) => {
      onEvent(e.payload);
    });
    return () => {
      void un();
    };
  } catch {
    return () => undefined;
  }
}

export function workspacePhaseLabel(phase: string, probe?: WorkspaceProbeView | null): string {
  switch (phase) {
    case "waiting_for_disk":
      return "等待路径";
    case "disk_settling":
      return "路径稳定中";
    case "checking":
      return "检查中";
    case "reconnecting":
      return "重连中";
    case "needs_engine_restart":
      return "需重启引擎";
    case "refreshing":
      return "刷新数据";
    case "ready":
      return "工作区就绪";
    case "error":
      return "工作区异常";
    default:
      if (probe?.state === "local") return "主盘就绪";
      if (probe?.state === "missing") return "路径不可用";
      if (probe?.state === "mismatch") return "身份不符";
      if (probe?.ok) return "工作区就绪";
      return "工作区";
  }
}

export { DEFAULT_PREFS };
