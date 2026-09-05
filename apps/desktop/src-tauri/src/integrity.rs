use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use ring::signature::{UnparsedPublicKey, ED25519};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};

const PRODUCT_ID: &str = "com.qr.suying";
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
struct RuntimeManifest {
    schema_version: u32,
    product_id: String,
    bundle_version: String,
    arch: String,
    created_at: String,
    key_id: String,
    files: Vec<RuntimeFile>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeFile {
    path: String,
    size: u64,
    sha256: String,
}

fn expected_arch() -> &'static str {
    match std::env::consts::ARCH {
        "aarch64" => "arm64",
        "x86_64" => "x86_64",
        other => other,
    }
}

fn safe_relative_path(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if path.is_absolute()
        || value.contains('\\')
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(format!("runtime manifest 含非法路径: {value}"));
    }
    Ok(path.to_path_buf())
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut source = fs::File::open(path)
        .map_err(|error| format!("无法读取 runtime 文件 {}: {error}", path.display()))?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let read = source
            .read(&mut buffer)
            .map_err(|error| format!("读取 runtime 文件失败 {}: {error}", path.display()))?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(hex::encode(digest.finalize()))
}

pub fn verify_runtime(runtime_root: &Path) -> Result<(), String> {
    let manifest_path = runtime_root.join("RUNTIME_MANIFEST.json");
    let signature_path = runtime_root.join("RUNTIME_MANIFEST.json.sig");
    let payload = fs::read(&manifest_path)
        .map_err(|error| format!("缺少 runtime 完整性清单 {}: {error}", manifest_path.display()))?;
    let signature_text = fs::read_to_string(&signature_path)
        .map_err(|error| format!("缺少 runtime 清单签名 {}: {error}", signature_path.display()))?;
    let manifest: RuntimeManifest = serde_json::from_slice(&payload)
        .map_err(|error| format!("runtime manifest schema 无效: {error}"))?;
    if manifest.schema_version != 1 || manifest.product_id != PRODUCT_ID {
        return Err("runtime manifest 产品或 schema 不匹配".to_string());
    }
    if manifest.arch != expected_arch() {
        return Err(format!(
            "runtime 架构不匹配: expect={}, got={}",
            expected_arch(),
            manifest.arch
        ));
    }
    if manifest.bundle_version.trim().is_empty() || manifest.created_at.trim().is_empty() {
        return Err("runtime manifest 缺少版本或创建时间".to_string());
    }

    let trusted: TrustedKeys = serde_json::from_str(TRUSTED_KEYS_JSON)
        .map_err(|error| format!("内嵌发布公钥无效: {error}"))?;
    if trusted.schema_version != 1 || trusted.product_id != PRODUCT_ID {
        return Err("内嵌发布公钥产品或 schema 不匹配".to_string());
    }
    let encoded_key = trusted
        .keys
        .get(&manifest.key_id)
        .ok_or_else(|| format!("runtime 使用不受信任的发布密钥: {}", manifest.key_id))?;
    let key_bytes = STANDARD
        .decode(encoded_key)
        .map_err(|_| "内嵌 Ed25519 公钥编码无效".to_string())?;
    if key_bytes.len() != 32 {
        return Err("内嵌 Ed25519 公钥长度无效".to_string());
    }
    let signature_bytes = STANDARD
        .decode(signature_text.trim())
        .map_err(|_| "runtime 签名编码无效".to_string())?;
    if signature_bytes.len() != 64 {
        return Err("runtime 签名长度无效".to_string());
    }
    UnparsedPublicKey::new(&ED25519, key_bytes)
        .verify(&payload, &signature_bytes)
        .map_err(|_| "runtime manifest 签名无效".to_string())?;

    let canonical_root = runtime_root
        .canonicalize()
        .map_err(|error| format!("无法解析 runtime 根目录: {error}"))?;
    let mut listed = BTreeSet::new();
    for item in &manifest.files {
        if !listed.insert(item.path.clone()) {
            return Err(format!("runtime manifest 重复文件: {}", item.path));
        }
        // Validate path shape early; existence/hash checked below.
        let _ = safe_relative_path(&item.path)?;
    }
    // Overlay installs (ditto merge) can leave files from an older flavor; only
    // verify signed manifest entries — do not require an exact on-disk file set.
    for item in &manifest.files {
        let relative = safe_relative_path(&item.path)?;
        let path = runtime_root.join(relative);
        let resolved = path
            .canonicalize()
            .map_err(|error| format!("runtime 文件缺失 {}: {error}", item.path))?;
        if !resolved.starts_with(&canonical_root) {
            return Err(format!("runtime 文件越界: {}", item.path));
        }
        let metadata = fs::metadata(&path)
            .map_err(|error| format!("runtime 文件不可读 {}: {error}", item.path))?;
        if !metadata.is_file() || metadata.len() != item.size {
            return Err(format!("runtime 文件大小不匹配: {}", item.path));
        }
        if sha256_file(&path)? != item.sha256 {
            return Err(format!("runtime 文件哈希不匹配: {}", item.path));
        }
    }
    Ok(())
}
