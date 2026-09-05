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

/** Super Mario coin–style blip via Web Audio (no copyrighted samples). */
function playMarioCoin(context: AudioContext, now: number): void {
  const master = context.createGain();
  master.connect(context.destination);
  master.gain.setValueAtTime(0.0001, now);

  const blip = (freq: number, start: number, dur: number, peak: number) => {
    const osc = context.createOscillator();
    const g = context.createGain();
    osc.type = "square";
    osc.frequency.setValueAtTime(freq, start);
    osc.connect(g);
    g.connect(master);
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(peak, start + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, start + dur);
    osc.start(start);
    osc.stop(start + dur + 0.02);
  };

  // Classic coin-ish jump: B5 → E6
  blip(987.77, now, 0.09, 0.14);
  blip(1318.51, now + 0.08, 0.22, 0.12);
  master.gain.exponentialRampToValueAtTime(0.2, now + 0.02);
  master.gain.exponentialRampToValueAtTime(0.0001, now + 0.38);
}

/** App 内提示音：跟随系统音量；优先短促双音 / Mario 币音，失败则静默。 */
export function playAlertSound(kind: AlertSoundKind = "ok"): boolean {
  try {
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
      beep(520, now, 0.16, 0.18);
      beep(380, now + 0.18, 0.22, 0.16);
    } else if (kind === "message") {
      beep(740, now, 0.28, 0.16);
    } else {
      beep(660, now, 0.12, 0.14);
      beep(880, now + 0.14, 0.18, 0.12);
    }

    window.setTimeout(() => void context.close(), 800);
    return true;
  } catch {
    return false;
  }
}
