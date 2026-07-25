/** Best-effort file drag for webviews (DownloadURL / uri-list). Prefer Finder reveal. */

import type { DragEvent } from "react";
import { outputVideoUrl } from "./api";

export function bindOutputFileDrag(
  e: DragEvent,
  opts: { id: number; path: string; mediaOk?: boolean },
): void {
  if (!opts.path || opts.mediaOk === false) return;
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
