import { invoke } from "@tauri-apps/api/core";

export type EngineStatus = {
  running: boolean;
  healthy: boolean;
  pid?: number | null;
  repo: string;
  python?: string;
  message?: string | null;
  /** true when engine code is inside the .app bundle */
  bundled?: boolean;
};

export async function getEngineStatus(): Promise<EngineStatus> {
  try {
    return await invoke<EngineStatus>("engine_status");
  } catch {
    return { running: false, healthy: false, pid: null, repo: "" };
  }
}

export async function startEngine(): Promise<EngineStatus> {
  return invoke<EngineStatus>("engine_start");
}

export async function stopEngine(): Promise<EngineStatus> {
  return invoke<EngineStatus>("engine_stop");
}

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
