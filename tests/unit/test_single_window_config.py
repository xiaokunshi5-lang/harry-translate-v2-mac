from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config_store import MAX_OVERLAY_WINDOWS, PRIMARY_WINDOW_ID, ConfigStore
from app.session_manager import MAX_OVERLAY_WINDOWS as SESSION_MAX_WINDOWS

ROOT = Path(__file__).resolve().parents[2]


class SingleWindowConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / ".env.example").write_text(
            "DASHSCOPE_API_KEY=sk-your-dashscope-api-key-here\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )
        self.env_patch = patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""})
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temporary.cleanup()

    def test_single_window_limit_and_legacy_multiwindow_migration(self) -> None:
        self.assertEqual(MAX_OVERLAY_WINDOWS, 1)
        self.assertEqual(SESSION_MAX_WINDOWS, 1)

        store = ConfigStore(self.root)
        updated = store.update_settings(
            {
                "overlay_window_mode": "multi",
                "multi_window": True,
                "max_overlay_windows": 3,
                "overlay_windows": [
                    {"window_id": "en", "target_lang": "en"},
                    {"window_id": "main", "target_lang": "fr"},
                    {"window_id": "zh", "target_lang": "zh"},
                ],
                "windows": [],
            }
        )
        self.assertEqual(updated["overlay_window_mode"], "single")
        self.assertNotIn("overlay_windows", updated)
        self.assertNotIn("multi_window", updated)
        self.assertNotIn("max_overlay_windows", updated)
        self.assertEqual(len(updated["windows"]), 1)
        self.assertEqual(updated["windows"][0]["window_id"], PRIMARY_WINDOW_ID)
        self.assertEqual(updated["windows"][0]["target_lang"], "fr")

        reloaded = ConfigStore(self.root).load_settings()
        self.assertEqual(reloaded["overlay_window_mode"], "single")
        self.assertEqual(len(reloaded["windows"]), 1)
        self.assertEqual(reloaded["windows"][0]["window_id"], "main")

    def test_ten_display_and_immersive_cycles_persist_cleanly(self) -> None:
        store = ConfigStore(self.root)
        store.update_settings(
            {
                "source_font_size": 15,
                "target_font_size": 20,
                "subtitle_emphasis": "translation_primary",
                "bilingual_layout_order": "source_top",
            }
        )
        cycle = ["bilingual", "translation", "original", "only_translation", "only_source"]
        for step in range(10):
            mode = cycle[step % len(cycle)]
            immersive = step % 2 == 1
            res = store.update_settings(
                {
                    "subtitle_display_mode": mode,
                    "overlay_immersive": immersive,
                }
            )
            self.assertIn(
                res["subtitle_display_mode"],
                {"bilingual", "translation", "original"},
            )
            self.assertEqual(res["overlay_window_mode"], "single")

        final = store.update_settings(
            {"subtitle_display_mode": "bilingual", "overlay_immersive": False}
        )
        restarted = ConfigStore(self.root).load_settings()
        self.assertEqual(restarted["subtitle_display_mode"], "bilingual")
        self.assertEqual(restarted["source_font_size"], 15)
        self.assertEqual(restarted["target_font_size"], 20)
        self.assertEqual(restarted["subtitle_emphasis"], "translation_primary")
        self.assertEqual(restarted["overlay_window_mode"], final["overlay_window_mode"])

    def test_zero_button_native_overlay_and_web_quick_term_bar_placement(self) -> None:
        overlay_tsx = (
            ROOT / "frontend/src/windows/overlay/OverlayWindow.tsx"
        ).read_text(encoding="utf-8")
        native_segment = overlay_tsx.split("isBrowserFallback ?", 1)[0]
        self.assertNotIn("<button", native_segment)
        self.assertNotIn("<a ", native_segment)
        self.assertNotIn("QuickTermBar", overlay_tsx)
        self.assertNotIn("LanguageStatusCapsule", overlay_tsx)
        self.assertIn("data-display-mode=", overlay_tsx)

        monitor_tsx = (ROOT / "frontend/src/screens/MonitorScreens.tsx").read_text(
            encoding="utf-8"
        )
        glossary_panel_tsx = (
            ROOT / "frontend/src/components/glossary/GlossaryPanel.tsx"
        ).read_text(encoding="utf-8")
        self.assertIn("QuickTermBar", monitor_tsx)
        self.assertIn("LanguageStatusCapsule", monitor_tsx)
        self.assertIn("QuickTermBar", glossary_panel_tsx)

    def test_third_party_notices_and_zero_agpl_compliance(self) -> None:
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("yuxino/Mimi", notices)
        self.assertIn("SakiRinn/LiveCaptions-Translator", notices)
        self.assertIn("Apache License", notices)

        for folder in ("app", "frontend/src", "src-tauri/src"):
            for path in (ROOT / folder).rglob("*"):
                if path.is_file() and path.suffix in {".py", ".ts", ".tsx", ".rs"}:
                    text = path.read_text(encoding="utf-8").casefold()
                    self.assertNotIn("dilmesh", text, str(path))
                    self.assertNotIn("sokuji", text, str(path))
                    self.assertNotIn("agpl-3.0", text, str(path))


if __name__ == "__main__":
    unittest.main()
