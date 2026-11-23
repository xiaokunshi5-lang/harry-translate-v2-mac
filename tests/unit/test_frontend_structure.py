from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


class FrontendStructureTests(unittest.TestCase):
    def test_locked_react_stack_and_node_baseline(self) -> None:
        package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["dependencies"]["react"], "19.2.8")
        self.assertEqual(package["dependencies"]["react-dom"], "19.2.8")
        self.assertEqual(package["dependencies"]["zustand"], "5.0.15")
        self.assertEqual(package["dependencies"]["lucide-react"], "1.31.0")
        self.assertEqual(package["devDependencies"]["typescript"], "5.9.3")
        self.assertEqual(package["devDependencies"]["tailwindcss"], "3.4.19")
        self.assertEqual(package["devDependencies"]["vite"], "8.3.8")
        self.assertEqual(package["devDependencies"]["postcss"], "8.5.33")
        self.assertTrue((FRONTEND / ".nvmrc").is_file())
        lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["lockfileVersion"], 3)
        self.assertEqual(lock["packages"][""]["dependencies"]["react"], "19.2.8")

    def test_five_settings_sections_and_web_routes_exist(self) -> None:
        settings = (FRONTEND / "src" / "screens" / "SettingsView.tsx").read_text(
            encoding="utf-8"
        )
        for label in ["模型与服务", "声音与语言", "字幕与外观", "热词与总结", "通用与诊断"]:
            with self.subTest(label=label):
                self.assertIn(label, settings)
        app = (FRONTEND / "src" / "App.tsx").read_text(encoding="utf-8")
        monitor = (FRONTEND / "src" / "screens" / "MonitorScreens.tsx").read_text(
            encoding="utf-8"
        )
        self.assertIn('"/settings"', app)
        for route in ["/en", "/zh", "/split", "/settings"]:
            with self.subTest(route=route):
                self.assertIn(route, app + monitor)
        subtitle_panel = (
            FRONTEND / "src" / "components" / "settings" / "SubtitlePanel.tsx"
        ).read_text(encoding="utf-8")
        for label in [
            "原文字号",
            "译文字号",
            "主次显示优先级",
            "双语排版顺序",
            "云端热词开关",
            "线预算上限",
        ]:
            with self.subTest(label=label):
                self.assertIn(label, subtitle_panel)
        self.assertIn("useChannel(selectedChannel", monitor)
        self.assertEqual(monitor.count("new ChannelSocket("), 1)
        self.assertNotIn("channelEn", monitor)
        self.assertNotIn("channelZh", monitor)

    def test_frontend_has_no_metering_or_v1_test_escape_hatches(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (FRONTEND / "src").rglob("*")
            if path.is_file() and path.suffix in {".ts", ".tsx", ".css"}
        )
        for forbidden in [
            "isLegacyStageTest",
            "CERT_NONE",
            "fetchStats(",
            "total_tokens",
            "cost_rmb",
            "localStorage",
            "sessionStorage",
            "AudioContext",
            "<audio",
        ]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, sources)

    def test_legacy_cutover_and_built_static_bundle(self) -> None:
        self.assertFalse((ROOT / "index.html").exists())
        for relative in [
            "legacy/v1/server.py",
            "legacy/v1/switch_key.py",
            "legacy/v1/index.html",
        ]:
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())
        for relative in ["server.py", "switch_key.py"]:
            text = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(compatibility_marker=relative):
                self.assertLessEqual(len(text.splitlines()), 20)
                self.assertIn("legacy/v1", text)
                self.assertNotIn("pyaudio", text.casefold())
                self.assertNotIn("CERT_NONE", text)
        dist = FRONTEND / "dist"
        self.assertTrue((dist / "index.html").is_file(), "run npm run build before pytest")
        self.assertTrue(any((dist / "assets").glob("*.js")))
        self.assertTrue(any((dist / "assets").glob("*.css")))

    def test_mimi_notice_is_present(self) -> None:
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("yuxino/Mimi", notices)
        self.assertIn("bce7164fcea4591b912050c298c9b72bdbeb6f61", notices)
        self.assertIn("Copyright (c) 2026 yuxino", notices)


if __name__ == "__main__":
    unittest.main()
