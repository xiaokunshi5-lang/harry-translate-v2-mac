from __future__ import annotations

import asyncio
import json
import os
import queue
import ssl
import statistics
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.engines.qwen_realtime import (
    EngineState,
    QwenRealtimeEngine,
    classify_engine_error,
)
from app.main import create_app
from app.session_manager import SessionController, SessionManager

ROOT = Path(__file__).resolve().parents[2]


def wait_for(predicate: Any, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not satisfied before timeout")


class FakeEngine:
    def __init__(self, state: EngineState = EngineState.READY) -> None:
        self.current_state = state
        self.current_reason = ""
        self.session_update_count = 0
        self.sent_frames: list[bytes] = []
        self.closed = False

    def connect(self) -> bool:
        self.session_update_count += 1
        self.current_state = EngineState.READY
        return True

    def send_audio(self, pcm_bytes: bytes) -> bool:
        if self.current_state != EngineState.READY:
            return False
        self.sent_frames.append(pcm_bytes)
        return True

    def close(self) -> None:
        self.closed = True
        self.current_state = EngineState.CLOSED

    def state_event(self) -> dict[str, Any]:
        return {
            "type": "engine_state",
            "state": self.current_state.value,
            "reason": self.current_reason,
        }


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: queue.Queue[str] = queue.Queue()
        self.closed = threading.Event()

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def recv(self) -> str:
        message = self.incoming.get()
        if not message:
            raise ConnectionError("closed")
        return message

    def push(self, payload: dict[str, Any]) -> None:
        self.incoming.put(json.dumps(payload))

    def close(self) -> None:
        self.closed.set()
        self.incoming.put("")


class AsyncClient:
    def __init__(self, delay: float = 0.0, close_delay: float = 0.0) -> None:
        self.delay = delay
        self.close_delay = close_delay
        self.received: list[dict[str, Any]] = []
        self.closed = False
        self.receive_times: list[float] = []

    async def send_text(self, message: str) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.received.append(json.loads(message))
        self.receive_times.append(time.perf_counter())

    async def close(self) -> None:
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        self.closed = True


class SessionGatingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.needs_setup = False

        def needs_setup() -> bool:
            return self.needs_setup

        self.manager = SessionManager(needs_setup)
        self.engine_en = FakeEngine()
        self.engine_zh = FakeEngine()
        self.manager.register_engine("en", self.engine_en)
        self.manager.register_engine("zh", self.engine_zh)

    def test_zero_clients_send_zero_audio_frames(self) -> None:
        frame = b"\x00" * 1_600
        for _ in range(40):
            self.assertFalse(self.manager.route_audio_frame("en", frame))
            self.assertFalse(self.manager.route_audio_frame("zh", frame))
        self.assertEqual(self.engine_en.sent_frames, [])
        self.assertEqual(self.engine_zh.sent_frames, [])

    def test_only_channel_with_client_streams_audio(self) -> None:
        client = object()
        self.manager.add_client("en", client)
        frame = b"\x00" * 1_600
        for _ in range(40):
            self.assertTrue(self.manager.route_audio_frame("en", frame))
            self.assertFalse(self.manager.route_audio_frame("zh", frame))
        self.assertEqual(len(self.engine_en.sent_frames), 40)
        self.assertEqual(self.engine_zh.sent_frames, [])

        self.manager.disconnect_client("en", client)
        self.assertFalse(self.manager.route_audio_frame("en", frame))
        self.assertEqual(len(self.engine_en.sent_frames), 40)

    def test_missing_setup_or_pause_fails_closed(self) -> None:
        client = object()
        self.manager.add_client("en", client)

        self.needs_setup = True
        self.assertFalse(self.manager.should_stream_audio("en"))
        self.needs_setup = False
        self.manager.paused = True
        self.assertFalse(self.manager.should_stream_audio("en"))
        self.manager.paused = False
        self.assertTrue(self.manager.should_stream_audio("en"))

    def test_controller_creates_one_engine_only_after_client_connects(self) -> None:
        created: list[FakeEngine] = []

        def factory() -> FakeEngine:
            engine = FakeEngine()
            created.append(engine)
            return engine

        controller = SessionController("en", self.manager, factory)
        self.manager.register_controller("en", controller)
        self.manager.add_client("en", object())

        async def scenario() -> None:
            self.assertTrue(await controller.start())
            self.assertTrue(await controller.start())
            self.assertEqual(len(created), 1)
            self.assertEqual(controller.session_update_count, 1)
            await controller.stop()

        self.manager.disconnect_client("en", next(iter(self.manager.connected_clients["en"])))
        self.assertFalse(asyncio.run(controller.start()))
        self.assertEqual(created, [])

        self.manager.add_client("en", object())
        asyncio.run(scenario())
        self.assertTrue(created[0].closed)

    def test_activating_one_channel_closes_the_other_upstream(self) -> None:
        manager = SessionManager(lambda: False)
        engines: dict[str, FakeEngine] = {}

        def factory(channel: str) -> FakeEngine:
            engine = FakeEngine()
            engines[channel] = engine
            return engine

        for channel in ("en", "zh"):
            manager.register_controller(
                channel,
                SessionController(channel, manager, lambda name=channel: factory(name)),
            )
            manager.add_client(channel, object())

        async def scenario() -> None:
            await manager.activate_channel("en")
            await manager.controller("en").start()  # type: ignore[union-attr]
            self.assertEqual(engines["en"].current_state, EngineState.READY)
            await manager.activate_channel("zh")
            await manager.controller("zh").start()  # type: ignore[union-attr]
            self.assertTrue(engines["en"].closed)
            self.assertEqual(engines["en"].current_state, EngineState.CLOSED)
            self.assertEqual(manager.active_channel, "zh")
            self.assertEqual(engines["zh"].current_state, EngineState.READY)
            await manager.stop_all()

        asyncio.run(scenario())


class BroadcastIsolationTests(unittest.TestCase):
    def test_slow_client_is_removed_without_blocking_healthy_client(self) -> None:
        manager = SessionManager(lambda: False)
        healthy = AsyncClient()
        slow = AsyncClient(delay=2.0)
        manager.add_client("en", healthy)
        manager.add_client("en", slow)

        async def run() -> list[float]:
            latencies: list[float] = []
            for index in range(10):
                started = time.perf_counter()
                delivered = await manager.broadcast_to_channel(
                    "en",
                    {
                        "type": "subtitle",
                        "id": index,
                        "source": "source",
                        "target": "target",
                        "is_final": index % 2 == 0,
                    },
                )
                self.assertEqual(delivered, 1)
                latencies.append((healthy.receive_times[-1] - started) * 1_000)
            return latencies

        latencies_ms = asyncio.run(run())
        self.assertEqual(len(healthy.received), 10)
        self.assertEqual(healthy.received[-1]["id"], 9)
        self.assertTrue(slow.closed)
        self.assertEqual(manager.active_client_count("en"), 1)
        self.assertLessEqual(
            statistics.quantiles(latencies_ms, n=100, method="inclusive")[98],
            20,
        )

    def test_slow_close_is_bounded_after_send_timeout(self) -> None:
        manager = SessionManager(lambda: False)
        healthy = AsyncClient()
        slow = AsyncClient(delay=2.0, close_delay=2.0)
        manager.add_client("en", healthy)
        manager.add_client("en", slow)

        async def run() -> float:
            started = time.perf_counter()
            delivered = await manager.broadcast_to_channel(
                "en",
                {"type": "subtitle", "id": 1, "source": "s", "target": "t"},
            )
            self.assertEqual(delivered, 1)
            return time.perf_counter() - started

        elapsed = asyncio.run(run())
        self.assertLess(elapsed, 0.4)
        self.assertEqual(len(healthy.received), 1)

    def test_metering_fields_are_rejected_before_broadcast(self) -> None:
        manager = SessionManager(lambda: False)
        manager.add_client("en", AsyncClient())
        with self.assertRaises(ValueError):
            asyncio.run(
                manager.broadcast_to_channel(
                    "en",
                    {"type": "subtitle", "usage": {"total_tokens": 1}},
                )
            )

    def test_thread_events_are_broadcast_in_publication_order(self) -> None:
        manager = SessionManager(lambda: False)
        client = AsyncClient()
        manager.add_client("en", client)

        async def scenario() -> None:
            manager.bind_loop(asyncio.get_running_loop())
            for value in (1, 2, 3):
                manager.publish_from_thread(
                    "en",
                    {"type": "subtitle", "id": value, "source": "s", "target": "t"},
                )
            deadline = time.monotonic() + 3
            while len(client.received) < 3 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            self.assertEqual([message["id"] for message in client.received], [1, 2, 3])
            await manager.stop_all()

        asyncio.run(scenario())


class RealtimeEngineTests(unittest.TestCase):
    def test_tls_state_machine_and_single_session_update(self) -> None:
        socket = FakeSocket()
        connector_calls: list[dict[str, Any]] = []

        def connector(_url: str, **kwargs: Any) -> FakeSocket:
            connector_calls.append(kwargs)
            return socket

        engine = QwenRealtimeEngine(
            api_key="sk-test1234567890abcd",
            target_lang="en",
            connector=connector,
        )
        self.assertEqual(engine.ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(engine.ssl_context.check_hostname)

        self.assertTrue(engine.connect())
        wait_for(
            lambda: any(
                message.get("type") == "session.update" for message in socket.sent
            )
        )
        self.assertEqual(engine.session_update_count, 1)
        self.assertEqual(connector_calls[0]["sslopt"]["cert_reqs"], ssl.CERT_REQUIRED)
        self.assertIs(connector_calls[0]["sslopt"]["context"], engine.ssl_context)

        socket.push({"type": "session.updated"})
        wait_for(lambda: engine.current_state == EngineState.READY)
        self.assertTrue(engine.send_audio(b"\x00" * 1_600))
        wait_for(
            lambda: any(
                message.get("type") == "input_audio_buffer.append"
                for message in socket.sent
            )
        )
        self.assertEqual(engine.session_update_count, 1)
        engine.close()
        self.assertEqual(engine.current_state, EngineState.CLOSED)

    def test_plaintext_websocket_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            QwenRealtimeEngine(
                api_key="sk-test1234567890abcd",
                target_lang="en",
                ws_url="ws://127.0.0.1:9000/realtime",
            )

    def test_close_prevents_late_handshake_after_blocked_connect(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        socket = FakeSocket()

        def connector(_url: str, **_kwargs: Any) -> FakeSocket:
            entered.set()
            release.wait(timeout=3)
            return socket

        engine = QwenRealtimeEngine(
            api_key="sk-test1234567890abcd",
            target_lang="en",
            connector=connector,
            thread_join_timeout=0.05,
        )
        self.assertTrue(engine.connect())
        self.assertTrue(entered.wait(timeout=2))
        engine.close()
        self.assertEqual(engine.current_state, EngineState.CLOSED)
        self.assertEqual(engine.session_update_count, 0)
        release.set()
        wait_for(lambda: socket.closed.is_set())
        self.assertEqual(engine.session_update_count, 0)
        self.assertEqual(socket.sent, [])

    def test_auth_and_quota_errors_are_classified(self) -> None:
        self.assertEqual(
            classify_engine_error("Unauthorized", status_code=401),
            EngineState.AUTH_ERROR,
        )
        self.assertEqual(
            classify_engine_error("quota exceeded", status_code=429),
            EngineState.QUOTA_ERROR,
        )

    def test_auth_failure_stops_reconnect_loop(self) -> None:
        calls = 0

        def connector(_url: str, **_kwargs: Any) -> FakeSocket:
            nonlocal calls
            calls += 1
            raise PermissionError("401 Unauthorized")

        engine = QwenRealtimeEngine(
            api_key="sk-test1234567890abcd",
            target_lang="en",
            connector=connector,
        )
        self.assertTrue(engine.connect())
        wait_for(lambda: engine.current_state == EngineState.AUTH_ERROR)
        time.sleep(0.1)
        self.assertEqual(calls, 1)
        engine.close()

    def test_error_event_does_not_emit_metering_fields(self) -> None:
        events: list[dict[str, Any]] = []
        engine = QwenRealtimeEngine(
            api_key="sk-test1234567890abcd",
            target_lang="zh",
            event_callback=events.append,
        )
        engine.handle_message({"type": "session.updated"})
        engine.handle_message(
            {
                "type": "response.text.delta",
                "delta": "hello",
                "usage": {"total_tokens": 99},
            }
        )
        engine.handle_message({"type": "response.done"})

        self.assertTrue(events)
        for event in events:
            self.assertNotIn("usage", event)
            self.assertNotIn("total_tokens", event)


class StaticSafetyTests(unittest.TestCase):
    def test_ssl_is_required_and_retired_security_patterns_are_absent(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [
                *(ROOT / "app").rglob("*.py"),
                ROOT / "legacy" / "v1" / "switch_key.py",
            ]
        )
        self.assertNotIn("CERT_NONE", sources)
        self.assertNotIn("all_connected == 0", sources)
        self.assertIn("certifi.where()", sources)

    def test_frontend_has_no_stats_polling_or_fake_ready_state(self) -> None:
        frontend = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "frontend" / "src").rglob("*")
            if path.is_file() and path.suffix in {".ts", ".tsx", ".css"}
        )
        self.assertNotIn("fetchStats", frontend)
        self.assertNotIn("/api/stats", frontend)
        self.assertNotIn("total_tokens", frontend)
        self.assertIn('event.type === "engine_state"', frontend)
        self.assertIn("引擎已就绪", frontend)
        self.assertIn("未配置 API Key", frontend)
        self.assertIn("零连接不推流", frontend)
        self.assertIn("useChannel(selectedChannel", frontend)
        self.assertNotIn("channelEn", frontend)
        self.assertNotIn("channelZh", frontend)
        self.assertIn("单一上游会话", frontend)

    def test_stats_route_is_not_registered(self) -> None:
        application = create_app(enable_audio_capture=False)
        paths = {getattr(route, "path", "") for route in application.routes}
        self.assertNotIn("/api/stats", paths)
        self.assertNotIn("/api/stats/reset", paths)
        self.assertIn("/ws/{channel}", paths)
        self.assertIn("/api/session/switch", paths)

    def test_websocket_without_key_reports_setup_and_never_claims_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / ".env.example").write_text(
                "DASHSCOPE_API_KEY=sk-your-dashscope-api-key-here\n",
                encoding="utf-8",
            )
            application = create_app(root, enable_audio_capture=False)
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

            scope = {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "scheme": "ws",
                "path": "/ws/en",
                "raw_path": b"/ws/en",
                "query_string": b"",
                "root_path": "",
                "headers": [(b"host", b"testserver")],
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 50000),
                "subprotocols": [],
                "state": {},
            }
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
                asyncio.run(application(scope, receive, send))

        self.assertEqual(messages[0]["type"], "websocket.accept")
        state_messages = [
            json.loads(message["text"])
            for message in messages
            if message.get("type") == "websocket.send"
        ]
        self.assertEqual(state_messages[0]["type"], "engine_state")
        self.assertEqual(state_messages[0]["state"], "idle")
        self.assertEqual(state_messages[0]["reason"], "needs_setup")
        self.assertNotEqual(state_messages[0]["state"], "ready")
        self.assertEqual(application.state.session_manager.total_active_clients(), 0)

    def test_v1_key_switcher_still_imports_without_certifi(self) -> None:
        v1_python = ROOT / ".venv" / "bin" / "python"
        self.assertTrue(v1_python.is_file())
        result = subprocess.run(
            [str(v1_python), "-c", "import switch_key"],
            cwd=ROOT / "legacy" / "v1",
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
