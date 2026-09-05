//! Native file drag-out so external browsers (Chrome / Kuaishou / Douyin) receive a real
//! filesystem file (with extension + UTI), not a WebView `text/uri-list` or HTTP URL.
//!
//! HTML5 `DownloadURL` / path-as-text drops are rejected by creator upload zones as
//! “unsupported format” even when the real file is valid MP4.

use std::path::{Path, PathBuf};
use std::sync::mpsc;

use tauri::WebviewWindow;

/// Start a system drag session carrying one local video file.
/// Returns once the OS dragging session has been started (not when drop finishes).
#[tauri::command]
pub fn start_file_drag(window: WebviewWindow, path: String) -> Result<(), String> {
    let raw = path.trim();
    if raw.is_empty() {
        return Err("无本地成片路径".into());
    }
    let p = PathBuf::from(raw);
    if !p.is_file() {
        return Err(format!("成片文件不存在：{}", p.display()));
    }
    let abs = p
        .canonicalize()
        .map_err(|e| format!("无法解析路径：{e}"))?;
    ensure_video_extension(&abs)?;

    // Cocoa `beginDraggingSession` must run on the main thread.
    let (tx, rx) = mpsc::channel();
    let win = window.clone();
    window
        .run_on_main_thread(move || {
            let item = drag::DragItem::Files(vec![abs]);
            let preview = drag::Image::Raw(include_bytes!("../icons/32x32.png").to_vec());
            let r = drag::start_drag(
                &win,
                item,
                preview,
                |_result, _cursor| {},
                drag::Options::default(),
            )
            .map_err(|e| format!("原生文件拖拽失败：{e}"));
            let _ = tx.send(r);
        })
        .map_err(|e| format!("无法在主线程启动拖拽：{e}"))?;

    rx.recv()
        .map_err(|_| "原生拖拽启动被中断".to_string())?
}

fn ensure_video_extension(path: &Path) -> Result<(), String> {
    let name = path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    // Platforms parse the dropped file by extension/UTI; reject ambiguous names early.
    const OK: &[&str] = &[
        ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".flv", ".wmv", ".mpeg", ".mpg", ".ts",
    ];
    if OK.iter().any(|ext| name.ends_with(ext)) {
        return Ok(());
    }
    Err(format!(
        "成片扩展名不被平台识别（当前文件名需含 .mp4 等）：{}",
        path.display()
    ))
}
