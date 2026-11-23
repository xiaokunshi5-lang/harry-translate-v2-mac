//! macOS click-through application with native-state de-duplication.
//! Follows the MIT `yuxino/Mimi` bce7164 behavior where a native mouse-ignoring
//! window never makes itself clickable through DOM hover.

#[derive(Debug, Clone, Default)]
pub struct PointerPassThroughState {
    last_applied_ignore: Option<bool>,
}

impl PointerPassThroughState {
    pub fn apply_if_changed(&mut self, ignore_cursor_events: bool) -> bool {
        if self.last_applied_ignore == Some(ignore_cursor_events) {
            return false;
        }
        self.last_applied_ignore = Some(ignore_cursor_events);
        true
    }

    pub fn last_applied_ignore(&self) -> Option<bool> {
        self.last_applied_ignore
    }
}

pub fn apply_ignore_cursor_events(
    window: &tauri::WebviewWindow,
    ignore: bool,
) -> Result<(), String> {
    window
        .set_ignore_cursor_events(ignore)
        .map_err(|err| err.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duplicate_native_click_through_values_are_skipped() {
        let mut state = PointerPassThroughState::default();
        assert!(state.apply_if_changed(true));
        assert!(!state.apply_if_changed(true));
        assert!(state.apply_if_changed(false));
        assert!(!state.apply_if_changed(false));
        assert_eq!(state.last_applied_ignore(), Some(false));
    }
}
