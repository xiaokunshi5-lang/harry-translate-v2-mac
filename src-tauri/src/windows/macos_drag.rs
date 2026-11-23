//! macOS native window drag via `performWindowDragWithEvent` adapted from MIT `yuxino/Mimi` (`bce7164`).
//! Copyright (c) 2026 yuxino — MIT License.

#[cfg(target_os = "macos")]
pub fn start_native_drag(window: &tauri::WebviewWindow) -> Result<(), String> {
    let win = window.clone();
    window
        .run_on_main_thread(move || unsafe {
            use objc2::MainThreadMarker;
            use objc2_app_kit::{NSApplication, NSEventType, NSWindow};

            let Ok(ptr) = win.ns_window() else {
                let _ = win.start_dragging();
                return;
            };
            if ptr.is_null() {
                let _ = win.start_dragging();
                return;
            }
            let ns_window: &NSWindow = &*(ptr as *const NSWindow);
            if let Some(mtm) = MainThreadMarker::new() {
                let app = NSApplication::sharedApplication(mtm);
                if let Some(event) = app.currentEvent() {
                    if event.r#type() == NSEventType::LeftMouseDown {
                        ns_window.performWindowDragWithEvent(&event);
                        return;
                    }
                }
            }
            let _ = win.start_dragging();
        })
        .map_err(|err| err.to_string())
}

#[cfg(not(target_os = "macos"))]
pub fn start_native_drag(window: &tauri::WebviewWindow) -> Result<(), String> {
    window.start_dragging().map_err(|err| err.to_string())
}
