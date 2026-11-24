from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "sk-your-dashscope-api-key-here"


class AsgiResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def json(self) -> dict[str, Any]:
        return json.loads(self.body)


async def asgi_http_request(
    application: Any,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> AsgiResponse:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    headers = [(b"host", b"testserver")]
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 50000),
        "state": {},
    }
    request_sent = False
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await application(scope, receive, send)
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return AsgiResponse(int(start["status"]), response_body)


class FakeAudioRuntime:
    instances: list["FakeAudioRuntime"] = []

    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.running = False
        self.start_calls = 0
        self.stop_calls = 0
        self.instances.append(self)

    @property
    def is_running(self) -> bool:
        return self.running

    def start(self) -> bool:
        self.start_calls += 1
        self.running = True
        return True

    def stop(self) -> None:
        self.stop_calls += 1
        self.running = False


class FakeController:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.stop_if_idle_calls = 0

    async def start(self) -> bool:
        self.start_calls += 1
        return True

    async def stop(self) -> None:
        self.stop_calls += 1

    async def stop_if_idle(self) -> None:
        self.stop_if_idle_calls += 1


class FailingController(FakeController):
    async def stop(self) -> None:
        self.stop_calls += 1
        raise RuntimeError("controller_stop_failed")


class FakeClient:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.close_code: int | None = None

    async def send_json(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def close(self, code: int = 1000) -> None:
        self.close_code = code


class OverlayControlHandler(BaseHTTPRequestHandler):
    visible = False

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        self._write_json({"ok": True, "visible": self.visible})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/hide":
            self.visible = False
            self._write_json({"ok": True, "visible": False})
            return
        self.send_error(404)

    def _write_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class ReproR1801QuietLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / ".env.example").write_text(
            f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )
        FakeAudioRuntime.instances = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_tauri_setup_is_quiet_and_registers_real_visibility(self) -> None:
        lib_source = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(
            encoding="utf-8"
        )
        controls_source = (
            ROOT / "src-tauri" / "src" / "windows" / "controls.rs"
        ).read_text(encoding="utf-8")
        setup_source = lib_source.split(".setup(move |app| {", 1)[1].split(
            ".build(tauri::generate_context!())",
            1,
        )[0]

        self.assertNotIn("show_primary_overlay_window(", setup_source)
        self.assertNotIn("set_visible(true)", setup_source)
        self.assertIn("register_overlay_control_blocking", setup_source)
        self.assertRegex(
            controls_source,
            r"serde_json::json!\(\{\s*\"control_url\"\s*:\s*control_url,\s*"
            r"\"visible\"\s*:\s*visible,?\s*\}\)",
        )
        self.assertIn("remaining_timeout(deadline)", controls_source)

    def test_registration_and_status_keep_the_native_overlay_hidden(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), OverlayControlHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        control_url = f"http://127.0.0.1:{server.server_address[1]}"
        application = create_app(self.root, enable_audio_capture=False)
        try:
            initial = asyncio.run(asgi_http_request(application, "/api/overlay/status"))
            initial_payload = initial.json()
            self.assertFalse(initial_payload["visible"])
            self.assertFalse(initial_payload["realtime_enabled"])

            registered = asyncio.run(
                asgi_http_request(
                    application,
                    "/api/overlay/register",
                    method="POST",
                    payload={"control_url": control_url, "visible": False},
                )
            )
            registered_payload = registered.json()
            self.assertEqual(registered.status, 200)
            self.assertFalse(registered_payload["visible"])
            self.assertFalse(registered_payload["realtime_enabled"])
            self.assertFalse(application.state.overlay_visible)
            self.assertTrue(application.state.overlay_gate_active)
            self.assertTrue(application.state.session_manager.paused)

            application.state.overlay_visible = True
            application.state.realtime_enabled = True
            application.state.session_manager.paused = False
            duplicate = asyncio.run(
                asgi_http_request(
                    application,
                    "/api/overlay/register",
                    method="POST",
                    payload={"control_url": control_url, "visible": False},
                )
            )
            duplicate_payload = duplicate.json()
            self.assertEqual(duplicate.status, 200)
            self.assertTrue(duplicate_payload["visible"])
            self.assertTrue(duplicate_payload["realtime_enabled"])
            self.assertFalse(application.state.session_manager.paused)

            status = asyncio.run(asgi_http_request(application, "/api/overlay/status"))
            status_payload = status.json()
            self.assertTrue(status_payload["registered"])
            self.assertFalse(status_payload["visible"])
            self.assertFalse(status_payload["realtime_enabled"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_hide_stops_main_flow_and_unregistered_gate_rejects_new_clients(
        self,
    ) -> None:
        with patch("app.main.AudioRuntime", FakeAudioRuntime):
            application = create_app(self.root, enable_audio_capture=True)

        manager = application.state.session_manager
        runtime = FakeAudioRuntime.instances[0]
        controller = FakeController()
        manager.register_controller("main", controller)
        manager.paused = False
        manager.needs_setup = lambda: False
        application.state.overlay_gate_active = True
        application.state.realtime_enabled = True
        application.state.overlay_visible = True
        application.state.overlay_control_url = "http://127.0.0.1:43210"
        runtime.running = True

        client = FakeClient()
        manager.add_client("main", client)
        with patch("app.main._overlay_control_request", return_value=True):
            hidden = asyncio.run(
                asgi_http_request(
                    application,
                    "/api/overlay/hide",
                    method="POST",
                    payload={},
                )
            )

        self.assertEqual(hidden.status, 200)
        self.assertEqual(client.close_code, 1000)
        self.assertEqual(manager.active_client_count("main"), 0)
        self.assertTrue(manager.paused)
        self.assertFalse(application.state.realtime_enabled)
        self.assertEqual(controller.stop_calls, 1)
        self.assertEqual(runtime.stop_calls, 1)
        self.assertFalse(runtime.is_running)

        controller.start_calls = 0
        controller.stop_calls = 0
        runtime.start_calls = 0
        runtime.stop_calls = 0
        websocket_messages: list[dict[str, Any]] = []
        websocket_step = 0

        async def receive() -> dict[str, Any]:
            nonlocal websocket_step
            websocket_step += 1
            if websocket_step == 1:
                return {"type": "websocket.connect"}
            return {"type": "websocket.disconnect", "code": 1000}

        async def send(message: dict[str, Any]) -> None:
            websocket_messages.append(message)

        asyncio.run(
            application(
                {
                    "type": "websocket",
                    "asgi": {"version": "3.0", "spec_version": "2.3"},
                    "http_version": "1.1",
                    "scheme": "ws",
                    "path": "/ws/main",
                    "raw_path": b"/ws/main",
                    "query_string": b"",
                    "root_path": "",
                    "headers": [(b"host", b"testserver")],
                    "server": ("testserver", 80),
                    "client": ("127.0.0.1", 50001),
                    "subprotocols": [],
                    "state": {},
                },
                receive,
                send,
            )
        )

        self.assertEqual(manager.active_client_count("main"), 0)
        self.assertEqual(controller.start_calls, 0)
        self.assertEqual(runtime.start_calls, 0)
        self.assertTrue(
            any(
                message.get("type") == "websocket.send"
                and json.loads(message.get("text", "{}"))
                == {
                    "type": "engine_state",
                    "state": "idle",
                    "reason": "realtime_disabled",
                }
                for message in websocket_messages
            )
        )

        websocket_messages.clear()
        websocket_step = 0
        application.state.realtime_enabled = True
        asyncio.run(
            application(
                {
                    "type": "websocket",
                    "asgi": {"version": "3.0", "spec_version": "2.3"},
                    "http_version": "1.1",
                    "scheme": "ws",
                    "path": "/ws/main",
                    "raw_path": b"/ws/main",
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
        self.assertEqual(controller.start_calls, 1)
        self.assertEqual(runtime.start_calls, 1)
        self.assertEqual(manager.active_client_count("main"), 0)

    def test_status_cannot_reenable_realtime_while_hide_is_in_flight(self) -> None:
        with patch("app.main.AudioRuntime", FakeAudioRuntime):
            application = create_app(self.root, enable_audio_capture=True)

        manager = application.state.session_manager
        runtime = FakeAudioRuntime.instances[0]
        controller = FakeController()
        manager.register_controller("main", controller)
        manager.paused = False
        application.state.overlay_gate_active = True
        application.state.realtime_enabled = True
        application.state.overlay_visible = True
        application.state.overlay_control_url = "http://127.0.0.1:43210"
        runtime.running = True
        manager.add_client("main", FakeClient())

        hide_started = threading.Event()
        hide_release = threading.Event()
        hide_finished = False

        def control_request(
            _control_url: str,
            action: str,
            *,
            method: str = "POST",
            timeout: float = 0.75,
        ) -> bool:
            del method, timeout
            if action == "hide":
                hide_started.set()
                hide_release.wait(timeout=1.0)
                nonlocal hide_finished
                hide_finished = True
            return True

        def control_health(
            _control_url: str,
            *,
            timeout: float = 0.35,
        ) -> dict[str, Any]:
            del timeout
            return {"ok": True, "visible": not hide_finished}

        async def run_race() -> tuple[AsgiResponse, AsgiResponse]:
            hide_task = asyncio.create_task(
                asgi_http_request(
                    application,
                    "/api/overlay/hide",
                    method="POST",
                    payload={},
                )
            )
            self.assertTrue(
                await asyncio.to_thread(hide_started.wait, 1.0),
                "hide did not reach the native control request",
            )
            status_task = asyncio.create_task(
                asgi_http_request(application, "/api/overlay/status")
            )
            await asyncio.sleep(0.05)
            self.assertFalse(
                status_task.done(),
                "status bypassed the overlay transition lock during hide",
            )
            hide_release.set()
            return await asyncio.gather(hide_task, status_task)

        try:
            with (
                patch("app.main._overlay_control_request", side_effect=control_request),
                patch("app.main._overlay_control_health", side_effect=control_health),
            ):
                hidden, status = asyncio.run(run_race())
        finally:
            hide_release.set()

        self.assertEqual(hidden.status, 200)
        self.assertEqual(status.status, 200)
        self.assertFalse(status.json()["realtime_enabled"])
        self.assertTrue(manager.paused)
        self.assertEqual(manager.active_client_count("main"), 0)
        self.assertEqual(controller.stop_calls, 1)
        self.assertEqual(runtime.stop_calls, 1)

    def test_hide_reports_stop_failure_instead_of_false_success(self) -> None:
        with patch("app.main.AudioRuntime", FakeAudioRuntime):
            application = create_app(self.root, enable_audio_capture=True)

        manager = application.state.session_manager
        runtime = FakeAudioRuntime.instances[0]
        controller = FailingController()
        manager.register_controller("main", controller)
        manager.paused = False
        application.state.overlay_gate_active = True
        application.state.realtime_enabled = True
        application.state.overlay_visible = True
        application.state.overlay_control_url = "http://127.0.0.1:43210"
        runtime.running = True
        manager.add_client("main", FakeClient())

        with patch("app.main._overlay_control_request", return_value=True):
            response = asyncio.run(
                asgi_http_request(
                    application,
                    "/api/overlay/hide",
                    method="POST",
                    payload={},
                )
            )

        payload = response.json()
        self.assertEqual(response.status, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["action"], "stop_failed")
        self.assertIn("controller:RuntimeError", payload["stop_error"])
        self.assertTrue(manager.paused)
        self.assertFalse(application.state.realtime_enabled)
        self.assertEqual(runtime.stop_calls, 1)

    def test_launch_control_failure_stops_existing_main_flow(self) -> None:
        with patch("app.main.AudioRuntime", FakeAudioRuntime):
            application = create_app(self.root, enable_audio_capture=True)

        manager = application.state.session_manager
        runtime = FakeAudioRuntime.instances[0]
        controller = FakeController()
        manager.register_controller("main", controller)
        manager.paused = False
        application.state.overlay_gate_active = True
        application.state.realtime_enabled = True
        application.state.overlay_visible = True
        application.state.overlay_control_url = "http://127.0.0.1:43210"
        runtime.running = True
        manager.add_client("main", FakeClient())

        with patch("app.main._overlay_control_request", return_value=False):
            response = asyncio.run(
                asgi_http_request(
                    application,
                    "/api/overlay/launch",
                    method="POST",
                    payload={},
                )
            )

        payload = response.json()
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["action"], "browser_fallback")
        self.assertFalse(application.state.realtime_enabled)
        self.assertFalse(application.state.overlay_visible)
        self.assertTrue(manager.paused)
        self.assertEqual(manager.active_client_count("main"), 0)
        self.assertGreaterEqual(controller.stop_calls, 1)
        self.assertGreaterEqual(runtime.stop_calls, 1)


if __name__ == "__main__":
    unittest.main()
