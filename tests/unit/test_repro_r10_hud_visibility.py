"""
【复现测试】ISSUE-r10-hud: R10 原生悬浮窗不可见（缺少桌面启动入口 & 启动阶段未应用/钳制 geometry）
- 根因定位:
  1. 根目录缺少启动 Tauri 原生悬浮窗的用户入口，bootstrap_macos.sh 无 --desktop 模式
  2. src-tauri/tauri.conf.json overlay 窗口缺少显式 x/y，且 lib.rs setup() 未从配置加载并钳制 presentation_frame
  3. work_area.rs 未将 AppKit 左下角 visibleFrame 转为顶左坐标系
- 修复前预期: 抛出 AssertionError，退出码 != 0 (3/3 红灯)
- 修复后预期: 断言全部通过，退出码 == 0 (绿灯)
"""
from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestReproR10HudVisibility(unittest.TestCase):
    def test_desktop_overlay_launcher_exists_and_keeps_legacy_web_entry(self) -> None:
        web_launcher = ROOT / "启动同传系统.command"
        self.assertTrue(web_launcher.is_file())
        self.assertNotIn("--desktop", web_launcher.read_text(encoding="utf-8"))

        desktop_launcher = ROOT / "启动桌面悬浮窗.command"
        self.assertTrue(desktop_launcher.is_file(), "缺少根目录桌面悬浮窗启动脚本 启动桌面悬浮窗.command")
        self.assertTrue(os.access(desktop_launcher, os.X_OK), "启动桌面悬浮窗.command 必须具备可执行权限")
        desktop_text = desktop_launcher.read_text(encoding="utf-8")
        self.assertIn("bootstrap_macos.sh", desktop_text)
        self.assertIn("--desktop", desktop_text)

        parsed = subprocess.run(
            ["bash", "-n", str(desktop_launcher)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

        bootstrap = (ROOT / "scripts" / "bootstrap_macos.sh").read_text(encoding="utf-8")
        self.assertIn("--desktop", bootstrap)
        self.assertIn("harry-translate-mac", bootstrap)
        self.assertIn("HARRY_PROJECT_ROOT", bootstrap)

    def test_tauri_conf_declares_explicit_on_screen_coordinates(self) -> None:
        conf = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        overlay = conf["app"]["windows"][0]
        self.assertIn("x", overlay, "overlay 窗口配置缺少显式 x 坐标")
        self.assertIn("y", overlay, "overlay 窗口配置缺少显式 y 坐标")
        self.assertGreaterEqual(overlay["x"], 0)
        self.assertGreaterEqual(overlay["y"], 0)

    def test_tauri_setup_loads_clamps_and_applies_startup_geometry(self) -> None:
        lib_rs = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        self.assertIn("apply_presentation_frame", lib_rs)
        self.assertIn("primary_work_area", lib_rs)
        self.assertIn("from_settings_file", lib_rs)

        overlay_rs = (ROOT / "src-tauri" / "src" / "windows" / "overlay.rs").read_text(encoding="utf-8")
        self.assertIn("pub fn from_settings_file", overlay_rs)

        work_area_rs = (ROOT / "src-tauri" / "src" / "windows" / "work_area.rs").read_text(encoding="utf-8")
        self.assertIn("appkit_visible_to_top_left_rect", work_area_rs)


if __name__ == "__main__":
    unittest.main()
