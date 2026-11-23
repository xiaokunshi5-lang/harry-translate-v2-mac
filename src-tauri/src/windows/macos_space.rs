//! macOS active Space follower (`NSWorkspaceActiveSpaceDidChangeNotification`, 250ms debounce)
//! adapted from MIT `yuxino/Mimi` (commit `bce7164`, v1.7.6).
//! Copyright (c) 2026 yuxino — MIT License.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use super::native::{
    configure_macos_summary_window, reassert_visible_macos_panel, show_macos_summary_window,
    NativePanelKind,
};
use crate::windows::summary::SUMMARY_WINDOW_LABEL;
use tauri::Manager;

pub const SPACE_FOLLOW_DEBOUNCE_MS: u64 = 250;

#[derive(Debug, Default)]
pub struct SpaceFollowCoordinator {
    generation: AtomicU64,
    cancelled: AtomicBool,
}

impl SpaceFollowCoordinator {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn bump_generation(&self) -> u64 {
        self.generation.fetch_add(1, Ordering::SeqCst) + 1
    }

    pub fn current_generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }

    pub fn is_current(&self, gen: u64) -> bool {
        self.generation.load(Ordering::SeqCst) == gen
    }

    pub fn debounce_duration(&self) -> Duration {
        Duration::from_millis(SPACE_FOLLOW_DEBOUNCE_MS)
    }

    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::SeqCst)
    }
}

#[cfg(target_os = "macos")]
pub fn install_space_observer(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    coordinator: Arc<SpaceFollowCoordinator>,
) -> Result<(), String> {
    let win = window.clone();
    window
        .run_on_main_thread(move || {
            unsafe {
                use block2::RcBlock;
                use objc2::runtime::AnyObject;
                use objc2_app_kit::{NSWorkspace, NSWorkspaceActiveSpaceDidChangeNotification};
                use objc2_foundation::NSNotification;
                use std::ptr::NonNull;

                let workspace = NSWorkspace::sharedWorkspace();
                let center = workspace.notificationCenter();
                let block = RcBlock::new(move |_notification: NonNull<NSNotification>| {
                    let gen = coordinator.bump_generation();
                    let coord = Arc::clone(&coordinator);
                    let target_window = win.clone();
                    let target_app = app.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(coord.debounce_duration());
                        if coord.is_cancelled() || !coord.is_current(gen) {
                            return;
                        }
                        let inner_win = target_window.clone();
                        let inner_app = target_app.clone();
                        let _ = target_window.run_on_main_thread(move || {
                            if !coord.is_cancelled() && inner_win.is_visible().unwrap_or(false) {
                                let _ = reassert_visible_macos_panel(
                                    &inner_win,
                                    NativePanelKind::Overlay,
                                );
                            }
                            if !coord.is_cancelled() {
                                if let Some(summary) =
                                    inner_app.get_webview_window(SUMMARY_WINDOW_LABEL)
                                {
                                    if summary.is_visible().unwrap_or(false) {
                                        let _ = configure_macos_summary_window(&summary);
                                        let _ = show_macos_summary_window(&summary);
                                    }
                                }
                            }
                        });
                    });
                });

                let observer = center.addObserverForName_object_queue_usingBlock(
                    Some(NSWorkspaceActiveSpaceDidChangeNotification),
                    None::<&AnyObject>,
                    None,
                    &block,
                );
                // Keep the observer token alive for the lifetime of the single-window app.
                std::mem::forget(observer);
            }
        })
        .map_err(|err| err.to_string())
}

#[cfg(not(target_os = "macos"))]
pub fn install_space_observer(
    _app: tauri::AppHandle,
    _window: tauri::WebviewWindow,
    _coordinator: Arc<SpaceFollowCoordinator>,
) -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn space_follow_generation_supersedes_older_events_with_250ms_debounce() {
        let coord = SpaceFollowCoordinator::new();
        assert_eq!(coord.debounce_duration(), Duration::from_millis(250));
        let g1 = coord.bump_generation();
        let g2 = coord.bump_generation();
        assert!(!coord.is_current(g1));
        assert!(coord.is_current(g2));
        assert_eq!(coord.current_generation(), 2);
        coord.cancel();
        assert!(coord.is_cancelled());
    }
}
