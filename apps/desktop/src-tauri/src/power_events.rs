//! macOS public workspace notifications → engine pause API → frontend acknowledgement.

use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager, State};

#[cfg(target_os = "macos")]
#[path = "macos_observer.rs"]
mod macos_observer;

const ENGINE_URL: &str = "http://127.0.0.1:8766";
const EVENT_NAME: &str = "suying://system-state";
/// Engine audit/DB can stall several seconds under load; 3s caused false timeouts.
const ENGINE_EVENT_TIMEOUT: Duration = Duration::from_secs(20);
const ENGINE_PAUSE_STATE_TIMEOUT: Duration = Duration::from_secs(5);
/// Cap outbox so a stuck historical storm cannot grow forever.
const OUTBOX_MAX_EVENTS: usize = 32;
/// Suppress repeated delivery_error emits while retrying the same head event.
const DELIVERY_ERROR_EMIT_COOLDOWN: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct SystemEventPrefs {
    pub enabled: bool,
    pub pause_on_system_sleep: bool,
    pub pause_on_power_off: bool,
    pub pause_on_session_inactive: bool,
    pub pause_on_screen_sleep: bool,
    pub auto_resume_on_wake: bool,
    pub auto_resume_on_session_active: bool,
    pub wake_settle_seconds: u32,
    pub event_debounce_ms: u64,
}

impl Default for SystemEventPrefs {
    fn default() -> Self {
        Self {
            enabled: true,
            pause_on_system_sleep: true,
            pause_on_power_off: true,
            pause_on_session_inactive: false,
            pause_on_screen_sleep: false,
            auto_resume_on_wake: true,
            auto_resume_on_session_active: false,
            wake_settle_seconds: 5,
            event_debounce_ms: 500,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SystemStateSnapshot {
    pub last_kind: Option<String>,
    pub last_event_id: Option<String>,
    pub last_at: Option<String>,
    pub prefs: SystemEventPrefs,
    pub supported: bool,
    pub delivery_ok: Option<bool>,
    pub delivery_error: Option<String>,
    pub engine_generation: Option<u64>,
    pub pending_events: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct PendingSystemEvent {
    event_id: String,
    kind: String,
    queued_at: String,
    generation_hint: Option<u64>,
}

pub struct PowerEventsState {
    prefs: Mutex<SystemEventPrefs>,
    token: String,
    last: Mutex<SystemStateSnapshot>,
    last_fire: Mutex<Option<(String, Instant)>>,
    engine_generation: Mutex<Option<u64>>,
    outbox: Mutex<Vec<PendingSystemEvent>>,
    delivery_running: Mutex<bool>,
    last_error_emit: Mutex<Option<(String, Instant)>>,
}

impl PowerEventsState {
    pub fn new() -> Self {
        let token = load_or_create_token().unwrap_or_default();
        let prefs = load_prefs();
        Self {
            prefs: Mutex::new(prefs.clone()),
            token,
            last: Mutex::new(SystemStateSnapshot {
                last_kind: None,
                last_event_id: None,
                last_at: None,
                prefs,
                supported: cfg!(target_os = "macos"),
                delivery_ok: None,
                delivery_error: None,
                engine_generation: None,
                pending_events: 0,
            }),
            last_fire: Mutex::new(None),
            engine_generation: Mutex::new(None),
            outbox: Mutex::new(load_outbox()),
            delivery_running: Mutex::new(false),
            last_error_emit: Mutex::new(None),
        }
    }

    pub fn operation_token(&self) -> Result<String, String> {
        if self.token.len() == 64 {
            Ok(self.token.clone())
        } else {
            Err("高级操作令牌不可用，请重启速影".to_string())
        }
    }
}

pub fn runtime_dir() -> PathBuf {
    let path = if let Ok(p) = std::env::var("SUYING_APP_RUNTIME_DIR") {
        PathBuf::from(p)
    } else {
        dirs_next::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Library")
            .join("Application Support")
            .join("com.qr.suying")
            .join("runtime")
    };
    let _ = fs::create_dir_all(&path);
    let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o700));
    path
}

fn prefs_path() -> PathBuf {
    runtime_dir().join("system_event_prefs.json")
}

fn token_path() -> PathBuf {
    runtime_dir().join("system_token.txt")
}

fn outbox_path() -> PathBuf {
    runtime_dir().join("system_event_outbox.json")
}

fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    if let Ok(mut random) = fs::File::open("/dev/urandom") {
        if random.read_exact(&mut bytes).is_ok() {
            return hex::encode(bytes);
        }
    }
    use sha2::{Digest, Sha256};
    let mut digest = Sha256::new();
    digest.update(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or_default()
            .to_le_bytes(),
    );
    digest.update(std::process::id().to_le_bytes());
    hex::encode(digest.finalize())
}

fn read_existing_token(path: &PathBuf) -> Result<String, String> {
    let metadata = fs::symlink_metadata(path)
        .map_err(|e| format!("读取系统事件令牌元数据失败: {e}"))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err("系统事件令牌路径必须是普通文件".into());
    }
    let token = fs::read_to_string(path)
        .map_err(|e| format!("读取系统事件令牌失败: {e}"))?
        .trim()
        .to_string();
    if token.len() != 64 || !token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("系统事件令牌格式无效".into());
    }
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .map_err(|e| format!("令牌权限修正失败: {e}"))?;
    Ok(token)
}

fn write_token_file(path: &PathBuf, token: &str) -> Result<(), String> {
    let temp = path.with_extension(format!(
        "txt.tmp.{}.{}",
        std::process::id(),
        now_stamp()
    ));
    let result = (|| -> Result<(), String> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temp)
            .map_err(|e| format!("创建系统事件令牌临时文件失败: {e}"))?;
        file.write_all(token.as_bytes())
            .and_then(|_| file.sync_all())
            .map_err(|e| format!("写入系统事件令牌失败: {e}"))?;
        fs::rename(&temp, path).map_err(|e| format!("提交系统事件令牌失败: {e}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

fn load_or_create_token() -> Result<String, String> {
    let path = token_path();
    match fs::symlink_metadata(&path) {
        Ok(_) => match read_existing_token(&path) {
            Ok(token) => return Ok(token),
            // Legacy installs used 32-hex tokens (or truncated files). Regenerate
            // instead of leaving an empty in-memory token that breaks sleep/wake.
            Err(err)
                if err.contains("系统事件令牌格式无效")
                    || err.contains("读取系统事件令牌失败") =>
            {
                let token = generate_token();
                write_token_file(&path, &token)?;
                return Ok(token);
            }
            Err(err) => return Err(err),
        },
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => {
            return Err(format!("检查系统事件令牌失败: {e}"));
        }
        Err(_) => {}
    }

    let token = generate_token();
    match OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)
    {
        Ok(mut file) => {
            file.write_all(token.as_bytes())
                .and_then(|_| file.sync_all())
                .map_err(|e| format!("写入系统事件令牌失败: {e}"))?;
            Ok(token)
        }
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => read_existing_token(&path),
        Err(e) => Err(format!("创建系统事件令牌失败: {e}")),
    }
}

fn load_prefs() -> SystemEventPrefs {
    fs::read_to_string(prefs_path())
        .ok()
        .and_then(|txt| serde_json::from_str::<SystemEventPrefs>(&txt).ok())
        .unwrap_or_default()
}

fn save_prefs(prefs: &SystemEventPrefs) -> Result<(), String> {
    let txt = serde_json::to_string_pretty(prefs).map_err(|e| e.to_string())?;
    let target = prefs_path();
    let temp = target.with_extension(format!("json.tmp.{}.{}", std::process::id(), now_stamp()));
    let result = (|| -> Result<(), String> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temp)
            .map_err(|e| format!("创建系统事件偏好临时文件失败: {e}"))?;
        file.write_all(txt.as_bytes())
            .and_then(|_| file.sync_all())
            .map_err(|e| format!("写入系统事件偏好失败: {e}"))?;
        fs::rename(&temp, &target).map_err(|e| format!("提交系统事件偏好失败: {e}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

fn load_outbox() -> Vec<PendingSystemEvent> {
    let mut events = fs::read_to_string(outbox_path())
        .ok()
        .and_then(|text| serde_json::from_str::<Vec<PendingSystemEvent>>(&text).ok())
        .unwrap_or_default();
    if events.len() > OUTBOX_MAX_EVENTS {
        events = events.split_off(events.len() - OUTBOX_MAX_EVENTS);
    }
    events
}

fn save_outbox(events: &[PendingSystemEvent]) -> Result<(), String> {
    let target = outbox_path();
    let temp = target.with_extension(format!("json.tmp.{}.{}", std::process::id(), now_stamp()));
    let payload = serde_json::to_vec(events).map_err(|error| error.to_string())?;
    let result = (|| -> Result<(), String> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temp)
            .map_err(|error| format!("创建系统事件队列失败: {error}"))?;
        file.write_all(&payload)
            .and_then(|_| file.sync_all())
            .map_err(|error| format!("保存系统事件队列失败: {error}"))?;
        fs::rename(&temp, &target).map_err(|error| format!("提交系统事件队列失败: {error}"))?;
        fs::set_permissions(&target, fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("修正系统事件队列权限失败: {error}"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

pub fn ensure_engine_env(cmd: &mut std::process::Command, _state: &PowerEventsState) {
    cmd.env("SUYING_APP_RUNTIME_DIR", runtime_dir());
}

fn now_stamp() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis().to_string())
        .unwrap_or_else(|_| "0".into())
}

fn should_debounce(state: &PowerEventsState, kind: &str) -> bool {
    let ms = state.prefs.lock().unwrap().event_debounce_ms;
    if ms == 0 {
        return false;
    }
    let mut last = state.last_fire.lock().unwrap();
    if let Some((previous, at)) = last.as_ref() {
        if previous == kind && at.elapsed() < Duration::from_millis(ms) {
            return true;
        }
    }
    *last = Some((kind.to_string(), Instant::now()));
    false
}

fn post_engine_event(
    token: &str,
    event_id: &str,
    kind: &str,
    generation_hint: Option<u64>,
) -> Result<Option<u64>, String> {
    let payload = serde_json::json!({
        "event_id": event_id,
        "kind": kind,
        "occurred_at": now_stamp(),
        "source": "tauri",
        "generation_hint": generation_hint,
    })
    .to_string();
    let response = ureq::post(&format!("{ENGINE_URL}/system/events"))
        .set("Content-Type", "application/json")
        .set("X-Suying-System-Token", token)
        .timeout(ENGINE_EVENT_TIMEOUT)
        .send_string(&payload)
        .map_err(|e| format!("引擎未确认系统事件: {e}"))?;
    let status = response.status();
    if !(200..300).contains(&status) {
        return Err(format!("引擎系统事件 HTTP {status}"));
    }
    let body: serde_json::Value = serde_json::from_reader(response.into_reader())
        .map_err(|e| format!("引擎系统事件响应无效: {e}"))?;
    // HTTP 200 = engine processed this event_id. applied=false is normal for
    // duplicates and policy-ignored kinds; those MUST leave the outbox.
    let applied = body.get("applied").and_then(|value| value.as_bool()) == Some(true);
    let duplicate = body.get("duplicate").and_then(|value| value.as_bool()) == Some(true);
    let acknowledged = body.get("acknowledged").and_then(|value| value.as_bool()) == Some(true);
    let has_state = body.get("state").and_then(|value| value.as_str()).is_some();
    if !(applied || duplicate || acknowledged || has_state) {
        return Err(format!(
            "引擎未确认系统事件（state={}）",
            body.get("state")
                .and_then(|value| value.as_str())
                .unwrap_or("unknown")
        ));
    }
    Ok(body.get("generation").and_then(|v| v.as_u64()))
}

fn fetch_engine_generation() -> Result<u64, String> {
    let response = ureq::get(&format!("{ENGINE_URL}/system/pause-state"))
        .timeout(ENGINE_PAUSE_STATE_TIMEOUT)
        .call()
        .map_err(|e| format!("无法读取引擎暂停代次: {e}"))?;
    let body: serde_json::Value = serde_json::from_reader(response.into_reader())
        .map_err(|e| format!("引擎暂停状态响应无效: {e}"))?;
    body.get("generation")
        .and_then(|value| value.as_u64())
        .ok_or_else(|| "引擎暂停状态缺少 generation".to_string())
}

fn should_emit_delivery_error(state: &PowerEventsState, error: &str) -> bool {
    let mut last = state.last_error_emit.lock().unwrap();
    if let Some((prev, at)) = last.as_ref() {
        if prev == error && at.elapsed() < DELIVERY_ERROR_EMIT_COOLDOWN {
            return false;
        }
    }
    *last = Some((error.to_string(), Instant::now()));
    true
}

fn enqueue_system_event(queue: &mut Vec<PendingSystemEvent>, event: PendingSystemEvent) {
    if let Some(tail) = queue.last() {
        if tail.kind == event.kind {
            queue.pop();
        }
    }
    queue.push(event);
    while queue.len() > OUTBOX_MAX_EVENTS {
        queue.remove(0);
    }
}

fn start_delivery(app: &AppHandle) {
    let Some(state) = app.try_state::<PowerEventsState>() else {
        return;
    };
    {
        let mut running = state.delivery_running.lock().unwrap();
        if *running {
            return;
        }
        *running = true;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        loop {
            let Some(state) = app.try_state::<PowerEventsState>() else {
                return;
            };
            let pending = state.outbox.lock().unwrap().first().cloned();
            let Some(pending) = pending else {
                *state.delivery_running.lock().unwrap() = false;
                return;
            };
            let is_resume = matches!(
                pending.kind.as_str(),
                "did_wake" | "session_active" | "screens_wake"
            );
            let effective_generation = if is_resume && pending.generation_hint.is_none() {
                fetch_engine_generation().ok()
            } else {
                pending.generation_hint
            };
            let result = if state.token.is_empty() {
                Err("桌面端系统事件令牌不可用".into())
            } else {
                post_engine_event(
                    &state.token,
                    &pending.event_id,
                    &pending.kind,
                    effective_generation,
                )
            };
            let (delivery_ok, delivery_error, generation) = match result {
                Ok(generation) => {
                    if let Some(value) = generation {
                        *state.engine_generation.lock().unwrap() = Some(value);
                    }
                    let mut queue = state.outbox.lock().unwrap();
                    if queue
                        .first()
                        .map(|event| event.event_id.as_str())
                        == Some(pending.event_id.as_str())
                    {
                        queue.remove(0);
                    }
                    let _ = save_outbox(&queue);
                    (true, None, generation)
                }
                Err(error) => {
                    let queue = state.outbox.lock().unwrap();
                    let _ = save_outbox(&queue);
                    (false, Some(error), effective_generation)
                }
            };
            let emit_error = if let Some(ref error) = delivery_error {
                should_emit_delivery_error(&state, error)
            } else {
                // Clear cooldown after a successful delivery.
                *state.last_error_emit.lock().unwrap() = None;
                false
            };
            let mut last = state.last.lock().unwrap();
            last.last_kind = Some(pending.kind.clone());
            last.last_event_id = Some(pending.event_id.clone());
            last.last_at = Some(now_stamp());
            last.prefs = state.prefs.lock().unwrap().clone();
            last.delivery_ok = Some(delivery_ok);
            last.delivery_error = delivery_error;
            last.engine_generation = generation.or(*state.engine_generation.lock().unwrap());
            last.pending_events = state.outbox.lock().unwrap().len();
            let snapshot = last.clone();
            drop(last);
            // On failure, only emit occasionally so UI does not toast every 5s retry.
            if delivery_ok || emit_error {
                let _ = app.emit(EVENT_NAME, snapshot);
            }
            if !delivery_ok {
                *state.delivery_running.lock().unwrap() = false;
                return;
            }
        }
    });
}

pub fn handle_native_event(app: &AppHandle, kind: &str) {
    let Some(state) = app.try_state::<PowerEventsState>() else {
        return;
    };
    let prefs = state.prefs.lock().unwrap().clone();
    if !prefs.enabled {
        return;
    }
    let allowed = match kind {
        "will_sleep" => prefs.pause_on_system_sleep,
        "will_power_off" => prefs.pause_on_power_off,
        "session_inactive" => prefs.pause_on_session_inactive,
        "screens_sleep" => prefs.pause_on_screen_sleep,
        "did_wake" => prefs.auto_resume_on_wake,
        "session_active" => prefs.auto_resume_on_session_active,
        "screens_wake" => prefs.pause_on_screen_sleep,
        _ => false,
    };
    if !allowed || should_debounce(&state, kind) {
        return;
    }
    let event = PendingSystemEvent {
        event_id: format!("{}-{}", kind, now_stamp()),
        kind: kind.to_string(),
        queued_at: now_stamp(),
        generation_hint: if matches!(kind, "did_wake" | "session_active" | "screens_wake") {
            *state.engine_generation.lock().unwrap()
        } else {
            None
        },
    };
    {
        let mut queue = state.outbox.lock().unwrap();
        enqueue_system_event(&mut queue, event);
        let _ = save_outbox(&queue);
    }
    start_delivery(app);
}

#[tauri::command]
pub fn system_events_snapshot(
    state: State<'_, PowerEventsState>,
) -> Result<SystemStateSnapshot, String> {
    let mut snapshot = state.last.lock().unwrap().clone();
    snapshot.prefs = state.prefs.lock().unwrap().clone();
    snapshot.supported = cfg!(target_os = "macos");
    snapshot.pending_events = state.outbox.lock().unwrap().len();
    Ok(snapshot)
}

#[tauri::command]
pub fn system_events_get_prefs(
    state: State<'_, PowerEventsState>,
) -> Result<SystemEventPrefs, String> {
    Ok(state.prefs.lock().unwrap().clone())
}

#[tauri::command]
pub fn system_events_set_prefs(
    state: State<'_, PowerEventsState>,
    prefs: SystemEventPrefs,
) -> Result<SystemEventPrefs, String> {
    save_prefs(&prefs)?;
    *state.prefs.lock().unwrap() = prefs.clone();
    state.last.lock().unwrap().prefs = prefs.clone();
    Ok(prefs)
}

pub fn install(app: AppHandle) {
    let retry_app = app.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(5));
        start_delivery(&retry_app);
    });
    start_delivery(&app);
    #[cfg(target_os = "macos")]
    macos_observer::install(app);
    #[cfg(not(target_os = "macos"))]
    let _ = app;
}
