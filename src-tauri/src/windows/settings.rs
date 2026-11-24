//! Native settings window lifecycle.
//!
//! The settings surface is a regular macOS window. It is intentionally created
//! at runtime so `tauri.conf.json` keeps its single declared overlay window.

use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};

pub const SETTINGS_WINDOW_LABEL: &str = "settings";
const SETTINGS_TITLE: &str = "HARRY Translate";
const SETTINGS_WIDTH: f64 = 1120.0;
const SETTINGS_HEIGHT: f64 = 760.0;
const SETTINGS_MIN_WIDTH: f64 = 820.0;
const SETTINGS_MIN_HEIGHT: f64 = 580.0;

pub fn ensure_settings_window(app: &AppHandle) -> Result<WebviewWindow, String> {
    if let Some(window) = app.get_webview_window(SETTINGS_WINDOW_LABEL) {
        return Ok(window);
    }

    let window = WebviewWindowBuilder::new(
        app,
        SETTINGS_WINDOW_LABEL,
        WebviewUrl::App("/settings".into()),
    )
    .title(SETTINGS_TITLE)
    .inner_size(SETTINGS_WIDTH, SETTINGS_HEIGHT)
    .min_inner_size(SETTINGS_MIN_WIDTH, SETTINGS_MIN_HEIGHT)
    .center()
    .resizable(true)
    .decorations(true)
    .transparent(false)
    .always_on_top(false)
    .skip_taskbar(false)
    .visible(false)
    .build()
    .map_err(|error| format!("settings_window_build_failed: {error}"))?;

    let window_for_close = window.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            if let Err(error) = window_for_close.hide() {
                tracing::warn!("could not hide settings window: {error}");
            }
        }
    });

    Ok(window)
}

pub fn show_and_focus_settings_window(app: &AppHandle) -> Result<(), String> {
    let window = ensure_settings_window(app)?;
    if window.is_minimized().unwrap_or(false) {
        window
            .unminimize()
            .map_err(|error| format!("settings_window_unminimize_failed: {error}"))?;
    }
    window
        .show()
        .map_err(|error| format!("settings_window_show_failed: {error}"))?;
    window
        .set_focus()
        .map_err(|error| format!("settings_window_focus_failed: {error}"))?;
    activate_application();
    Ok(())
}

#[cfg(target_os = "macos")]
fn activate_application() {
    let Some(main_thread) = objc2::MainThreadMarker::new() else {
        return;
    };
    let application = objc2_app_kit::NSApplication::sharedApplication(main_thread);
    #[allow(deprecated)]
    application.activateIgnoringOtherApps(true);
}

#[cfg(not(target_os = "macos"))]
fn activate_application() {}

#[cfg(test)]
mod tests {
    #[test]
    fn settings_window_is_a_regular_runtime_webview_window() {
        let source = include_str!("settings.rs");
        let implementation = source.split("#[cfg(test)]").next().unwrap();
        assert!(implementation.contains("SETTINGS_WINDOW_LABEL: &str = \"settings\""));
        assert!(implementation.contains("WebviewWindowBuilder::new"));
        assert!(implementation.contains("WebviewUrl::App(\"/settings\".into())"));
        assert!(implementation.contains("WindowEvent::CloseRequested"));
        assert!(implementation.contains("api.prevent_close()"));
        assert!(implementation.contains(".hide()"));
        assert!(implementation.contains(".unminimize()"));
        assert!(implementation.contains(".show()"));
        assert!(implementation.contains(".set_focus()"));
        assert!(!implementation.contains("to_panel"));
        assert!(!implementation.contains("/ws/main"));
    }
}
