//! Volume mount/unmount events + workspace reconnect commands.

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager, State};

const ENGINE_URL: &str = "http://127.0.0.1:8766";
const EVENT_NAME: &str = "suying://workspace-state";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct WorkspaceSyncPrefs {
    pub auto_reconnect_on_mount: bool,
    pub mount_settle_ms: u64,
}

impl Default for WorkspaceSyncPrefs {
    fn default() -> Self {
        // Local-first: mount edge auto-reconnect is opt-in for optional external paths.
        Self {
            auto_reconnect_on_mount: false,
            mount_settle_ms: 1500,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceProbeView {
    pub ok: bool,
    pub state: String,
    pub data_root: Option<String>,
    pub reasons: Vec<String>,
    pub workspace_id: Option<String>,
    pub volume_uuid: Option<String>,
    pub engine_status: Option<String>,
    pub engine_healthy: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkspaceStateSnapshot {
    pub last_kind: Option<String>,
    pub last_at: Option<String>,
    pub prefs: WorkspaceSyncPrefs,
    pub supported: bool,
    pub probe: Option<WorkspaceProbeView>,
    pub phase: String,
    pub message: Option<String>,
    pub error: Option<String>,
}

pub struct WorkspaceEventsState {
    prefs: Mutex<WorkspaceSyncPrefs>,
    last: Mutex<WorkspaceStateSnapshot>,
    last_auto_cycle: Mutex<Option<String>>,
    last_fire: Mutex<Option<(String, Instant)>>,
}

impl WorkspaceEventsState {
    pub fn new() -> Self {
        let prefs = load_prefs();
        Self {
            prefs: Mutex::new(prefs.clone()),
            last: Mutex::new(WorkspaceStateSnapshot {
                last_kind: None,
                last_at: None,
                prefs,
                supported: cfg!(target_os = "macos"),
                probe: None,
                phase: "idle".into(),
                message: None,
                error: None,
            }),
            last_auto_cycle: Mutex::new(None),
            last_fire: Mutex::new(None),
        }
    }
}

fn runtime_dir() -> PathBuf {
    if let Ok(p) = std::env::var("SUYING_APP_RUNTIME_DIR") {
        PathBuf::from(p)
    } else {
        dirs_next::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Library")
            .join("Application Support")
            .join("com.qr.suying")
            .join("runtime")
    }
}

fn prefs_path() -> PathBuf {
    runtime_dir().join("workspace_sync_prefs.json")
}

fn load_prefs() -> WorkspaceSyncPrefs {
    let path = prefs_path();
    if let Ok(txt) = fs::read_to_string(&path) {
        if let Ok(p) = serde_json::from_str::<WorkspaceSyncPrefs>(&txt) {
            return p;
        }
    }
    WorkspaceSyncPrefs::default()
}

fn save_prefs(prefs: &WorkspaceSyncPrefs) {
    let _ = fs::create_dir_all(runtime_dir());
    if let Ok(txt) = serde_json::to_string_pretty(prefs) {
        let _ = fs::write(prefs_path(), txt);
    }
}

fn now_iso() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("{secs}")
}

fn http_json(method: &str, path: &str) -> Result<serde_json::Value, String> {
    let url = format!("{ENGINE_URL}{path}");
    let resp = if method == "POST" {
        ureq::post(&url)
            .timeout(Duration::from_secs(8))
            .set("Content-Type", "application/json")
            .send_string("{}")
            .map_err(|e| e.to_string())?
    } else {
        ureq::get(&url)
            .timeout(Duration::from_secs(8))
            .call()
            .map_err(|e| e.to_string())?
    };
    let status = resp.status();
    let body = resp.into_string().unwrap_or_default();
    if !(200..300).contains(&status) {
        return Err(format!("HTTP {status}: {body}"));
    }
    serde_json::from_str(&body).map_err(|e| e.to_string())
}

fn health_payload_reachable(v: &serde_json::Value) -> bool {
    // Any parseable /health body means the control plane answered.
    !v.is_null()
}

fn health_payload_workspace_ok(v: &serde_json::Value) -> bool {
    let status = v.get("status").and_then(|x| x.as_str()).unwrap_or("");
    let ws = v
        .get("workspace_state")
        .and_then(|x| x.as_str())
        .unwrap_or("");
    // Legacy engines without workspace_state: keep previous ok-only semantics.
    if ws.is_empty() {
        return status == "ok";
    }
    status == "ok" && (ws == "ready" || ws == "local")
}

/// Single /health fetch used by status probes (avoid double HTTP on every tick).
pub fn fetch_health() -> Option<serde_json::Value> {
    http_json("GET", "/health").ok()
}

/// /readiness: license + workspace + runtime integrity (business-usable).
pub fn fetch_readiness() -> Option<serde_json::Value> {
    http_json("GET", "/readiness").ok()
}

pub fn engine_ready() -> bool {
    fetch_readiness()
        .and_then(|v| v.get("ready").and_then(|x| x.as_bool()))
        .unwrap_or(false)
}

pub fn readiness_failure_message() -> String {
    match fetch_readiness() {
        Some(v) => {
            if v.get("ready").and_then(|x| x.as_bool()) == Some(true) {
                return String::new();
            }
            if let Some(reason) = v
                .get("license_locked_reason")
                .and_then(|x| x.as_str())
                .filter(|s| !s.is_empty())
            {
                return reason.to_string();
            }
            if let Some(reason) = v
                .get("runtime_source_reason")
                .and_then(|x| x.as_str())
                .filter(|s| !s.is_empty())
            {
                return reason.to_string();
            }
            if v.get("workspace_ready").and_then(|x| x.as_bool()) == Some(false) {
                let state = v
                    .get("workspace_state")
                    .and_then(|x| x.as_str())
                    .unwrap_or("unknown");
                return format!("工作区未就绪: {state}");
            }
            "引擎不可用于业务 API".to_string()
        }
        None => "无法读取 /readiness".to_string(),
    }
}

/// True when the engine HTTP control plane responds (any workspace state).
pub fn engine_reachable() -> bool {
    ureq::get(&format!("{ENGINE_URL}/health"))
        .timeout(Duration::from_millis(800))
        .call()
        .map(|r| r.status() == 200)
        .unwrap_or(false)
}

/// True only when engine reports status=ok and workspace_state is ready/local.
pub fn engine_workspace_healthy() -> bool {
    fetch_health()
        .map(|v| health_payload_workspace_ok(&v))
        .unwrap_or(false)
}

/// Reachable + workspace-healthy + business-ready from /health and /readiness.
pub fn engine_reach_and_healthy() -> (bool, bool) {
    match fetch_health() {
        Some(v) => {
            let reachable = health_payload_reachable(&v);
            let workspace_ok = health_payload_workspace_ok(&v);
            let ready = engine_ready();
            (reachable, workspace_ok && ready)
        }
        None => (false, false),
    }
}

fn probe_from_engine() -> WorkspaceProbeView {
    let health = fetch_health();
    let status_api = http_json("GET", "/workspace/status").ok();
    let ws = status_api
        .as_ref()
        .and_then(|v| v.get("workspace"))
        .cloned()
        .or_else(|| health.as_ref().and_then(|v| v.get("workspace")).cloned());
    let engine_status = health
        .as_ref()
        .and_then(|v| v.get("status"))
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    let state = status_api
        .as_ref()
        .and_then(|v| v.get("state"))
        .and_then(|x| x.as_str())
        .or_else(|| {
            health
                .as_ref()
                .and_then(|v| v.get("workspace_state"))
                .and_then(|x| x.as_str())
        })
        .unwrap_or("unknown")
        .to_string();
    let ok = status_api
        .as_ref()
        .and_then(|v| v.get("ok"))
        .and_then(|x| x.as_bool())
        .unwrap_or(state == "ready" || state == "local");
    let reasons = ws
        .as_ref()
        .and_then(|w| w.get("reasons"))
        .and_then(|r| r.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();
    let engine_healthy = health
        .as_ref()
        .map(health_payload_workspace_ok)
        .unwrap_or(false);
    WorkspaceProbeView {
        ok,
        state,
        data_root: ws
            .as_ref()
            .and_then(|w| w.get("data_root"))
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        reasons,
        workspace_id: ws
            .as_ref()
            .and_then(|w| w.get("workspace_id"))
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        volume_uuid: ws
            .as_ref()
            .and_then(|w| w.get("volume_uuid"))
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        engine_status,
        engine_healthy,
    }
}

fn emit_snapshot(app: &AppHandle, state: &WorkspaceEventsState) {
    if let Ok(snap) = state.last.lock() {
        let _ = app.emit(EVENT_NAME, snap.clone());
    }
}

fn set_phase(state: &WorkspaceEventsState, phase: &str, message: Option<String>, error: Option<String>) {
    if let Ok(mut last) = state.last.lock() {
        last.phase = phase.into();
        last.message = message;
        last.error = error;
        last.last_at = Some(now_iso());
    }
}

pub fn handle_volume_event(app: &AppHandle, kind: &str) {
    let state = app.state::<WorkspaceEventsState>();
    {
        let prefs = state.prefs.lock().unwrap().clone();
        let mut fire = state.last_fire.lock().unwrap();
        if let Some((prev_kind, at)) = fire.as_ref() {
            if prev_kind == kind && at.elapsed() < Duration::from_millis(prefs.event_debounce_compat()) {
                return;
            }
        }
        *fire = Some((kind.to_string(), Instant::now()));
    }

    {
        let mut last = state.last.lock().unwrap();
        last.last_kind = Some(kind.into());
        last.last_at = Some(now_iso());
        last.prefs = state.prefs.lock().unwrap().clone();
    }

    if kind == "did_unmount" {
        let probe = probe_from_engine();
        // Main-disk (local) workspace stays ready; only external path loss needs wait state.
        if probe.state == "local" || (probe.ok && probe.volume_uuid.is_none()) {
            set_phase(
                &state,
                "ready",
                Some("主盘工作区仍可用".into()),
                None,
            );
        } else {
            set_phase(
                &state,
                "waiting_for_disk",
                Some("外接路径已卸载".into()),
                None,
            );
        }
        if let Ok(mut last) = state.last.lock() {
            last.probe = Some(probe);
        }
        emit_snapshot(app, &state);
        return;
    }

    if kind == "did_mount" {
        let prefs = state.prefs.lock().unwrap().clone();
        // Skip mount noise when auto-reconnect is off (default local-first).
        if !prefs.auto_reconnect_on_mount {
            let probe = probe_from_engine();
            if let Ok(mut last) = state.last.lock() {
                last.probe = Some(probe.clone());
            }
            set_phase(
                &state,
                if probe.ok { "ready" } else { "waiting_for_disk" },
                Some(if probe.ok {
                    "工作区就绪（路径变化自动重连已关）".into()
                } else {
                    "工作区路径不可用".into()
                }),
                None,
            );
            emit_snapshot(app, &state);
            return;
        }
        set_phase(
            &state,
            "disk_settling",
            Some("检测到卷变化，等待稳定…".into()),
            None,
        );
        emit_snapshot(app, &state);
        let settle = prefs.mount_settle_ms;
        let handle = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(settle));
            let st = handle.state::<WorkspaceEventsState>();
            set_phase(&st, "checking", Some("正在检查工作区…".into()), None);
            emit_snapshot(&handle, &st);
            let probe = probe_from_engine();
            if let Ok(mut last) = st.last.lock() {
                last.probe = Some(probe.clone());
            }
            emit_snapshot(&handle, &st);
            // Edge-triggered auto reconnect: one cycle per ready volume identity.
            let cycle_key = format!(
                "{}|{}",
                probe.volume_uuid.clone().unwrap_or_default(),
                probe.workspace_id.clone().unwrap_or_default()
            );
            if probe.ok {
                let mut guard = st.last_auto_cycle.lock().unwrap();
                if guard.as_ref() == Some(&cycle_key) {
                    set_phase(&st, "ready", Some("本挂载周期已刷新过".into()), None);
                    emit_snapshot(&handle, &st);
                    return;
                }
                *guard = Some(cycle_key);
                drop(guard);
                let _ = reconnect_inner(&handle, &st, false);
            } else {
                set_phase(
                    &st,
                    "waiting_for_disk",
                    Some(probe.reasons.first().cloned().unwrap_or_else(|| "工作区未就绪".into())),
                    None,
                );
                emit_snapshot(&handle, &st);
            }
        });
    }
}

trait EventDebounceCompat {
    fn event_debounce_compat(&self) -> u64;
}

impl EventDebounceCompat for WorkspaceSyncPrefs {
    fn event_debounce_compat(&self) -> u64 {
        800
    }
}

fn reconnect_inner(app: &AppHandle, state: &WorkspaceEventsState, manual: bool) -> Result<WorkspaceStateSnapshot, String> {
    set_phase(
        state,
        "checking",
        Some(if manual {
            "手动刷新：检查工作区…".into()
        } else {
            "路径变化：检查工作区…".into()
        }),
        None,
    );
    emit_snapshot(app, state);

    let probe = probe_from_engine();
    if let Ok(mut last) = state.last.lock() {
        last.probe = Some(probe.clone());
    }
    if !probe.ok {
        let msg = probe
            .reasons
            .first()
            .cloned()
            .unwrap_or_else(|| format!("工作区不可用（{}）", probe.state));
        set_phase(state, "error", None, Some(msg.clone()));
        emit_snapshot(app, state);
        return Err(msg);
    }

    set_phase(state, "reconnecting", Some("工作区就绪，重连引擎…".into()), None);
    emit_snapshot(app, state);

    // Ask engine to bind identity / start services; if still blocked, App will restart.
    let _ = http_json("POST", "/workspace/reconnect");

    // If health still not fully ok, caller (frontend) should stop+start engine.
    let healthy = engine_workspace_healthy();
    let phase = if healthy { "ready" } else { "needs_engine_restart" };
    let message = if healthy {
        Some("工作区已刷新".into())
    } else {
        Some("工作区已识别，需重启引擎后刷新".into())
    };
    set_phase(state, phase, message, None);
    let probe2 = probe_from_engine();
    if let Ok(mut last) = state.last.lock() {
        last.probe = Some(probe2);
    }
    emit_snapshot(app, state);
    state.last.lock().map(|s| s.clone()).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn workspace_events_snapshot(
    state: State<'_, WorkspaceEventsState>,
) -> Result<WorkspaceStateSnapshot, String> {
    let mut snap = state.last.lock().map_err(|e| e.to_string())?.clone();
    snap.prefs = state.prefs.lock().map_err(|e| e.to_string())?.clone();
    snap.probe = Some(probe_from_engine());
    Ok(snap)
}

#[tauri::command]
pub fn workspace_sync_get_prefs(
    state: State<'_, WorkspaceEventsState>,
) -> Result<WorkspaceSyncPrefs, String> {
    Ok(state.prefs.lock().map_err(|e| e.to_string())?.clone())
}

#[tauri::command]
pub fn workspace_sync_set_prefs(
    state: State<'_, WorkspaceEventsState>,
    prefs: WorkspaceSyncPrefs,
) -> Result<WorkspaceSyncPrefs, String> {
    save_prefs(&prefs);
    *state.prefs.lock().map_err(|e| e.to_string())? = prefs.clone();
    if let Ok(mut last) = state.last.lock() {
        last.prefs = prefs.clone();
    }
    Ok(prefs)
}

#[tauri::command]
pub fn workspace_probe_now(
    state: State<'_, WorkspaceEventsState>,
) -> Result<WorkspaceProbeView, String> {
    let probe = probe_from_engine();
    if let Ok(mut last) = state.last.lock() {
        last.probe = Some(probe.clone());
        last.phase = if probe.ok || probe.state == "local" {
            "ready".into()
        } else {
            "waiting_for_disk".into()
        };
        last.last_at = Some(now_iso());
    }
    Ok(probe)
}

#[tauri::command]
pub fn workspace_reconnect_now(
    app: AppHandle,
    state: State<'_, WorkspaceEventsState>,
) -> Result<WorkspaceStateSnapshot, String> {
    // Clear auto-cycle so manual always runs.
    if let Ok(mut g) = state.last_auto_cycle.lock() {
        *g = None;
    }
    reconnect_inner(&app, &state, true)
}

pub fn install(app: AppHandle) {
    #[cfg(target_os = "macos")]
    {
        // Mount observers are installed from macos_observer via volume callback hook.
        let _ = app;
    }
    #[cfg(not(target_os = "macos"))]
    let _ = app;
}
