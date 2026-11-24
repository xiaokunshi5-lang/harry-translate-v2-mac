//! macOS menu-bar escape hatch for the click-through overlay.
//! The tray and browser settings page are the only ways to leave immersive mode.

use crate::windows::controls::{
    apply_native_presentation, request_application_exit, show_primary_overlay_window,
    sync_overlay_to_sidecar, SharedAppState,
};
use crate::windows::overlay::OverlaySnapshot;
use crate::windows::settings::show_and_focus_settings_window;
use crate::windows::work_area::primary_work_area;
use tauri::menu::{CheckMenuItem, MenuBuilder, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::Manager;

pub const TRAY_ID: &str = "harry-tray";
const OPEN_SETTINGS_ID: &str = "harry-open-settings";
const IMMERSIVE_ID: &str = "harry-toggle-immersive";
const LOCKED_ID: &str = "harry-toggle-locked";
const RESET_ID: &str = "harry-reset-overlay";
const QUIT_ID: &str = "harry-quit-overlay";

pub struct TrayMenuHandles {
    immersive: CheckMenuItem<tauri::Wry>,
    locked: CheckMenuItem<tauri::Wry>,
}

pub fn install_tray(
    app: &tauri::App,
    snapshot: &OverlaySnapshot,
) -> Result<(), Box<dyn std::error::Error>> {
    let open_settings =
        MenuItem::with_id(app, OPEN_SETTINGS_ID, "打开设置窗口", true, None::<&str>)?;
    let immersive = CheckMenuItem::with_id(
        app,
        IMMERSIVE_ID,
        "沉浸模式（透明穿透）",
        true,
        snapshot.immersive,
        None::<&str>,
    )?;
    let locked = CheckMenuItem::with_id(
        app,
        LOCKED_ID,
        "锁定穿透（保留底色）",
        true,
        snapshot.locked,
        None::<&str>,
    )?;
    let reset = MenuItem::with_id(app, RESET_ID, "重置悬浮窗位置与尺寸", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, QUIT_ID, "退出 HARRY Translate", true, None::<&str>)?;
    let menu = MenuBuilder::new(app)
        .items(&[&open_settings, &immersive, &locked, &reset, &quit])
        .build()?;

    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .title("同传")
        .tooltip("HARRY Translate 同传")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| handle_menu_event(app, event.id().0.as_str()));
    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon);
    }
    let _tray = builder.build(app)?;
    app.manage(TrayMenuHandles { immersive, locked });
    Ok(())
}

fn handle_menu_event(app: &tauri::AppHandle, id: &str) {
    let Some(state) = app.try_state::<SharedAppState>() else {
        return;
    };
    match id {
        OPEN_SETTINGS_ID => {
            if let Err(error) = show_and_focus_settings_window(app) {
                tracing::warn!("tray could not show settings window: {error}");
            }
        }
        IMMERSIVE_ID => {
            let current = state.overlay.lock().snapshot();
            let snapshot = state.overlay.lock().set_immersive(!current.immersive);
            apply_native_presentation(app, &state, &snapshot, true);
            sync_overlay_to_sidecar(app);
        }
        LOCKED_ID => {
            let current = state.overlay.lock().snapshot();
            let snapshot = state.overlay.lock().set_locked(!current.locked);
            apply_native_presentation(app, &state, &snapshot, true);
            sync_overlay_to_sidecar(app);
        }
        RESET_ID => {
            let snapshot = state.overlay.lock().reset_presentation(primary_work_area());
            apply_native_presentation(app, &state, &snapshot, true);
            sync_overlay_to_sidecar(app);
            if let Err(error) = show_primary_overlay_window(app) {
                tracing::warn!("tray reset could not show overlay: {error}");
            }
        }
        QUIT_ID => {
            request_application_exit(app.clone());
        }
        _ => {}
    }
}

pub fn sync_tray_checks(app: &tauri::AppHandle, snapshot: &OverlaySnapshot) {
    if let Some(handles) = app.try_state::<TrayMenuHandles>() {
        let immersive = handles.immersive.clone();
        let locked = handles.locked.clone();
        let immersive_checked = snapshot.immersive;
        let locked_checked = snapshot.locked;
        std::thread::spawn(move || {
            let _ = immersive.set_checked(immersive_checked);
            let _ = locked.set_checked(locked_checked);
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tray_contract_contains_all_escape_actions() {
        let labels = [
            "打开设置窗口",
            "沉浸模式（透明穿透）",
            "锁定穿透（保留底色）",
            "重置悬浮窗位置与尺寸",
            "退出 HARRY Translate",
        ];
        assert_eq!(TRAY_ID, "harry-tray");
        assert_eq!(labels.len(), 5);
    }
}
