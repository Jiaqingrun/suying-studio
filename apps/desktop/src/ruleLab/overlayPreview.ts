import { convertFileSrc } from "@tauri-apps/api/core";
import { isTauri } from "../engineControl";

/** Absolute filesystem path from mask/sticker source id. */
export function overlayFilePath(source: string): string | null {
  const s = String(source || "").trim();
  if (!s) return null;
  if (s.startsWith("user:")) {
    const p = s.slice(5).trim();
    return p || null;
  }
  if (s.startsWith("/") && /\.(png|jpe?g|webp|gif)$/i.test(s)) return s;
  return null;
}

/** URL usable in <img> for a local overlay file (Tauri asset protocol). */
export function overlayPreviewSrc(source: string): string | null {
  const path = overlayFilePath(source);
  if (!path) return null;
  if (isTauri()) {
    try {
      return convertFileSrc(path);
    } catch {
      return null;
    }
  }
  // Browser preview: may be blocked by CSP; still useful in some local hosts.
  return `file://${encodeURI(path)}`;
}

export function isGeomMask(source: string): boolean {
  return String(source || "").startsWith("geom_");
}

export function isEmojiSticker(source: string): boolean {
  const s = String(source || "");
  return s === "twemoji" || s === "emoji" || s.startsWith("twemoji:") || s.startsWith("emoji:");
}

export function emojiGlyph(source: string): string {
  const s = String(source || "");
  if (s.includes(":")) return s.split(":").slice(1).join(":") || "✦";
  return "✦";
}
