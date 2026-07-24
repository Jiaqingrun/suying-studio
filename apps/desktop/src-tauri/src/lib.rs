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

fn repo_root() -> PathBuf {
    if let Ok(p) = std::env::var("SUYING_ROOT").or_else(|_| std::env::var("MONTAGE_ROOT")) {
        return PathBuf::from(p);
    }
    let home = dirs_next::home_dir().unwrap_or_else(|| PathBuf::from("."));
    let candidate = home.join("QR/dev/montage-studio");
    if candidate.join("engine/main.py").exists() {
        return candidate;
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    for ancestor in cwd.ancestors().take(6) {
        if ancestor.join("engine/main.py").exists() {
            return ancestor.to_path_buf();
        }
        if ancestor.join("../engine/main.py").exists() {
            return ancestor.join("..").canonicalize().unwrap_or(ancestor.to_path_buf());
        }
    }
    candidate
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

fn health_ok() -> bool {
    ureq::get("http://127.0.0.1:8766/health")
        .timeout(Duration::from_millis(800))
        .call()
        .map(|r| r.status() == 200)
        .unwrap_or(false)
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

/// GUI apps get a minimal PATH (often /usr/bin first). Prefer conda / brew /
/// explicit env so we don't hit Apple CLT python without deps.
fn resolve_python() -> Result<PathBuf, String> {
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
    let candidates = [
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
                    "找到的 python3（{p}）没有安装 uvicorn。请用 Anaconda/Homebrew 的 Python，或设置 SUYING_PYTHON=/opt/anaconda3/bin/python3"
                ));
            }
        }
    }

    Err(
        "找不到可用的 Python（需已安装 uvicorn）。请安装依赖或设置环境变量 SUYING_PYTHON。".into(),
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
}

fn status_inner(state: &Mutex<EngineState>, message: Option<String>) -> EngineStatus {
    let healthy = health_ok();
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
                if alive || healthy {
                    pid = Some(p);
                } else {
                    let _ = fs::remove_file(pid_file());
                }
            }
        }
    }
    let python = resolve_python()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|e| format!("(不可用) {e}"));
    EngineStatus {
        running: healthy || pid.is_some(),
        healthy,
        pid,
        repo: repo_root().display().to_string(),
        python,
        message,
    }
}

#[tauri::command]
fn engine_status(state: tauri::State<'_, Mutex<EngineState>>) -> EngineStatus {
    status_inner(&state, None)
}

#[tauri::command]
fn engine_start(state: tauri::State<'_, Mutex<EngineState>>) -> Result<EngineStatus, String> {
    if health_ok() {
        return Ok(status_inner(&state, Some("引擎已在运行".into())));
    }

    let root = repo_root();
    if !root.join("engine/main.py").exists() {
        return Err(format!("找不到引擎目录: {}", root.display()));
    }
    let python = resolve_python()?;

    // If we already own a live child, wait briefly for health
    {
        let mut st = state.lock().unwrap();
        if let Some(child) = st.child.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                drop(st);
                for _ in 0..20 {
                    thread::sleep(Duration::from_millis(250));
                    if health_ok() {
                        return Ok(status_inner(&state, Some("引擎已就绪".into())));
                    }
                }
                return Ok(status_inner(
                    &state,
                    Some("引擎进程仍在启动中，请稍候".into()),
                ));
            }
        }
    }

    let log_path = log_file();
    {
        let mut marker = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .map_err(|e| e.to_string())?;
        use std::io::Write;
        let _ = writeln!(
            marker,
            "\n==== engine_start {} python={} cwd={} ====",
            chrono_lite_now(),
            python.display(),
            root.display()
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
        .stdin(Stdio::null())
        .stdout(Stdio::from(log_file_handle))
        .stderr(Stdio::from(err_file));

    // Ensure child can find conda libs / sibling tools
    if let Some(bin) = python.parent() {
        let path = std::env::var("PATH").unwrap_or_default();
        let prepend = bin.display().to_string();
        cmd.env("PATH", format!("{prepend}:{path}"));
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

    // Poll briefly; fail fast if process exits (don't freeze UI for 20s)
    for _ in 0..24 {
        thread::sleep(Duration::from_millis(250));
        if health_ok() {
            return Ok(status_inner(&state, Some("引擎已启动".into())));
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
        &state,
        Some("引擎已拉起，健康检查仍未通过，请查看侧栏状态或 engine.log".into()),
    ))
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
    let mut st = state.lock().unwrap();
    if let Some(mut child) = st.child.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    if let Ok(txt) = fs::read_to_string(pid_file()) {
        if let Ok(pid) = txt.trim().parse::<i32>() {
            let _ = Command::new("kill").arg(pid.to_string()).status();
            thread::sleep(Duration::from_millis(300));
            let _ = Command::new("kill").args(["-9", &pid.to_string()]).status();
        }
    }
    let _ = fs::remove_file(pid_file());
    drop(st);
    thread::sleep(Duration::from_millis(200));
    Ok(status_inner(&state, Some("引擎已停止".into())))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(Mutex::new(EngineState { child: None }))
        .invoke_handler(tauri::generate_handler![engine_status, engine_start, engine_stop])
        .setup(|_app| {
            let _ = repo_root();
            let _ = data_dir();
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running 速影");
}
