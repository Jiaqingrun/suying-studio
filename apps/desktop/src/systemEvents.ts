/** GSystemPause: Tauri system-event bridge (macOS sleep / screen / session). */

export type SystemEventPrefs = {
  enabled: boolean;
  pause_on_system_sleep: boolean;
  pause_on_power_off: boolean;
  pause_on_session_inactive: boolean;
  pause_on_screen_sleep: boolean;
  auto_resume_on_wake: boolean;
  auto_resume_on_session_active: boolean;
  wake_settle_seconds: number;
  event_debounce_ms: number;
};

export type SystemStateSnapshot = {
  last_kind?: string | null;
  last_event_id?: string | null;
  last_at?: string | null;
  prefs: SystemEventPrefs;
  supported: boolean;
  delivery_ok?: boolean | null;
  delivery_error?: string | null;
  engine_generation?: number | null;
  pending_events?: number;
};

const DEFAULT_PREFS: SystemEventPrefs = {
  enabled: true,
  pause_on_system_sleep: true,
  pause_on_power_off: true,
  pause_on_session_inactive: false,
  pause_on_screen_sleep: false,
  auto_resume_on_wake: true,
  auto_resume_on_session_active: false,
  wake_settle_seconds: 5,
  event_debounce_ms: 500,
};

function isTauri(): boolean {
  return typeof window !== "undefined" && !!(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
}

export async function getSystemEventPrefs(): Promise<SystemEventPrefs> {
  if (!isTauri()) return { ...DEFAULT_PREFS };
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return (await invoke("system_events_get_prefs")) as SystemEventPrefs;
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export async function setSystemEventPrefs(prefs: SystemEventPrefs): Promise<SystemEventPrefs> {
  if (!isTauri()) return prefs;
  const { invoke } = await import("@tauri-apps/api/core");
  return (await invoke("system_events_set_prefs", { prefs })) as SystemEventPrefs;
}

export async function listenSystemState(
  onEvent: (snap: SystemStateSnapshot) => void,
): Promise<() => void> {
  if (!isTauri()) return () => undefined;
  try {
    const { listen } = await import("@tauri-apps/api/event");
    const un = await listen<SystemStateSnapshot>("suying://system-state", (e) => {
      onEvent(e.payload);
    });
    return () => {
      void un();
    };
  } catch {
    return () => undefined;
  }
}

export { DEFAULT_PREFS };
