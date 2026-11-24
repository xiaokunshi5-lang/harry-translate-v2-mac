//! Tauri v2 Mac single-window thin shell + Python FastAPI Sidecar coordinator.

pub mod sidecar;
pub mod windows;

use parking_lot::Mutex;
use sidecar::PythonSidecarManager;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tauri::menu::{Menu, MenuBuilder, MenuEvent, MenuItem, PredefinedMenuItem, SubmenuBuilder};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent, Wry};
use windows::controls::{self, persist_snapshot_best_effort, OverlayControlServer, SharedAppState};
use windows::macos_pointer::PointerPassThroughState;
use windows::macos_space::{install_space_observer, SpaceFollowCoordinator};
use windows::native::{configure_macos_overlay_window, NativePanelKind};
use windows::overlay::{OverlayState, PRIMARY_OVERLAY_LABEL};
use windows::settings::show_and_focus_settings_window;
use windows::summary::SummaryWindowState;
use windows::tray::install_tray;

const MENU_OPEN_SETTINGS_ID: &str = "harry-menu-open-settings";
const MENU_QUIT_ID: &str = "harry-menu-quit";

fn resolve_project_root() -> PathBuf {
    if let Ok(explicit) = std::env::var("HARRY_PROJECT_ROOT") {
        let candidate = PathBuf::from(explicit);
        if candidate.is_dir() {
            return candidate;
        }
    }
    if cfg!(debug_assertions) {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        if let Some(root) = manifest_dir.parent() {
            return root.to_path_buf();
        }
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let app_data = home
        .join("Library")
        .join("Application Support")
        .join("HARRY Translate");
    let _ = std::fs::create_dir_all(&app_data);
    app_data
}

fn should_open_settings() -> bool {
    if std::env::var("HARRY_NO_OPEN_SETTINGS").as_deref() == Ok("1") {
        return false;
    }
    !std::env::args().any(|arg| arg == "--no-open-settings")
}

fn build_app_menu(app: &AppHandle<Wry>) -> tauri::Result<Menu<Wry>> {
    let open_settings = MenuItem::with_id(
        app,
        MENU_OPEN_SETTINGS_ID,
        "打开设置窗口",
        true,
        Some("CmdOrCtrl+,"),
    )?;
    let quit = MenuItem::with_id(
        app,
        MENU_QUIT_ID,
        "退出 HARRY Translate",
        true,
        Some("CmdOrCtrl+Q"),
    )?;
    let about = PredefinedMenuItem::about(app, None, None)?;

    let app_menu = SubmenuBuilder::new(app, app.package_info().name.clone())
        .item(&about)
        .separator()
        .item(&open_settings)
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        .item(&quit)
        .build()?;
    let edit_menu = SubmenuBuilder::new(app, "编辑")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;
    let window_menu = SubmenuBuilder::new(app, "窗口")
        .minimize()
        .maximize()
        .separator()
        .close_window()
        .build()?;
    MenuBuilder::new(app)
        .items(&[&app_menu, &edit_menu, &window_menu])
        .build()
}

fn handle_app_menu_event(app: &AppHandle<Wry>, event: MenuEvent) {
    match event.id().0.as_str() {
        MENU_OPEN_SETTINGS_ID => {
            if let Err(error) = show_and_focus_settings_window(app) {
                tracing::warn!("app menu could not show settings window: {error}");
            }
        }
        MENU_QUIT_ID => controls::request_application_exit(app.clone()),
        _ => {}
    }
}

pub fn run() {
    let project_root = resolve_project_root();
    let sidecar = Arc::new(PythonSidecarManager::new(project_root.clone(), None));
    let overlay = Arc::new(Mutex::new(OverlayState::default()));
    let pointer = Arc::new(Mutex::new(PointerPassThroughState::default()));
    let summary = Arc::new(Mutex::new(SummaryWindowState::default()));
    let space_coordinator = Arc::new(SpaceFollowCoordinator::new());
    let space_coordinator_for_events = Arc::clone(&space_coordinator);
    let geometry_revision = Arc::new(AtomicU64::new(0));

    let shared_state = SharedAppState {
        project_root: project_root.clone(),
        sidecar: Arc::clone(&sidecar),
        overlay: Arc::clone(&overlay),
        pointer: Arc::clone(&pointer),
        summary: Arc::clone(&summary),
        geometry_revision: Arc::clone(&geometry_revision),
        quitting: Arc::new(std::sync::atomic::AtomicBool::new(false)),
    };

    let sidecar_for_setup = Arc::clone(&sidecar);
    let sidecar_for_events = Arc::clone(&sidecar);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if should_open_settings() {
                if let Err(error) = show_and_focus_settings_window(app) {
                    tracing::warn!("secondary launch could not show settings window: {error}");
                }
            }
        }))
        .plugin(tauri_nspanel::init())
        .plugin(tauri_plugin_opener::init())
        .menu(build_app_menu)
        .on_menu_event(handle_app_menu_event)
        .manage(shared_state)
        .invoke_handler(tauri::generate_handler![
            windows::controls::app_quit,
            windows::controls::get_backend_endpoint,
            windows::controls::open_auxiliary_monitor_in_browser,
            windows::controls::overlay_get_state,
            windows::controls::overlay_get_visibility,
            windows::controls::overlay_native_diagnostics,
            windows::controls::overlay_set_mode,
            windows::controls::overlay_set_locked,
            windows::controls::overlay_sync_presentation,
            windows::controls::overlay_start_drag,
            windows::controls::overlay_resize,
            windows::controls::overlay_commit_geometry,
            windows::controls::overlay_set_geometry,
            windows::controls::overlay_open_settings_in_browser,
            windows::controls::overlay_list_system_fonts,
            windows::summary::summary_set_enabled,
            windows::summary::summary_set_expanded,
            windows::summary::summary_close,
            windows::summary::summary_forward_update,
        ])
        .setup(move |app| {
            let initial_snapshot = app.state::<SharedAppState>().overlay.lock().snapshot();
            if let Err(error) = install_tray(app, &initial_snapshot) {
                tracing::error!("failed to install tray escape hatch: {error}");
            }

            let control_url = match OverlayControlServer::start(app.handle().clone()) {
                Ok(server) => {
                    let url = server.control_url();
                    app.manage(server);
                    Some(url)
                }
                Err(error) => {
                    tracing::error!("failed to start overlay control service: {error}");
                    None
                }
            };

            if let Some(overlay_window) = app.get_webview_window(PRIMARY_OVERLAY_LABEL) {
                let state = app.state::<SharedAppState>();
                let work_area = windows::work_area::primary_work_area();
                let settings_path = state.project_root.join("config").join("user_settings.json");
                let loaded = OverlayState::from_settings_file(&settings_path, work_area);
                let snapshot = {
                    let mut guard = state.overlay.lock();
                    *guard = loaded;
                    guard.snapshot()
                };

                configure_macos_overlay_window(&overlay_window, NativePanelKind::Overlay)
                    .map_err(|error| std::io::Error::other(error))?;
                let _ = install_space_observer(
                    app.handle().clone(),
                    overlay_window.clone(),
                    Arc::clone(&space_coordinator),
                );
                windows::controls::apply_presentation_frame(app.handle(), &snapshot);
                controls::apply_native_presentation(app.handle(), &state, &snapshot, false);

                let overlay_for_events = Arc::clone(&state.overlay);
                let project_root_for_events = state.project_root.clone();
                let revision_for_events = Arc::clone(&state.geometry_revision);
                let scale_factor = overlay_window.scale_factor().unwrap_or(1.0);
                overlay_window.on_window_event(move |event| {
                    let WindowEvent::Moved(position) = event else {
                        return;
                    };
                    let logical = position.to_logical::<f64>(scale_factor);
                    overlay_for_events
                        .lock()
                        .set_user_position(logical.x, logical.y);
                    let revision = revision_for_events.fetch_add(1, Ordering::SeqCst) + 1;
                    let overlay_for_timer = Arc::clone(&overlay_for_events);
                    let revision_for_timer = Arc::clone(&revision_for_events);
                    let project_root_for_timer = project_root_for_events.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(Duration::from_millis(350));
                        if revision_for_timer.load(Ordering::SeqCst) != revision {
                            return;
                        }
                        let snapshot = overlay_for_timer.lock().snapshot();
                        persist_snapshot_best_effort(&project_root_for_timer, &snapshot);
                    });
                });
            }

            let skip_sidecar = std::env::var("HARRY_SKIP_SIDECAR").as_deref() == Ok("1");
            let endpoint = if skip_sidecar {
                None
            } else {
                match sidecar_for_setup.start(Some(app.handle().clone())) {
                    Ok(endpoint) => Some(endpoint),
                    Err(error) => {
                        tracing::error!("failed to start Python FastAPI sidecar: {error}");
                        None
                    }
                }
            };

            if let (Some(endpoint), Some(control_url)) = (endpoint.as_ref(), control_url.as_ref()) {
                let visible = app.state::<OverlayControlServer>().is_visible();
                if let Err(error) = controls::register_overlay_control_blocking(
                    endpoint,
                    control_url.clone(),
                    visible,
                ) {
                    tracing::warn!(
                        "initial overlay control registration failed; retrying in background: {error}"
                    );
                    controls::register_overlay_control(
                        endpoint,
                        control_url.clone(),
                        visible,
                    );
                }
            }

            if endpoint.is_some() && should_open_settings() {
                if let Err(error) = show_and_focus_settings_window(app.handle()) {
                    tracing::warn!("could not open native settings window: {error}");
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building HARRY Translate Mac Tauri application");

    app.run(move |app_handle, event| match event {
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => {
            if let Err(error) = show_and_focus_settings_window(app_handle) {
                tracing::warn!("Dock reopen could not show settings window: {error}");
            }
        }
        RunEvent::ExitRequested {
            code: None, api, ..
        } => {
            api.prevent_exit();
            controls::request_application_exit(app_handle.clone());
        }
        RunEvent::ExitRequested { .. } | RunEvent::Exit => {
            space_coordinator_for_events.cancel();
            sidecar_for_events.shutdown();
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    #[test]
    fn default_build_enables_custom_protocol_for_embedded_frontend() {
        assert!(cfg!(feature = "custom-protocol"));
        assert!(!tauri::is_dev());
    }
}
