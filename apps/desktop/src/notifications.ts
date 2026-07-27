import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";

export type NotificationPermissionState = "granted" | "denied" | "unavailable";

function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export async function getNotificationPermission(): Promise<NotificationPermissionState> {
  if (!isTauriRuntime()) return "unavailable";
  try {
    return (await isPermissionGranted()) ? "granted" : "unavailable";
  } catch {
    return "unavailable";
  }
}

export async function ensureNotificationPermission(): Promise<NotificationPermissionState> {
  if (!isTauriRuntime()) return "unavailable";
  try {
    if (await isPermissionGranted()) return "granted";
    return (await requestPermission()) === "granted" ? "granted" : "denied";
  } catch {
    return "unavailable";
  }
}

export async function notifyReachMessage(title: string, body: string): Promise<boolean> {
  if ((await ensureNotificationPermission()) !== "granted") return false;
  sendNotification({
    title: title.trim() || "速影 · 新消息",
    body: body.trim().slice(0, 500) || "有新的平台消息，请进入触达助手查看。",
    sound: "default",
  });
  return true;
}

export function playReachMessageSound(): boolean {
  try {
    const AudioContextClass =
      window.AudioContext ||
      (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextClass) return false;
    const context = new AudioContextClass();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(740, context.currentTime);
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.16, context.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.32);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.34);
    oscillator.addEventListener("ended", () => void context.close());
    return true;
  } catch {
    return false;
  }
}
