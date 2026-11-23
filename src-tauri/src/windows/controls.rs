//! Tauri IPC command handlers for Sidecar discovery and single-window overlay controls.
//! Adapted from MIT `yuxino/Mimi` (commit `bce7164`, v1.7.6).

use crate::sidecar::{PythonSidecarManager, SidecarEndpoint};
use crate::windows::macos_drag::start_native_drag;
use crate::windows::macos_pointer::{apply_ignore_cursor_events, PointerPassThroughState};
use crate::windows::native::{
    configure_macos_overlay_window, hide_macos_panel, native_panel_diagnostics, show_macos_panel,
    NativePanelDiagnostics, NativePanelKind,
};
use crate::windows::overlay::{
    persist_hud_geometry, OverlayDisplayMode, OverlayMode, OverlaySnapshot, OverlayState,
    PRIMARY_OVERLAY_LABEL,
};
use crate::windows::resize::ResizeEdge;
use crate::windows::summary::SummaryWindowState;
use crate::windows::work_area::primary_work_area;
use parking_lot::Mutex;
use serde::Serialize;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::Duration;
use tauri::{Emitter, LogicalPosition, LogicalSize, Manager, Position, Size, State};
use tauri_plugin_opener::OpenerExt;

pub struct SharedAppState {
    pub project_root: PathBuf,
    pub sidecar: Arc<PythonSidecarManager>,
    pub overlay: Arc<Mutex<OverlayState>>,
    pub pointer: Arc<Mutex<PointerPassThroughState>>,
    pub summary: Arc<Mutex<SummaryWindowState>>,
    pub geometry_revision: Arc<AtomicU64>,
}

pub struct OverlayControlServer {
    address: SocketAddr,
    visible: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OverlayNativeDiagnostics {
    overlay: NativePanelDiagnostics,
    summary: Option<NativePanelDiagnostics>,
}

impl OverlayControlServer {
    pub fn start(app: tauri::AppHandle) -> std::io::Result<Self> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        listener.set_nonblocking(true)?;
        let stop = Arc::new(AtomicBool::new(false));
        let visible = Arc::new(AtomicBool::new(false));
        let stop_for_thread = Arc::clone(&stop);
        let visible_for_thread = Arc::clone(&visible);
        let thread = thread::spawn(move || {
            while !stop_for_thread.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        handle_control_connection(stream, &app, Arc::clone(&visible_for_thread));
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(20));
                    }
                    Err(error) => {
                        tracing::warn!("overlay control accept failed: {error}");
                        thread::sleep(Duration::from_millis(50));
                    }
                }
            }
        });
        Ok(Self {
            address,
            visible,
            stop,
            thread: Some(thread),
        })
    }

    pub fn control_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.address.port())
    }

    pub fn is_visible(&self) -> bool {
        self.visible.load(Ordering::SeqCst)
    }

    pub fn set_visible(&self, visible: bool) {
        self.visible.store(visible, Ordering::SeqCst);
    }
}

impl Drop for OverlayControlServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn write_control_response(stream: &mut TcpStream, status: &str, body: &str) {
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let _ = stream.write_all(response.as_bytes());
}

fn handle_control_connection(
    mut stream: TcpStream,
    app: &tauri::AppHandle,
    visible: Arc<AtomicBool>,
) {
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    let mut buffer = [0_u8; 4096];
    let Ok(read) = stream.read(&mut buffer) else {
        return;
    };
    if read == 0 {
        return;
    }
    let request = String::from_utf8_lossy(&buffer[..read]);
    let Some(request_line) = request.lines().next() else {
        return;
    };
    let mut parts = request_line.split_whitespace();
    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("");
    match (method, path) {
        ("GET", "/health") => {
            let body = serde_json::json!({
                "ok": true,
                "visible": visible.load(Ordering::SeqCst),
            })
            .to_string();
            write_control_response(&mut stream, "200 OK", &body);
        }
        ("GET", "/diagnostics") => match collect_native_diagnostics(app) {
            Ok(diagnostics) => {
                let body = serde_json::to_string(&diagnostics).unwrap_or_else(|_| {
                    r#"{"ok":false,"error":"diagnostics_serialization_failed"}"#.to_string()
                });
                write_control_response(&mut stream, "200 OK", &body);
            }
            Err(error) => {
                let body = serde_json::json!({
                    "ok": false,
                    "error": error,
                })
                .to_string();
                write_control_response(&mut stream, "503 Service Unavailable", &body);
            }
        },
        ("POST", "/show") | ("POST", "/hide") => {
            let show = method == "POST" && path == "/show";
            let result = if show {
                show_primary_overlay_window(app)
            } else {
                hide_primary_overlay(app)
            };
            if let Err(error) = result {
                let body = serde_json::json!({
                    "ok": false,
                    "error": error,
                })
                .to_string();
                write_control_response(&mut stream, "503 Service Unavailable", &body);
                return;
            }
            visible.store(show, Ordering::SeqCst);
            let _ = app.emit_to(
                PRIMARY_OVERLAY_LABEL,
                "overlay-visibility-changed",
                serde_json::json!({ "visible": show }),
            );
            let body = serde_json::json!({
                "ok": true,
                "visible": show,
            })
            .to_string();
            write_control_response(&mut stream, "200 OK", &body);
        }
        ("POST", "/shutdown") => {
            write_control_response(
                &mut stream,
                "202 Accepted",
                r#"{"ok":true,"action":"shutdown"}"#,
            );
            let app_for_exit = app.clone();
            thread::spawn(move || {
                thread::sleep(Duration::from_millis(25));
                app_for_exit.exit(0);
            });
        }
        _ => {
            write_control_response(
                &mut stream,
                "404 Not Found",
                r#"{"ok":false,"error":"not_found"}"#,
            );
        }
    }
}

pub(crate) fn show_primary_overlay_window(app: &tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window(PRIMARY_OVERLAY_LABEL)
        .ok_or_else(|| "overlay_window_missing".to_string())?;
    configure_macos_overlay_window(&window, NativePanelKind::Overlay)?;
    show_macos_panel(app, PRIMARY_OVERLAY_LABEL)?;
    if let Some(server) = app.try_state::<OverlayControlServer>() {
        server.set_visible(true);
    }
    Ok(())
}

pub(crate) fn hide_primary_overlay(app: &tauri::AppHandle) -> Result<(), String> {
    hide_macos_panel(app, PRIMARY_OVERLAY_LABEL)?;
    if let Some(server) = app.try_state::<OverlayControlServer>() {
        server.set_visible(false);
    }
    Ok(())
}

fn collect_native_diagnostics(app: &tauri::AppHandle) -> Result<OverlayNativeDiagnostics, String> {
    let overlay = native_panel_diagnostics(app, PRIMARY_OVERLAY_LABEL)?;
    let summary = app
        .get_webview_window(crate::windows::summary::SUMMARY_WINDOW_LABEL)
        .and_then(|_| {
            native_panel_diagnostics(app, crate::windows::summary::SUMMARY_WINDOW_LABEL).ok()
        });
    Ok(OverlayNativeDiagnostics { overlay, summary })
}

fn register_with_sidecar(endpoint: &SidecarEndpoint, control_url: &str) -> Result<(), String> {
    let body = serde_json::json!({ "control_url": control_url }).to_string();
    let request = format!(
        "POST /api/overlay/register HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        endpoint.port,
        body.len(),
        body
    );
    let address: SocketAddr = ([127, 0, 0, 1], endpoint.port).into();
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(500))
        .map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_millis(750)))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_millis(500)))
        .map_err(|e| e.to_string())?;
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut response = [0_u8; 128];
    let read = stream.read(&mut response).map_err(|e| e.to_string())?;
    let head = String::from_utf8_lossy(&response[..read]);
    if head.starts_with("HTTP/1.1 2") || head.starts_with("HTTP/1.0 2") {
        Ok(())
    } else {
        Err(format!("sidecar rejected control registration: {head}"))
    }
}

pub(crate) fn register_overlay_control(endpoint: &SidecarEndpoint, control_url: String) {
    let endpoint = endpoint.clone();
    thread::spawn(move || {
        for attempt in 0..3 {
            match register_with_sidecar(&endpoint, &control_url) {
                Ok(()) => return,
                Err(error) if attempt < 2 => {
                    tracing::debug!(
                        "overlay control registration attempt {} failed: {error}",
                        attempt + 1
                    );
                    thread::sleep(Duration::from_millis(75));
                }
                Err(error) => {
                    tracing::warn!("overlay control registration failed: {error}");
                }
            }
        }
    });
}

pub(crate) fn apply_presentation_frame(app: &tauri::AppHandle, snapshot: &OverlaySnapshot) {
    if let Some(window) = app.get_webview_window(PRIMARY_OVERLAY_LABEL) {
        let frame = snapshot.presentation_frame;
        let _ = window.set_size(Size::Logical(LogicalSize::new(frame.width, frame.height)));
        let _ = window.set_position(Position::Logical(LogicalPosition::new(frame.x, frame.y)));
    }
}

pub(crate) fn persist_snapshot_best_effort(project_root: &PathBuf, snapshot: &OverlaySnapshot) {
    let settings_path = project_root.join("config").join("user_settings.json");
    let _ = persist_hud_geometry(&settings_path, snapshot);
}

pub(crate) fn apply_native_presentation(
    app: &tauri::AppHandle,
    state: &SharedAppState,
    snapshot: &OverlaySnapshot,
    persist: bool,
) {
    apply_presentation_frame(app, snapshot);
    let ignore_cursor_events = snapshot.immersive || (snapshot.locked && snapshot.click_through);
    let should_apply = state.pointer.lock().apply_if_changed(ignore_cursor_events);
    if should_apply {
        if let Some(window) = app.get_webview_window(PRIMARY_OVERLAY_LABEL) {
            let _ = apply_ignore_cursor_events(&window, ignore_cursor_events);
        }
    }
    if persist {
        persist_snapshot_best_effort(&state.project_root, snapshot);
    }
    crate::windows::tray::sync_tray_checks(app, snapshot);
    let _ = app.emit_to(
        PRIMARY_OVERLAY_LABEL,
        "overlay-presentation-changed",
        snapshot.clone(),
    );
}

pub(crate) fn settings_url(endpoint: &SidecarEndpoint) -> String {
    format!("{}/settings", endpoint.base_url.trim_end_matches('/'))
}

pub(crate) fn open_settings_in_browser(app: &tauri::AppHandle) -> Result<(), String> {
    let state = app
        .try_state::<SharedAppState>()
        .ok_or_else(|| "overlay_state_missing".to_string())?;
    let endpoint = state
        .sidecar
        .current_endpoint()
        .ok_or_else(|| "sidecar_not_ready".to_string())?;
    app.opener()
        .open_url(settings_url(&endpoint), None::<&str>)
        .map_err(|error| error.to_string())
}

fn put_overlay_settings(
    endpoint: &SidecarEndpoint,
    snapshot: &OverlaySnapshot,
) -> Result<(), String> {
    let body = serde_json::json!({
        "settings": {
            "overlay_immersive": snapshot.immersive,
            "is_overlay_locked": snapshot.locked,
            "overlay_click_through": snapshot.click_through,
            "hud_geometry": {
                "locked": snapshot.locked,
            },
        },
    })
    .to_string();
    let request = format!(
        "PUT /api/config HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        endpoint.port,
        body.len(),
        body
    );
    let address: SocketAddr = ([127, 0, 0, 1], endpoint.port).into();
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(250))
        .map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_millis(500)))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_millis(500)))
        .map_err(|e| e.to_string())?;
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut response = [0u8; 64];
    let _ = stream.read(&mut response);
    Ok(())
}

pub(crate) fn sync_overlay_to_sidecar(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<SharedAppState>() else {
        return;
    };
    let Some(endpoint) = state.sidecar.current_endpoint() else {
        return;
    };
    let snapshot = state.overlay.lock().snapshot();
    std::thread::spawn(move || {
        let _ = put_overlay_settings(&endpoint, &snapshot);
    });
}

#[tauri::command]
pub fn get_backend_endpoint(state: State<'_, SharedAppState>) -> Result<SidecarEndpoint, String> {
    if let Some(endpoint) = state.sidecar.current_endpoint() {
        return Ok(endpoint);
    }
    Err("sidecar_not_ready".to_string())
}

#[tauri::command]
pub fn overlay_get_state(state: State<'_, SharedAppState>) -> OverlaySnapshot {
    state.overlay.lock().snapshot()
}

#[tauri::command]
pub fn overlay_native_diagnostics(
    app: tauri::AppHandle,
) -> Result<OverlayNativeDiagnostics, String> {
    collect_native_diagnostics(&app)
}

#[tauri::command]
pub fn overlay_get_visibility(app: tauri::AppHandle) -> bool {
    let Some(window) = app.get_webview_window(PRIMARY_OVERLAY_LABEL) else {
        return false;
    };
    window.is_visible().unwrap_or(false)
}

#[tauri::command]
pub fn overlay_set_mode(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
    mode: OverlayMode,
) -> Result<OverlaySnapshot, String> {
    let work_area = primary_work_area();
    let snapshot = state.overlay.lock().set_mode(mode, work_area);
    apply_native_presentation(&app, &state, &snapshot, true);
    Ok(snapshot)
}

#[tauri::command]
pub fn overlay_set_locked(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
    locked: bool,
) -> Result<OverlaySnapshot, String> {
    let snapshot = state.overlay.lock().set_locked(locked);
    apply_native_presentation(&app, &state, &snapshot, true);
    Ok(snapshot)
}

#[tauri::command]
pub fn overlay_sync_presentation(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
    immersive: bool,
    locked: bool,
    click_through: bool,
    display_mode: Option<String>,
    persist: Option<bool>,
) -> Result<OverlaySnapshot, String> {
    let snapshot = {
        let mut guard = state.overlay.lock();
        if let Some(mode) = display_mode {
            let work_area = primary_work_area();
            guard.resize_for_only_mode(OverlayDisplayMode::from_str_mode(&mode), work_area);
        }
        guard.set_presentation(immersive, locked, click_through)
    };
    apply_native_presentation(&app, &state, &snapshot, persist.unwrap_or(false));
    Ok(snapshot)
}

#[tauri::command]
pub fn overlay_start_drag(app: tauri::AppHandle) -> Result<(), String> {
    let Some(window) = app.get_webview_window(PRIMARY_OVERLAY_LABEL) else {
        return Err("overlay_window_missing".to_string());
    };
    start_native_drag(&window)
}

#[tauri::command]
pub fn overlay_resize(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
    edge: String,
    dx: f64,
    dy: f64,
) -> Result<OverlaySnapshot, String> {
    let parsed_edge =
        ResizeEdge::from_str_edge(&edge).ok_or_else(|| format!("invalid resize edge: {edge}"))?;
    let work_area = primary_work_area();
    let snapshot = state
        .overlay
        .lock()
        .resize_by_edge(parsed_edge, dx, dy, work_area);
    apply_presentation_frame(&app, &snapshot);
    Ok(snapshot)
}

#[tauri::command]
pub fn overlay_commit_geometry(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
) -> Result<OverlaySnapshot, String> {
    let snapshot = state.overlay.lock().snapshot();
    apply_presentation_frame(&app, &snapshot);
    persist_snapshot_best_effort(&state.project_root, &snapshot);
    Ok(snapshot)
}

#[tauri::command]
pub fn overlay_set_geometry(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
) -> Result<OverlaySnapshot, String> {
    let work_area = primary_work_area();
    let snapshot = state
        .overlay
        .lock()
        .set_user_geometry(x, y, width, height, work_area);
    apply_native_presentation(&app, &state, &snapshot, true);
    Ok(snapshot)
}

#[tauri::command]
pub fn overlay_open_settings_in_browser(app: tauri::AppHandle) -> Result<(), String> {
    open_settings_in_browser(&app)
}

#[tauri::command]
pub fn overlay_list_system_fonts() -> Vec<String> {
    vec![
        "PingFang SC".to_string(),
        "SF Pro Display".to_string(),
        "SF Pro Text".to_string(),
        "Hiragino Sans GB".to_string(),
        "Helvetica Neue".to_string(),
    ]
}

#[cfg(test)]
mod tests {
    #[test]
    fn visibility_ipc_targets_the_primary_window_and_fails_closed() {
        let source = include_str!("controls.rs");
        let implementation = source
            .split("#[cfg(test)]")
            .next()
            .expect("controls implementation");
        assert!(implementation.contains("pub fn overlay_get_visibility"));
        assert!(implementation.contains("get_webview_window(PRIMARY_OVERLAY_LABEL)"));
        assert!(implementation.contains("is_visible().unwrap_or(false)"));
        assert!(include_str!("../lib.rs").contains("overlay_get_visibility"));
        assert_eq!(crate::windows::overlay::MAX_OVERLAY_WINDOWS, 1);
    }
}
