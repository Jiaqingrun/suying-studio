//! Advanced-settings password gate backed by macOS Keychain.
//! Stores salted SHA-256 verifier only; unlock session lives in process memory.

use serde::Serialize;
use sha2::{Digest, Sha256};
use std::sync::Mutex;
use std::time::{Duration, Instant};

const SERVICE: &str = "com.qr.suying";
const ACCOUNT: &str = "advanced-settings-password";
const UNLOCK_TTL: Duration = Duration::from_secs(15 * 60);
const MAX_FAILS: u32 = 5;
const FAIL_COOLDOWN: Duration = Duration::from_secs(30);

struct UnlockSession {
    until: Instant,
}

struct FailState {
    count: u32,
    locked_until: Option<Instant>,
}

pub struct SettingsLockState {
    session: Mutex<Option<UnlockSession>>,
    fails: Mutex<FailState>,
}

impl Default for SettingsLockState {
    fn default() -> Self {
        Self {
            session: Mutex::new(None),
            fails: Mutex::new(FailState {
                count: 0,
                locked_until: None,
            }),
        }
    }
}

#[derive(Serialize)]
pub struct SettingsPasswordStatus {
    pub configured: bool,
    pub unlocked: bool,
    pub unlocked_remaining_sec: u64,
    pub fail_cooldown_sec: u64,
}

fn keyring_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new(SERVICE, ACCOUNT).map_err(|e| format!("钥匙串不可用: {e}"))
}

fn random_salt() -> [u8; 16] {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let mut salt = [0u8; 16];
    let pid = std::process::id() as u64;
    let mix = nanos ^ pid.wrapping_mul(0x9E3779B97F4A7C15);
    for (i, b) in salt.iter_mut().enumerate() {
        *b = ((mix >> ((i % 8) * 8)) ^ ((i as u64).wrapping_mul(17))) as u8;
    }
    // Extra entropy from stack address
    let addr = &salt as *const _ as usize as u64;
    for (i, b) in salt.iter_mut().enumerate() {
        *b ^= ((addr >> ((i % 8) * 8)) as u8).wrapping_add(i as u8);
    }
    salt
}

fn hash_password(password: &str, salt: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"suying-advanced-v1");
    hasher.update(salt);
    hasher.update(password.as_bytes());
    hex::encode(hasher.finalize())
}

fn encode_record(salt: &[u8], hash_hex: &str) -> String {
    format!("{}:{}", hex::encode(salt), hash_hex)
}

fn decode_record(raw: &str) -> Result<(Vec<u8>, String), String> {
    let (salt_hex, hash_hex) = raw
        .split_once(':')
        .ok_or_else(|| "钥匙串记录损坏，请重新设置高级密码".to_string())?;
    let salt = hex::decode(salt_hex).map_err(|_| "钥匙串盐值损坏".to_string())?;
    if salt.len() < 8 || hash_hex.len() != 64 {
        return Err("钥匙串记录格式无效".into());
    }
    Ok((salt, hash_hex.to_string()))
}

fn read_record() -> Result<Option<String>, String> {
    let entry = keyring_entry()?;
    match entry.get_password() {
        Ok(p) if !p.is_empty() => Ok(Some(p)),
        Ok(_) => Ok(None),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("读取钥匙串失败: {e}")),
    }
}

fn write_record(value: &str) -> Result<(), String> {
    let entry = keyring_entry()?;
    entry
        .set_password(value)
        .map_err(|e| format!("写入钥匙串失败: {e}"))
}

fn delete_record() -> Result<(), String> {
    let entry = keyring_entry()?;
    match entry.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("删除钥匙串失败: {e}")),
    }
}

fn validate_password_strength(password: &str) -> Result<(), String> {
    if password.chars().count() < 6 {
        return Err("密码至少 6 位".into());
    }
    if password.chars().count() > 128 {
        return Err("密码过长".into());
    }
    Ok(())
}

fn check_fail_gate(state: &SettingsLockState) -> Result<(), String> {
    let fails = state.fails.lock().map_err(|_| "锁状态损坏".to_string())?;
    if let Some(until) = fails.locked_until {
        if Instant::now() < until {
            let left = until.saturating_duration_since(Instant::now()).as_secs();
            return Err(format!("尝试过多，请 {left} 秒后再试"));
        }
    }
    Ok(())
}

fn register_fail(state: &SettingsLockState) {
    if let Ok(mut fails) = state.fails.lock() {
        fails.count += 1;
        if fails.count >= MAX_FAILS {
            fails.locked_until = Some(Instant::now() + FAIL_COOLDOWN);
            fails.count = 0;
        }
    }
}

fn clear_fails(state: &SettingsLockState) {
    if let Ok(mut fails) = state.fails.lock() {
        fails.count = 0;
        fails.locked_until = None;
    }
}

fn unlock_now(state: &SettingsLockState) {
    if let Ok(mut session) = state.session.lock() {
        *session = Some(UnlockSession {
            until: Instant::now() + UNLOCK_TTL,
        });
    }
}

fn session_remaining(state: &SettingsLockState) -> u64 {
    let Ok(mut session) = state.session.lock() else {
        return 0;
    };
    match session.as_ref() {
        Some(s) if Instant::now() < s.until => s.until.saturating_duration_since(Instant::now()).as_secs(),
        Some(_) => {
            *session = None;
            0
        }
        None => 0,
    }
}

pub fn status(state: &SettingsLockState) -> Result<SettingsPasswordStatus, String> {
    let configured = read_record()?.is_some();
    let remaining = session_remaining(state);
    let fail_cooldown = {
        let fails = state.fails.lock().map_err(|_| "锁状态损坏".to_string())?;
        fails
            .locked_until
            .map(|u| u.saturating_duration_since(Instant::now()).as_secs())
            .unwrap_or(0)
    };
    Ok(SettingsPasswordStatus {
        configured,
        unlocked: remaining > 0,
        unlocked_remaining_sec: remaining,
        fail_cooldown_sec: fail_cooldown,
    })
}

pub fn create_password(state: &SettingsLockState, password: String) -> Result<SettingsPasswordStatus, String> {
    validate_password_strength(&password)?;
    if read_record()?.is_some() {
        return Err("已设置高级密码，请使用修改密码".into());
    }
    let salt = random_salt();
    let hash = hash_password(&password, &salt);
    write_record(&encode_record(&salt, &hash))?;
    clear_fails(state);
    unlock_now(state);
    status(state)
}

pub fn verify_password(state: &SettingsLockState, password: String) -> Result<SettingsPasswordStatus, String> {
    check_fail_gate(state)?;
    let raw = read_record()?.ok_or_else(|| "尚未设置高级密码".to_string())?;
    let (salt, expected) = decode_record(&raw)?;
    let got = hash_password(&password, &salt);
    if got != expected {
        register_fail(state);
        return Err("密码不正确".into());
    }
    clear_fails(state);
    unlock_now(state);
    status(state)
}

pub fn change_password(
    state: &SettingsLockState,
    old_password: String,
    new_password: String,
) -> Result<SettingsPasswordStatus, String> {
    validate_password_strength(&new_password)?;
    check_fail_gate(state)?;
    let raw = read_record()?.ok_or_else(|| "尚未设置高级密码".to_string())?;
    let (salt, expected) = decode_record(&raw)?;
    let got = hash_password(&old_password, &salt);
    if got != expected {
        register_fail(state);
        return Err("旧密码不正确".into());
    }
    let new_salt = random_salt();
    let new_hash = hash_password(&new_password, &new_salt);
    write_record(&encode_record(&new_salt, &new_hash))?;
    clear_fails(state);
    unlock_now(state);
    status(state)
}

pub fn lock(state: &SettingsLockState) -> Result<SettingsPasswordStatus, String> {
    if let Ok(mut session) = state.session.lock() {
        *session = None;
    }
    status(state)
}

pub fn require_unlocked(state: &SettingsLockState) -> Result<(), String> {
    if session_remaining(state) == 0 {
        return Err("高级配置已锁定，请先验证密码".into());
    }
    // Sliding TTL on successful access
    unlock_now(state);
    Ok(())
}

pub fn clear_password(state: &SettingsLockState, password: String) -> Result<SettingsPasswordStatus, String> {
    check_fail_gate(state)?;
    let raw = read_record()?.ok_or_else(|| "尚未设置高级密码".to_string())?;
    let (salt, expected) = decode_record(&raw)?;
    if hash_password(&password, &salt) != expected {
        register_fail(state);
        return Err("密码不正确".into());
    }
    delete_record()?;
    clear_fails(state);
    if let Ok(mut session) = state.session.lock() {
        *session = None;
    }
    status(state)
}
