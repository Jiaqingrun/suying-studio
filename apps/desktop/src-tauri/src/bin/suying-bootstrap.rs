use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use ring::signature::{UnparsedPublicKey, ED25519};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};

const PRODUCT_ID: &str = "com.qr.suying";
const TRUSTED_KEYS_JSON: &str =
    include_str!("../../../../../engine/security/trusted_release_keys.json");

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TrustedKeys {
    schema_version: u32,
    product_id: String,
    keys: BTreeMap<String, String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DepotManifest {
    schema_version: u32,
    product_id: String,
    depot_seq: u64,
    created_at: String,
    key_id: String,
    objects: BTreeMap<String, DepotObject>,
    components: Vec<DepotComponent>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DepotObject {
    size: u64,
    sha256: String,
    object_path: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DepotComponent {
    component_id: String,
    kind: String,
    arch: String,
    files: BTreeMap<String, String>,
    executable_files: Vec<String>,
}

#[derive(Deserialize)]
struct LegalManifest {
    schema_version: String,
    distribution_status: String,
    covered_components: Vec<String>,
    packages: Vec<LegalPackage>,
    gaps: Vec<serde_json::Value>,
}

#[derive(Deserialize)]
struct LegalPackage {
    name: String,
    version: String,
    license_expression: String,
    source_url: String,
    component_ids: Vec<String>,
    license_paths: Vec<String>,
    source_required: bool,
    corresponding_source_paths: Vec<String>,
}

#[derive(Serialize)]
struct InstallPlan {
    ok: bool,
    depot_seq: u64,
    arch: String,
    profile: String,
    required_bytes: u64,
    workspace: String,
    components: BTreeMap<String, String>,
    offline_only: bool,
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file =
        fs::File::open(path).map_err(|error| format!("读取失败 {}: {error}", path.display()))?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|error| error.to_string())?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(hex::encode(digest.finalize()))
}

fn safe_relative(value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if value.is_empty()
        || path.is_absolute()
        || value.contains('\\')
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(format!("非法相对路径: {value}"));
    }
    Ok(path.to_path_buf())
}

fn valid_component_id(value: &str) -> bool {
    (2..=64).contains(&value.len())
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || (index > 0 && b"._-".contains(&byte))
        })
}

#[cfg(unix)]
fn available_bytes(path: &Path) -> Result<u64, String> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let raw =
        CString::new(path.as_os_str().as_bytes()).map_err(|_| "磁盘路径包含 NUL".to_string())?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    let result = unsafe { libc::statvfs(raw.as_ptr(), stats.as_mut_ptr()) };
    if result != 0 {
        return Err(format!(
            "读取磁盘空间失败: {}",
            std::io::Error::last_os_error()
        ));
    }
    let stats = unsafe { stats.assume_init() };
    Ok(u64::from(stats.f_bavail).saturating_mul(stats.f_frsize))
}

fn verify_depot(root: &Path) -> Result<DepotManifest, String> {
    let payload =
        fs::read(root.join("depot.json")).map_err(|error| format!("缺少 depot.json: {error}"))?;
    let signature = fs::read_to_string(root.join("depot.json.sig"))
        .map_err(|error| format!("缺少 depot.json.sig: {error}"))?;
    let manifest: DepotManifest =
        serde_json::from_slice(&payload).map_err(|error| format!("depot schema 无效: {error}"))?;
    if manifest.schema_version != 1
        || manifest.product_id != PRODUCT_ID
        || manifest.created_at.is_empty()
    {
        return Err("depot 产品或 schema 无效".to_string());
    }
    let trusted: TrustedKeys = serde_json::from_str(TRUSTED_KEYS_JSON)
        .map_err(|error| format!("内嵌公钥无效: {error}"))?;
    if trusted.schema_version != 1 || trusted.product_id != PRODUCT_ID {
        return Err("内嵌公钥产品不匹配".to_string());
    }
    let key = STANDARD
        .decode(
            trusted
                .keys
                .get(&manifest.key_id)
                .ok_or("depot 使用不受信任的密钥")?,
        )
        .map_err(|_| "公钥编码无效".to_string())?;
    let sig = STANDARD
        .decode(signature.trim())
        .map_err(|_| "depot 签名编码无效".to_string())?;
    UnparsedPublicKey::new(&ED25519, key)
        .verify(&payload, &sig)
        .map_err(|_| "depot 签名无效".to_string())?;

    let mut referenced = BTreeSet::new();
    let mut component_ids = BTreeSet::new();
    for component in &manifest.components {
        if !valid_component_id(&component.component_id)
            || !component_ids.insert(component.component_id.clone())
            || component.kind.is_empty()
            || !component
                .executable_files
                .iter()
                .all(|path| component.files.contains_key(path))
        {
            return Err(format!("组件 schema 无效: {}", component.component_id));
        }
        for digest in component.files.values() {
            if !manifest.objects.contains_key(digest) {
                return Err(format!("组件引用缺失对象: {digest}"));
            }
            referenced.insert(digest.clone());
        }
    }
    if referenced != manifest.objects.keys().cloned().collect() {
        return Err("depot CAS 引用不闭合".to_string());
    }
    let object_dir = root.join("objects/sha256");
    let actual_objects: BTreeSet<String> = fs::read_dir(&object_dir)
        .map_err(|error| format!("读取 CAS 对象目录失败: {error}"))?
        .filter_map(|entry| entry.ok())
        .filter(|entry| entry.path().is_file())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    if actual_objects != manifest.objects.keys().cloned().collect() {
        return Err("depot CAS 文件集合与签名清单不一致".to_string());
    }
    for (digest, object) in &manifest.objects {
        if object.sha256 != *digest || object.object_path != format!("objects/sha256/{digest}") {
            return Err(format!("CAS 对象键不匹配: {digest}"));
        }
        let relative = safe_relative(&object.object_path)?;
        let path = root.join(relative);
        if path.is_symlink()
            || !path.is_file()
            || fs::metadata(&path)
                .map_err(|error| error.to_string())?
                .len()
                != object.size
            || sha256_file(&path)? != *digest
        {
            return Err(format!("CAS 对象损坏: {digest}"));
        }
    }
    verify_legal_closure(root, &manifest)?;
    Ok(manifest)
}

fn verify_legal_closure(root: &Path, manifest: &DepotManifest) -> Result<(), String> {
    let legal_components: Vec<&DepotComponent> = manifest
        .components
        .iter()
        .filter(|item| item.component_id == "legal" || item.kind == "legal")
        .collect();
    if legal_components.len() != 1 {
        return Err("外部分发离线仓必须且只能包含一个 legal 组件".to_string());
    }
    let legal = legal_components[0];
    if legal.component_id != "legal" || legal.kind != "legal" || legal.arch != "any" {
        return Err("legal 组件 schema 无效".to_string());
    }
    for required in ["THIRD_PARTY_MANIFEST.json", "DELIVERY_NOTICE.zh-CN.md"] {
        if !legal.files.contains_key(required) {
            return Err(format!("legal 组件缺少 {required}"));
        }
    }
    let legal_digest = &legal.files["THIRD_PARTY_MANIFEST.json"];
    let legal_bytes = fs::read(root.join(&manifest.objects[legal_digest].object_path))
        .map_err(|error| format!("读取 THIRD_PARTY_MANIFEST 失败: {error}"))?;
    let document: LegalManifest = serde_json::from_slice(&legal_bytes)
        .map_err(|error| format!("THIRD_PARTY_MANIFEST schema 无效: {error}"))?;
    if document.schema_version != "suying.third-party.v1"
        || document.distribution_status != "ready"
        || !document.gaps.is_empty()
    {
        return Err("第三方许可证清单未达到 ready".to_string());
    }
    let expected: BTreeSet<String> = manifest
        .components
        .iter()
        .filter(|item| item.component_id != "legal")
        .map(|item| item.component_id.clone())
        .collect();
    if document
        .covered_components
        .into_iter()
        .collect::<BTreeSet<_>>()
        != expected
    {
        return Err("legal 组件覆盖范围与 depot 组件闭包不一致".to_string());
    }
    if document.packages.is_empty() {
        return Err("THIRD_PARTY_MANIFEST packages 为空".to_string());
    }
    let mut package_coverage = BTreeSet::new();
    for package in document.packages {
        if package.name.trim().is_empty()
            || package.version.trim().is_empty()
            || package.license_expression.trim().is_empty()
            || package.source_url.trim().is_empty()
            || package.component_ids.is_empty()
            || package.license_paths.is_empty()
        {
            return Err("第三方 package 必填字段不完整".to_string());
        }
        if !package.component_ids.iter().all(|id| expected.contains(id))
            || !package
                .license_paths
                .iter()
                .all(|path| legal.files.contains_key(path))
        {
            return Err(format!("第三方 package 闭包无效: {}", package.name));
        }
        package_coverage.extend(package.component_ids);
        if package.source_required
            && (package.corresponding_source_paths.is_empty()
                || !package
                    .corresponding_source_paths
                    .iter()
                    .all(|path| legal.files.contains_key(path)))
        {
            return Err(format!("对应源码材料缺失: {}", package.name));
        }
    }
    if package_coverage != expected {
        return Err("第三方 package 未覆盖全部 depot 组件".to_string());
    }
    Ok(())
}

fn materialize(
    depot: &Path,
    manifest: &DepotManifest,
    component: &DepotComponent,
    destination: &Path,
) -> Result<(), String> {
    let staging = destination.with_extension(format!("staging-{}", std::process::id()));
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir_all(&staging).map_err(|error| error.to_string())?;
    for (relative, digest) in &component.files {
        let target = staging.join(safe_relative(relative)?);
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        fs::copy(depot.join(&manifest.objects[digest].object_path), &target)
            .map_err(|error| format!("物化组件失败: {error}"))?;
        if sha256_file(&target)? != *digest {
            return Err(format!(
                "物化组件 SHA256 校验失败: {}/{relative}",
                component.component_id
            ));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = if component.executable_files.contains(relative) {
                0o755
            } else {
                0o644
            };
            fs::set_permissions(&target, fs::Permissions::from_mode(mode))
                .map_err(|error| error.to_string())?;
        }
    }
    let previous = destination.with_extension(format!("previous-{}", std::process::id()));
    if destination.exists() {
        fs::rename(destination, &previous).map_err(|error| error.to_string())?;
    }
    if let Err(error) = fs::rename(&staging, destination) {
        if previous.exists() {
            let _ = fs::rename(&previous, destination);
        }
        return Err(error.to_string());
    }
    let _ = fs::remove_dir_all(previous);
    Ok(())
}

fn argument(name: &str) -> Result<String, String> {
    let args: Vec<String> = env::args().collect();
    let index = args
        .iter()
        .position(|value| value == name)
        .ok_or_else(|| format!("缺少参数 {name}"))?;
    args.get(index + 1)
        .cloned()
        .ok_or_else(|| format!("缺少参数值 {name}"))
}

fn run() -> Result<(), String> {
    let depot = PathBuf::from(argument("--depot")?)
        .canonicalize()
        .map_err(|error| error.to_string())?;
    let profile = argument("--profile")?;
    if !["lite", "standard", "pro", "max"].contains(&profile.as_str()) {
        return Err("profile 必须是 lite/standard/pro/max".to_string());
    }
    let workspace = PathBuf::from(argument("--workspace")?);
    fs::create_dir_all(&workspace).map_err(|error| error.to_string())?;
    let manifest = verify_depot(&depot)?;
    let arch = match env::consts::ARCH {
        "aarch64" => "arm64",
        value => value,
    };
    let ids = vec![
        "app".to_string(),
        "ollama".to_string(),
        "ffmpeg".to_string(),
        format!("models-{profile}"),
        "legal".to_string(),
    ];
    let mut paths = BTreeMap::new();
    let mut digests = BTreeSet::new();
    let components: Vec<&DepotComponent> = ids
        .iter()
        .map(|id| {
            let component = manifest
                .components
                .iter()
                .find(|item| item.component_id == **id)
                .ok_or_else(|| format!("离线仓缺少组件: {id}"))?;
            if component.arch != "any" && component.arch != arch {
                return Err(format!("组件架构不匹配: {id}"));
            }
            Ok(component)
        })
        .collect::<Result<_, String>>()?;
    for component in &components {
        digests.extend(component.files.values().cloned());
    }
    let required_bytes: u64 = digests
        .iter()
        .map(|digest| manifest.objects[digest].size)
        .sum();
    let reserve_bytes = 10 * 1024_u64.pow(3);
    let free = available_bytes(&workspace)?;
    if free < required_bytes.saturating_add(reserve_bytes) {
        return Err(format!(
            "磁盘空间不足: required={}, available={free}",
            required_bytes.saturating_add(reserve_bytes)
        ));
    }
    for component in components {
        let destination = workspace.join(&component.component_id);
        materialize(&depot, &manifest, component, &destination)?;
        paths.insert(
            component.component_id.clone(),
            destination.display().to_string(),
        );
    }
    let plan = InstallPlan {
        ok: true,
        depot_seq: manifest.depot_seq,
        arch: arch.to_string(),
        profile,
        required_bytes,
        workspace: workspace.display().to_string(),
        components: paths,
        offline_only: true,
    };
    let json = serde_json::to_string_pretty(&plan).map_err(|error| error.to_string())?;
    fs::write(workspace.join("install-plan.json"), format!("{json}\n"))
        .map_err(|error| error.to_string())?;
    println!("{json}");
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("ERROR: {error}");
        std::process::exit(1);
    }
}
