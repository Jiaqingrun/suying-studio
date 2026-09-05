use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use ring::rand::{SecureRandom, SystemRandom};
use ring::signature::{UnparsedPublicKey, ED25519};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;

const PRODUCT_ID: &str = "com.qr.suying";
const KEYCHAIN_SERVICE: &str = "com.qr.suying.license";
const DEVICE_SECRET_ACCOUNT: &str = "device-binding-secret-v1";
const ISSUE_SEQ_ACCOUNT: &str = "highest-license-issue-seq";
const TRUSTED_KEYS_JSON: &str =
    include_str!("../../../../engine/security/trusted_release_keys.json");

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TrustedKeys {
    schema_version: u32,
    product_id: String,
    keys: BTreeMap<String, String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LicenseEnvelope {
    payload_b64: String,
    signature: String,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct LicensePayload {
    schema_version: u32,
    product_id: String,
    license_id: String,
    device_key_id: String,
    delivery_id: String,
    customer_ref: String,
    features: Vec<String>,
    issued_at: String,
    issue_seq: u64,
    #[serde(default = "default_perpetual")]
    perpetual: bool,
    #[serde(default = "default_license_kind")]
    license_kind: String,
    #[serde(default)]
    trial_days: Option<u32>,
    #[serde(default)]
    term_days: Option<u32>,
    #[serde(default)]
    daily_produce_cap: Option<u32>,
    #[serde(default)]
    daily_upload_cap: Option<u32>,
    #[serde(default)]
    lock_mode: Option<String>,
    #[serde(default)]
    ops_unlock_allowed: bool,
    #[serde(default)]
    expires_at: Option<String>,
    #[serde(default)]
    clock_anchor: Option<String>,
    key_id: String,
}

fn default_perpetual() -> bool { true }
fn default_license_kind() -> String { "perpetual".to_string() }

#[derive(Serialize)]
pub struct LicenseStatus {
    pub authorized: bool,
    pub development_build: bool,
    pub reason: String,
    pub license_id: Option<String>,
    pub device_key_id: Option<String>,
    pub delivery_id: Option<String>,
    pub customer_ref: Option<String>,
    pub issue_seq: Option<u64>,
    pub features: Vec<String>,
    pub license_kind: String,
    pub expires_at: Option<String>,
    pub trial_remaining_sec: Option<u64>,
    pub remaining_sec: Option<u64>,
    pub ops_unlock_allowed: bool,
    pub code: String,
}

#[derive(Serialize)]
pub struct LicenseRequest {
    pub schema_version: u32,
    pub product_id: &'static str,
    pub device_key_id: String,
    pub machine_hint: String,
    pub created_at_unix: u64,
}

fn security_dir() -> PathBuf {
    dirs_next::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Suying/runtime/security")
}

fn license_path() -> PathBuf {
    security_dir().join("license.suying-license")
}

#[cfg(not(target_os = "macos"))]
fn keyring_entry(account: &str) -> Result<keyring::Entry, String> {
    keyring::Entry::new(KEYCHAIN_SERVICE, account)
        .map_err(|error| format!("许可证钥匙串不可用: {error}"))
}

#[derive(Debug)]
struct SecurityCommandOutput {
    code: Option<i32>,
    stdout: String,
    stderr: String,
}

trait SecurityCommandRunner {
    fn run(&mut self, arguments: &[String]) -> Result<SecurityCommandOutput, String>;
}

struct ProcessSecurityCommandRunner;

impl SecurityCommandRunner for ProcessSecurityCommandRunner {
    fn run(&mut self, arguments: &[String]) -> Result<SecurityCommandOutput, String> {
        let output = Command::new("/usr/bin/security")
            .args(arguments)
            .output()
            .map_err(|error| format!("无法执行 macOS Keychain 命令: {error}"))?;
        Ok(SecurityCommandOutput {
            code: output.status.code(),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        })
    }
}

enum SecurityPassword {
    Missing,
    Present(String),
}

fn security_item_missing(output: &SecurityCommandOutput) -> bool {
    let stderr = output.stderr.to_ascii_lowercase();
    output.code == Some(44)
        || stderr.contains("could not be found")
        || stderr.contains("specified item could not be found")
}

fn security_arguments(operation: &str, account: &str, value: Option<&str>) -> Vec<String> {
    let mut arguments = vec![operation.to_string()];
    if operation == "add-generic-password" {
        arguments.push("-U".to_string());
    }
    arguments.extend([
        "-s".to_string(),
        KEYCHAIN_SERVICE.to_string(),
        "-a".to_string(),
        account.to_string(),
    ]);
    if let Some(password) = value {
        arguments.extend(["-w".to_string(), password.to_string()]);
    } else if operation == "find-generic-password" {
        arguments.push("-w".to_string());
    }
    arguments
}

fn macos_security_password<R: SecurityCommandRunner>(
    runner: &mut R,
    account: &str,
) -> Result<SecurityPassword, String> {
    let password_output =
        runner.run(&security_arguments("find-generic-password", account, None))?;
    if password_output.code == Some(0) {
        let value = password_output.stdout.trim();
        if !value.is_empty() {
            return Ok(SecurityPassword::Present(value.to_string()));
        }
    }

    let exists_arguments = vec![
        "find-generic-password".to_string(),
        "-s".to_string(),
        KEYCHAIN_SERVICE.to_string(),
        "-a".to_string(),
        account.to_string(),
    ];
    let exists_output = runner.run(&exists_arguments)?;
    if exists_output.code == Some(0) {
        return Err(format!(
            "Keychain 条目存在但当前会话无法读取，拒绝覆盖: {account}"
        ));
    }
    if security_item_missing(&exists_output) && security_item_missing(&password_output) {
        return Ok(SecurityPassword::Missing);
    }
    Err(format!(
        "无法确认 Keychain 条目状态，拒绝覆盖 {account}: {}",
        exists_output.stderr.trim()
    ))
}

fn macos_set_security_password<R: SecurityCommandRunner>(
    runner: &mut R,
    account: &str,
    value: &str,
) -> Result<(), String> {
    let output = runner.run(&security_arguments(
        "add-generic-password",
        account,
        Some(value),
    ))?;
    if output.code != Some(0) {
        return Err(format!(
            "无法保存许可证状态到 Keychain: {}",
            output.stderr.trim()
        ));
    }
    match macos_security_password(runner, account)? {
        SecurityPassword::Present(stored) if stored == value => Ok(()),
        SecurityPassword::Present(_) => Err("Keychain 写入后回读值不一致".to_string()),
        SecurityPassword::Missing => Err("Keychain 写入后条目仍不存在".to_string()),
    }
}

fn platform_uuid() -> Result<String, String> {
    let output = Command::new("/usr/sbin/ioreg")
        .args(["-rd1", "-c", "IOPlatformExpertDevice"])
        .output()
        .map_err(|error| format!("无法读取设备身份: {error}"))?;
    if !output.status.success() {
        return Err("无法读取 IOPlatformUUID".to_string());
    }
    let text = String::from_utf8_lossy(&output.stdout);
    for line in text.lines() {
        if line.contains("IOPlatformUUID") {
            let value = line
                .split('=')
                .nth(1)
                .unwrap_or("")
                .trim()
                .trim_matches('"');
            if !value.is_empty() {
                return Ok(value.to_string());
            }
        }
    }
    Err("设备缺少 IOPlatformUUID".to_string())
}

fn decode_device_secret(encoded: &str) -> Result<Vec<u8>, String> {
    let decoded = STANDARD
        .decode(encoded.trim())
        .map_err(|_| "设备密钥编码损坏".to_string())?;
    if decoded.len() != 32 {
        return Err("设备密钥长度损坏".to_string());
    }
    Ok(decoded)
}

fn macos_device_secret_with<R, G>(runner: &mut R, generate: G) -> Result<Vec<u8>, String>
where
    R: SecurityCommandRunner,
    G: FnOnce() -> Result<Vec<u8>, String>,
{
    match macos_security_password(runner, DEVICE_SECRET_ACCOUNT)? {
        SecurityPassword::Present(encoded) => decode_device_secret(&encoded),
        SecurityPassword::Missing => {
            let secret = generate()?;
            if secret.len() != 32 {
                return Err("新生成的设备密钥长度无效".to_string());
            }
            let encoded = STANDARD.encode(&secret);
            macos_set_security_password(runner, DEVICE_SECRET_ACCOUNT, &encoded)?;
            let confirmed = match macos_security_password(runner, DEVICE_SECRET_ACCOUNT)? {
                SecurityPassword::Present(value) => decode_device_secret(&value)?,
                SecurityPassword::Missing => {
                    return Err("设备密钥写入后回读失败".to_string());
                }
            };
            if confirmed != secret {
                return Err("设备密钥写入后回读不一致".to_string());
            }
            Ok(secret)
        }
    }
}

#[cfg(target_os = "macos")]
fn device_secret() -> Result<Vec<u8>, String> {
    macos_device_secret_with(&mut ProcessSecurityCommandRunner, || {
        let mut secret = vec![0_u8; 32];
        SystemRandom::new()
            .fill(&mut secret)
            .map_err(|_| "无法生成设备随机密钥".to_string())?;
        Ok(secret)
    })
}

#[cfg(not(target_os = "macos"))]
fn device_secret() -> Result<Vec<u8>, String> {
    let entry = keyring_entry(DEVICE_SECRET_ACCOUNT)?;
    match entry.get_password() {
        Ok(encoded) => decode_device_secret(&encoded),
        Err(keyring::Error::NoEntry) => {
            let mut secret = [0_u8; 32];
            SystemRandom::new()
                .fill(&mut secret)
                .map_err(|_| "无法生成设备随机密钥".to_string())?;
            entry
                .set_password(&STANDARD.encode(secret))
                .map_err(|error| format!("无法保存设备密钥到 Keychain: {error}"))?;
            let stored = entry
                .get_password()
                .map_err(|error| format!("无法回读设备密钥: {error}"))?;
            if decode_device_secret(&stored)? != secret {
                return Err("设备密钥写入后回读不一致".to_string());
            }
            Ok(secret.to_vec())
        }
        Err(error) => Err(format!(
            "设备密钥条目存在或状态不明但不可读，拒绝覆盖: {error}"
        )),
    }
}

fn device_key_id() -> Result<String, String> {
    let mut digest = Sha256::new();
    digest.update(b"suying-device-binding-v1\0");
    digest.update(platform_uuid()?.as_bytes());
    digest.update(b"\0");
    digest.update(device_secret()?);
    Ok(hex::encode(digest.finalize()))
}

#[cfg(target_os = "macos")]
fn read_issue_seq() -> Result<u64, String> {
    match macos_security_password(&mut ProcessSecurityCommandRunner, ISSUE_SEQ_ACCOUNT)? {
        SecurityPassword::Missing => Ok(0),
        SecurityPassword::Present(value) => value
            .parse::<u64>()
            .map_err(|_| "Keychain 防降级序号损坏".to_string()),
    }
}

#[cfg(not(target_os = "macos"))]
fn read_issue_seq() -> Result<u64, String> {
    match keyring_entry(ISSUE_SEQ_ACCOUNT)?.get_password() {
        Ok(value) => value
            .parse::<u64>()
            .map_err(|_| "Keychain 防降级序号损坏".to_string()),
        Err(keyring::Error::NoEntry) => Ok(0),
        Err(error) => Err(format!("Keychain 防降级序号不可读: {error}")),
    }
}

#[cfg(target_os = "macos")]
fn write_issue_seq(value: u64) -> Result<(), String> {
    macos_set_security_password(
        &mut ProcessSecurityCommandRunner,
        ISSUE_SEQ_ACCOUNT,
        &value.to_string(),
    )
}

#[cfg(not(target_os = "macos"))]
fn write_issue_seq(value: u64) -> Result<(), String> {
    let entry = keyring_entry(ISSUE_SEQ_ACCOUNT)?;
    entry
        .set_password(&value.to_string())
        .map_err(|error| format!("无法保存许可证防降级状态: {error}"))?;
    let stored = entry
        .get_password()
        .map_err(|error| format!("无法回读许可证防降级状态: {error}"))?;
    if stored != value.to_string() {
        return Err("许可证防降级状态写入后回读不一致".to_string());
    }
    Ok(())
}

fn trial_clock_account(license_id: &str) -> String {
    format!("trial-max-seen-v1:{license_id}")
}

#[cfg(target_os = "macos")]
fn read_trial_max_seen(license_id: &str) -> Result<Option<OffsetDateTime>, String> {
    match macos_security_password(
        &mut ProcessSecurityCommandRunner,
        &trial_clock_account(license_id),
    )? {
        SecurityPassword::Missing => Ok(None),
        SecurityPassword::Present(value) => OffsetDateTime::parse(&value, &Rfc3339)
            .map(Some)
            .map_err(|_| "体验期防回拨时钟损坏".to_string()),
    }
}

#[cfg(not(target_os = "macos"))]
fn read_trial_max_seen(license_id: &str) -> Result<Option<OffsetDateTime>, String> {
    match keyring_entry(&trial_clock_account(license_id))?.get_password() {
        Ok(value) => OffsetDateTime::parse(&value, &Rfc3339)
            .map(Some)
            .map_err(|_| "体验期防回拨时钟损坏".to_string()),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(format!("体验期防回拨时钟不可读: {error}")),
    }
}

#[cfg(target_os = "macos")]
fn write_trial_max_seen(license_id: &str, value: OffsetDateTime) -> Result<(), String> {
    let encoded = value
        .format(&Rfc3339)
        .map_err(|_| "体验期防回拨时钟编码失败".to_string())?;
    macos_set_security_password(
        &mut ProcessSecurityCommandRunner,
        &trial_clock_account(license_id),
        &encoded,
    )
}

#[cfg(not(target_os = "macos"))]
fn write_trial_max_seen(license_id: &str, value: OffsetDateTime) -> Result<(), String> {
    let encoded = value
        .format(&Rfc3339)
        .map_err(|_| "体验期防回拨时钟编码失败".to_string())?;
    keyring_entry(&trial_clock_account(license_id))?
        .set_password(&encoded)
        .map_err(|error| format!("无法保存体验期防回拨时钟: {error}"))
}

fn enforce_trial_monotonic_clock(
    license_id: &str,
    now: OffsetDateTime,
) -> Result<(), String> {
    let previous = read_trial_max_seen(license_id)?;
    if trial_clock_update_required(previous, now)? {
        write_trial_max_seen(license_id, now)?;
    }
    Ok(())
}

fn trial_clock_update_required(
    previous: Option<OffsetDateTime>,
    now: OffsetDateTime,
) -> Result<bool, String> {
    let Some(max_seen) = previous else {
        return Ok(true);
    };
    if now + time::Duration::minutes(5) < max_seen {
        return Err("检测到系统时钟回拨，体验期已锁定".to_string());
    }
    Ok(now > max_seen + time::Duration::minutes(1))
}

fn verify_envelope_bytes(bytes: &[u8]) -> Result<LicensePayload, String> {
    let envelope: LicenseEnvelope =
        serde_json::from_slice(bytes).map_err(|error| format!("许可证 envelope 无效: {error}"))?;
    let payload_bytes = STANDARD
        .decode(envelope.payload_b64)
        .map_err(|_| "许可证 payload 编码无效".to_string())?;
    let payload: LicensePayload = serde_json::from_slice(&payload_bytes)
        .map_err(|error| format!("许可证 payload schema 无效: {error}"))?;
    if !matches!(payload.schema_version, 1 | 2)
        || payload.product_id != PRODUCT_ID
        || payload.license_id.trim().is_empty()
        || payload.delivery_id.trim().is_empty()
        || payload.customer_ref.trim().is_empty()
        || payload.features.is_empty()
        || payload.issued_at.trim().is_empty()
    {
        return Err("许可证产品或必填字段无效".to_string());
    }
    let trusted: TrustedKeys = serde_json::from_str(TRUSTED_KEYS_JSON)
        .map_err(|error| format!("内嵌许可公钥无效: {error}"))?;
    if trusted.schema_version != 1 || trusted.product_id != PRODUCT_ID {
        return Err("内嵌许可公钥产品或 schema 不匹配".to_string());
    }
    let encoded_key = trusted
        .keys
        .get(&payload.key_id)
        .ok_or_else(|| format!("许可证使用不受信任的密钥: {}", payload.key_id))?;
    let key = STANDARD
        .decode(encoded_key)
        .map_err(|_| "许可公钥编码无效".to_string())?;
    let signature = STANDARD
        .decode(envelope.signature)
        .map_err(|_| "许可证签名编码无效".to_string())?;
    if key.len() != 32 || signature.len() != 64 {
        return Err("许可证公钥或签名长度无效".to_string());
    }
    UnparsedPublicKey::new(&ED25519, key)
        .verify(&payload_bytes, &signature)
        .map_err(|_| "许可证签名无效".to_string())?;
    if payload.device_key_id != device_key_id()? {
        return Err("许可证不属于本机".to_string());
    }
    let highest = read_issue_seq()?;
    if payload.issue_seq < highest {
        return Err(format!(
            "拒绝许可证降级: issue_seq={} < {highest}",
            payload.issue_seq
        ));
    }
    Ok(payload)
}

fn trial_status(payload: &LicensePayload) -> Result<Option<(String, u64)>, String> {
    // Shared time-bound evaluation for trial + term; perpetual returns Ok(None).
    if payload.license_kind == "perpetual" {
        if !payload.perpetual {
            return Err("许可证永久标记无效".to_string());
        }
        return Ok(None);
    }
    let is_trial = payload.license_kind == "trial";
    let is_term = payload.license_kind == "term";
    if !is_trial && !is_term {
        return Err("未知许可证类型".to_string());
    }
    if payload.perpetual
        || payload.lock_mode.as_deref() != Some("hard_all")
        || !payload.ops_unlock_allowed
    {
        return Err(if is_trial {
            "体验许可证合同字段无效".to_string()
        } else {
            "年期许可证合同字段无效".to_string()
        });
    }
    if is_trial && payload.trial_days != Some(3) {
        return Err("体验许可证合同字段无效".to_string());
    }
    if is_term && payload.term_days != Some(365) {
        return Err("年期许可证合同字段无效".to_string());
    }
    if is_trial
        && payload.schema_version == 1
        && (payload.daily_produce_cap != Some(10) || payload.daily_upload_cap != Some(10))
    {
        return Err("旧版体验许可证合同字段无效".to_string());
    }
    let expires = payload
        .expires_at
        .as_deref()
        .ok_or_else(|| {
            if is_trial {
                "体验许可证缺少 expires_at".to_string()
            } else {
                "年期许可证缺少 expires_at".to_string()
            }
        })?;
    let anchor = payload
        .clock_anchor
        .as_deref()
        .ok_or_else(|| {
            if is_trial {
                "体验许可证缺少 clock_anchor".to_string()
            } else {
                "年期许可证缺少 clock_anchor".to_string()
            }
        })?;
    let expiry = OffsetDateTime::parse(expires, &Rfc3339)
        .map_err(|_| if is_trial { "体验期截止时间无效" } else { "授权截止时间无效" }.to_string())?;
    let anchor = OffsetDateTime::parse(anchor, &Rfc3339)
        .map_err(|_| if is_trial { "体验期时钟锚点无效" } else { "授权时钟锚点无效" }.to_string())?;
    let now = OffsetDateTime::now_utc();
    enforce_trial_monotonic_clock(&payload.license_id, now)?;
    if now < anchor {
        return Err("系统时钟早于授权锚点".to_string());
    }
    if now >= expiry {
        return Err(if is_trial {
            "体验期已结束".to_string()
        } else {
            "授权已到期，请联系运维人员".to_string()
        });
    }
    Ok(Some((
        expires.to_string(),
        (expiry - now).whole_seconds().max(0) as u64,
    )))
}

fn verify_installed() -> Result<LicensePayload, String> {
    let path = license_path();
    // 客户授权 UI 不暴露本机存放路径；缺文件统一文案即可。
    let bytes = fs::read(&path).map_err(|_| "未安装许可证".to_string())?;
    verify_envelope_bytes(&bytes)
}

fn status_from_result(result: Result<LicensePayload, String>) -> LicenseStatus {
    match result {
        Ok(payload) => match trial_status(&payload) {
            Ok(bound) => {
                let remaining = bound.as_ref().map(|(_, remaining)| *remaining);
                LicenseStatus {
                    authorized: true,
                    development_build: false,
                    reason: "authorized".to_string(),
                    license_id: Some(payload.license_id),
                    device_key_id: Some(payload.device_key_id),
                    delivery_id: Some(payload.delivery_id),
                    customer_ref: Some(payload.customer_ref),
                    issue_seq: Some(payload.issue_seq),
                    features: payload.features,
                    license_kind: payload.license_kind.clone(),
                    expires_at: bound.as_ref().map(|(expires_at, _)| expires_at.clone()),
                    trial_remaining_sec: if payload.license_kind == "trial" {
                        remaining
                    } else {
                        None
                    },
                    remaining_sec: remaining,
                    ops_unlock_allowed: payload.ops_unlock_allowed,
                    code: "".to_string(),
                }
            }
            Err(reason) => {
                let code = if reason.contains("到期") || reason.contains("体验期已结束") {
                    if payload.license_kind == "term" {
                        "TERM_EXPIRED"
                    } else {
                        "TRIAL_EXPIRED"
                    }
                } else if reason.contains("时钟") {
                    "CLOCK_ROLLBACK"
                } else {
                    "INVALID"
                }
                .to_string();
                LicenseStatus {
                    authorized: false,
                    development_build: false,
                    reason,
                    license_id: Some(payload.license_id),
                    device_key_id: Some(payload.device_key_id),
                    delivery_id: Some(payload.delivery_id),
                    customer_ref: Some(payload.customer_ref),
                    issue_seq: Some(payload.issue_seq),
                    features: payload.features,
                    license_kind: payload.license_kind,
                    expires_at: payload.expires_at,
                    trial_remaining_sec: Some(0),
                    remaining_sec: Some(0),
                    ops_unlock_allowed: payload.ops_unlock_allowed,
                    code,
                }
            }
        },
        Err(reason) => {
            let code = if reason.contains("不属于本机") {
                "DEVICE_MISMATCH"
            } else if reason.contains("未安装") {
                "MISSING"
            } else {
                "INVALID"
            }
            .to_string();
            LicenseStatus {
                authorized: false,
                development_build: false,
                reason,
                license_id: None,
                device_key_id: device_key_id().ok(),
                delivery_id: None,
                customer_ref: None,
                issue_seq: None,
                features: Vec::new(),
                license_kind: "".to_string(),
                expires_at: None,
                trial_remaining_sec: None,
                remaining_sec: None,
                ops_unlock_allowed: false,
                code,
            }
        }
    }
}

pub fn require_valid_license_or_ops_unlocked(ops_unlocked: bool) -> Result<bool, String> {
    if cfg!(debug_assertions) {
        return Ok(false);
    }
    let payload = verify_installed()?;
    match trial_status(&payload) {
        Ok(_) => Ok(false),
        Err(_error)
            if ops_unlocked
                && matches!(payload.license_kind.as_str(), "trial" | "term")
                && payload.ops_unlock_allowed =>
        {
            Ok(true)
        }
        Err(error) => Err(error),
    }
}

/// After the App has validated the license via Keychain, inject binding into the
/// child engine so Python does not depend on `security -w` (SSH/ACL/reboot races).
pub fn engine_license_env() -> Vec<(String, String)> {
    let mut pairs = vec![
        (
            "SUYING_ALLOW_CACHED_LICENSE_BINDING".to_string(),
            "1".to_string(),
        ),
    ];
    if let Ok(secret) = device_secret() {
        pairs.push((
            "SUYING_DEVICE_SECRET_B64".to_string(),
            STANDARD.encode(secret),
        ));
    }
    pairs
}

pub fn license_status_with_ops(ops_unlocked: bool) -> LicenseStatus {
    let mut status = license_status_inner();
    if !status.authorized
        && matches!(status.license_kind.as_str(), "trial" | "term")
        && status.ops_unlock_allowed
        && ops_unlocked
    {
        status.authorized = true;
        status.reason = if status.license_kind == "term" {
            "term_ops_session_unlocked".to_string()
        } else {
            "trial_ops_session_unlocked".to_string()
        };
        status.code = "".to_string();
    }
    status
}

fn license_status_inner() -> LicenseStatus {
    if cfg!(debug_assertions) {
        return LicenseStatus {
            authorized: true,
            development_build: true,
            reason: "development_build".to_string(),
            license_id: None,
            device_key_id: device_key_id().ok(),
            delivery_id: None,
            customer_ref: None,
            issue_seq: None,
            features: vec!["core".to_string()],
            license_kind: "development".to_string(),
            expires_at: None,
            trial_remaining_sec: None,
            remaining_sec: None,
            ops_unlock_allowed: false,
            code: "".to_string(),
        };
    }
    status_from_result(verify_installed())
}

#[tauri::command]
pub fn license_request() -> Result<LicenseRequest, String> {
    let id = device_key_id()?;
    Ok(LicenseRequest {
        schema_version: 1,
        product_id: PRODUCT_ID,
        machine_hint: id.chars().take(12).collect(),
        device_key_id: id,
        created_at_unix: SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|value| value.as_secs())
            .unwrap_or(0),
    })
}

#[tauri::command]
pub fn license_install_path(path: String) -> Result<LicenseStatus, String> {
    let source = Path::new(&path);
    if !source.is_file() {
        return Err("许可证文件不存在".to_string());
    }
    let bytes = fs::read(source).map_err(|error| format!("无法读取许可证: {error}"))?;
    let payload = verify_envelope_bytes(&bytes)?;
    let target = license_path();
    fs::create_dir_all(target.parent().ok_or("许可证目录无效")?)
        .map_err(|error| format!("无法创建许可证目录: {error}"))?;
    let temp = target.with_extension(format!("tmp-{}", std::process::id()));
    fs::write(&temp, &bytes).map_err(|error| format!("无法暂存许可证: {error}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temp, fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("无法设置许可证权限: {error}"))?;
    }
    let previous_seq = read_issue_seq()?;
    write_issue_seq(payload.issue_seq)?;
    if let Err(error) = fs::rename(&temp, &target) {
        let _ = write_issue_seq(previous_seq);
        let _ = fs::remove_file(&temp);
        return Err(format!("无法安装许可证: {error}"));
    }
    Ok(status_from_result(Ok(payload)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    struct MockSecurityRunner {
        outputs: VecDeque<Result<SecurityCommandOutput, String>>,
        calls: Vec<Vec<String>>,
    }

    impl MockSecurityRunner {
        fn new(outputs: Vec<SecurityCommandOutput>) -> Self {
            Self {
                outputs: outputs.into_iter().map(Ok).collect(),
                calls: Vec::new(),
            }
        }
    }

    impl SecurityCommandRunner for MockSecurityRunner {
        fn run(&mut self, arguments: &[String]) -> Result<SecurityCommandOutput, String> {
            self.calls.push(arguments.to_vec());
            self.outputs
                .pop_front()
                .unwrap_or_else(|| Err("unexpected security command".to_string()))
        }
    }

    fn output(code: i32, stdout: &str, stderr: &str) -> SecurityCommandOutput {
        SecurityCommandOutput {
            code: Some(code),
            stdout: stdout.to_string(),
            stderr: stderr.to_string(),
        }
    }

    #[test]
    fn device_secret_read_is_stable_and_does_not_write() {
        let secret = vec![7_u8; 32];
        let encoded = STANDARD.encode(&secret);
        let mut runner = MockSecurityRunner::new(vec![output(0, &(encoded + "\n"), "")]);

        let first =
            macos_device_secret_with(&mut runner, || panic!("existing secret must not generate"))
                .unwrap();

        assert_eq!(first, secret);
        assert_eq!(runner.calls.len(), 1);
        assert_eq!(runner.calls[0][0], "find-generic-password");
    }

    #[test]
    fn unreadable_existing_device_secret_fails_closed_without_write() {
        let mut runner = MockSecurityRunner::new(vec![
            output(36, "", "User interaction is not allowed."),
            output(0, "", ""),
        ]);

        let error = macos_device_secret_with(&mut runner, || {
            panic!("unreadable secret must not generate")
        })
        .unwrap_err();

        assert!(error.contains("存在但当前会话无法读取"));
        assert_eq!(runner.calls.len(), 2);
        assert!(runner
            .calls
            .iter()
            .all(|arguments| arguments[0] == "find-generic-password"));
    }

    #[test]
    fn missing_device_secret_generates_once_and_confirms_write() {
        let secret = vec![9_u8; 32];
        let encoded = STANDARD.encode(&secret);
        let missing = "The specified item could not be found in the keychain.";
        let mut runner = MockSecurityRunner::new(vec![
            output(44, "", missing),
            output(44, "", missing),
            output(0, "", ""),
            output(0, &(encoded.clone() + "\n"), ""),
            output(0, &(encoded + "\n"), ""),
        ]);

        let actual = macos_device_secret_with(&mut runner, || Ok(secret.clone())).unwrap();

        assert_eq!(actual, secret);
        assert_eq!(
            runner
                .calls
                .iter()
                .filter(|arguments| arguments[0] == "add-generic-password")
                .count(),
            1
        );
    }

    #[test]
    fn trial_clock_rejects_material_rollback_and_tolerates_small_adjustment() {
        let max_seen = OffsetDateTime::from_unix_timestamp(1_800_000_000).unwrap();
        assert!(trial_clock_update_required(
            Some(max_seen),
            max_seen - time::Duration::minutes(6)
        )
        .is_err());
        assert!(!trial_clock_update_required(
            Some(max_seen),
            max_seen - time::Duration::minutes(2)
        )
        .unwrap());
        assert!(trial_clock_update_required(
            Some(max_seen),
            max_seen + time::Duration::minutes(2)
        )
        .unwrap());
    }
}
