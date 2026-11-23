"""R11 AC-R11-01: system-audio probes degrade instead of crashing."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.audio.device_probe import SystemAudioProbe
from app.main import create_app
from tests.unit.test_bootstrap_config import PLACEHOLDER, asgi_request


def json_request(application, path, method="GET", payload=None):
    response = asyncio.run(asgi_request(application, path, method=method, payload=payload))
    return response, json.loads(response.body)


def make_probe(status: str, loopback_available: bool) -> SystemAudioProbe:
    return SystemAudioProbe(
        backend="sounddevice",
        loopback_available=loopback_available,
        loopback_devices=[{"index": 2, "name": "BlackHole 2ch"}] if loopback_available else [],
        input_devices=[{"index": 0, "name": "MacBook Pro Microphone"}],
        status=status,
        hint="install blackhole" if not loopback_available else "",
    )


class SystemAudioProbeTests(unittest.TestCase):
    def test_probe_result_has_required_contract_fields(self) -> None:
        probe = make_probe("loopback_missing", False)
        self.assertEqual(probe.status, "loopback_missing")
        self.assertFalse(probe.loopback_available)
        self.assertEqual(probe.input_devices[0]["name"], "MacBook Pro Microphone")

    def test_api_selects_microphone_when_loopback_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n"
            )
            app = create_app(root, enable_audio_capture=False)
            probe = make_probe("loopback_missing", False)
            with patch("app.main.probe_system_audio", return_value=probe):
                response, payload = json_request(
                    app,
                    "/api/audio/input",
                    method="POST",
                    payload={"mode": "both"},
                )
            self.assertEqual(response.status, 200)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "loopback_missing")
            self.assertTrue(payload["system_audio_idle"])
            self.assertEqual(payload["settings"]["audio_input"], "microphone")

    def test_api_accepts_available_loopback_for_both(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n"
            )
            app = create_app(root, enable_audio_capture=False)
            probe = make_probe("available", True)
            with patch("app.main.probe_system_audio", return_value=probe):
                response, payload = json_request(
                    app,
                    "/api/audio/input",
                    method="POST",
                    payload={"mode": "both"},
                )
            self.assertEqual(response.status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "available")
            self.assertEqual(payload["settings"]["audio_input"], "both")


if __name__ == "__main__":
    unittest.main()
