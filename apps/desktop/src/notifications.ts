import {
  isPermissionGranted,
  requestPermission,
} from "@tauri-apps/plugin-notification";

export type NotificationPermissionState = "granted" | "denied" | "unavailable";

export type AlertSoundKind = "ok" | "alert" | "message" | "mario";

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

export function playReachMessageSound(): boolean {
  return playAlertSound("mario");
}

/** Soft watercolor chime (gentle sine stack; no copyrighted samples). */
function playMarioCoin(context: AudioContext, now: number): void {
  const master = context.createGain();
  master.connect(context.destination);
  master.gain.setValueAtTime(0.0001, now);

  const blip = (freq: number, start: number, dur: number, peak: number) => {
    const osc = context.createOscillator();
    const g = context.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, start);
    osc.connect(g);
    g.connect(master);
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(peak, start + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
    osc.start(start);
    osc.stop(start + dur + 0.04);
  };

  // Soft rising chime: A5 → C#6
  blip(880, now, 0.16, 0.07);
  blip(1108.73, now + 0.1, 0.28, 0.055);
  master.gain.exponentialRampToValueAtTime(0.12, now + 0.03);
  master.gain.exponentialRampToValueAtTime(0.0001, now + 0.48);
}

/** Keep in sync with soundBed mute (avoids circular import; prefer cache over LS). */
let soundBedMutedFlag = false;

export function syncAlertSoundMute(muted: boolean): void {
  soundBedMutedFlag = muted;
}

try {
  if (typeof window !== "undefined" && localStorage.getItem("suying.soundBed.muted.v1") === "1") {
    soundBedMutedFlag = true;
  }
} catch {
  /* ignore */
}

/** App 内提示音：跟随系统音量；优先短促双音 / Mario 币音，失败则静默。
 *  受 SoundBed 静音开关约束（设置 → 偏好）。L19 真 wav 试听不走本函数。
 */
export function playAlertSound(kind: AlertSoundKind = "ok"): boolean {
  try {
    if (soundBedMutedFlag) return false;
    try {
      if (localStorage.getItem("suying.soundBed.muted.v1") === "1") return false;
    } catch {
      /* ignore */
    }
    const AudioContextClass =
      window.AudioContext ||
      (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextClass) return false;
    const context = new AudioContextClass();
    const now = context.currentTime;

    if (kind === "mario") {
      playMarioCoin(context, now);
      window.setTimeout(() => void context.close(), 600);
      return true;
    }

    const gain = context.createGain();
    gain.connect(context.destination);
    gain.gain.setValueAtTime(0.0001, now);

    const beep = (freq: number, start: number, dur: number, peak: number) => {
      const osc = context.createOscillator();
      osc.type = kind === "alert" ? "triangle" : "sine";
      osc.frequency.setValueAtTime(freq, start);
      osc.connect(gain);
      gain.gain.exponentialRampToValueAtTime(peak, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);
      osc.start(start);
      osc.stop(start + dur + 0.02);
    };

    if (kind === "alert") {
      beep(480, now, 0.18, 0.1);
      beep(360, now + 0.2, 0.26, 0.08);
    } else if (kind === "message") {
      beep(620, now, 0.32, 0.08);
    } else {
      beep(560, now, 0.14, 0.07);
      beep(740, now + 0.16, 0.22, 0.06);
    }

    window.setTimeout(() => void context.close(), 800);
    return true;
  } catch {
    return false;
  }
}
