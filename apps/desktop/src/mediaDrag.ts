/** File drag for publish/review: Tauri = real OS file; browser preview = best-effort URL. */

import type { DragEvent, PointerEvent } from "react";
import { invoke } from "@tauri-apps/api/core";
import { outputVideoUrl } from "./api";
import { isTauri } from "./engineControl";

export type OutputDragOpts = {
  id: number;
  path: string;
  mediaOk?: boolean;
  onError?: (message: string) => void;
};

const DRAG_THRESHOLD_PX = 6;

/** Start native OS file drag (returns once session starts). External browsers get a real .mp4. */
async function startNativeOutputFileDrag(path: string): Promise<void> {
  const target = String(path || "").trim();
  if (!target) throw new Error("无本地成片路径");
  if (!isTauri()) {
    throw new Error("请使用桌面 App 拖拽，或点「访达中显示」再拖文件");
  }
  await invoke("start_file_drag", { path: target });
}

/**
 * HTML5 dragStart (browser / non-native fallback).
 * On Tauri: cancel WebView fake MIME — those land as non-files and platforms reject format.
 */
export function bindOutputFileDrag(e: DragEvent, opts: OutputDragOpts): void {
  if (!opts.path || opts.mediaOk === false) return;

  if (isTauri()) {
    // WebView cannot put a real File onto the system pasteboard via setData.
    e.preventDefault();
    e.stopPropagation();
    return;
  }

  const name = opts.path.split("/").pop() || "video.mp4";
  const url = outputVideoUrl(opts.id);
  try {
    e.dataTransfer.setData("DownloadURL", `video/mp4:${name}:${url}`);
    e.dataTransfer.setData("text/uri-list", url);
    e.dataTransfer.setData("text/plain", opts.path);
    e.dataTransfer.effectAllowed = "copy";
  } catch {
    /* ignore */
  }
}

/**
 * Tauri: arm native file drag after a short pointer move (avoids click-to-drag glitches).
 * Zone should set draggable={false} in App.
 */
export function bindOutputFilePointerDown(e: PointerEvent, opts: OutputDragOpts): void {
  if (!isTauri()) return;
  if (e.button !== 0) return;
  if (!opts.path || opts.mediaOk === false) return;
  const t = e.target as HTMLElement | null;
  if (t?.closest?.("video") && !t.closest?.(".publish-drag-zone")) return;

  const el = e.currentTarget as HTMLElement | null;
  if (!el) return;

  const startX = e.clientX;
  const startY = e.clientY;
  const path = opts.path;
  const onError = opts.onError;
  let started = false;
  const pointerId = e.pointerId;

  try {
    el.setPointerCapture(pointerId);
  } catch {
    /* ignore */
  }

  const cleanup = () => {
    el.removeEventListener("pointermove", onMove);
    el.removeEventListener("pointerup", onEnd);
    el.removeEventListener("pointercancel", onEnd);
    try {
      el.releasePointerCapture(pointerId);
    } catch {
      /* ignore */
    }
  };

  const onMove = (ev: globalThis.PointerEvent) => {
    if (started) return;
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (dx * dx + dy * dy < DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) return;
    started = true;
    cleanup();
    void startNativeOutputFileDrag(path).catch((err) => {
      onError?.(String(err));
    });
  };

  const onEnd = () => {
    cleanup();
  };

  el.addEventListener("pointermove", onMove);
  el.addEventListener("pointerup", onEnd);
  el.addEventListener("pointercancel", onEnd);
}

/** Whether the drag zone should use HTML5 draggable attribute. */
export function outputDragHtmlEnabled(): boolean {
  return !isTauri();
}
