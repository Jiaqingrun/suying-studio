import { invoke } from "@tauri-apps/api/core";
import { isTauri } from "./engineControl";

export type SettingsPasswordStatus = {
  configured: boolean;
  unlocked: boolean;
  unlocked_remaining_sec: number;
  fail_cooldown_sec: number;
};

const memoryFallback = {
  configured: false,
  unlocked: false,
  /** Dev-only salted verifier — never store plaintext. */
  salt: "",
  hash: "",
};

async function browserHash(password: string, salt: string): Promise<string> {
  const enc = new TextEncoder();
  const data = enc.encode(`suying-advanced-v1:${salt}:${password}`);
  const dig = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(dig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function browserStatus(): SettingsPasswordStatus {
  return {
    configured: memoryFallback.configured,
    unlocked: memoryFallback.unlocked,
    unlocked_remaining_sec: memoryFallback.unlocked ? 900 : 0,
    fail_cooldown_sec: 0,
  };
}

export async function settingsPasswordStatus(): Promise<SettingsPasswordStatus> {
  if (!isTauri()) return browserStatus();
  return invoke<SettingsPasswordStatus>("settings_password_status");
}

export async function settingsPasswordVerify(password: string): Promise<SettingsPasswordStatus> {
  if (!isTauri()) {
    if (!memoryFallback.configured) throw new Error("尚未设置高级密码");
    const got = await browserHash(password, memoryFallback.salt);
    if (got !== memoryFallback.hash) throw new Error("密码不正确");
    memoryFallback.unlocked = true;
    return browserStatus();
  }
  return invoke<SettingsPasswordStatus>("settings_password_verify", { password });
}

export async function settingsPasswordLock(): Promise<SettingsPasswordStatus> {
  if (!isTauri()) {
    memoryFallback.unlocked = false;
    return browserStatus();
  }
  return invoke<SettingsPasswordStatus>("settings_password_lock");
}

export async function settingsPasswordChange(password: string): Promise<SettingsPasswordStatus> {
  if (!isTauri()) throw new Error("浏览器预览不能修改高级密码");
  return invoke<SettingsPasswordStatus>("settings_password_change", { password });
}

export async function settingsOperationToken(): Promise<string> {
  if (!isTauri()) {
    if (!memoryFallback.unlocked) throw new Error("高级功能已锁定，请先验证密码");
    return "browser-preview";
  }
  return invoke<string>("settings_operation_token");
}
