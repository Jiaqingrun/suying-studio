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
  hash: "",
};

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

export async function settingsPasswordCreate(password: string): Promise<SettingsPasswordStatus> {
  if (!isTauri()) {
    if (password.length < 6) throw new Error("密码至少 6 位");
    memoryFallback.configured = true;
    memoryFallback.unlocked = true;
    memoryFallback.hash = password;
    return browserStatus();
  }
  return invoke<SettingsPasswordStatus>("settings_password_create", { password });
}

export async function settingsPasswordVerify(password: string): Promise<SettingsPasswordStatus> {
  if (!isTauri()) {
    if (!memoryFallback.configured) throw new Error("尚未设置高级密码");
    if (password !== memoryFallback.hash) throw new Error("密码不正确");
    memoryFallback.unlocked = true;
    return browserStatus();
  }
  return invoke<SettingsPasswordStatus>("settings_password_verify", { password });
}

export async function settingsPasswordChange(
  oldPassword: string,
  newPassword: string,
): Promise<SettingsPasswordStatus> {
  if (!isTauri()) {
    if (oldPassword !== memoryFallback.hash) throw new Error("旧密码不正确");
    if (newPassword.length < 6) throw new Error("密码至少 6 位");
    memoryFallback.hash = newPassword;
    memoryFallback.unlocked = true;
    return browserStatus();
  }
  return invoke<SettingsPasswordStatus>("settings_password_change", {
    oldPassword,
    newPassword,
  });
}

export async function settingsPasswordLock(): Promise<SettingsPasswordStatus> {
  if (!isTauri()) {
    memoryFallback.unlocked = false;
    return browserStatus();
  }
  return invoke<SettingsPasswordStatus>("settings_password_lock");
}

export async function settingsPasswordClear(password: string): Promise<SettingsPasswordStatus> {
  if (!isTauri()) {
    if (password !== memoryFallback.hash) throw new Error("密码不正确");
    memoryFallback.configured = false;
    memoryFallback.unlocked = false;
    memoryFallback.hash = "";
    return browserStatus();
  }
  return invoke<SettingsPasswordStatus>("settings_password_clear", { password });
}

export async function settingsAdvancedRequireUnlocked(): Promise<void> {
  if (!isTauri()) {
    if (!memoryFallback.unlocked) throw new Error("高级配置已锁定，请先验证密码");
    return;
  }
  return invoke("settings_advanced_require_unlocked");
}
