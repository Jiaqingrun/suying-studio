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
  listen?: boolean;
  control_plane?: boolean;
  business_ready?: boolean;
  offline_class?: string;
  offline_detail?: string;
  agent_loaded?: boolean;
  agent_plist_exists?: boolean;
};

export type SupervisorSnapshot = {
  agent_label: string;
  agent_plist_exists: boolean;
  agent_loaded: boolean;
  license_cache_ready: boolean;
  wrapper_exists: boolean;
  port: number;
  listen: boolean;
  health_ok: boolean;
  business_ready: boolean;
  offline_class: string;
  offline_detail: string;
  agent_err_tail: string;
  engine_log_tail: string;
};

export async function getEngineStatus(): Promise<EngineStatus> {
  try {
    return await invoke<EngineStatus>("engine_status");
  } catch {
    return {
      running: false,
      healthy: false,
      pid: null,
      repo: "",
      listen: false,
      control_plane: false,
      business_ready: false,
      offline_class: "unknown",
      offline_detail: "",
    };
  }
}

export async function startEngine(): Promise<EngineStatus> {
  return invoke<EngineStatus>("engine_start");
}

export async function stopEngine(): Promise<EngineStatus> {
  return invoke<EngineStatus>("engine_stop");
}

export async function getEngineSupervisor(): Promise<SupervisorSnapshot | null> {
  try {
    return await invoke<SupervisorSnapshot>("engine_supervisor_snapshot");
  } catch {
    return null;
  }
}

/** Human copy for offline_class (engine process / control plane / business). */
export function offlineClassLabel(st: EngineStatus | null | undefined): string {
  const c = st?.offline_class || "";
  switch (c) {
    case "ok":
      return "业务已就绪";
    case "starting":
      return "引擎正在启动";
    case "control_plane_not_ready":
      return "服务在线，业务未就绪";
    case "listen_unhealthy":
      return "端口在听但健康检查失败";
    case "not_listening":
      return "引擎未监听";
    case "no_agent":
      return "未安装开机自启（LaunchAgent）";
    case "license_cache_missing":
      return "许可缓存缺失";
    case "license":
      return "许可证不可用";
    case "workspace":
      return "工作区未就绪";
    case "integrity":
      return "运行时完整性失败";
    default:
      if (st?.control_plane && !st.healthy) return "服务在线，业务未就绪";
      if (st?.running) return "服务状态异常";
      return "设备服务未就绪";
  }
}

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
