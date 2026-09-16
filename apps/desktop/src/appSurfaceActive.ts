/**
 * P0.3 — App surface activity for control-plane polls.
 *
 * macOS Tauri minimize / blur often leaves `document.visibilityState === "visible"`.
 * Active only when: document visible AND not minimized AND focused.
 * Does **not** thaw FrozenTab; only gates poll ticks.
 *
 * See docs/APP_POLL_BUDGET.md · perf plan P0.3.
 */

type Listener = () => void;

let docVisible =
  typeof document === "undefined" ? true : document.visibilityState === "visible";
let winMinimized = false;
let winFocused = true;
let installed = false;
const listeners = new Set<Listener>();

function isTauri(): boolean {
  return (
    typeof window !== "undefined" &&
    !!(window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
  );
}

export function isAppSurfaceActive(): boolean {
  return docVisible && !winMinimized && winFocused;
}

export function subscribeAppSurfaceActive(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emitIfChanged(before: boolean): void {
  if (isAppSurfaceActive() !== before) {
    for (const listener of listeners) listener();
  }
}

async function refreshTauriFlags(): Promise<void> {
  if (!isTauri()) return;
  try {
    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    const w = getCurrentWindow();
    const before = isAppSurfaceActive();
    const [minimized, focused] = await Promise.all([w.isMinimized(), w.isFocused()]);
    winMinimized = minimized;
    winFocused = focused;
    emitIfChanged(before);
  } catch {
    // Dev / non-window contexts: keep last known flags.
  }
}

/** Apply flags from Rust `suying://app-surface` (minimize/focus bridge). */
export function applyNativeAppSurface(flags: {
  minimized?: boolean;
  focused?: boolean;
}): void {
  const before = isAppSurfaceActive();
  if (typeof flags.minimized === "boolean") winMinimized = flags.minimized;
  if (typeof flags.focused === "boolean") winFocused = flags.focused;
  emitIfChanged(before);
}

/**
 * Install document + Tauri listeners once. Safe to call from App mount.
 * Returns disposer (tests / HMR).
 */
export async function installAppSurfaceBridge(): Promise<() => void> {
  if (installed) return () => undefined;
  installed = true;

  const onVis = () => {
    const before = isAppSurfaceActive();
    docVisible = document.visibilityState === "visible";
    emitIfChanged(before);
    void refreshTauriFlags();
  };
  document.addEventListener("visibilitychange", onVis);

  const unsubs: Array<() => void> = [];

  if (isTauri()) {
    try {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      const { listen } = await import("@tauri-apps/api/event");
      const w = getCurrentWindow();
      await refreshTauriFlags();

      unsubs.push(
        await w.onFocusChanged(({ payload: focused }) => {
          const before = isAppSurfaceActive();
          winFocused = focused;
          emitIfChanged(before);
          void refreshTauriFlags();
        }),
      );
      unsubs.push(
        await w.onResized(() => {
          void refreshTauriFlags();
        }),
      );
      unsubs.push(
        await listen<{ minimized?: boolean; focused?: boolean }>("suying://app-surface", (e) => {
          applyNativeAppSurface(e.payload || {});
        }),
      );
    } catch {
      // Keep document.visibilityState fallback.
    }
  }

  return () => {
    document.removeEventListener("visibilitychange", onVis);
    for (const u of unsubs) u();
    installed = false;
  };
}

/** Test helper — reset module state between vitest cases. */
export function __resetAppSurfaceForTests(state?: {
  docVisible?: boolean;
  winMinimized?: boolean;
  winFocused?: boolean;
}): void {
  docVisible = state?.docVisible ?? true;
  winMinimized = state?.winMinimized ?? false;
  winFocused = state?.winFocused ?? true;
  listeners.clear();
  installed = false;
}
