//! macOS non-activating NSPanel presentation for the subtitle and summary windows.
//! Panel conversion follows MIT `yuxino/Mimi` commit `bce7164` through
//! `tauri-nspanel` 2.1.1 (MIT OR Apache-2.0).

use serde::Serialize;
use std::sync::mpsc;
use std::time::Duration;
use tauri::{AppHandle, Manager};

pub const MACOS_SCREEN_SAVER_WINDOW_LEVEL: isize = 1000;

pub const COLLECTION_CAN_JOIN_ALL_SPACES: usize = 1 << 0;
pub const COLLECTION_STATIONARY: usize = 1 << 4;
pub const COLLECTION_IGNORES_CYCLE: usize = 1 << 6;
pub const COLLECTION_FULL_SCREEN_AUXILIARY: usize = 1 << 8;
pub const COLLECTION_CAN_JOIN_ALL_APPLICATIONS: usize = 1 << 18;

const MAIN_THREAD_NATIVE_TIMEOUT: Duration = Duration::from_millis(1_500);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NativePanelKind {
    Overlay,
    Summary,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NativePanelDiagnostics {
    pub label: String,
    pub class_name: String,
    pub window_level: isize,
    pub collection_behavior: usize,
    pub visible: bool,
    pub frame_x: f64,
    pub frame_y: f64,
    pub frame_width: f64,
    pub frame_height: f64,
    pub screen_origin_x: f64,
    pub screen_origin_y: f64,
    pub screen_width: f64,
    pub screen_height: f64,
}

pub const fn overlay_window_level() -> isize {
    MACOS_SCREEN_SAVER_WINDOW_LEVEL
}

pub const fn overlay_collection_behavior_bits() -> usize {
    COLLECTION_CAN_JOIN_ALL_SPACES
        | COLLECTION_STATIONARY
        | COLLECTION_IGNORES_CYCLE
        | COLLECTION_FULL_SCREEN_AUXILIARY
        | COLLECTION_CAN_JOIN_ALL_APPLICATIONS
}

#[cfg(target_os = "macos")]
mod macos {
    use super::*;
    use objc2::runtime::AnyObject as RuntimeAnyObject;
    use objc2_app_kit::{
        NSColor, NSScreen, NSWindow as AppKitNSWindow, NSWindowCollectionBehavior,
        NSWindowStyleMask,
    };
    use tauri::WebviewWindow;
    use tauri_nspanel::{ManagerExt, PanelHandle, WebviewWindowExt};

    tauri_nspanel::tauri_panel! {
        panel!(HarrySubtitlePanel {
            config: {
                can_become_key_window: true,
                can_become_main_window: false,
                is_floating_panel: true,
                hides_on_deactivate: false,
                becomes_key_only_if_needed: true
            }
        })

        panel!(HarrySummaryPanel {
            config: {
                can_become_key_window: true,
                can_become_main_window: false,
                is_floating_panel: true,
                hides_on_deactivate: false,
                becomes_key_only_if_needed: false
            }
        })
    }

    fn run_on_main_thread_result<T, F>(app: &AppHandle, action: F) -> Result<T, String>
    where
        T: Send + 'static,
        F: FnOnce() -> Result<T, String> + Send + 'static,
    {
        if objc2::MainThreadMarker::new().is_some() {
            return action();
        }
        let (sender, receiver) = mpsc::sync_channel(1);
        app.run_on_main_thread(move || {
            let _ = sender.send(action());
        })
        .map_err(|error| error.to_string())?;
        receiver
            .recv_timeout(MAIN_THREAD_NATIVE_TIMEOUT)
            .map_err(|_| "native_main_thread_timeout".to_string())?
    }

    fn panel_for_window(
        window: &WebviewWindow,
        kind: NativePanelKind,
    ) -> Result<PanelHandle<tauri::Wry>, String> {
        let app = window.app_handle();
        if let Ok(panel) = app.get_webview_panel(window.label()) {
            return Ok(panel);
        }
        match kind {
            NativePanelKind::Overlay => window
                .to_panel::<HarrySubtitlePanel>()
                .map_err(|error| format!("overlay_panel_conversion_failed: {error}")),
            NativePanelKind::Summary => window
                .to_panel::<HarrySummaryPanel>()
                .map_err(|error| format!("summary_panel_conversion_failed: {error}")),
        }
    }

    fn desired_collection_behavior(
        current: NSWindowCollectionBehavior,
    ) -> NSWindowCollectionBehavior {
        let incompatible = NSWindowCollectionBehavior::MoveToActiveSpace
            | NSWindowCollectionBehavior::Managed
            | NSWindowCollectionBehavior::Transient
            | NSWindowCollectionBehavior::ParticipatesInCycle
            | NSWindowCollectionBehavior::Primary
            | NSWindowCollectionBehavior::Auxiliary
            | NSWindowCollectionBehavior::FullScreenPrimary
            | NSWindowCollectionBehavior::FullScreenNone;
        (current & !incompatible)
            | NSWindowCollectionBehavior::from_bits_retain(overlay_collection_behavior_bits())
    }

    fn apply_panel_properties(
        panel: &PanelHandle<tauri::Wry>,
        kind: NativePanelKind,
    ) -> Result<(), String> {
        let native = panel.as_panel();
        panel.set_floating_panel(true);
        panel.set_hides_on_deactivate(false);
        panel.set_becomes_key_only_if_needed(kind == NativePanelKind::Overlay);
        panel
            .set_style_mask(native.styleMask() | NSWindowStyleMask::NonactivatingPanel)
            .map_err(|error| format!("native_panel_style_failed: {error}"))?;
        native.setCollectionBehavior(desired_collection_behavior(native.collectionBehavior()));
        panel.set_has_shadow(false);
        panel.set_opaque(false);
        panel.set_movable_by_window_background(false);
        let clear = NSColor::clearColor();
        native.setBackgroundColor(Some(&clear));
        // NSPanel floating-panel and style updates may reset the level.
        // Screen-saver level must therefore be the final presentation write.
        native.setLevel(overlay_window_level());
        tracing::info!(
            label = panel.label(),
            class = %native.class().name().to_string_lossy(),
            level = native.level(),
            collection_behavior = native.collectionBehavior().bits(),
            "native panel configured"
        );
        Ok(())
    }

    pub fn configure_macos_overlay_window(
        window: &WebviewWindow,
        kind: NativePanelKind,
    ) -> Result<(), String> {
        let dispatcher = window.app_handle().clone();
        let window = window.clone();
        run_on_main_thread_result(&dispatcher, move || {
            let panel = panel_for_window(&window, kind)?;
            apply_panel_properties(&panel, kind)
        })
    }

    pub fn show_macos_panel(app: &AppHandle, label: &str) -> Result<(), String> {
        let app = app.clone();
        let dispatcher = app.clone();
        let label = label.to_string();
        run_on_main_thread_result(&dispatcher, move || {
            let panel = app
                .get_webview_panel(&label)
                .map_err(|_| format!("native_panel_missing:{label}"))?;
            panel.as_panel().orderFrontRegardless();
            if panel.is_visible() {
                Ok(())
            } else {
                Err(format!("native_panel_not_visible:{label}"))
            }
        })
    }

    pub fn hide_macos_panel(app: &AppHandle, label: &str) -> Result<(), String> {
        let app = app.clone();
        let dispatcher = app.clone();
        let label = label.to_string();
        run_on_main_thread_result(&dispatcher, move || {
            let panel = app
                .get_webview_panel(&label)
                .map_err(|_| format!("native_panel_missing:{label}"))?;
            panel.as_panel().orderOut(None);
            if panel.is_visible() {
                Err(format!("native_panel_still_visible:{label}"))
            } else {
                Ok(())
            }
        })
    }

    pub fn reassert_visible_macos_panel(
        window: &WebviewWindow,
        kind: NativePanelKind,
    ) -> Result<(), String> {
        if !window.is_visible().unwrap_or(false) {
            return Ok(());
        }
        configure_macos_overlay_window(window, kind)?;
        show_macos_panel(window.app_handle(), window.label())
    }

    pub fn configure_macos_summary_window(window: &WebviewWindow) -> Result<(), String> {
        let dispatcher = window.app_handle().clone();
        let window = window.clone();
        run_on_main_thread_result(&dispatcher, move || {
            let pointer = window.ns_window().map_err(|error| error.to_string())?;
            if pointer.is_null() {
                return Err(format!("native_window_pointer_missing:{}", window.label()));
            }
            let native: &AppKitNSWindow = unsafe { &*pointer.cast() };
            native.setCollectionBehavior(desired_collection_behavior(native.collectionBehavior()));
            native.setHidesOnDeactivate(false);
            native.setLevel(overlay_window_level());
            tracing::info!(
                label = window.label(),
                class = %native.class().name().to_string_lossy(),
                level = native.level(),
                collection_behavior = native.collectionBehavior().bits(),
                "native summary window configured"
            );
            Ok(())
        })
    }

    pub fn show_macos_summary_window(window: &WebviewWindow) -> Result<(), String> {
        let dispatcher = window.app_handle().clone();
        let window = window.clone();
        run_on_main_thread_result(&dispatcher, move || {
            let pointer = window.ns_window().map_err(|error| error.to_string())?;
            if pointer.is_null() {
                return Err(format!("native_window_pointer_missing:{}", window.label()));
            }
            let native: &AppKitNSWindow = unsafe { &*pointer.cast() };
            native.orderFrontRegardless();
            if native.isVisible() {
                Ok(())
            } else {
                Err(format!("native_summary_not_visible:{}", window.label()))
            }
        })
    }

    pub fn close_macos_summary_window(window: &WebviewWindow) -> Result<(), String> {
        window.close().map_err(|error| error.to_string())
    }

    pub fn native_panel_diagnostics(
        app: &AppHandle,
        label: &str,
    ) -> Result<NativePanelDiagnostics, String> {
        let app = app.clone();
        let dispatcher = app.clone();
        let label = label.to_string();
        run_on_main_thread_result(&dispatcher, move || {
            let window = app
                .get_webview_window(&label)
                .ok_or_else(|| format!("native_window_missing:{label}"))?;
            let pointer = window.ns_window().map_err(|error| error.to_string())?;
            if pointer.is_null() {
                return Err(format!("native_window_pointer_missing:{label}"));
            }
            let native: &AppKitNSWindow = unsafe { &*pointer.cast() };
            let any_object: &RuntimeAnyObject = unsafe { &*pointer.cast() };
            let class_name = any_object.class().name().to_string_lossy().into_owned();
            let frame = native
                .screen()
                .map(|screen: objc2::rc::Retained<NSScreen>| screen.frame())
                .unwrap_or_default();
            let window_frame = native.frame();
            Ok(NativePanelDiagnostics {
                label,
                class_name,
                window_level: native.level() as isize,
                collection_behavior: native.collectionBehavior().bits(),
                visible: native.isVisible(),
                frame_x: window_frame.origin.x,
                frame_y: window_frame.origin.y,
                frame_width: window_frame.size.width,
                frame_height: window_frame.size.height,
                screen_origin_x: frame.origin.x,
                screen_origin_y: frame.origin.y,
                screen_width: frame.size.width,
                screen_height: frame.size.height,
            })
        })
    }
}

#[cfg(target_os = "macos")]
pub use macos::{
    close_macos_summary_window, configure_macos_overlay_window, configure_macos_summary_window,
    hide_macos_panel, native_panel_diagnostics, reassert_visible_macos_panel, show_macos_panel,
    show_macos_summary_window,
};

#[cfg(not(target_os = "macos"))]
pub fn configure_macos_overlay_window(
    _window: &tauri::WebviewWindow,
    _kind: NativePanelKind,
) -> Result<(), String> {
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn show_macos_panel(_app: &AppHandle, _label: &str) -> Result<(), String> {
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn hide_macos_panel(_app: &AppHandle, _label: &str) -> Result<(), String> {
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn reassert_visible_macos_panel(
    _window: &tauri::WebviewWindow,
    _kind: NativePanelKind,
) -> Result<(), String> {
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn configure_macos_summary_window(_window: &tauri::WebviewWindow) -> Result<(), String> {
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn show_macos_summary_window(_window: &tauri::WebviewWindow) -> Result<(), String> {
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub fn close_macos_summary_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    window.close().map_err(|error| error.to_string())
}

#[cfg(not(target_os = "macos"))]
pub fn native_panel_diagnostics(
    _app: &AppHandle,
    _label: &str,
) -> Result<NativePanelDiagnostics, String> {
    Err("macos_only".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_level_and_collection_behavior_match_mimi_nonactivating_overlay() {
        assert_eq!(overlay_window_level(), 1000);
        let bits = overlay_collection_behavior_bits();
        assert_ne!(bits & COLLECTION_CAN_JOIN_ALL_SPACES, 0);
        assert_ne!(bits & COLLECTION_STATIONARY, 0);
        assert_ne!(bits & COLLECTION_IGNORES_CYCLE, 0);
        assert_ne!(bits & COLLECTION_FULL_SCREEN_AUXILIARY, 0);
        assert_ne!(bits & COLLECTION_CAN_JOIN_ALL_APPLICATIONS, 0);
    }
}
