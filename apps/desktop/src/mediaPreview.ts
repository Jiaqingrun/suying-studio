import { convertFileSrc } from "@tauri-apps/api/core";
import { outputCoverUrl, outputVideoUrl } from "./api";
import { isTauri } from "./engineControl";

/**
 * Prefer local asset:// URL in Tauri (reliable for <video>).
 * Fall back to engine HTTP stream for browser / when path missing.
 */
export function previewVideoSrc(
  outputId: number,
  localPath?: string | null,
  bust?: string | number | null,
): string {
  const path = (localPath || "").trim();
  if (isTauri() && path) {
    try {
      return convertFileSrc(path);
    } catch {
      /* fall through */
    }
  }
  return outputVideoUrl(outputId, bust);
}

export function previewCoverSrc(
  outputId: number,
  index: number,
  localPath?: string | null,
  bust?: string | number | null,
): string {
  const path = (localPath || "").trim();
  if (isTauri() && path) {
    try {
      return convertFileSrc(path);
    } catch {
      /* fall through */
    }
  }
  return outputCoverUrl(outputId, index, bust);
}
