from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app.config_store import ConfigStore

ROOT = Path(__file__).resolve().parents[2]


class DesktopPanelContractTests(unittest.TestCase):
    def test_config_store_clamps_hud_geometry_and_locks_single_window_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / ".env.example").write_text(
                "DASHSCOPE_API_KEY=sk-your-dashscope-api-key-here\n",
                encoding="utf-8",
            )
            store = ConfigStore(root)
            updated = store.update_settings(
                {
                    "overlay_window_mode": "multi",
                    "hud_geometry": {
                        "x": 200,
                        "y": 120,
                        "width": 1500,
                        "height": 800,
                        "mode": "invalid",
                        "locked": True,
                    },
                }
            )
            self.assertEqual(updated["overlay_window_mode"], "single")
            self.assertEqual(updated["hud_geometry"]["width"], 1200)
            self.assertEqual(updated["hud_geometry"]["height"], 600)
            self.assertEqual(updated["hud_geometry"]["mode"], "expanded")
            self.assertTrue(updated["hud_geometry"]["locked"])

            shrunk = store.update_settings(
                {
                    "hud_geometry": {
                        "width": 100,
                        "height": 40,
                        "mode": "collapsed",
                    }
                }
            )
            self.assertEqual(shrunk["hud_geometry"]["width"], 360)
            self.assertEqual(shrunk["hud_geometry"]["height"], 100)
            self.assertEqual(shrunk["hud_geometry"]["mode"], "collapsed")

    def test_frontend_overlay_interaction_files_respect_single_window_and_bounds(self) -> None:
        overlay_dir = ROOT / "frontend" / "src" / "windows" / "overlay"
        required = [
            overlay_dir / "OverlayWindow.tsx",
            overlay_dir / "interaction" / "ResizeHandles.tsx",
            overlay_dir / "interaction" / "OverlayDragArea.tsx",
            overlay_dir / "interaction" / "overlayContentDrag.ts",
            overlay_dir / "interaction" / "resizeIpc.ts",
            overlay_dir / "layout" / "overlayChrome.css",
        ]
        for path in required:
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file())

        resize_ipc = (overlay_dir / "interaction" / "resizeIpc.ts").read_text(encoding="utf-8")
        self.assertIn("MAX_OVERLAY_WINDOWS: 1", resize_ipc)
        self.assertIn("COLLAPSED_WIDTH: 280", resize_ipc)
        self.assertIn("COLLAPSED_HEIGHT: 54", resize_ipc)
        self.assertIn("MIN_WIDTH: 360", resize_ipc)
        self.assertIn("MAX_WIDTH: 1200", resize_ipc)

        overlay_tsx = (overlay_dir / "OverlayWindow.tsx").read_text(encoding="utf-8")
        self.assertEqual(overlay_tsx.count('new ChannelSocket("main"'), 1)

    def test_rust_windows_unit_tests_pass(self) -> None:
        result = subprocess.run(
            [
                "cargo",
                "test",
                "--offline",
                "--manifest-path",
                str(ROOT / "src-tauri" / "Cargo.toml"),
                "--lib",
                "windows",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)


if __name__ == "__main__":
    unittest.main()
