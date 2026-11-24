from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.main import create_app
from tests.unit.test_repro_r18_01_quiet_launch_and_stop import (
    PLACEHOLDER,
    asgi_http_request,
)


class PassiveConfigSocket:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def close(self, code: int = 1000) -> None:
        self.closed = True


class CountingController:
    def __init__(self) -> None:
        self.start_calls = 0

    async def start(self) -> bool:
        self.start_calls += 1
        return True

    async def stop(self) -> None:
        return

    async def stop_if_idle(self) -> None:
        return


class ReproR1802RealtimeSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / ".env.example").write_text(
            f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_status_and_config_push_share_realtime_enabled_snapshot(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        passive_socket = PassiveConfigSocket()
        application.state.passive_config_sockets.add(passive_socket)

        async def run_scenario() -> tuple[dict[str, Any], dict[str, Any]]:
            registered = await asgi_http_request(
                application,
                "/api/overlay/register",
                method="POST",
                payload={
                    "control_url": "http://127.0.0.1:43210",
                    "visible": False,
                },
            )
            with patch(
                "app.main._overlay_control_health",
                return_value={"ok": True, "visible": False},
            ):
                status = await asgi_http_request(
                    application,
                    "/api/overlay/status",
                )
            return registered.json(), status.json()

        registered, status = asyncio.run(run_scenario())
        self.assertFalse(registered["visible"])
        self.assertFalse(registered["realtime_enabled"])
        self.assertFalse(status["visible"])
        self.assertFalse(status["realtime_enabled"])
        self.assertTrue(status["registered"])
        self.assertEqual(status["fallback_url"], "/hud?fallback=browser")

        event = passive_socket.events[-1]
        self.assertEqual(event["type"], "overlay_status")
        self.assertEqual(event["overlay"]["visible"], status["visible"])
        self.assertEqual(
            event["overlay"]["realtime_enabled"],
            status["realtime_enabled"],
        )
        self.assertEqual(
            event["overlay"]["registered"],
            status["registered"],
        )

    def test_launch_and_hide_broadcast_the_authoritative_override_state(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        passive_socket = PassiveConfigSocket()
        application.state.passive_config_sockets.add(passive_socket)

        async def run_scenario() -> tuple[dict[str, Any], dict[str, Any]]:
            await asgi_http_request(
                application,
                "/api/overlay/register",
                method="POST",
                payload={
                    "control_url": "http://127.0.0.1:43210",
                    "visible": False,
                },
            )
            with patch("app.main._overlay_control_request", return_value=True):
                launched = await asgi_http_request(
                    application,
                    "/api/overlay/launch",
                    method="POST",
                    payload={},
                )
                hidden = await asgi_http_request(
                    application,
                    "/api/overlay/hide",
                    method="POST",
                    payload={},
                )
            return launched.json(), hidden.json()

        launched, hidden = asyncio.run(run_scenario())
        self.assertTrue(launched["ok"])
        self.assertTrue(launched["realtime_enabled"])
        self.assertTrue(hidden["ok"])
        self.assertFalse(hidden["realtime_enabled"])

        overlay_events = [
            event
            for event in passive_socket.events
            if event.get("type") == "overlay_status"
        ]
        self.assertEqual(
            [event["overlay"]["realtime_enabled"] for event in overlay_events],
            [False, True, False],
        )

    def test_config_handshake_is_passive_and_emits_overlay_snapshot(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        controller = CountingController()
        manager = application.state.session_manager
        manager.register_controller("main", controller)
        messages: list[dict[str, Any]] = []
        receive_count = 0

        async def receive() -> dict[str, Any]:
            nonlocal receive_count
            receive_count += 1
            if receive_count == 1:
                return {"type": "websocket.connect"}
            return {"type": "websocket.disconnect", "code": 1000}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        asyncio.run(
            application(
                {
                    "type": "websocket",
                    "asgi": {"version": "3.0", "spec_version": "2.3"},
                    "http_version": "1.1",
                    "scheme": "ws",
                    "path": "/ws/config",
                    "raw_path": b"/ws/config",
                    "query_string": b"",
                    "root_path": "",
                    "headers": [(b"host", b"testserver")],
                    "server": ("testserver", 80),
                    "client": ("127.0.0.1", 50002),
                    "subprotocols": [],
                    "state": {},
                },
                receive,
                send,
            )
        )

        events = [
            json.loads(message["text"])
            for message in messages
            if message.get("type") == "websocket.send"
        ]
        self.assertEqual(events[0]["type"], "config_updated")
        self.assertEqual(events[1]["type"], "overlay_status")
        self.assertIn("realtime_enabled", events[1]["overlay"])
        self.assertEqual(controller.start_calls, 0)
        self.assertEqual(manager.active_client_count("main"), 0)


if __name__ == "__main__":
    unittest.main()
