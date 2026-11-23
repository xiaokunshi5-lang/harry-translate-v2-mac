from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.config_store import ConfigStore
from app.main import create_app
from tests.unit.test_bootstrap_config import asgi_request

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "sk-your-dashscope-api-key-here"
FORBIDDEN_KEYS = {"usage", "usage_stats", "input_tokens", "output_tokens"}


class _RecordingClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.messages.append(json.loads(message))

    async def close(self) -> None:
        return None


def _assert_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        assert FORBIDDEN_KEYS.isdisjoint(value), value
        for child in value.values():
            _assert_no_forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_keys(child)


class R105PureOverlayReproTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / ".env.example").write_text(
            f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )
        self.environment_patch = patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""})
        self.environment_patch.start()

    def tearDown(self) -> None:
        self.environment_patch.stop()
        self.temporary.cleanup()

    def test_defaults_and_normalization_match_r105_contract(self) -> None:
        store = ConfigStore(self.root)
        defaults = store.load_settings()
        self.assertFalse(defaults["overlay_immersive"])
        self.assertTrue(defaults["overlay_click_through"])
        self.assertFalse(defaults["is_overlay_locked"])
        self.assertFalse(defaults["hud_geometry"]["locked"])

        normalized = store.update_settings(
            {
                "background_opacity": "35",
                "is_overlay_locked": True,
                "hud_geometry": {"locked": False},
            }
        )
        self.assertEqual(normalized["background_opacity"], 35)
        self.assertIsInstance(normalized["background_opacity"], int)
        self.assertTrue(normalized["is_overlay_locked"])
        self.assertTrue(normalized["hud_geometry"]["locked"])

        low = store.update_settings({"background_opacity": -20})
        high = store.update_settings({"background_opacity": 900, "is_overlay_locked": False})
        self.assertEqual(low["background_opacity"], 1)
        self.assertEqual(high["background_opacity"], 100)
        self.assertFalse(high["is_overlay_locked"])
        self.assertFalse(high["hud_geometry"]["locked"])

    def test_config_updates_are_broadcast_without_metering_fields(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        manager = application.state.session_manager
        client = _RecordingClient()
        manager.add_client("main", client)

        async def update() -> None:
            response = await asgi_request(
                application,
                "/api/config",
                method="PUT",
                payload={
                    "settings": {
                        "overlay_immersive": True,
                        "background_opacity": 35,
                        "is_overlay_locked": True,
                        "overlay_click_through": True,
                    }
                },
            )
            self.assertEqual(response.status, 200)
            switched = await asgi_request(
                application,
                "/api/session/switch",
                method="POST",
                payload={"source_lang": "auto", "target_lang": "ja"},
            )
            self.assertEqual(switched.status, 200)

        asyncio.run(update())
        config_events = [
            message for message in client.messages if message.get("type") == "config_updated"
        ]
        self.assertGreaterEqual(len(config_events), 2)
        event = config_events[0]
        self.assertEqual(event["settings"]["background_opacity"], 35)
        self.assertTrue(event["settings"]["overlay_immersive"])
        self.assertTrue(event["settings"]["is_overlay_locked"])
        self.assertTrue(event["settings"]["hud_geometry"]["locked"])
        _assert_no_forbidden_keys(event)

    def test_pure_overlay_immersive_and_tray_contract_is_present(self) -> None:
        overlay_path = ROOT / "frontend/src/windows/overlay/OverlayWindow.tsx"
        css_path = ROOT / "frontend/src/windows/overlay/layout/overlayChrome.css"
        appearance_path = ROOT / "frontend/src/lib/subtitles/subtitleAppearance.ts"
        tray_path = ROOT / "src-tauri/src/windows/tray.rs"
        overlay_rust_path = ROOT / "src-tauri/src/windows/overlay.rs"
        settings_store_path = ROOT / "frontend/src/store/settingsStore.ts"
        subtitle_panel_path = ROOT / "frontend/src/components/settings/SubtitlePanel.tsx"

        for path in (
            appearance_path,
            tray_path,
            overlay_path,
            css_path,
            overlay_rust_path,
            settings_store_path,
            subtitle_panel_path,
        ):
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file())

        overlay_source = overlay_path.read_text(encoding="utf-8")
        native_source = overlay_source.split("isBrowserFallback ?", 1)[0]
        self.assertNotIn("<button", native_source)
        self.assertNotIn("<a ", native_source)
        self.assertNotIn("overlay-capsule", native_source)
        self.assertIn("data-presentation=", overlay_source)
        self.assertIn('"background-blend"', overlay_source)
        self.assertIn("IMMERSIVE_TEXT_SHADOW", overlay_source)
        self.assertIn("100%", overlay_source)

        css = css_path.read_text(encoding="utf-8")
        self.assertNotIn("backdrop-filter", css)

        appearance = appearance_path.read_text(encoding="utf-8")
        self.assertIn("rgba(0, 0, 0", appearance)
        self.assertIn("IMMERSIVE_TEXT_SHADOW", appearance)

        tray = tray_path.read_text(encoding="utf-8")
        for required in (
            '"harry-tray"',
            "打开配置页（浏览器）",
            "沉浸模式（透明穿透）",
            "锁定穿透（保留底色）",
            "重置悬浮窗位置与尺寸",
            "退出悬浮窗",
        ):
            with self.subTest(required=required):
                self.assertIn(required, tray)

        overlay_rust = overlay_rust_path.read_text(encoding="utf-8")
        self.assertIn("effective_click_through", overlay_rust)
        self.assertIn("immersive || (locked && click_through_enabled)", overlay_rust)

        settings_store = settings_store_path.read_text(encoding="utf-8")
        self.assertIn("applyImmediate", settings_store)
        subtitle_panel = subtitle_panel_path.read_text(encoding="utf-8")
        self.assertIn("overlay_immersive", subtitle_panel)
        self.assertIn("is_overlay_locked", subtitle_panel)
        self.assertIn("沉浸模式下已去除背景底色", subtitle_panel)


if __name__ == "__main__":
    unittest.main()
