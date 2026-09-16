import { convertFileSrc } from "@tauri-apps/api/core";
import { outputCoverUrl, outputVideoUrl } from "./api";
import { isTauri } from "./engineControl";

export type MediaPreviewOpts = {
  /**
   * When false, skip Tauri asset:// (path may be stale / file missing).
   * Default: true when a local path is provided.
   */
  localOk?: boolean;
  /** Force engine HTTP stream (Range + existence checks). */
  preferHttp?: boolean;
};

/**
 * In-app video URL.
 * Prefer local asset:// only when the file is known present; otherwise (or on
 * preferHttp) use engine HTTP so missing/stale paths do not poison <video>.
 */
export function previewVideoSrc(
  outputId: number,
  localPath?: string | null,
  bust?: string | number | null,
  opts?: MediaPreviewOpts,
): string {
  if (opts?.preferHttp) {
    return outputVideoUrl(outputId, bust);
  }
  const path = (localPath || "").trim();
  const allowLocal = opts?.localOk !== false;
  if (allowLocal && isTauri() && path) {
    try {
      return convertFileSrc(path);
    } catch {
      /* fall through */
    }
  }
  return outputVideoUrl(outputId, bust);
}

/** Engine HTTP always — use after asset:// <video>/<img> onError. */
export function previewVideoHttpSrc(
  outputId: number,
  bust?: string | number | null,
): string {
  return outputVideoUrl(outputId, bust);
}

export function previewCoverSrc(
  outputId: number,
  index: number,
  localPath?: string | null,
  bust?: string | number | null,
  opts?: MediaPreviewOpts,
): string {
  if (opts?.preferHttp) {
    return outputCoverUrl(outputId, index, bust);
  }
  const path = (localPath || "").trim();
  const allowLocal = opts?.localOk !== false;
  if (allowLocal && isTauri() && path) {
    try {
      return convertFileSrc(path);
    } catch {
      /* fall through */
    }
  }
  return outputCoverUrl(outputId, index, bust);
}

export function previewCoverHttpSrc(
  outputId: number,
  index: number,
  bust?: string | number | null,
): string {
  return outputCoverUrl(outputId, index, bust);
}
