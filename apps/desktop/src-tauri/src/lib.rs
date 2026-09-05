mod engine_supervisor;
mod integrity;
mod licensing;
mod media_drag;
mod power_events;
mod settings_lock;
mod workspace_events;

use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

struct EngineState {
    child: Option<Child>,
}

/// macOS: …/速影.app/Contents/Resources when running from a bundled App.
fn bundle_resources_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let macos_dir = exe.parent()?;
    if macos_dir.file_name()?.to_str()? != "MacOS" {
        return None;
    }
    let contents = macos_dir.parent()?;
    let resources = contents.join("Resources");
    if resources.is_dir() {
        Some(resources)
    } else {
        None
    }
}

fn bundled_studio_root() -> Option<PathBuf> {
    let resources = bundle_resources_dir()?;
    let studio = resources.join("runtime").join("studio");
    if studio.join("engine").join("main.py").exists() {
        Some(studio)
    } else {
        None
    }
}

fn bundled_python() -> Option<PathBuf> {
    let resources = bundle_resources_dir()?;
    let candidates = [
        resources.join("runtime/python/bin/python3"),
        resources.join("runtime/python/bin/python"),
    ];
    for c in candidates {
        if c.exists() && python_has_uvicorn(&c) {
            return Some(c);
        }
    }
    // Present but missing deps — still return so resolve_python can error clearly.
    let raw = resources.join("runtime/python/bin/python3");
    if raw.exists() {
        return Some(raw);
    }
    None
}

fn repo_root() -> PathBuf {
    // Packaged App: engine lives inside the bundle (scheme A).
    if let Some(studio) = bundled_studio_root() {
        return studio;
    }
    if let Ok(p) = std::env::var("SUYING_ROOT").or_else(|_| std::env::var("MONTAGE_ROOT")) {
        return PathBuf::from(p);
    }
    let home = dirs_next::home_dir().unwrap_or_else(|| PathBuf::from("."));
    // Customer/runtime first, then developer checkout.
    let candidates = [
        home.join("QR/dev/速影"),
        home.join("Suying/montage-studio"),
        home.join("Suying/runtime"),
        home.join("QR/dev/montage-studio"),
    ];
    for candidate in &candidates {
        if candidate.join("engine/main.py").exists() {
            return candidate.clone();
        }
    }
    // Near the .app: sibling montage-studio/ or Contents/Resources layout.
    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors().take(8) {
            let sib = ancestor.join("montage-studio");
            if sib.join("engine/main.py").exists() {
                return sib;
            }
            if ancestor.join("engine/main.py").exists() {
                return ancestor.to_path_buf();
            }
        }
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    for ancestor in cwd.ancestors().take(6) {
        if ancestor.join("engine/main.py").exists() {
            return ancestor.to_path_buf();
        }
        if ancestor.join("../engine/main.py").exists() {
            return ancestor
                .join("..")
                .canonicalize()
                .unwrap_or(ancestor.to_path_buf());
        }
    }
    home.join("QR/dev/速影")
}

fn data_dir() -> PathBuf {
    let home = dirs_next::home_dir().unwrap_or_else(|| PathBuf::from("."));
    let suying = home.join("Suying/data");
    let legacy = home.join("MontageStudio/data");
    if suying.exists() {
        suying
    } else if legacy.exists() {
        legacy
    } else {
        let _ = fs::create_dir_all(&suying);
        suying
    }
}

fn pid_file() -> PathBuf {
    data_dir().join("engine.pid")
}

fn log_file() -> PathBuf {
    data_dir().join("engine.log")
}

const ENGINE_PORT: u16 = 8766;

pub(crate) fn listener_pid_on_port(port: u16) -> Option<u32> {
    let script = format!("lsof -t -nP -iTCP:{port} -sTCP:LISTEN 2>/dev/null | head -1");
    let out = Command::new("sh").args(["-c", &script]).output().ok()?;
    if !out.status.success() {
        return None;
    }
    let binding = String::from_utf8_lossy(&out.stdout);
    let txt = binding.trim();
    if txt.is_empty() {
        return None;
    }
    txt.parse().ok()
}

fn process_command_line(pid: u32) -> Option<String> {
    let pid_s = pid.to_string();
    let out = Command::new("ps")
        .args(["-p", &pid_s, "-o", "command="])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let cmd = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if cmd.is_empty() {
        None
    } else {
        Some(cmd)
    }
}

fn is_suying_engine_command(cmd: &str) -> bool {
    cmd.contains("engine.main")
}

/// Stale when the packaged python or studio path no longer exists (e.g. temp App removed).
fn is_stale_suying_engine_command(cmd: &str) -> bool {
    if !is_suying_engine_command(cmd) {
        return false;
    }
    let parts: Vec<&str> = cmd.split_whitespace().collect();
    if let Some(py) = parts.first() {
        if py.contains("python") && !Path::new(py).exists() {
            return true;
        }
    }
    for token in &parts {
        if token.contains(".app/")
            && (token.contains("runtime/python") || token.contains("runtime/studio"))
            && !Path::new(token).exists()
        {
            return true;
        }
    }
    false
}

fn kill_pid_gracefully(pid: u32) {
    let _ = Command::new("kill").arg(pid.to_string()).status();
    thread::sleep(Duration::from_millis(400));
    let still = Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if still {
        let _ = Command::new("kill")
            .args(["-9", &pid.to_string()])
            .status();
    }
}

/// Kill only confirmed stale suying engine listeners; never touch external processes.
fn clear_stale_suying_listeners() -> bool {
    let Some(pid) = listener_pid_on_port(ENGINE_PORT) else {
        return false;
    };
    let Some(cmd) = process_command_line(pid) else {
        return false;
    };
    if !is_stale_suying_engine_command(&cmd) {
        return false;
    }
    kill_pid_gracefully(pid);
    thread::sleep(Duration::from_millis(300));
    true
}

fn engine_ready() -> bool {
    workspace_events::engine_ready()
}

fn engine_reachable() -> bool {
    workspace_events::engine_reachable()
}

fn python_has_uvicorn(python: &Path) -> bool {
    Command::new(python)
        .args(["-c", "import uvicorn"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// GUI apps get a minimal PATH (often /usr/bin first). Prefer bundle /
/// conda / brew / explicit env so we don't hit Apple CLT python without deps.
fn resolve_python() -> Result<PathBuf, String> {
    if let Some(bundled) = bundled_python() {
        if python_has_uvicorn(&bundled) {
            return Ok(bundled);
        }
        return Err(format!(
            "App 内嵌 Python 缺少 uvicorn（{}）。请重新运行打包脚本 embed-app-runtime。",
            bundled.display()
        ));
    }

    if let Ok(p) = std::env::var("SUYING_PYTHON").or_else(|_| std::env::var("MONTAGE_PYTHON")) {
        let path = PathBuf::from(&p);
        if path.exists() {
            if python_has_uvicorn(&path) {
                return Ok(path);
            }
            return Err(format!(
                "SUYING_PYTHON/MONTAGE_PYTHON 指向的解释器缺少 uvicorn: {p}"
            ));
        }
        return Err(format!("SUYING_PYTHON/MONTAGE_PYTHON 不存在: {p}"));
    }

    let home = dirs_next::home_dir().unwrap_or_else(|| PathBuf::from("."));
    let studio = repo_root();
    let candidates = [
        studio.join(".venv/bin/python3"),
        studio.join(".venv/bin/python"),
        home.join("QR/dev/速影/.venv/bin/python3"),
        home.join("QR/dev/速影/.venv/bin/python"),
        home.join("QR/dev/montage-studio/.venv/bin/python3"),
        home.join("QR/dev/montage-studio/.venv/bin/python"),
        PathBuf::from("/opt/anaconda3/bin/python3"),
        PathBuf::from("/opt/homebrew/bin/python3"),
        PathBuf::from("/usr/local/bin/python3"),
        PathBuf::from("/opt/homebrew/Caskroom/miniconda/base/bin/python3"),
        PathBuf::from("/opt/miniconda3/bin/python3"),
    ];
    for c in &candidates {
        if c.exists() && python_has_uvicorn(c) {
            return Ok(c.clone());
        }
    }

    // Last resort: whatever `python3` resolves to in current PATH
    if let Ok(out) = Command::new("/usr/bin/which").arg("python3").output() {
        if out.status.success() {
            let p = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !p.is_empty() {
                let path = PathBuf::from(&p);
                if python_has_uvicorn(&path) {
                    return Ok(path);
                }
                return Err(format!(
                    "找到的 python3（{p}）没有安装 uvicorn。请用内嵌运行时重新打包，或设置 SUYING_PYTHON。"
                ));
            }
        }
    }

    Err(
        "找不到可用的 Python（需已安装 uvicorn）。一体包请用 scripts/embed-app-runtime.sh 打包；开发机可设置 SUYING_PYTHON。"
            .into(),
    )
}

fn read_log_tail(max_chars: usize) -> String {
    let path = log_file();
    let Ok(txt) = fs::read_to_string(&path) else {
        return String::new();
    };
    if txt.len() <= max_chars {
        return txt;
    }
    txt[txt.len() - max_chars..].to_string()
}

#[derive(Serialize)]
struct EngineStatus {
    running: bool,
    healthy: bool,
    pid: Option<u32>,
    repo: String,
    python: String,
    message: Option<String>,
    bundled: bool,
    /// 8766 LISTEN
    listen: bool,
    /// GET /health 200
    control_plane: bool,
    /// /readiness.ready
    business_ready: bool,
    offline_class: String,
    offline_detail: String,
    agent_loaded: bool,
    agent_plist_exists: bool,
}

fn status_inner(state: &Mutex<EngineState>, message: Option<String>) -> EngineStatus {
    // One /health for both reachable + workspace-healthy (was 2× HTTP per engine_status).
    let (reachable, healthy) = workspace_events::engine_reach_and_healthy();
    let business_ready = workspace_events::engine_ready();
    let listen = listener_pid_on_port(ENGINE_PORT).is_some();
    let readiness_msg = if business_ready {
        String::new()
    } else {
        workspace_events::readiness_failure_message()
    };
    let (offline_class, offline_detail) =
        engine_supervisor::classify(listen, reachable, business_ready, &readiness_msg);
    let mut st = state.lock().unwrap();
    let mut pid = None;
    if let Some(child) = st.child.as_mut() {
        match child.try_wait() {
            Ok(Some(_)) => {
                st.child = None;
                let _ = fs::remove_file(pid_file());
            }
            Ok(None) => pid = Some(child.id()),
            Err(_) => {}
        }
    }
    if pid.is_none() {
        if let Ok(txt) = fs::read_to_string(pid_file()) {
            if let Ok(p) = txt.trim().parse::<u32>() {
                // Stale pid file: only trust if process exists or health ok
                let alive = Command::new("kill")
                    .args(["-0", &p.to_string()])
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false);
                if alive || reachable {
                    pid = Some(p);
                } else {
                    let _ = fs::remove_file(pid_file());
                }
            }
        }
    }
    if pid.is_none() {
        if let Some(p) = listener_pid_on_port(ENGINE_PORT) {
            pid = Some(p);
        }
    }
    let python = resolve_python()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|e| format!("(不可用) {e}"));
    EngineStatus {
        running: reachable || listen || pid.is_some(),
        healthy,
        pid,
        repo: repo_root().display().to_string(),
        python,
        message,
        bundled: bundled_studio_root().is_some(),
        listen,
        control_plane: reachable,
        business_ready,
        offline_class,
        offline_detail,
        agent_loaded: engine_supervisor::agent_plist_loaded(),
        agent_plist_exists: engine_supervisor::agent_plist_exists(),
    }
}

#[tauri::command]
fn engine_status(state: tauri::State<'_, Mutex<EngineState>>) -> EngineStatus {
    status_inner(&state, None)
}

/// Prefer LaunchAgent; fall back to App-owned spawn only when agent path
/// is unavailable (dev tree / first install before agent).
fn ensure_engine_via_agent_or_spawn(
    state: &Mutex<EngineState>,
    power: &power_events::PowerEventsState,
    ops_license_unlock: bool,
) -> Result<EngineStatus, String> {
    let log_path = log_file();
    let root = repo_root();

    // Path A: LaunchAgent install / kickstart (bundled product authority).
    if bundled_studio_root().is_some() || engine_supervisor::agent_plist_exists() {
        let mut agent_notes: Vec<String> = Vec::new();
        if !engine_supervisor::agent_plist_exists() {
            match engine_supervisor::try_install_agent(&root) {
                Ok(msg) => agent_notes.push(msg),
                Err(e) => agent_notes.push(format!("agent install: {e}")),
            }
        } else if !engine_reachable() {
            match engine_supervisor::try_kickstart_agent() {
                Ok(msg) => agent_notes.push(msg),
                Err(e) => agent_notes.push(format!("agent kickstart: {e}")),
            }
        }
        if engine_supervisor::wait_health_ok(12_000, 300) {
            let msg = if engine_ready() {
                format!(
                    "引擎控制面就绪（LaunchAgent）{}",
                    if agent_notes.is_empty() {
                        String::new()
                    } else {
                        format!(" · {}", agent_notes.join("; "))
                    }
                )
            } else {
                format!(
                    "引擎已在线，业务尚未就绪：{} · {}",
                    workspace_events::readiness_failure_message(),
                    agent_notes.join("; ")
                )
            };
            return Ok(status_inner(state, Some(msg)));
        }
        // Agent path failed — continue to spawn only outside package or as last resort.
        if bundled_studio_root().is_some() && engine_supervisor::license_cache_ready() {
            let tail = {
                let err = engine_supervisor::agent_err_log();
                fs::read_to_string(err).unwrap_or_default()
            };
            let tail_snip: String = tail.chars().rev().take(600).collect::<String>().chars().rev().collect();
            return Err(format!(
                "LaunchAgent 未能拉起引擎。notes={} 日志尾：\n{}",
                agent_notes.join("; "),
                tail_snip
            ));
        }
    }

    // Path B: direct spawn (dev / no agent)
    if !root.join("engine/main.py").exists() {
        return Err(format!(
            "找不到引擎目录: {}。一体包应含 Contents/Resources/runtime/studio；开发机请设置 SUYING_ROOT。",
            root.display()
        ));
    }
    let python = resolve_python()?;

    {
        let mut st = state.lock().unwrap();
        if let Some(child) = st.child.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                drop(st);
                for _ in 0..20 {
                    thread::sleep(Duration::from_millis(250));
                    if engine_reachable() {
                        return Ok(status_inner(state, Some("引擎进程仍在启动中".into())));
                    }
                }
                return Ok(status_inner(
                    state,
                    Some("引擎进程仍在启动中，请稍候".into()),
                ));
            }
        }
    }

    {
        let mut marker = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|e| e.to_string())?;
        use std::io::Write;
        let _ = writeln!(
            marker,
            "\n==== engine_start {} python={} cwd={} bundled={} mode=spawn ====",
            chrono_lite_now(),
            python.display(),
            root.display(),
            bundled_studio_root().is_some()
        );
    }

    let log_file_handle = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| e.to_string())?;
    let err_file = log_file_handle.try_clone().map_err(|e| e.to_string())?;

    let mut cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("engine.main")
        .current_dir(&root)
        .env("PYTHONPATH", &root)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("SUYING_ROOT", &root)
        .env("MONTAGE_ROOT", &root)
        .env("SUYING_ALLOW_CACHED_LICENSE_BINDING", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log_file_handle))
        .stderr(Stdio::from(err_file));
    if bundle_resources_dir().is_some() {
        cmd.env("SUYING_BUNDLED_RUNTIME", "1");
    }

    for (key, value) in licensing::engine_license_env() {
        cmd.env(key, value);
    }
    if ops_license_unlock {
        cmd.env("SUYING_OPS_LICENSE_UNLOCK", "1");
    }

    power_events::ensure_engine_env(&mut cmd, power);

    let mut path_prepend: Vec<String> = Vec::new();
    if let Some(bin) = python.parent() {
        path_prepend.push(bin.display().to_string());
    }
    if let Some(resources) = bundle_resources_dir() {
        let ffmpeg_bin = resources.join("runtime/ffmpeg");
        if ffmpeg_bin.is_dir() {
            path_prepend.push(ffmpeg_bin.display().to_string());
        }
    }
    for brew in [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/opt/homebrew/sbin",
        "/usr/local/sbin",
    ] {
        let p = PathBuf::from(brew);
        if p.is_dir() {
            path_prepend.push(brew.to_string());
        }
    }
    let base_path =
        std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/bin:/usr/sbin:/sbin".into());
    if !path_prepend.is_empty() {
        cmd.env("PATH", format!("{}:{}", path_prepend.join(":"), base_path));
    } else {
        cmd.env("PATH", base_path);
    }

    let child = cmd
        .spawn()
        .map_err(|e| format!("启动失败（{}）: {e}", python.display()))?;

    let pid = child.id();
    let _ = fs::write(pid_file(), pid.to_string());
    {
        let mut st = state.lock().unwrap();
        st.child = Some(child);
    }

    for _ in 0..40 {
        thread::sleep(Duration::from_millis(250));
        if engine_reachable() {
            let msg = if engine_ready() {
                "引擎已启动（App 子进程）".to_string()
            } else {
                format!(
                    "引擎控制面已启动，业务检查中：{}",
                    workspace_events::readiness_failure_message()
                )
            };
            return Ok(status_inner(state, Some(msg)));
        }
        let mut st = state.lock().unwrap();
        if let Some(child) = st.child.as_mut() {
            if let Ok(Some(status)) = child.try_wait() {
                st.child = None;
                let _ = fs::remove_file(pid_file());
                let tail = read_log_tail(1200);
                return Err(format!(
                    "引擎进程立即退出（python={}, code={status:?}）。日志末尾：\n{tail}",
                    python.display()
                ));
            }
        }
    }

    Ok(status_inner(
        state,
        Some("引擎已拉起，控制面尚未响应，请查看 engine.log".into()),
    ))
}

#[tauri::command]
fn engine_start(
    state: tauri::State<'_, Mutex<EngineState>>,
    power: tauri::State<'_, power_events::PowerEventsState>,
    lock: tauri::State<'_, settings_lock::SettingsLockState>,
) -> Result<EngineStatus, String> {
    // Prefer disk license cache so reboot/Keychain races don't block spawn.
    let ops_unlocked = settings_lock::require_unlocked(&lock).is_ok();
    let ops_license_unlock = match licensing::require_valid_license_or_ops_unlocked(ops_unlocked)
    {
        Ok(v) => v,
        Err(error) if engine_supervisor::license_cache_ready() => {
            // App Keychain flaky but cache present — agent/spawn use cache.
            let _ = error;
            false
        }
        Err(error) => {
            return Err(format!("速影许可证校验失败，已禁止启动引擎：{error}"));
        }
    };
    if let Some(resources) = bundle_resources_dir() {
        integrity::verify_runtime(&resources.join("runtime")).map_err(|error| {
            format!("速影运行时完整性校验失败，已禁止启动引擎（请重装一体包）：{error}")
        })?;
    }
    if engine_ready() {
        return Ok(status_inner(&state, Some("引擎已在运行".into())));
    }

    if engine_reachable() {
        let cleared = clear_stale_suying_listeners();
        if cleared {
            thread::sleep(Duration::from_millis(400));
        } else {
            // Control plane up → treat as success; surface business reason in message.
            let reason = workspace_events::readiness_failure_message();
            let msg = if reason.is_empty() {
                "引擎控制面已在线".into()
            } else {
                format!("引擎控制面已在线：{reason}")
            };
            return Ok(status_inner(&state, Some(msg)));
        }
        if engine_reachable() {
            let reason = workspace_events::readiness_failure_message();
            return Ok(status_inner(
                &state,
                Some(if reason.is_empty() {
                    "引擎控制面已在线".into()
                } else {
                    format!("引擎控制面已在线：{reason}")
                }),
            ));
        }
    }

    if let Some(pid) = listener_pid_on_port(ENGINE_PORT) {
        let cmd = process_command_line(pid).unwrap_or_default();
        if !is_suying_engine_command(&cmd) {
            return Err(format!("8766 端口被其他进程占用（PID {pid}）：{cmd}"));
        }
        // Stale listener without health — clear and restart
        if is_stale_suying_engine_command(&cmd) {
            kill_pid_gracefully(pid);
            thread::sleep(Duration::from_millis(300));
        }
    }

    ensure_engine_via_agent_or_spawn(&state, &power, ops_license_unlock)
}

#[tauri::command]
fn engine_supervisor_snapshot() -> engine_supervisor::SupervisorSnapshot {
    engine_supervisor::snapshot(&log_file())
}

fn chrono_lite_now() -> String {
    // Avoid extra chrono dep; local wall-ish stamp via system
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("{secs}")
}

#[tauri::command]
fn engine_stop(state: tauri::State<'_, Mutex<EngineState>>) -> Result<EngineStatus, String> {
    // Only tear down App-owned child. LaunchAgent KeepAlive is the long-lived
    // authority — never kill a managed listener on soft UI stop.
    let mut st = state.lock().unwrap();
    let mut killed_owned = false;
    if let Some(mut child) = st.child.take() {
        let _ = child.kill();
        let _ = child.wait();
        killed_owned = true;
    }
    if let Ok(txt) = fs::read_to_string(pid_file()) {
        if let Ok(pid) = txt.trim().parse::<u32>() {
            // Only kill if still our recorded child pid and not agent-managed path
            // when agent is loaded: agent owns the port.
            if !engine_supervisor::agent_plist_loaded() {
                let _ = Command::new("kill").arg(pid.to_string()).status();
                thread::sleep(Duration::from_millis(300));
                let _ = Command::new("kill").args(["-9", &pid.to_string()]).status();
                killed_owned = true;
            }
        }
    }
    let _ = fs::remove_file(pid_file());
    drop(st);
    thread::sleep(Duration::from_millis(200));
    let msg = if engine_supervisor::agent_plist_loaded() && engine_reachable() {
        "已停止 App 持有的子进程；LaunchAgent 引擎仍在运行".into()
    } else if killed_owned {
        "引擎已停止".into()
    } else if engine_reachable() {
        "未持有子进程；引擎仍由 LaunchAgent/外部会话运行".into()
    } else {
        "引擎已停止".into()
    };
    Ok(status_inner(&state, Some(msg)))
}

#[tauri::command]
fn settings_password_status(
    lock: tauri::State<'_, settings_lock::SettingsLockState>,
) -> Result<settings_lock::SettingsPasswordStatus, String> {
    settings_lock::status(&lock)
}

#[tauri::command]
fn settings_password_verify(
    lock: tauri::State<'_, settings_lock::SettingsLockState>,
    password: String,
) -> Result<settings_lock::SettingsPasswordStatus, String> {
    settings_lock::verify_password(&lock, password)
}

#[tauri::command]
fn settings_password_lock(
    lock: tauri::State<'_, settings_lock::SettingsLockState>,
) -> Result<settings_lock::SettingsPasswordStatus, String> {
    settings_lock::lock(&lock)
}

#[tauri::command]
fn settings_password_change(
    lock: tauri::State<'_, settings_lock::SettingsLockState>,
    password: String,
) -> Result<settings_lock::SettingsPasswordStatus, String> {
    settings_lock::change_password(&lock, password)
}

#[tauri::command]
fn settings_advanced_require_unlocked(
    lock: tauri::State<'_, settings_lock::SettingsLockState>,
) -> Result<(), String> {
    settings_lock::require_unlocked(&lock)
}

#[tauri::command]
fn settings_operation_token(
    lock: tauri::State<'_, settings_lock::SettingsLockState>,
    power: tauri::State<'_, power_events::PowerEventsState>,
) -> Result<String, String> {
    settings_lock::require_unlocked(&lock)?;
    power.operation_token()
}

#[tauri::command]
fn license_status(
    lock: tauri::State<'_, settings_lock::SettingsLockState>,
) -> licensing::LicenseStatus {
    licensing::license_status_with_ops(settings_lock::require_unlocked(&lock).is_ok())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(Mutex::new(EngineState { child: None }))
        .manage(settings_lock::SettingsLockState::default())
        .manage(power_events::PowerEventsState::new())
        .manage(workspace_events::WorkspaceEventsState::new())
        .invoke_handler(tauri::generate_handler![
            engine_status,
            engine_start,
            engine_stop,
            engine_supervisor_snapshot,
            settings_password_status,
            settings_password_verify,
            settings_password_lock,
            settings_password_change,
            settings_advanced_require_unlocked,
            settings_operation_token,
            license_status,
            licensing::license_request,
            licensing::license_install_path,
            media_drag::start_file_drag,
            power_events::system_events_snapshot,
            power_events::system_events_get_prefs,
            power_events::system_events_set_prefs,
            workspace_events::workspace_events_snapshot,
            workspace_events::workspace_sync_get_prefs,
            workspace_events::workspace_sync_set_prefs,
            workspace_events::workspace_probe_now,
            workspace_events::workspace_reconnect_now
        ])
        .setup(|app| {
            let _ = repo_root();
            let _ = data_dir();
            power_events::install(app.handle().clone());
            workspace_events::install(app.handle().clone());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running 速影");
}
