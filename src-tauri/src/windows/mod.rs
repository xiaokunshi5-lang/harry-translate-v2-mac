//! Mac single-window overlay subsystem adapted from MIT `yuxino/Mimi` (commit `bce7164`, v1.7.6).
//! Copyright (c) 2026 yuxino — MIT License.

pub mod controls;
pub mod macos_drag;
pub mod macos_pointer;
pub mod macos_space;
pub mod native;
pub mod overlay;
pub mod resize;
pub mod settings;
pub mod summary;
pub mod tray;
pub mod work_area;

pub use overlay::MAX_OVERLAY_WINDOWS;
