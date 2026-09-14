/**
 * SoundBed — UI 沉浸提示音（切页 / 成功失败 / 消息 / 产线跃迁）。
 * 默认开启；设置可静音。不拦截 L19 真 wav 试听（那条走 preview API，不经本模块）。
 */
import { playAlertSound, type AlertSoundKind } from "./notifications";

const MUTE_KEY = "suying.soundBed.muted.v1";

export type SoundBedEvent =
  | "tab"
  | "ok"
  | "alert"
  | "message"
  | "pipeline"
  | "celebrate";

function readMuted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

let mutedCache = typeof window !== "undefined" ? readMuted() : false;

export function isSoundBedMuted(): boolean {
  return mutedCache;
}

export function setSoundBedMuted(muted: boolean): void {
  mutedCache = muted;
  try {
    localStorage.setItem(MUTE_KEY, muted ? "1" : "0");
  } catch {
    /* ignore quota */
  }
  window.dispatchEvent(new CustomEvent("suying:soundbed", { detail: { muted } }));
}

export function useSoundBedMutedSync(onChange: (muted: boolean) => void): () => void {
  const handler = () => onChange(isSoundBedMuted());
  window.addEventListener("suying:soundbed", handler);
  return () => window.removeEventListener("suying:soundbed", handler);
}

const EVENT_KIND: Record<SoundBedEvent, AlertSoundKind> = {
  tab: "message",
  ok: "ok",
  alert: "alert",
  message: "mario",
  pipeline: "ok",
  celebrate: "mario",
};

/** 播放 SoundBed 事件；静音时立即返回 false。 */
export function playSoundBed(event: SoundBedEvent): boolean {
  if (isSoundBedMuted()) return false;
  return playAlertSound(EVENT_KIND[event]);
}
