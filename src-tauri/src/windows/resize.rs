//! Eight-direction resize geometry adapted from MIT `yuxino/Mimi` (commit `bce7164`).
//! Copyright (c) 2026 yuxino — MIT License.

use crate::windows::work_area::{clamp_frame_to_work_area, ScreenRect};
use serde::{Deserialize, Serialize};

pub struct SubtitleOverlayMetrics;

impl SubtitleOverlayMetrics {
    pub const DEFAULT_WIDTH: f64 = 580.0;
    pub const DEFAULT_HEIGHT: f64 = 240.0;
    pub const MIN_WIDTH: f64 = 360.0;
    pub const MIN_HEIGHT: f64 = 100.0;
    pub const MAX_WIDTH: f64 = 1200.0;
    pub const MAX_HEIGHT: f64 = 600.0;
    pub const COLLAPSED_WIDTH: f64 = 280.0;
    pub const COLLAPSED_HEIGHT: f64 = 54.0;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ResizeEdge {
    N,
    S,
    E,
    W,
    Ne,
    Nw,
    Se,
    Sw,
}

impl ResizeEdge {
    pub fn from_str_edge(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "n" => Some(Self::N),
            "s" => Some(Self::S),
            "e" => Some(Self::E),
            "w" => Some(Self::W),
            "ne" => Some(Self::Ne),
            "nw" => Some(Self::Nw),
            "se" => Some(Self::Se),
            "sw" => Some(Self::Sw),
            _ => None,
        }
    }
}

pub fn clamp_expanded_size(width: f64, height: f64) -> (f64, f64) {
    (
        width.clamp(
            SubtitleOverlayMetrics::MIN_WIDTH,
            SubtitleOverlayMetrics::MAX_WIDTH,
        ),
        height.clamp(
            SubtitleOverlayMetrics::MIN_HEIGHT,
            SubtitleOverlayMetrics::MAX_HEIGHT,
        ),
    )
}

pub fn apply_resize_delta(
    start: ScreenRect,
    edge: ResizeEdge,
    dx: f64,
    dy: f64,
    work_area: ScreenRect,
) -> ScreenRect {
    let mut left = start.x;
    let mut top = start.y;
    let mut right = start.x + start.width;
    let mut bottom = start.y + start.height;

    match edge {
        ResizeEdge::N => top += dy,
        ResizeEdge::S => bottom += dy,
        ResizeEdge::E => right += dx,
        ResizeEdge::W => left += dx,
        ResizeEdge::Ne => {
            top += dy;
            right += dx;
        }
        ResizeEdge::Nw => {
            top += dy;
            left += dx;
        }
        ResizeEdge::Se => {
            bottom += dy;
            right += dx;
        }
        ResizeEdge::Sw => {
            bottom += dy;
            left += dx;
        }
    }

    let raw_width = right - left;
    let raw_height = bottom - top;
    let (clamped_w, clamped_h) = clamp_expanded_size(raw_width, raw_height);

    if matches!(edge, ResizeEdge::W | ResizeEdge::Nw | ResizeEdge::Sw) {
        left = right - clamped_w;
    }
    if matches!(edge, ResizeEdge::N | ResizeEdge::Ne | ResizeEdge::Nw) {
        top = bottom - clamped_h;
    }

    clamp_frame_to_work_area(
        ScreenRect {
            x: left,
            y: top,
            width: clamped_w,
            height: clamped_h,
        },
        work_area,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resize_clamps_above_max_and_below_min() {
        let work_area = ScreenRect {
            x: 0.0,
            y: 0.0,
            width: 1920.0,
            height: 1080.0,
        };
        let start = ScreenRect {
            x: 200.0,
            y: 120.0,
            width: 580.0,
            height: 240.0,
        };

        let oversized = apply_resize_delta(start, ResizeEdge::Se, 920.0, 560.0, work_area);
        assert_eq!(oversized.width, SubtitleOverlayMetrics::MAX_WIDTH);
        assert_eq!(oversized.height, SubtitleOverlayMetrics::MAX_HEIGHT);
        assert_eq!(oversized.x, 200.0);
        assert_eq!(oversized.y, 120.0);

        let undersized = apply_resize_delta(start, ResizeEdge::Se, -480.0, -200.0, work_area);
        assert_eq!(undersized.width, SubtitleOverlayMetrics::MIN_WIDTH);
        assert_eq!(undersized.height, SubtitleOverlayMetrics::MIN_HEIGHT);
    }

    #[test]
    fn north_west_resize_anchors_opposite_corner() {
        let work_area = ScreenRect {
            x: 0.0,
            y: 0.0,
            width: 1920.0,
            height: 1080.0,
        };
        let start = ScreenRect {
            x: 300.0,
            y: 300.0,
            width: 580.0,
            height: 240.0,
        };
        let shrunk = apply_resize_delta(start, ResizeEdge::Nw, 500.0, 500.0, work_area);
        assert_eq!(shrunk.width, SubtitleOverlayMetrics::MIN_WIDTH);
        assert_eq!(shrunk.height, SubtitleOverlayMetrics::MIN_HEIGHT);
        assert_eq!(shrunk.x + shrunk.width, start.x + start.width);
        assert_eq!(shrunk.y + shrunk.height, start.y + start.height);
    }
}
