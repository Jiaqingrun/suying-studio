import { revealItemInDir } from "@tauri-apps/plugin-opener";
import { isTauri } from "./engineControl";

/** Unified “打开文件位置” for customer-facing surfaces. */
export async function openMediaTarget(path: string): Promise<void> {
  const target = String(path || "").trim();
  if (!target) throw new Error("没有可打开的文件路径");
  if (!isTauri()) {
    throw new Error("当前环境无法打开访达，请在桌面 App 中操作");
  }
  await revealItemInDir(target);
}
