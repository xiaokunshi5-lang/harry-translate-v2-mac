//! Single-window overlay state machine & atomic persistence adapted from MIT `yuxino/Mimi` (commit `bce7164`).
//! Copyright (c) 2026 yuxino — MIT License.

use crate::windows::resize::{
    apply_resize_delta, clamp_expanded_size, ResizeEdge, SubtitleOverlayMetrics,
};
use crate::windows::work_area::{clamp_frame_to_work_area, ScreenRect};
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::Path;

pub const MAX_OVERLAY_WINDOWS: usize = 1;
pub const PRIMARY_OVERLAY_LABEL: &str = "overlay";
pub const ONLY_MODE_HEIGHT_RATIO: f64 = 0.62;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OverlayMode {
    Expanded,
    Collapsed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OverlayDisplayMode {
    Bilingual,
    Translation,
    Original,
}

impl OverlayDisplayMode {
    pub fn from_str_mode(raw: &str) -> Self {
        match raw.trim().to_ascii_lowercase().as_str() {
            "translation" | "only_translation" => Self::Translation,
            "original" | "only_source" => Self::Original,
            _ => Self::Bilingual,
        }
    }

    pub fn is_only_mode(self) -> bool {
        matches!(self, Self::Translation | Self::Original)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct OverlayFrame {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl From<ScreenRect> for OverlayFrame {
    fn from(rect: ScreenRect) -> Self {
        Self {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
        }
    }
}

impl From<OverlayFrame> for ScreenRect {
    fn from(frame: OverlayFrame) -> Self {
        Self {
            x: frame.x,
            y: frame.y,
            width: frame.width,
            height: frame.height,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct OverlaySnapshot {
    pub max_overlay_windows: usize,
    pub active_windows: Vec<String>,
    pub mode: OverlayMode,
    pub display_mode: OverlayDisplayMode,
    pub locked: bool,
    pub click_through: bool,
    pub hover_unlocked: bool,
    pub immersive: bool,
    pub user_frame: OverlayFrame,
    pub presentation_frame: OverlayFrame,
}

#[derive(Debug, Clone)]
pub struct OverlayState {
    active_windows: Vec<String>,
    mode: OverlayMode,
    display_mode: OverlayDisplayMode,
    bilingual_base_height: f64,
    locked: bool,
    click_through: bool,
    hover_unlocked: bool,
    immersive: bool,
    user_frame: OverlayFrame,
    presentation_frame: OverlayFrame,
}

impl Default for OverlayState {
    fn default() -> Self {
        let default_frame = OverlayFrame {
            x: 200.0,
            y: 120.0,
            width: SubtitleOverlayMetrics::DEFAULT_WIDTH,
            height: SubtitleOverlayMetrics::DEFAULT_HEIGHT,
        };
        Self {
            active_windows: vec![PRIMARY_OVERLAY_LABEL.to_string()],
            mode: OverlayMode::Expanded,
            display_mode: OverlayDisplayMode::Bilingual,
            bilingual_base_height: SubtitleOverlayMetrics::DEFAULT_HEIGHT,
            locked: false,
            click_through: true,
            hover_unlocked: false,
            immersive: false,
            user_frame: default_frame,
            presentation_frame: default_frame,
        }
    }
}

impl OverlayState {
    pub fn snapshot(&self) -> OverlaySnapshot {
        OverlaySnapshot {
            max_overlay_windows: MAX_OVERLAY_WINDOWS,
            active_windows: self.active_windows.clone(),
            mode: self.mode,
            display_mode: self.display_mode,
            locked: self.locked,
            click_through: self.click_through,
            hover_unlocked: self.hover_unlocked,
            immersive: self.immersive,
            user_frame: self.user_frame,
            presentation_frame: self.presentation_frame,
        }
    }

    pub fn register_overlay_window(&mut self, label: &str) -> Result<(), String> {
        let normalized = label.trim();
        if normalized.is_empty() {
            return Err("overlay window label cannot be empty".to_string());
        }
        if self.active_windows.iter().any(|existing| existing == normalized) {
            return Ok(());
        }
        if self.active_windows.len() >= MAX_OVERLAY_WINDOWS {
            return Err(format!(
                "single-window invariant violated: MAX_OVERLAY_WINDOWS={MAX_OVERLAY_WINDOWS}"
            ));
        }
        self.active_windows.push(normalized.to_string());
        Ok(())
    }

    pub fn active_window_count(&self) -> usize {
        self.active_windows.len()
    }

    pub fn user_frame(&self) -> OverlayFrame {
        self.user_frame
    }

    pub fn presentation_frame(&self) -> OverlayFrame {
        self.presentation_frame
    }

    pub fn mode(&self) -> OverlayMode {
        self.mode
    }

    pub fn set_user_geometry(
        &mut self,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
        work_area: ScreenRect,
    ) -> OverlaySnapshot {
        let (clamped_w, clamped_h) = clamp_expanded_size(width, height);
        let bounded = clamp_frame_to_work_area(
            ScreenRect {
                x,
                y,
                width: clamped_w,
                height: clamped_h,
            },
            work_area,
        );
        self.user_frame = OverlayFrame::from(bounded);
        self.bilingual_base_height = if self.display_mode.is_only_mode() {
            clamp_expanded_size(
                self.user_frame.width,
                (self.user_frame.height / ONLY_MODE_HEIGHT_RATIO).round(),
            )
            .1
        } else {
            self.user_frame.height
        };
        if self.mode == OverlayMode::Expanded {
            self.presentation_frame = self.user_frame;
        } else {
            self.presentation_frame.x = self.user_frame.x;
            self.presentation_frame.y = self.user_frame.y;
        }
        self.snapshot()
    }

    pub fn resize_by_edge(
        &mut self,
        edge: ResizeEdge,
        dx: f64,
        dy: f64,
        work_area: ScreenRect,
    ) -> OverlaySnapshot {
        let next = apply_resize_delta(ScreenRect::from(self.user_frame), edge, dx, dy, work_area);
        self.user_frame = OverlayFrame::from(next);
        self.bilingual_base_height = if self.display_mode.is_only_mode() {
            clamp_expanded_size(
                self.user_frame.width,
                (self.user_frame.height / ONLY_MODE_HEIGHT_RATIO).round(),
            )
            .1
        } else {
            self.user_frame.height
        };
        if self.mode == OverlayMode::Expanded {
            self.presentation_frame = self.user_frame;
        }
        self.snapshot()
    }

    /// Adapted from Apache-2.0 `SakiRinn/LiveCaptions-Translator` (`ResizeForOnlyMode`):
    /// adjust window height and compensate `y` around the bottom anchor so switching
    /// between `bilingual` and `translation`/`original` never causes vertical jumps.
    pub fn resize_for_only_mode(
        &mut self,
        display_mode: OverlayDisplayMode,
        work_area: ScreenRect,
    ) -> OverlaySnapshot {
        let was_only = self.display_mode.is_only_mode();
        let is_only = display_mode.is_only_mode();
        self.display_mode = display_mode;
        if was_only == is_only {
            return self.snapshot();
        }
        let bottom_anchor = self.user_frame.y + self.user_frame.height;
        let target_height = if is_only {
            self.bilingual_base_height = self.user_frame.height;
            clamp_expanded_size(
                self.user_frame.width,
                (self.bilingual_base_height * ONLY_MODE_HEIGHT_RATIO).round(),
            )
            .1
        } else {
            clamp_expanded_size(self.user_frame.width, self.bilingual_base_height).1
        };
        let compensated_y = bottom_anchor - target_height;
        let bounded = clamp_frame_to_work_area(
            ScreenRect {
                x: self.user_frame.x,
                y: compensated_y,
                width: self.user_frame.width,
                height: target_height,
            },
            work_area,
        );
        self.user_frame = OverlayFrame::from(bounded);
        if self.mode == OverlayMode::Expanded {
            self.presentation_frame = self.user_frame;
        } else {
            self.presentation_frame.x = self.user_frame.x;
            self.presentation_frame.y = self.user_frame.y;
        }
        self.snapshot()
    }

    pub fn set_mode(&mut self, mode: OverlayMode, work_area: ScreenRect) -> OverlaySnapshot {
        self.mode = mode;
        match mode {
            OverlayMode::Collapsed => {
                let collapsed = clamp_frame_to_work_area(
                    ScreenRect {
                        x: self.user_frame.x,
                        y: self.user_frame.y,
                        width: SubtitleOverlayMetrics::COLLAPSED_WIDTH,
                        height: SubtitleOverlayMetrics::COLLAPSED_HEIGHT,
                    },
                    work_area,
                );
                self.presentation_frame = OverlayFrame::from(collapsed);
            }
            OverlayMode::Expanded => {
                let (w, h) = clamp_expanded_size(self.user_frame.width, self.user_frame.height);
                let restored = clamp_frame_to_work_area(
                    ScreenRect {
                        x: self.user_frame.x,
                        y: self.user_frame.y,
                        width: w,
                        height: h,
                    },
                    work_area,
                );
                self.user_frame = OverlayFrame::from(restored);
                self.presentation_frame = self.user_frame;
            }
        }
        self.snapshot()
    }

    pub fn set_locked(&mut self, locked: bool) -> OverlaySnapshot {
        self.locked = locked;
        if !locked {
            self.hover_unlocked = false;
        }
        self.snapshot()
    }

    pub fn set_presentation(
        &mut self,
        immersive: bool,
        locked: bool,
        click_through_enabled: bool,
    ) -> OverlaySnapshot {
        self.immersive = immersive;
        self.locked = locked;
        self.click_through = click_through_enabled;
        self.hover_unlocked = false;
        self.snapshot()
    }

    pub fn set_immersive(&mut self, immersive: bool) -> OverlaySnapshot {
        self.immersive = immersive;
        self.snapshot()
    }

    pub fn set_hover_unlocked(&mut self, hover_unlocked: bool) -> OverlaySnapshot {
        self.hover_unlocked = hover_unlocked;
        self.snapshot()
    }

    pub fn is_locked(&self) -> bool {
        self.locked
    }

    pub fn is_click_through(&self) -> bool {
        self.click_through
    }

    pub fn is_immersive(&self) -> bool {
        self.immersive
    }

    pub fn effective_click_through(&self) -> bool {
        let immersive = self.immersive;
        let locked = self.locked;
        let click_through_enabled = self.click_through;
        immersive || (locked && click_through_enabled)
    }

    pub fn set_user_position(&mut self, x: f64, y: f64) -> OverlaySnapshot {
        self.user_frame.x = x;
        self.user_frame.y = y;
        if self.mode == OverlayMode::Expanded {
            self.presentation_frame = self.user_frame;
        } else {
            self.presentation_frame.x = x;
            self.presentation_frame.y = y;
        }
        self.snapshot()
    }

    pub fn reset_presentation(&mut self, work_area: ScreenRect) -> OverlaySnapshot {
        self.immersive = false;
        self.locked = false;
        self.hover_unlocked = false;
        self.click_through = true;
        self.mode = OverlayMode::Expanded;
        self.display_mode = OverlayDisplayMode::Bilingual;
        self.bilingual_base_height = SubtitleOverlayMetrics::DEFAULT_HEIGHT;
        let reset = clamp_frame_to_work_area(
            ScreenRect {
                x: 200.0,
                y: 120.0,
                width: SubtitleOverlayMetrics::DEFAULT_WIDTH,
                height: SubtitleOverlayMetrics::DEFAULT_HEIGHT,
            },
            work_area,
        );
        self.user_frame = OverlayFrame::from(reset);
        self.presentation_frame = self.user_frame;
        self.snapshot()
    }

    pub fn reclamp_to_work_area(&mut self, work_area: ScreenRect) -> OverlaySnapshot {
        let mode = self.mode;
        self.set_user_geometry(
            self.user_frame.x,
            self.user_frame.y,
            self.user_frame.width,
            self.user_frame.height,
            work_area,
        );
        self.set_mode(mode, work_area)
    }

    pub fn from_settings_file(settings_path: &Path, work_area: ScreenRect) -> Self {
        let mut state = Self::default();
        if let Ok(raw) = fs::read_to_string(settings_path) {
            if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&raw) {
                let mut x = state.user_frame.x;
                let mut y = state.user_frame.y;
                let mut width = state.user_frame.width;
                let mut height = state.user_frame.height;
                let mut mode = OverlayMode::Expanded;
                let mut geometry_locked = false;
                if let Some(geo) = parsed.get("hud_geometry").and_then(|v| v.as_object()) {
                    x = geo.get("x").and_then(|v| v.as_f64()).unwrap_or(x);
                    y = geo.get("y").and_then(|v| v.as_f64()).unwrap_or(y);
                    width = geo.get("width").and_then(|v| v.as_f64()).unwrap_or(width);
                    height = geo.get("height").and_then(|v| v.as_f64()).unwrap_or(height);
                    geometry_locked = geo
                        .get("locked")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false);
                    mode = match geo.get("mode").and_then(|v| v.as_str()) {
                        Some("collapsed") => OverlayMode::Collapsed,
                        _ => OverlayMode::Expanded,
                    };
                }
                let locked = parsed
                    .get("is_overlay_locked")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(geometry_locked);
                let immersive = parsed
                    .get("overlay_immersive")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                let click_through = parsed
                    .get("overlay_click_through")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(true);
                let display_mode = parsed
                    .get("subtitle_display_mode")
                    .and_then(|v| v.as_str())
                    .map(OverlayDisplayMode::from_str_mode)
                    .unwrap_or(OverlayDisplayMode::Bilingual);
                state.set_user_geometry(x, y, width, height, work_area);
                state.display_mode = display_mode;
                state.bilingual_base_height = if display_mode.is_only_mode() {
                    clamp_expanded_size(
                        state.user_frame.width,
                        (state.user_frame.height / ONLY_MODE_HEIGHT_RATIO).round(),
                    )
                    .1
                } else {
                    state.user_frame.height
                };
                state.set_presentation(immersive, locked, click_through);
                state.set_mode(mode, work_area);
                return state;
            }
        }
        state.reclamp_to_work_area(work_area);
        state
    }
}

pub fn persist_hud_geometry(settings_path: &Path, snapshot: &OverlaySnapshot) -> Result<(), String> {
    if let Some(parent) = settings_path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let mut root: serde_json::Map<String, serde_json::Value> = if settings_path.is_file() {
        let raw = fs::read_to_string(settings_path).map_err(|err| err.to_string())?;
        serde_json::from_str::<serde_json::Value>(&raw)
            .ok()
            .and_then(|value| value.as_object().cloned())
            .unwrap_or_default()
    } else {
        serde_json::Map::new()
    };

    root.insert(
        "overlay_window_mode".to_string(),
        serde_json::Value::String("single".to_string()),
    );
    root.insert(
        "is_overlay_locked".to_string(),
        serde_json::Value::Bool(snapshot.locked),
    );
    root.insert(
        "overlay_immersive".to_string(),
        serde_json::Value::Bool(snapshot.immersive),
    );
    root.insert(
        "overlay_click_through".to_string(),
        serde_json::Value::Bool(snapshot.click_through),
    );
    root.insert(
        "hud_geometry".to_string(),
        serde_json::json!({
            "x": snapshot.user_frame.x.round() as i64,
            "y": snapshot.user_frame.y.round() as i64,
            "width": snapshot.user_frame.width.round() as i64,
            "height": snapshot.user_frame.height.round() as i64,
            "mode": match snapshot.mode {
                OverlayMode::Expanded => "expanded",
                OverlayMode::Collapsed => "collapsed",
            },
            "locked": snapshot.locked,
        }),
    );

    let serialized = serde_json::to_string_pretty(&serde_json::Value::Object(root))
        .map_err(|err| err.to_string())?
        + "\n";
    let tmp_path = settings_path.with_extension("json.tmp");
    {
        let mut file = fs::File::create(&tmp_path).map_err(|err| err.to_string())?;
        file.write_all(serialized.as_bytes())
            .map_err(|err| err.to_string())?;
        file.sync_all().map_err(|err| err.to_string())?;
    }
    fs::rename(&tmp_path, settings_path).map_err(|err| err.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn work_area() -> ScreenRect {
        ScreenRect {
            x: 0.0,
            y: 0.0,
            width: 1920.0,
            height: 1080.0,
        }
    }

    #[test]
    fn single_window_invariant_rejects_second_overlay() {
        let mut state = OverlayState::default();
        assert_eq!(MAX_OVERLAY_WINDOWS, 1);
        assert_eq!(state.active_window_count(), 1);
        assert!(state.register_overlay_window("overlay").is_ok());
        assert!(state.register_overlay_window("overlay-2").is_err());
        assert_eq!(state.active_window_count(), 1);
    }

    #[test]
    fn collapsed_mini_bar_preserves_user_frame_and_restores_expanded() {
        let mut state = OverlayState::default();
        state.set_user_geometry(200.0, 120.0, 580.0, 240.0, work_area());
        state.resize_by_edge(ResizeEdge::Se, 920.0, 560.0, work_area());
        assert_eq!(state.user_frame().width, 1200.0);
        assert_eq!(state.user_frame().height, 600.0);

        state.set_user_geometry(200.0, 120.0, 580.0, 240.0, work_area());
        state.resize_by_edge(ResizeEdge::Se, -480.0, -200.0, work_area());
        assert_eq!(state.user_frame().width, 360.0);
        assert_eq!(state.user_frame().height, 100.0);

        state.set_user_geometry(200.0, 120.0, 580.0, 240.0, work_area());
        let collapsed = state.set_mode(OverlayMode::Collapsed, work_area());
        assert_eq!(collapsed.presentation_frame.width, 280.0);
        assert_eq!(collapsed.presentation_frame.height, 54.0);
        assert_eq!(collapsed.user_frame.width, 580.0);
        assert_eq!(collapsed.user_frame.height, 240.0);

        let restored = state.set_mode(OverlayMode::Expanded, work_area());
        assert_eq!(restored.presentation_frame.width, 580.0);
        assert_eq!(restored.presentation_frame.height, 240.0);
        assert_eq!(restored.max_overlay_windows, 1);
    }

    #[test]
    fn persist_hud_geometry_writes_atomically_and_preserves_existing_keys() {
        let dir = std::env::temp_dir().join(format!(
            "harry-overlay-persist-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let settings_path = dir.join("user_settings.json");
        fs::write(&settings_path, r#"{"target_lang":"ja","summary_enabled":false}"#).unwrap();

        let mut state = OverlayState::default();
        state.set_user_geometry(240.0, 150.0, 720.0, 280.0, work_area());
        state.set_locked(true);
        persist_hud_geometry(&settings_path, &state.snapshot()).unwrap();

        let raw = fs::read_to_string(&settings_path).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(parsed["target_lang"], "ja");
        assert_eq!(parsed["summary_enabled"], false);
        assert_eq!(parsed["overlay_window_mode"], "single");
        assert_eq!(parsed["hud_geometry"]["width"], 720);
        assert_eq!(parsed["hud_geometry"]["height"], 280);
        assert_eq!(parsed["hud_geometry"]["locked"], true);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn from_settings_file_clamps_offscreen_negative_coordinates() {
        let dir = std::env::temp_dir().join(format!(
            "harry-overlay-load-clamp-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let settings_path = dir.join("user_settings.json");
        fs::write(
            &settings_path,
            r#"{"hud_geometry":{"x":-726,"y":211,"width":580,"height":240,"mode":"expanded","locked":false}}"#,
        )
        .unwrap();

        let area = ScreenRect {
            x: 0.0,
            y: 25.0,
            width: 1512.0,
            height: 957.0,
        };
        let state = OverlayState::from_settings_file(&settings_path, area);
        assert_eq!(state.presentation_frame().x, 0.0);
        assert_eq!(state.presentation_frame().y, 211.0);
        assert_eq!(state.presentation_frame().width, 580.0);
        assert_eq!(state.presentation_frame().height, 240.0);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn from_settings_file_loads_presentation_and_lock_aliases() {
        let dir = std::env::temp_dir().join(format!(
            "harry-overlay-load-presentation-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let settings_path = dir.join("user_settings.json");
        fs::write(
            &settings_path,
            r#"{"overlay_immersive":true,"overlay_click_through":false,"is_overlay_locked":true,"hud_geometry":{"x":200,"y":120,"width":580,"height":240,"mode":"expanded","locked":false}}"#,
        )
        .unwrap();

        let state = OverlayState::from_settings_file(&settings_path, work_area());
        assert!(state.is_immersive());
        assert!(state.is_locked());
        assert!(!state.is_click_through());
        assert!(state.effective_click_through());
        assert!(state.snapshot().locked);

        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn effective_click_through_truth_table_matches_r105_contract() {
        let mut state = OverlayState::default();
        for immersive in [false, true] {
            for locked in [false, true] {
                for click_through in [false, true] {
                    state.set_presentation(immersive, locked, click_through);
                    assert_eq!(
                        state.effective_click_through(),
                        immersive || (locked && click_through)
                    );
                }
            }
        }
    }

    #[test]
    fn reset_presentation_restores_visible_unlocked_default_frame() {
        let mut state = OverlayState::default();
        state.set_presentation(true, true, true);
        state.set_user_geometry(-500.0, 900.0, 1200.0, 600.0, work_area());
        let reset = state.reset_presentation(work_area());
        assert_eq!(reset.user_frame.x, 200.0);
        assert_eq!(reset.user_frame.y, 120.0);
        assert_eq!(reset.user_frame.width, 580.0);
        assert_eq!(reset.user_frame.height, 240.0);
        assert!(!reset.immersive);
        assert!(!reset.locked);
        assert!(reset.click_through);
    }

    #[test]
    fn resize_for_only_mode_preserves_bottom_anchor_across_ten_cycles() {
        let mut state = OverlayState::default();
        state.set_user_geometry(200.0, 120.0, 580.0, 240.0, work_area());
        let initial_bottom = state.user_frame().y + state.user_frame().height;

        for _ in 0..10 {
            let trans = state.resize_for_only_mode(OverlayDisplayMode::Translation, work_area());
            assert_eq!(trans.display_mode, OverlayDisplayMode::Translation);
            assert!(trans.user_frame.height < 240.0);
            assert_eq!(
                (trans.user_frame.y + trans.user_frame.height - initial_bottom).abs(),
                0.0
            );

            let orig = state.resize_for_only_mode(OverlayDisplayMode::Original, work_area());
            assert_eq!(orig.display_mode, OverlayDisplayMode::Original);
            assert_eq!(orig.user_frame.height, trans.user_frame.height);
            assert_eq!(orig.user_frame.y, trans.user_frame.y);

            let restored = state.resize_for_only_mode(OverlayDisplayMode::Bilingual, work_area());
            assert_eq!(restored.display_mode, OverlayDisplayMode::Bilingual);
            assert_eq!(restored.user_frame.height, 240.0);
            assert_eq!(restored.user_frame.y, 120.0);
        }
    }

    #[test]
    fn from_settings_file_preserves_only_mode_without_double_shrinking() {
        let dir = std::env::temp_dir().join(format!(
            "harry-overlay-only-mode-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let settings_path = dir.join("user_settings.json");
        fs::write(
            &settings_path,
            r#"{"subtitle_display_mode":"translation","hud_geometry":{"x":200,"y":211,"width":580,"height":149,"mode":"expanded","locked":false}}"#,
        )
        .unwrap();

        let mut state = OverlayState::from_settings_file(&settings_path, work_area());
        let already_only = state.resize_for_only_mode(OverlayDisplayMode::Original, work_area());
        assert_eq!(already_only.user_frame.height, 149.0);
        assert_eq!(already_only.user_frame.y, 211.0);

        let expanded = state.resize_for_only_mode(OverlayDisplayMode::Bilingual, work_area());
        assert_eq!(expanded.user_frame.height, 240.0);
        assert_eq!(expanded.user_frame.y + expanded.user_frame.height, 360.0);

        let _ = fs::remove_dir_all(&dir);
    }
}
