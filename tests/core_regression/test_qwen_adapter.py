from __future__ import annotations

import asyncio
import json
import queue
import ssl
import threading
import time
import unittest
import websocket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.engines.base import EngineState, RealtimeTranslatorEngine
from app.engines.qwen_realtime import (
    QwenRealtimeAdapter,
    classify_engine_error,
    resolve_ws_url,
)
from app.engines.registry import EngineRegistry
from app.session_manager import MAX_OVERLAY_WINDOWS, SessionPool

ROOT = Path(__file__).resolve().parents[2]


def wait_for(predicate: Any, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not satisfied before timeout")


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


class ControlFrameSocket(FakeSocket):
    def __init__(self) -> None:
        super().__init__()
        self.frames: queue.Queue[tuple[int, Any]] = queue.Queue()

    def push_control(self, opcode: int, data: Any = b"") -> None:
        self.frames.put((opcode, data))

    def recv_data_frame(self, control_frame: bool = False) -> tuple[int, Any]:
        self.assert_control = control_frame
        opcode, data = self.frames.get()
        if not data and opcode != websocket.ABNF.OPCODE_CLOSE:
            raise ConnectionError("closed")
        return opcode, SimpleNamespace(data=data)


class FakeEngine(RealtimeTranslatorEngine):
    def __init__(
        self,
        *,
        window_id: str,
        channel_id: str,
        source_lang: str,
        target_lang: str,
        **_kwargs: object,
    ) -> None:
        self.window_id = window_id
        self.channel_id = channel_id
        self.source_lang = source_lang
        self.target_lang = target_lang
        self._state = EngineState.IDLE
        self._session_update_count = 0
        self.frames: list[bytes] = []
        self.closed = False

    @property
    def current_state(self) -> EngineState:
        return self._state

    @property
    def session_update_count(self) -> int:
        return self._session_update_count

    def connect(self) -> bool:
        self._state = EngineState.READY
        self._session_update_count = 1
        return True

    def close(self) -> None:
        self.closed = True
        self._state = EngineState.CLOSED

    def live_update_session(
        self,
        source_lang: str,
        target_lang: str,
        corpus_phrases: dict[str, str] | list[tuple[str, str]] | None,
        hotwords: list[str] | None = None,
    ) -> bool:
        self.source_lang = source_lang
        self.target_lang = target_lang
        return True

    def update_api_key(self, api_key: str, workspace_id: str | None = None) -> None:
        return None

    def send_audio_chunk(self, pcm_bytes: bytes) -> bool:
        if self._state != EngineState.READY:
            return False
        self.frames.append(pcm_bytes)
        return True

    def commit_turn(self) -> bool:
        return self._state == EngineState.READY


class FakeClient:
    pass


class QwenRoutingTests(unittest.TestCase):
    def test_1007_is_rate_limited_quota_state(self) -> None:
        self.assertEqual(
            classify_engine_error("1007 Requests rate limit exceeded"),
            EngineState.QUOTA_ERROR,
        )

    def test_ordinary_key_never_uses_private_maas_url(self) -> None:
        resolved = resolve_ws_url(
            "wss://workspace-1.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
            "sk-test1234567890",
        )
        self.assertIn("dashscope.aliyuncs.com", resolved)
        self.assertNotIn("maas.aliyuncs.com", resolved)
        self.assertIn("model=qwen3.8-livetranslate-flash-realtime", resolved)

    def test_private_key_requires_workspace_for_template(self) -> None:
        with self.assertRaisesRegex(ValueError, "workspace_id_required"):
            resolve_ws_url(
                "wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
                "sk-ws-private123",
            )
        resolved = resolve_ws_url(
            "wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
            "sk-ws-private123",
            workspace_id="space-123",
        )
        self.assertIn("space-123.cn-beijing.maas.aliyuncs.com", resolved)

    def test_plaintext_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "realtime_url_must_use_wss"):
            resolve_ws_url("ws://127.0.0.1:9000/realtime", "sk-test1234567890")


class QwenAdapterTests(unittest.TestCase):
    def test_single_tls_handshake_target_language_and_corpus(self) -> None:
        socket = FakeSocket()
        calls: list[tuple[str, dict[str, Any]]] = []

        def connector(url: str, **kwargs: Any) -> FakeSocket:
            calls.append((url, kwargs))
            return socket

        engine = QwenRealtimeAdapter(
            api_key="sk-test1234567890abcd",
            source_lang="auto",
            target_lang="fr",
            ws_url="wss://misconfigured.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
            corpus_phrases=[("黄仁勋", "Jensen Huang")],
            window_id="win-1",
            channel_id="win-1",
            connector=connector,
            session_finish_drain=0.0,
        )
        self.assertIn("dashscope.aliyuncs.com", calls_url := engine.ws_url)
        self.assertNotIn("misconfigured", calls_url)
        self.assertEqual(engine.ssl_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(engine.connect())
        wait_for(lambda: engine.session_update_count == 1)

        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][1]["sslopt"]["context"], engine.ssl_context)
        self.assertEqual(calls[0][1]["sslopt"]["cert_reqs"], ssl.CERT_REQUIRED)
        self.assertNotIn("workspace", json.dumps(calls[0][1]["header"]).casefold())

        update = next(message for message in socket.sent if message["type"] == "session.update")
        self.assertEqual(update["session"]["output_modalities"], ["text"])
        self.assertEqual(update["session"]["translation"]["language"], "fr")
        self.assertEqual(
            update["session"]["translation"]["corpus"]["phrases"],
            {"黄仁勋": "Jensen Huang"},
        )
        self.assertNotIn("turn_detection", update["session"])
        self.assertNotIn("source_language", update["session"])
        self.assertNotIn("usage", json.dumps(update))

        socket.push({"type": "session.updated"})
        wait_for(lambda: engine.current_state == EngineState.READY)
        self.assertTrue(engine.send_audio_chunk(b"\x00" * 1_600))
        wait_for(lambda: any(item["type"] == "input_audio_buffer.append" for item in socket.sent))
        self.assertEqual(engine.session_update_count, 1)
        engine.close()
        self.assertEqual(socket.sent[-1]["type"], "session.finish")

    def test_empty_corpus_produces_minimal_105_byte_session_update(self) -> None:
        socket = FakeSocket()
        engine = QwenRealtimeAdapter(
            api_key="sk-test1234567890abcd",
            target_lang="en",
            corpus_phrases={},
            connector=lambda *_args, **_kwargs: socket,
            session_finish_drain=0.0,
        )
        self.assertTrue(engine.connect())
        wait_for(lambda: engine.session_update_count == 1)
        update = next(item for item in socket.sent if item["type"] == "session.update")
        self.assertNotIn("corpus", update["session"]["translation"])
        self.assertNotIn("turn_detection", update["session"])
        self.assertEqual(len(json.dumps(update).encode("utf-8")), 105)
        engine.close()

    def test_400_suspends_corpus_without_resetting_backoff_on_ready(self) -> None:
        sockets = [FakeSocket(), FakeSocket()]
        connector_calls = 0

        def connector(_url: str, **_kwargs: Any) -> FakeSocket:
            nonlocal connector_calls
            socket = sockets[connector_calls]
            connector_calls += 1
            return socket

        engine = QwenRealtimeAdapter(
            api_key="sk-test1234567890abcd",
            target_lang="en",
            corpus_phrases={"黄仁勋": "Jensen Huang"},
            connector=connector,
            session_finish_drain=0.0,
            enable_health_check=False,
        )
        self.assertTrue(engine.connect())
        wait_for(lambda: engine.session_update_count == 1)
        first_update = next(
            item for item in sockets[0].sent if item["type"] == "session.update"
        )
        self.assertIn("corpus", first_update["session"]["translation"])

        sockets[0].push({"type": "session.updated"})
        wait_for(lambda: engine.current_state == EngineState.READY)
        sockets[0].push(
            {
                "type": "error",
                "error": {
                    "code": "<400>",
                    "message": (
                        "InternalError.Algo.InvalidParameter: "
                        "Range of input length should be [1, 30720]"
                    ),
                },
            }
        )
        wait_for(lambda: connector_calls == 2, timeout=4)
        self.assertTrue(engine.corpus_suspended)
        self.assertGreaterEqual(engine.reconnect_attempt, 1)
        self.assertIn("<400>", engine.current_reason)

        second_update = next(
            item for item in sockets[1].sent if item["type"] == "session.update"
        )
        self.assertNotIn("corpus", second_update["session"]["translation"])
        self.assertNotIn("turn_detection", second_update["session"])
        sockets[1].push({"type": "session.updated"})
        wait_for(lambda: engine.current_state == EngineState.READY)
        self.assertGreaterEqual(engine.reconnect_attempt, 1)
        self.assertIn("corpus_suspended", engine.current_reason)
        engine.close()

    def test_session_updated_never_immediately_clears_reconnect_attempt(self) -> None:
        engine = QwenRealtimeAdapter(
            api_key="sk-test1234567890abcd",
            target_lang="zh",
            enable_health_check=False,
        )
        with engine._state_lock:
            engine._reconnect_attempt = 3
        engine.handle_message({"type": "session.updated"})
        self.assertEqual(engine.reconnect_attempt, 3)
        engine.close()

    def test_pong_control_frame_marks_connection_healthy(self) -> None:
        socket = ControlFrameSocket()
        engine = QwenRealtimeAdapter(
            api_key="sk-test1234567890abcd",
            target_lang="zh",
            enable_health_check=False,
            recovery_continuity=0.0,
        )
        engine._running = True
        engine._socket = socket
        engine._reconnect_attempt = 4
        socket.push_control(
            websocket.ABNF.OPCODE_TEXT,
            json.dumps({"type": "session.updated"}).encode("utf-8"),
        )
        socket.push_control(websocket.ABNF.OPCODE_PONG, b"ok")
        socket.push_control(websocket.ABNF.OPCODE_CLOSE)

        engine._read_until_disconnect()

        self.assertEqual(engine.reconnect_attempt, 0)
        self.assertEqual(engine.current_state, EngineState.READY)
        self.assertTrue(engine._last_health_at > 0)
        engine.close()

    def test_response_usage_is_ignored_and_callback_errors_are_isolated(self) -> None:
        events: list[dict[str, Any]] = []
        structured: list[Any] = []

        def broken_state_callback(_event: Any) -> None:
            raise RuntimeError("callback must not leak")

        engine = QwenRealtimeAdapter(
            api_key="sk-test1234567890abcd",
            target_lang="zh",
            window_id="win-1",
            channel_id="win-1",
            event_callback=events.append,
            on_state_change=broken_state_callback,
            on_delta=structured.append,
        )
        engine.handle_message({"type": "session.updated"})
        engine.handle_message(
            {
                "type": "response.text.delta",
                "delta": "hello",
                "usage": {"total_tokens": 99},
            }
        )
        engine.handle_message({"type": "response.done", "response": {"usage": {"total_tokens": 99}}})
        self.assertTrue(events)
        self.assertTrue(structured)
        for event in events:
            self.assertNotIn("usage", event)
            self.assertNotIn("total_tokens", event)
        engine.close()


class SessionPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = EngineRegistry(ROOT / "config" / "provider_catalog.json")
        self.created: list[FakeEngine] = []

        def factory(**kwargs: Any) -> FakeEngine:
            engine = FakeEngine(**kwargs)
            self.created.append(engine)
            return engine

        self.registry.register("qwen", factory)

    def test_single_window_limit_and_immediate_close(self) -> None:
        pool = SessionPool(self.registry, jitter_sec=0.0)
        self.assertEqual(MAX_OVERLAY_WINDOWS, 1)
        self.assertEqual(SessionPool.MAX_WINDOWS, 1)
        pool.register_window(
            "main",
            audio_source="microphone",
            source_lang="en",
            target_lang="zh",
        )
        with self.assertRaisesRegex(ValueError, "window_limit_reached"):
            pool.register_window(
                "win-2",
                audio_source="system",
                source_lang="zh",
                target_lang="fr",
            )
        pool.add_client("main", FakeClient())

        async def scenario() -> None:
            await pool.start_window("main")
            self.assertTrue(pool.route_audio_frame("main", b"one"))
            self.assertEqual(self.created[0].frames, [b"one"])
            await pool.close_window("main")
            self.assertTrue(self.created[0].closed)
            self.assertFalse(pool.route_audio_frame("main", b"late"))
            self.assertEqual(self.created[0].target_lang, "zh")

        asyncio.run(scenario())

    def test_invalid_switch_keeps_old_engine(self) -> None:
        pool = SessionPool(self.registry, jitter_sec=0.0)
        pool.register_window(
            "main",
            audio_source="microphone",
            source_lang="auto",
            target_lang="zh",
        )
        pool.add_client("main", FakeClient())

        async def scenario() -> None:
            await pool.start_window("main")
            old_engine = self.created[0]
            with self.assertRaises(ValueError):
                await pool.switch_engine(
                    "main",
                    provider="qwen",
                    source_lang="xx",
                    target_lang="yy",
                )
            self.assertFalse(old_engine.closed)
            self.assertIs(pool.slot("main").engine, old_engine)  # type: ignore[union-attr]

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
