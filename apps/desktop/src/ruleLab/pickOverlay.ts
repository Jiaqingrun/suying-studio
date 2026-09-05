import { open } from "@tauri-apps/plugin-dialog";
import { isTauri } from "../engineControl";

/** Pick a local PNG/WebP/JPEG for mask or sticker overlay. */
export async function pickOverlayImage(title = "选择图片"): Promise<string | null> {
  if (!isTauri()) {
    const path = window.prompt(`${title}（输入本机绝对路径）`, "");
    return path && path.trim() ? path.trim() : null;
  }
  const selected = await open({
    multiple: false,
    title,
    filters: [{ name: "图片", extensions: ["png", "webp", "jpg", "jpeg"] }],
  });
  return typeof selected === "string" ? selected : null;
}

export function toUserSource(path: string): string {
  const p = path.trim();
  if (!p) return p;
  if (p.startsWith("user:")) return p;
  return `user:${p}`;
}
