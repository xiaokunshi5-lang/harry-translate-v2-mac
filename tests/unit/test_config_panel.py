from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config_store import ConfigStore
from app.main import create_app
from tests.unit.test_bootstrap_config import PLACEHOLDER, VALID_KEY, asgi_request


class ConfigPanelTests(unittest.TestCase):
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

    def test_config_returns_legacy_and_ui_aliases_without_plaintext(self) -> None:
        store = ConfigStore(self.root)
        store.set_api_key(VALID_KEY)
        application = create_app(self.root, enable_audio_capture=False)
        response = asyncio_run(asgi_request(application, "/api/config"))
        payload = json.loads(response.body)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["api_key_masked"], "sk-****abcd")
        self.assertEqual(payload["key_mask"], "sk-****abcd")
        self.assertEqual(payload["user_settings"], payload["settings"])
        self.assertEqual(payload["user_settings"]["active_provider"], "qwen")
        self.assertEqual(payload["user_settings"]["source_font_size"], 15)
        self.assertEqual(payload["user_settings"]["target_font_size"], 18)
        self.assertEqual(
            payload["user_settings"]["subtitle_emphasis"],
            "translation_primary",
        )
        self.assertEqual(payload["user_settings"]["bilingual_layout_order"], "source_top")
        self.assertTrue(payload["user_settings"]["glossary_enabled"])
        self.assertEqual(payload["user_settings"]["glossary_wire_budget_bytes"], 4096)
        self.assertEqual(payload["user_settings"]["overlay_window_mode"], "single")
        self.assertFalse(payload["user_settings"]["overlay_immersive"])
        self.assertTrue(payload["user_settings"]["overlay_click_through"])
        self.assertIn("qwen", payload["provider_catalog"]["providers"])
        self.assertNotIn(VALID_KEY.encode("utf-8"), response.body)

    def test_empty_secret_does_not_overwrite_and_settings_survive_restart(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        accepted = asyncio_run(
            asgi_request(
                application,
                "/api/config/secrets",
                method="POST",
                payload={"api_key": VALID_KEY},
            )
        )
        self.assertEqual(accepted.status, 200)

        empty = asyncio_run(
            asgi_request(
                application,
                "/api/config/secrets",
                method="POST",
                payload={"api_key": "   "},
            )
        )
        self.assertEqual(empty.status, 200)
        self.assertFalse(json.loads(empty.body)["needs_setup"])

        update = asyncio_run(
            asgi_request(
                application,
                "/api/config",
                method="PUT",
                payload={
                    "settings": {
                        "source_lang": "fr",
                        "target_lang": "zh",
                        "font_size": 28,
                    }
                },
            )
        )
        self.assertEqual(update.status, 200)

        restarted = create_app(self.root, enable_audio_capture=False)
        response = asyncio_run(asgi_request(restarted, "/api/config"))
        payload = json.loads(response.body)
        self.assertEqual(payload["user_settings"]["source_lang"], "fr")
        self.assertEqual(payload["user_settings"]["target_lang"], "zh")
        self.assertEqual(payload["user_settings"]["font_size"], 28)
        self.assertEqual(payload["user_settings"]["source_font_size"], 28)
        self.assertEqual(payload["user_settings"]["target_font_size"], 28)
        self.assertEqual(payload["api_key_masked"], "sk-****abcd")

    def test_display_and_glossary_settings_are_clamped_and_persisted(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        update = asyncio_run(
            asgi_request(
                application,
                "/api/config",
                method="PUT",
                payload={
                    "settings": {
                        "source_font_size": 99,
                        "target_font_size": 1,
                        "subtitle_emphasis": "invalid",
                        "bilingual_layout_order": "target_top",
                        "glossary_enabled": False,
                        "glossary_wire_budget_bytes": 99_999,
                        "overlay_window_mode": "multi",
                        "overlay_immersive": False,
                        "overlay_click_through": False,
                    }
                },
            )
        )
        self.assertEqual(update.status, 200)
        settings = json.loads(update.body)["user_settings"]
        self.assertEqual(settings["source_font_size"], 48)
        self.assertEqual(settings["target_font_size"], 12)
        self.assertEqual(settings["subtitle_emphasis"], "translation_primary")
        self.assertEqual(settings["bilingual_layout_order"], "target_top")
        self.assertFalse(settings["glossary_enabled"])
        self.assertEqual(settings["glossary_wire_budget_bytes"], 6144)
        self.assertEqual(settings["overlay_window_mode"], "single")
        self.assertFalse(settings["overlay_immersive"])
        self.assertFalse(settings["overlay_click_through"])

        restarted = ConfigStore(self.root)
        persisted = restarted.load_settings()
        for key in (
            "source_font_size",
            "target_font_size",
            "subtitle_emphasis",
            "bilingual_layout_order",
            "glossary_enabled",
            "glossary_wire_budget_bytes",
            "overlay_window_mode",
            "overlay_immersive",
            "overlay_click_through",
        ):
            with self.subTest(key=key):
                self.assertEqual(persisted[key], settings[key])

    def test_invalid_language_pair_returns_400_before_persisting(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        response = asyncio_run(
            asgi_request(
                application,
                "/api/config",
                method="PUT",
                payload={
                    "settings": {
                        "source_lang": "xx",
                        "target_lang": "yy",
                    }
                },
            )
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(json.loads(response.body)["error"], "unsupported_language_pair")
        self.assertEqual(ConfigStore(self.root).load_settings()["target_lang"], "zh")

    def test_single_session_switch_updates_target_without_parallel_channel(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        response = asyncio_run(
            asgi_request(
                application,
                "/api/session/switch",
                method="POST",
                payload={"source_lang": "auto", "target_lang": "ja"},
            )
        )
        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["user_settings"]["target_lang"], "ja")
        self.assertEqual(payload["engine_state"]["type"], "engine_state")
        self.assertEqual(ConfigStore(self.root).load_settings()["target_lang"], "ja")

    def test_catalog_and_bounded_provider_probe_contract(self) -> None:
        ConfigStore(self.root).set_api_key(VALID_KEY)
        application = create_app(self.root, enable_audio_capture=False)
        catalog = asyncio_run(asgi_request(application, "/api/providers/catalog"))
        self.assertEqual(catalog.status, 200)
        self.assertEqual(json.loads(catalog.body)["default_provider"], "qwen")

        probe_result = {
            "ok": True,
            "provider": "qwen",
            "routed_endpoint": "wss://<workspace>.maas.aliyuncs.com/api-ws/v1/realtime",
            "latency_ms": 12.5,
            "error_code": None,
        }
        with patch("app.main.probe_provider_endpoint", return_value=probe_result) as probe:
            response = asyncio_run(
                asgi_request(
                    application,
                    "/api/providers/test",
                    method="POST",
                    payload={
                        "provider": "qwen",
                        "source_lang": "auto",
                        "target_lang": "de",
                        "workspace_id": "space-1",
                    },
                )
            )
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body), probe_result)
        self.assertTrue(probe.called)
        self.assertEqual(probe.call_args.kwargs["api_key"], VALID_KEY)
        self.assertNotIn(VALID_KEY.encode("utf-8"), response.body)

    def test_corrupt_settings_are_backed_up_and_replaced_with_defaults(self) -> None:
        store = ConfigStore(self.root)
        store.settings_path.write_text("{broken-json", encoding="utf-8")
        loaded = ConfigStore(self.root).load_settings()
        backups = list((self.root / "config").glob("user_settings.corrupt-*.json"))
        self.assertEqual(loaded["active_provider"], "qwen")
        self.assertEqual(len(backups), 1)
        self.assertIn("broken-json", backups[0].read_text(encoding="utf-8"))
        self.assertEqual(json.loads(store.settings_path.read_text(encoding="utf-8"))["target_lang"], "zh")


def asyncio_run(awaitable: object) -> object:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
