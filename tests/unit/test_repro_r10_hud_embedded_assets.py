"""
【复现测试】ISSUE-r10-hud-embedded: Tauri 悬浮窗因缺少 custom-protocol 内嵌前端与透明/拖拽修复导致页面不可见且不可拖动
- 根因定位:
  1. src-tauri/Cargo.toml 未定义并默认启用 custom-protocol = ["tauri/custom-protocol"]，调试二进制连接死地址 http://127.0.0.1:5173/hud
  2. scripts/bootstrap_macos.sh 未带 --features custom-protocol 构建且仅当二进制缺失时才编译
  3. overlayChrome.css 未清除全局 theme.css 的 #f5f2eb 背景与 min-width: 320px
  4. macos_drag.rs 未检查 NSEventType::LeftMouseDown 导致非左键按下事件下拖拽不稳定
- 修复前预期: 抛出 AssertionError，退出码 != 0 (3/3 红灯)
- 修复后预期: 断言全部通过，退出码 == 0 (绿灯)
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestReproR10HudEmbeddedAssets(unittest.TestCase):
    def test_cargo_toml_enables_custom_protocol_by_default(self) -> None:
        cargo_toml = (ROOT / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
        self.assertIn('custom-protocol = ["tauri/custom-protocol"]', cargo_toml)
        self.assertIn('default = ["custom-protocol"]', cargo_toml)

    def test_bootstrap_builds_with_custom_protocol_feature(self) -> None:
        bootstrap = (ROOT / "scripts" / "bootstrap_macos.sh").read_text(encoding="utf-8")
        self.assertIn("--features custom-protocol", bootstrap)

    def test_overlay_css_clears_root_background_for_native_overlay(self) -> None:
        css = (
            ROOT / "frontend" / "src" / "windows" / "overlay" / "layout" / "overlayChrome.css"
        ).read_text(encoding="utf-8")
        self.assertIn("overlay-stage:not(.overlay-stage--browser)", css)
        self.assertIn("background: transparent !important", css)

    def test_macos_drag_checks_left_mouse_down_and_falls_back_to_start_dragging(self) -> None:
        drag_rs = (ROOT / "src-tauri" / "src" / "windows" / "macos_drag.rs").read_text(
            encoding="utf-8"
        )
        self.assertIn("NSEventType::LeftMouseDown", drag_rs)
        self.assertIn("performWindowDragWithEvent", drag_rs)
        self.assertIn("win.start_dragging()", drag_rs)


if __name__ == "__main__":
    unittest.main()
