//! LaunchAgent-first engine supervision (com.qr.suying.engine).
//! App prefers kickstart/attach over owning a long-lived child process.

use serde::Serialize;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::thread;
use std::time::Duration;

pub const ENGINE_PORT: u16 = 8766;
pub const AGENT_LABEL: &str = "com.qr.suying.engine";

fn home_dir() -> PathBuf {
    dirs_next::home_dir().unwrap_or_else(|| PathBuf::from("."))
}

pub fn agent_plist_path() -> PathBuf {
    home_dir()
        .join("Library/LaunchAgents")
        .join(format!("{AGENT_LABEL}.plist"))
}

pub fn agent_wrapper_path() -> PathBuf {
    home_dir().join("Suying/runtime/bin/suying-engine-agent.sh")
}

pub fn agent_err_log() -> PathBuf {
    home_dir().join("Suying/logs/engine-agent.err.log")
}

pub fn license_cache_ready() -> bool {
    let base = home_dir().join("Suying/runtime/security");
    base.join("license.suying-license").is_file() && base.join("device-key-id").is_file()
}

pub fn agent_plist_loaded() -> bool {
    let uid = unsafe { libc::getuid() };
    let target = format!("gui/{uid}/{AGENT_LABEL}");
    Command::new("launchctl")
        .args(["print", &target])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

pub fn agent_plist_exists() -> bool {
    agent_plist_path().is_file()
}

/// Prefer reinstall script from studio tree; otherwise kickstart existing.
pub fn try_kickstart_agent() -> Result<String, String> {
    if !agent_plist_exists() {
        return Err("LaunchAgent plist 未安装（com.qr.suying.engine）".into());
    }
    if !license_cache_ready() {
        return Err("缺少许可证或 device-key-id 磁盘缓存；请先打开速影完成授权".into());
    }
    let uid = unsafe { libc::getuid() };
    let target = format!("gui/{uid}/{AGENT_LABEL}");
    let out = Command::new("launchctl")
        .args(["kickstart", "-k", &target])
        .output()
        .map_err(|e| format!("launchctl kickstart 失败: {e}"))?;
    if !out.status.success() {
        let plist = agent_plist_path();
        let _ = Command::new("launchctl")
            .args([
                "bootstrap",
                &format!("gui/{uid}"),
                plist.to_str().unwrap_or(""),
            ])
            .status();
        let out2 = Command::new("launchctl")
            .args(["kickstart", "-k", &target])
            .output()
            .map_err(|e| format!("launchctl kickstart 重试失败: {e}"))?;
        if !out2.status.success() {
            let stderr = String::from_utf8_lossy(&out2.stderr);
            return Err(format!("kickstart 失败: {stderr}"));
        }
    }
    Ok(format!("已 kickstart {AGENT_LABEL}"))
}

/// Run install-engine-agent.sh from bundled/devtools studio when present.
pub fn try_install_agent(studio_root: &std::path::Path) -> Result<String, String> {
    let script = studio_root.join("scripts/install-engine-agent.sh");
    if !script.is_file() {
        return Err(format!("缺少 {}", script.display()));
    }
    if !license_cache_ready() {
        return Err("缺少许可证磁盘缓存，无法安装 LaunchAgent".into());
    }
    let out = Command::new("bash")
        .arg(&script)
        .arg("install")
        .output()
        .map_err(|e| format!("安装 agent 失败: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        let stdout = String::from_utf8_lossy(&out.stdout);
        return Err(format!(
            "install-engine-agent 失败: {}{}",
            stderr.trim(),
            if stdout.trim().is_empty() {
                String::new()
            } else {
                format!(" | {}", stdout.trim())
            }
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

pub fn wait_health_ok(max_ms: u64, step_ms: u64) -> bool {
    let steps = (max_ms / step_ms.max(1)).max(1);
    for _ in 0..steps {
        if crate::workspace_events::engine_reachable() {
            return true;
        }
        thread::sleep(Duration::from_millis(step_ms));
    }
    false
}

fn tail_file(path: &std::path::Path, max_chars: usize) -> String {
    let Ok(txt) = fs::read_to_string(path) else {
        return String::new();
    };
    if txt.len() <= max_chars {
        return txt;
    }
    txt[txt.len() - max_chars..].to_string()
}

#[derive(Debug, Clone, Serialize)]
pub struct SupervisorSnapshot {
    pub agent_label: String,
    pub agent_plist_exists: bool,
    pub agent_loaded: bool,
    pub license_cache_ready: bool,
    pub wrapper_exists: bool,
    pub port: u16,
    pub listen: bool,
    pub health_ok: bool,
    pub business_ready: bool,
    pub offline_class: String,
    pub offline_detail: String,
    pub agent_err_tail: String,
    pub engine_log_tail: String,
}

pub fn classify(
    listen: bool,
    health_ok: bool,
    business_ready: bool,
    readiness_msg: &str,
) -> (String, String) {
    if business_ready {
        return ("ok".into(), String::new());
    }
    if health_ok && !business_ready {
        let detail = if readiness_msg.is_empty() {
            "控制面在线，业务未就绪（工作区/许可/暂停）".into()
        } else {
            readiness_msg.to_string()
        };
        return ("control_plane_not_ready".into(), detail);
    }
    if listen && !health_ok {
        return (
            "listen_unhealthy".into(),
            "8766 有监听但 /health 不可用".into(),
        );
    }
    if !listen {
        if !agent_plist_exists() {
            return (
                "no_agent".into(),
                "无 LaunchAgent；关 App/重启后引擎不会自动拉起".into(),
            );
        }
        if !license_cache_ready() {
            return (
                "license_cache_missing".into(),
                "缺许可证或 device-key-id，agent/引擎无法常驻".into(),
            );
        }
        return (
            "not_listening".into(),
            "8766 未监听；可 kickstart agent 或查 engine-agent.err.log".into(),
        );
    }
    ("unknown".into(), readiness_msg.to_string())
}

pub fn snapshot(engine_log: &std::path::Path) -> SupervisorSnapshot {
    let listen = crate::listener_pid_on_port(ENGINE_PORT).is_some();
    let health_ok = crate::workspace_events::engine_reachable();
    let business_ready = crate::workspace_events::engine_ready();
    let readiness_msg = if business_ready {
        String::new()
    } else {
        crate::workspace_events::readiness_failure_message()
    };
    let (offline_class, offline_detail) =
        classify(listen, health_ok, business_ready, &readiness_msg);
    SupervisorSnapshot {
        agent_label: AGENT_LABEL.into(),
        agent_plist_exists: agent_plist_exists(),
        agent_loaded: agent_plist_loaded(),
        license_cache_ready: license_cache_ready(),
        wrapper_exists: agent_wrapper_path().is_file(),
        port: ENGINE_PORT,
        listen,
        health_ok,
        business_ready,
        offline_class,
        offline_detail,
        agent_err_tail: tail_file(&agent_err_log(), 800),
        engine_log_tail: tail_file(engine_log, 800),
    }
}
