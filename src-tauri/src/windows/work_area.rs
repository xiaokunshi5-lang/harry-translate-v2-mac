//! Screen work-area clamping adapted from MIT `yuxino/Mimi` (commit `bce7164`).
//! Copyright (c) 2026 yuxino — MIT License.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ScreenRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl ScreenRect {
    pub fn default_work_area() -> Self {
        Self {
            x: 0.0,
            y: 0.0,
            width: 1440.0,
            height: 900.0,
        }
    }
}

pub fn clamp_frame_to_work_area(frame: ScreenRect, work_area: ScreenRect) -> ScreenRect {
    let width = frame.width.min(work_area.width).max(1.0);
    let height = frame.height.min(work_area.height).max(1.0);
    let max_x = (work_area.x + work_area.width - width).max(work_area.x);
    let max_y = (work_area.y + work_area.height - height).max(work_area.y);
    ScreenRect {
        x: frame.x.clamp(work_area.x, max_x),
        y: frame.y.clamp(work_area.y, max_y),
        width,
        height,
    }
}

pub fn appkit_visible_to_top_left_rect(
    screen_height: f64,
    visible_x: f64,
    visible_y: f64,
    visible_width: f64,
    visible_height: f64,
) -> ScreenRect {
    let top_y = (screen_height - (visible_y + visible_height)).max(0.0);
    ScreenRect {
        x: visible_x,
        y: top_y,
        width: visible_width.max(360.0),
        height: visible_height.max(100.0),
    }
}

#[cfg(target_os = "macos")]
pub fn primary_work_area() -> ScreenRect {
    use objc2::MainThreadMarker;
    use objc2_app_kit::NSScreen;

    let Some(mtm) = MainThreadMarker::new() else {
        return ScreenRect::default_work_area();
    };
    let Some(screen) = NSScreen::mainScreen(mtm) else {
        return ScreenRect::default_work_area();
    };
    let frame = screen.frame();
    let visible = screen.visibleFrame();
    appkit_visible_to_top_left_rect(
        frame.size.height,
        visible.origin.x,
        visible.origin.y,
        visible.size.width,
        visible.size.height,
    )
}

#[cfg(not(target_os = "macos"))]
pub fn primary_work_area() -> ScreenRect {
    ScreenRect::default_work_area()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clamp_frame_to_work_area_keeps_window_inside_bounds() {
        let work_area = ScreenRect {
            x: 0.0,
            y: 25.0,
            width: 1280.0,
            height: 775.0,
        };
        let clamped = clamp_frame_to_work_area(
            ScreenRect {
                x: -120.0,
                y: 900.0,
                width: 580.0,
                height: 240.0,
            },
            work_area,
        );
        assert_eq!(clamped.x, 0.0);
        assert_eq!(clamped.y, 25.0 + 775.0 - 240.0);
        assert_eq!(clamped.width, 580.0);
        assert_eq!(clamped.height, 240.0);
    }

    #[test]
    fn appkit_visible_to_top_left_rect_converts_bottom_left_origin() {
        let rect = appkit_visible_to_top_left_rect(982.0, 0.0, 68.0, 1512.0, 889.0);
        assert_eq!(rect.x, 0.0);
        assert_eq!(rect.y, 25.0);
        assert_eq!(rect.width, 1512.0);
        assert_eq!(rect.height, 889.0);
    }
}
