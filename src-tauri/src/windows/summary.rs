//! Independent non-activating glass summary window.

use crate::windows::controls::SharedAppState;
use crate::windows::native::{
    close_macos_summary_window, configure_macos_summary_window, show_macos_summary_window,
};
use crate::windows::work_area::{clamp_frame_to_work_area, primary_work_area, ScreenRect};
use serde_json::Value;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tauri::{
    Emitter, LogicalPosition, LogicalSize, Manager, Position, Size, State, WebviewUrl,
    WebviewWindowBuilder, WindowEvent,
};

pub const SUMMARY_WINDOW_LABEL: &str = "summary";
pub const SUMMARY_ICON_SIZE: f64 = 48.0;
pub const SUMMARY_EXPANDED_WIDTH: f64 = 420.0;
pub const SUMMARY_EXPANDED_HEIGHT: f64 = 520.0;

#[derive(Debug)]
pub struct SummaryWindowState {
    pub expanded: bool,
    pub x: Option<f64>,
    pub y: Option<f64>,
    requested_enabled: bool,
    revision: AtomicU64,
}

impl Default for SummaryWindowState {
    fn default() -> Self {
        Self {
            expanded: false,
            x: None,
            y: None,
            requested_enabled: false,
            revision: AtomicU64::new(0),
        }
    }
}

impl SummaryWindowState {
    pub fn remember(&mut self, x: f64, y: f64, expanded: bool) {
        self.x = Some(x);
        self.y = Some(y);
        self.expanded = expanded;
        self.revision.fetch_add(1, Ordering::SeqCst);
    }

    pub fn record_move(&mut self, x: f64, y: f64) -> u64 {
        self.x = Some(x);
        self.y = Some(y);
        self.revision.fetch_add(1, Ordering::SeqCst) + 1
    }

    pub fn revision(&self) -> u64 {
        self.revision.load(Ordering::SeqCst)
    }

    pub fn set_requested_enabled(&mut self, enabled: bool) {
        self.requested_enabled = enabled;
    }

    pub fn requested_enabled(&self) -> bool {
        self.requested_enabled
    }

    pub fn reset(&mut self) {
        self.expanded = false;
        self.x = None;
        self.y = None;
        self.revision.fetch_add(1, Ordering::SeqCst);
    }
}

pub fn summary_size(expanded: bool) -> (f64, f64) {
    if expanded {
        (SUMMARY_EXPANDED_WIDTH, SUMMARY_EXPANDED_HEIGHT)
    } else {
        (SUMMARY_ICON_SIZE, SUMMARY_ICON_SIZE)
    }
}

pub fn summary_frame(
    expanded: bool,
    anchor_x: f64,
    anchor_y: f64,
    work_area: ScreenRect,
) -> ScreenRect {
    let (width, height) = summary_size(expanded);
    clamp_frame_to_work_area(
        ScreenRect {
            x: anchor_x,
            y: anchor_y,
            width,
            height,
        },
        work_area,
    )
}

pub fn transition_summary_frame(
    current: ScreenRect,
    expanded: bool,
    work_area: ScreenRect,
) -> ScreenRect {
    let right_edge = current.x + current.width;
    let (width, _height) = summary_size(expanded);
    summary_frame(expanded, right_edge - width, current.y, work_area)
}

fn geometry_path(project_root: &Path) -> PathBuf {
    project_root.join(".agents").join("summary_geometry.json")
}

fn initial_summary_frame(project_root: &Path, expanded: bool) -> ScreenRect {
    let work_area = primary_work_area();
    let path = geometry_path(project_root);
    let stored = fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok());
    let x = stored
        .as_ref()
        .and_then(|value| value.get("x"))
        .and_then(Value::as_f64);
    let y = stored
        .as_ref()
        .and_then(|value| value.get("y"))
        .and_then(Value::as_f64);
    let (width, _height) = summary_size(expanded);
    let fallback_x = work_area.x + work_area.width - width - 24.0;
    let fallback_y = work_area.y + 96.0;
    summary_frame(
        expanded,
        x.unwrap_or(fallback_x),
        y.unwrap_or(fallback_y),
        work_area,
    )
}

fn persist_summary_geometry(path: &Path, x: f64, y: f64) -> Result<(), String> {
    let payload = serde_json::json!({
        "x": x.round() as i64,
        "y": y.round() as i64,
    });
    let serialized = serde_json::to_string_pretty(&payload).map_err(|err| err.to_string())? + "\n";
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let tmp_path = path.with_extension("json.summary.tmp");
    {
        let mut file = fs::File::create(&tmp_path).map_err(|err| err.to_string())?;
        file.write_all(serialized.as_bytes())
            .map_err(|err| err.to_string())?;
        file.sync_all().map_err(|err| err.to_string())?;
    }
    fs::rename(&tmp_path, path).map_err(|err| err.to_string())
}

fn schedule_geometry_persist(
    project_root: PathBuf,
    summary: Arc<parking_lot::Mutex<SummaryWindowState>>,
    x: f64,
    y: f64,
    revision: u64,
) {
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(350));
        if summary.lock().revision() != revision {
            return;
        }
        let _ = persist_summary_geometry(&geometry_path(&project_root), x, y);
    });
}

fn install_move_tracking(window: &tauri::WebviewWindow, state: &SharedAppState) {
    let summary = Arc::clone(&state.summary);
    let project_root = state.project_root.clone();
    let scale_factor = window.scale_factor().unwrap_or(1.0);
    window.on_window_event(move |event| {
        let WindowEvent::Moved(position) = event else {
            return;
        };
        let logical = position.to_logical::<f64>(scale_factor);
        let revision = {
            let mut guard = summary.lock();
            guard.record_move(logical.x, logical.y)
        };
        schedule_geometry_persist(
            project_root.clone(),
            Arc::clone(&summary),
            logical.x,
            logical.y,
            revision,
        );
    });
}

fn create_summary_window(
    app: &tauri::AppHandle,
    state: &SharedAppState,
) -> Result<tauri::WebviewWindow, String> {
    let frame = initial_summary_frame(&state.project_root, false);
    let window = WebviewWindowBuilder::new(
        app,
        SUMMARY_WINDOW_LABEL,
        WebviewUrl::App("index.html?window=summary".into()),
    )
    .title("HARRY Summary")
    .inner_size(frame.width, frame.height)
    .position(frame.x, frame.y)
    .transparent(true)
    .decorations(false)
    .always_on_top(true)
    .shadow(false)
    .resizable(false)
    .skip_taskbar(true)
    .focused(false)
    .accept_first_mouse(true)
    .visible(false)
    .build()
    .map_err(|error| error.to_string())?;
    configure_macos_summary_window(&window)?;
    install_move_tracking(&window, state);
    show_macos_summary_window(&window)?;
    state.summary.lock().remember(frame.x, frame.y, false);
    Ok(window)
}

fn schedule_summary_reconcile(app: tauri::AppHandle) {
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(50));
        let task_app = app.clone();
        let _ = app.run_on_main_thread(move || {
            let state = task_app.state::<SharedAppState>();
            if !state.summary.lock().requested_enabled() {
                return;
            }
            if let Some(window) = task_app.get_webview_window(SUMMARY_WINDOW_LABEL) {
                if let Err(error) = configure_macos_summary_window(&window)
                    .and_then(|_| show_macos_summary_window(&window))
                {
                    tracing::warn!("failed to reconcile summary window: {error}");
                }
                return;
            }
            if let Err(error) = create_summary_window(&task_app, &state) {
                tracing::error!("failed to create summary window: {error}");
            }
        });
    });
}

#[tauri::command]
pub fn summary_set_enabled(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
    enabled: bool,
) -> Result<bool, String> {
    if !enabled {
        state.summary.lock().set_requested_enabled(false);
        if let Some(window) = app.get_webview_window(SUMMARY_WINDOW_LABEL) {
            close_macos_summary_window(&window)?;
        }
        let geometry = {
            let mut guard = state.summary.lock();
            let geometry = guard.x.zip(guard.y);
            guard.reset();
            geometry
        };
        if let Some((x, y)) = geometry {
            let _ = persist_summary_geometry(&geometry_path(&state.project_root), x, y);
        }
        return Ok(false);
    }
    state.summary.lock().set_requested_enabled(true);
    schedule_summary_reconcile(app);
    Ok(true)
}

#[tauri::command]
pub fn summary_set_expanded(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
    expanded: bool,
) -> Result<bool, String> {
    let window = app
        .get_webview_window(SUMMARY_WINDOW_LABEL)
        .ok_or_else(|| "summary_window_missing".to_string())?;
    let scale_factor = window.scale_factor().unwrap_or(1.0);
    let position = window
        .outer_position()
        .map_err(|error| error.to_string())?
        .to_logical::<f64>(scale_factor);
    let size = window
        .outer_size()
        .map_err(|error| error.to_string())?
        .to_logical::<f64>(scale_factor);
    let current = ScreenRect {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
    };
    let frame = transition_summary_frame(current, expanded, primary_work_area());
    window
        .set_size(Size::Logical(LogicalSize::new(frame.width, frame.height)))
        .map_err(|error| error.to_string())?;
    window
        .set_position(Position::Logical(LogicalPosition::new(frame.x, frame.y)))
        .map_err(|error| error.to_string())?;
    state.summary.lock().remember(frame.x, frame.y, expanded);
    Ok(expanded)
}

#[tauri::command]
pub fn summary_close(
    app: tauri::AppHandle,
    state: State<'_, SharedAppState>,
) -> Result<(), String> {
    state.summary.lock().set_requested_enabled(false);
    if let Some(window) = app.get_webview_window(SUMMARY_WINDOW_LABEL) {
        close_macos_summary_window(&window)?;
    }
    let geometry = {
        let mut guard = state.summary.lock();
        let geometry = guard.x.zip(guard.y);
        guard.reset();
        geometry
    };
    if let Some((x, y)) = geometry {
        let _ = persist_summary_geometry(&geometry_path(&state.project_root), x, y);
    }
    Ok(())
}

#[tauri::command]
pub fn summary_forward_update(app: tauri::AppHandle, event: Value) -> Result<bool, String> {
    if app.get_webview_window(SUMMARY_WINDOW_LABEL).is_none() {
        return Ok(false);
    }
    app.emit_to(SUMMARY_WINDOW_LABEL, "summary-update", event)
        .map_err(|error| error.to_string())?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn work_area() -> ScreenRect {
        ScreenRect {
            x: 0.0,
            y: 24.0,
            width: 1440.0,
            height: 876.0,
        }
    }

    #[test]
    fn icon_and_expanded_dimensions_are_exact() {
        assert_eq!(summary_size(false), (48.0, 48.0));
        assert_eq!(summary_size(true), (420.0, 520.0));
    }

    #[test]
    fn requested_enablement_is_tracked_independently_from_geometry_reset() {
        let mut state = SummaryWindowState::default();
        assert!(!state.requested_enabled());
        state.set_requested_enabled(true);
        state.reset();
        assert!(state.requested_enabled());
        state.set_requested_enabled(false);
        assert!(!state.requested_enabled());
    }

    #[test]
    fn transition_preserves_right_edge_and_clamps_into_work_area() {
        let initial = summary_frame(false, 1360.0, 96.0, work_area());
        let expanded = transition_summary_frame(initial, true, work_area());
        assert_eq!(expanded.width, 420.0);
        assert_eq!(expanded.height, 520.0);
        assert_eq!(
            (expanded.x + expanded.width - (initial.x + initial.width)).abs(),
            0.0
        );

        let collapsed = transition_summary_frame(expanded, false, work_area());
        assert_eq!(collapsed.width, 48.0);
        assert_eq!(collapsed.height, 48.0);
        assert!(collapsed.x >= work_area().x);
        assert!(collapsed.x + collapsed.width <= work_area().x + work_area().width);
    }

    #[test]
    fn geometry_is_persisted_outside_business_settings() {
        let root =
            std::env::temp_dir().join(format!("harry-summary-geometry-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let path = geometry_path(&root);
        persist_summary_geometry(&path, 456.4, 123.6).unwrap();
        let parsed: Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
        assert_eq!(parsed["x"], 456);
        assert_eq!(parsed["y"], 124);
        assert!(!root.join("config").join("user_settings.json").exists());
        let _ = fs::remove_dir_all(&root);
    }
}
