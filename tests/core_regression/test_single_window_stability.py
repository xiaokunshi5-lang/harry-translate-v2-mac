from __future__ import annotations

import asyncio
import json
import os
import queue
import tempfile
import threading
import time
import unittest
import websocket
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.audio.device_probe import SystemAudioProbe
from app.config_store import ConfigStore
from app.engines.base import EngineState
from app.engines.qwen_realtime import (
    CONNECT_TIMEOUT_SEC,
    HEALTH_CHECK_INTERVAL_SEC,
    HEALTH_PROBE_TIMEOUT_SEC,
    SEND_TIMEOUT_SEC,
    SESSION_FINISH_DRAIN_SEC,
    SETUP_TIMEOUT_SEC,
    QwenRealtimeAdapter,
)
from app.main import create_app
from app.session_manager import MAX_OVERLAY_WINDOWS, sanitize_target_for_lang
from tests.unit.test_bootstrap_config import VALID_KEY, asgi_request


def _wait_until(predicate: Any, *, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition timed out")


class _MockSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: queue.Queue[str] = queue.Queue()
        self.closed = threading.Event()
        self.pings = 0
        self.timeout_history: list[float | None] = []

    def settimeout(self, timeout: float | None) -> None:
        self.timeout_history.append(timeout)

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def ping(self) -> None:
        self.pings += 1

    def recv(self) -> str:
        item = self.incoming.get()
        if not item:
            raise ConnectionError("closed")
        return item

    def push(self, payload: dict[str, Any]) -> None:
        self.incoming.put(json.dumps(payload))

    def close(self) -> None:
        self.closed.set()
        self.incoming.put("")


class _MockControlSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.frames: queue.Queue[tuple[int, Any]] = queue.Queue()
        self.pings = 0
        self.closed = threading.Event()

    def settimeout(self, _timeout: float | None) -> None:
        return None

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def ping(self) -> None:
        self.pings += 1

    def push_text(self, payload: dict[str, Any]) -> None:
        self.frames.put((websocket.ABNF.OPCODE_TEXT, json.dumps(payload).encode("utf-8")))

    def push_pong(self) -> None:
        self.frames.put((websocket.ABNF.OPCODE_PONG, b"ok"))

    def recv_data_frame(self, control_frame: bool = False) -> tuple[int, Any]:
        assert control_frame
        opcode, data = self.frames.get()
        if not data and opcode != websocket.ABNF.OPCODE_CLOSE:
            raise ConnectionError("closed")
        return opcode, SimpleNamespace(data=data)

    def close(self) -> None:
        self.closed.set()
        self.frames.put((websocket.ABNF.OPCODE_CLOSE, b""))


class _WsRecordingClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.closed = False

    async def send_text(self, message: str) -> None:
        self.messages.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True


class _ControllableEngine:
    def __init__(self, target_lang: str) -> None:
        self.target_lang = target_lang
        self.source_lang = "auto"
        self.current_state = EngineState.READY
        self.session_update_count = 1
        self.sent_frames: list[bytes] = []
        self.live_updates: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False
        self.finish_sent = False

    def connect(self) -> bool:
        self.current_state = EngineState.READY
        return True

    def send_audio(self, pcm_bytes: bytes) -> bool:
        if self.current_state != EngineState.READY:
            return False
        self.sent_frames.append(pcm_bytes)
        return True

    def live_update_session(
        self,
        source_lang: str,
        target_lang: str,
        phrases: dict[str, str],
    ) -> bool:
        self.live_updates.append((source_lang, target_lang, dict(phrases)))
        return True

    def close(self) -> None:
        self.finish_sent = True
        self.closed = True
        self.current_state = EngineState.CLOSED

    def state_event(self) -> dict[str, Any]:
        return {
            "type": "engine_state",
            "state": self.current_state.value,
            "health_status": "connected" if self.current_state == EngineState.READY else "closed",
            "reason": "",
        }


class SingleWindowStabilityTests(unittest.TestCase):
    def test_ws_ping_pong_recovery_continuity_and_400_fallback(self) -> None:
        self.assertEqual(CONNECT_TIMEOUT_SEC, 15.0)
        self.assertEqual(SETUP_TIMEOUT_SEC, 5.0)
        self.assertEqual(SEND_TIMEOUT_SEC, 5.0)
        self.assertEqual(HEALTH_CHECK_INTERVAL_SEC, 10.0)
        self.assertEqual(HEALTH_PROBE_TIMEOUT_SEC, 4.0)
        self.assertEqual(SESSION_FINISH_DRAIN_SEC, 0.25)

        # 1) <400> InvalidParameter triggers corpus suspension & 105 B minimal handshake
        sockets_400 = [_MockSocket(), _MockSocket()]
        idx_400 = 0

        def connect_400(_url: str, **_kwargs: Any) -> _MockSocket:
            nonlocal idx_400
            sock = sockets_400[idx_400]
            idx_400 += 1
            return sock

        engine = QwenRealtimeAdapter(
            api_key=VALID_KEY,
            target_lang="en",
            corpus_phrases={"黄仁勋": "Jensen Huang"},
            connector=connect_400,
            session_finish_drain=0.0,
            enable_health_check=False,
        )
        self.assertTrue(engine.connect())
        _wait_until(lambda: len(sockets_400[0].sent) >= 1)
        self.assertEqual(sockets_400[0].timeout_history[0], 5.0)

        sockets_400[0].push({"type": "session.updated"})
        _wait_until(lambda: engine.current_state == EngineState.READY)
        self.assertEqual(sockets_400[0].timeout_history[-1], 5.0)
        self.assertEqual(engine.state_event()["health_status"], "connected")

        sockets_400[0].push(
            {
                "type": "error",
                "error": {
                    "code": "<400>",
                    "message": "InvalidParameter: Range of input length [1, 30720]",
                },
            }
        )
        _wait_until(lambda: idx_400 == 2)
        self.assertIn("<400>", engine.current_reason)
        self.assertNotEqual(engine.current_reason, "upstream_disconnected")
        self.assertTrue(engine.corpus_suspended)

        minimal_update = next(
            msg for msg in sockets_400[1].sent if msg["type"] == "session.update"
        )
        self.assertEqual(len(json.dumps(minimal_update).encode("utf-8")), 105)
        self.assertNotIn("corpus", minimal_update["session"]["translation"])
        self.assertNotIn("turn_detection", minimal_update["session"])

        sockets_400[1].push({"type": "session.updated"})
        _wait_until(lambda: engine.current_state == EngineState.READY)
        self.assertEqual(engine.state_event()["health_status"], "degraded_minimal_corpus")
        self.assertGreaterEqual(engine.reconnect_attempt, 1)
        engine.close()

        # 2) Ping probe timeout triggers controlled reconnect
        ping_sockets = [_MockControlSocket(), _MockControlSocket()]
        ping_idx = 0

        def connect_ping(_url: str, **_kwargs: Any) -> _MockControlSocket:
            nonlocal ping_idx
            sock = ping_sockets[min(ping_idx, len(ping_sockets) - 1)]
            ping_idx += 1
            return sock

        ping_engine = QwenRealtimeAdapter(
            api_key=VALID_KEY,
            target_lang="zh",
            connector=connect_ping,
            health_check_interval=0.05,
            health_probe_timeout=0.05,
            reconnect_base=0.02,
            recovery_continuity=10.0,
            session_finish_drain=0.0,
        )
        self.assertTrue(ping_engine.connect())
        ping_sockets[0].push_text({"type": "session.updated"})
        _wait_until(lambda: ping_idx >= 2, timeout=4.0)
        self.assertGreaterEqual(ping_sockets[0].pings, 1)
        self.assertGreaterEqual(ping_engine.reconnect_attempt, 1)
        ping_engine.close()

        # 3) 1007 rate limit triggers >= 1s backoff; session.updated never resets attempt;
        #    only sustained health > recovery_continuity resets reconnect_attempt to 0.
        rl_sockets = [_MockControlSocket(), _MockControlSocket()]
        rl_idx = 0

        def connect_rl(_url: str, **_kwargs: Any) -> _MockControlSocket:
            nonlocal rl_idx
            sock = rl_sockets[min(rl_idx, len(rl_sockets) - 1)]
            rl_idx += 1
            return sock

        rl_engine = QwenRealtimeAdapter(
            api_key=VALID_KEY,
            target_lang="zh",
            connector=connect_rl,
            reconnect_base=0.5,
            health_check_interval=0.05,
            health_probe_timeout=2.0,
            recovery_continuity=0.15,
            session_finish_drain=0.0,
        )
        self.assertTrue(rl_engine.connect())
        rl_sockets[0].push_text({"type": "session.updated"})
        _wait_until(lambda: rl_engine.current_state == EngineState.READY)
        rl_sockets[0].push_text(
            {"type": "error", "error": {"code": "1007", "message": "rate limit exceeded"}}
        )
        _wait_until(lambda: rl_engine.current_state == EngineState.QUOTA_ERROR)
        self.assertGreaterEqual(rl_engine.state_event()["backoff_seconds"], 1.0)
        self.assertEqual(rl_engine.state_event()["health_status"], "reconnecting(backoff)")
        _wait_until(lambda: rl_idx == 2, timeout=4.0)

        rl_sockets[1].push_text({"type": "session.updated"})
        _wait_until(lambda: rl_engine.current_state == EngineState.READY)
        # Immediately after session.updated, attempt MUST remain >= 1.
        self.assertGreaterEqual(rl_engine.reconnect_attempt, 1)

        time.sleep(0.2)
        rl_sockets[1].push_pong()
        _wait_until(lambda: rl_engine.reconnect_attempt == 0, timeout=3.0)
        rl_engine.close()

        # 4) close() sends session.finish, drains <= 250ms on session.finished, and blocks late audio
        drain_socket = _MockSocket()
        drain_engine = QwenRealtimeAdapter(
            api_key=VALID_KEY,
            target_lang="zh",
            connector=lambda *_a, **_kw: drain_socket,
            session_finish_drain=0.25,
            enable_health_check=False,
        )
        self.assertTrue(drain_engine.connect())
        drain_socket.push({"type": "session.updated"})
        _wait_until(lambda: drain_engine.current_state == EngineState.READY)
        threading.Timer(
            0.03,
            lambda: drain_socket.push({"type": "session.finished"}),
        ).start()
        close_started = time.perf_counter()
        drain_engine.close()
        close_elapsed_ms = (time.perf_counter() - close_started) * 1_000
        self.assertLessEqual(close_elapsed_ms, 250.0)
        self.assertEqual(drain_socket.sent[-1]["type"], "session.finish")
        self.assertFalse(drain_engine.send_audio_chunk(b"\x00" * 1_600))

    def test_fast_language_switch_purity_and_window_close_drain(self) -> None:
        self.assertEqual(MAX_OVERLAY_WINDOWS, 1)

        # Purity filter checks across languages
        self.assertEqual(sanitize_target_for_lang("我们开始", "我们开始", "fr"), "")
        self.assertEqual(
            sanitize_target_for_lang("Commençons dès maintenant", "我们开始", "fr"),
            "Commençons dès maintenant",
        )
        self.assertEqual(sanitize_target_for_lang("España üöß", "西班牙", "es"), "España üöß")
        self.assertEqual(sanitize_target_for_lang("hello world", "hello world", "zh"), "")
        self.assertEqual(sanitize_target_for_lang("Qwen 3.8", "Qwen 3.8", "zh"), "Qwen 3.8")
        self.assertEqual(
            sanitize_target_for_lang("こんにちは", "你好", "ja"), "こんにちは"
        )
        self.assertEqual(
            sanitize_target_for_lang("안녕하세요", "你好", "ko"), "안녕하세요"
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={VALID_KEY}\nWORKSPACE_ID=\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": VALID_KEY}):
                Store = ConfigStore(root)
                Store.update_settings({"source_lang": "zh", "target_lang": "en"})
                app = create_app(root, enable_audio_capture=False)
                manager = app.state.session_manager

                created_engines: list[_ControllableEngine] = []
                app.state.engine_registry.register(
                    "qwen",
                    lambda **kwargs: (
                        created_engines.append(
                            _ControllableEngine(str(kwargs.get("target_lang", "zh")))
                        )
                        or created_engines[-1]
                    ),
                )

                client = _WsRecordingClient()

                async def run_flow() -> None:
                    manager.bind_loop(asyncio.get_running_loop())
                    # /ws/subtitles maps to main channel cleanly
                    await manager.activate_channel("main")
                    manager.add_client("main", client)
                    controller = manager.controller("main")
                    assert controller is not None
                    self.assertTrue(await controller.start())
                    first_engine = created_engines[0]
                    old_gen = manager.channel_generation("main")

                    # QuickTermBar session add updates active single session within wire budget
                    add_resp = await asgi_request(
                        app,
                        "/api/glossary/session/add",
                        method="POST",
                        payload={
                            "source": "人工智能",
                            "target": "Intelligence artificielle",
                            "bucket": "terms_trans",
                            "lang_key": "zh->fr",
                        },
                    )
                    self.assertEqual(add_resp.status, 200)
                    quota = json.loads(add_resp.body)["quota"]
                    self.assertLessEqual(quota["wire_bytes"], 6144)

                    # Switch target language zh->en to zh->fr and audio_input=system (missing loopback)
                    started = time.perf_counter()
                    with patch(
                        "app.main.probe_system_audio",
                        return_value=SystemAudioProbe(
                            backend="sounddevice",
                            loopback_available=False,
                            loopback_devices=[],
                            input_devices=[],
                            status="loopback_missing",
                            hint="Install BlackHole 2ch",
                        ),
                    ):
                        switch_resp = await asgi_request(
                            app,
                            "/api/session/switch",
                            method="POST",
                            payload={
                                "source_lang": "zh",
                                "target_lang": "fr",
                                "audio_input": "system",
                            },
                        )
                    elapsed_ms = (time.perf_counter() - started) * 1_000
                    self.assertEqual(switch_resp.status, 200)
                    switch_payload = json.loads(switch_resp.body)
                    self.assertEqual(switch_payload["channel_b_status"], "loopback_missing")
                    self.assertTrue(switch_payload["system_audio_idle"])
                    self.assertEqual(switch_payload["user_settings"]["audio_input"], "microphone")
                    self.assertTrue(first_engine.finish_sent)
                    self.assertTrue(first_engine.closed)
                    self.assertLess(elapsed_ms, 250.0)
                    self.assertEqual(len(created_engines), 2)
                    self.assertEqual(created_engines[1].target_lang, "fr")

                    # Stale event from old generation must never reach client
                    manager.publish_from_thread(
                        "main",
                        {
                            "type": "subtitle",
                            "id": 99,
                            "source_text": "旧句子",
                            "target_text": "Old English Leak",
                            "is_final": True,
                        },
                        generation=old_gen,
                    )
                    await asyncio.sleep(0.05)
                    self.assertFalse(
                        any(
                            msg.get("target_text") == "Old English Leak"
                            for msg in client.messages
                        )
                    )

                    # Transient CJK echo is suppressed, valid French with accents passes 100%
                    new_gen = manager.channel_generation("main")
                    manager.publish_from_thread(
                        "main",
                        {
                            "type": "subtitle",
                            "id": 100,
                            "source_text": "我们开始",
                            "target_text": "我们开始",
                            "is_final": False,
                        },
                        generation=new_gen,
                    )
                    manager.publish_from_thread(
                        "main",
                        {
                            "type": "subtitle",
                            "id": 101,
                            "source_text": "我们开始",
                            "target_text": "Commençons dès maintenant",
                            "is_final": True,
                        },
                        generation=new_gen,
                    )
                    deadline = time.monotonic() + 3.0
                    while not any(
                        msg.get("target_text") == "Commençons dès maintenant"
                        for msg in client.messages
                    ):
                        if time.monotonic() >= deadline:
                            raise AssertionError("french subtitle did not arrive before timeout")
                        await asyncio.sleep(0.01)
                    echo_msg = next(
                        msg for msg in client.messages if msg.get("id") == 100
                    )
                    self.assertEqual(echo_msg["target_text"], "")

                    # Closing overlay stops upstream and drops subsequent audio frames
                    manager.disconnect_client("main", client)
                    await controller.stop_if_idle()
                    await manager.deactivate_channel_if_idle("main")
                    self.assertTrue(created_engines[1].finish_sent)
                    self.assertEqual(manager.active_client_count("main"), 0)
                    self.assertFalse(manager.route_audio_frame("main", b"\x00" * 1_600))
                    self.assertEqual(created_engines[1].sent_frames, [])
                    await manager.stop_all()

                asyncio.run(run_flow())

                # Verify real ASGI /ws/subtitles route binds to the single "main" channel
                ws_messages: list[dict[str, Any]] = []
                ws_step = 0

                async def ws_receive() -> dict[str, Any]:
                    nonlocal ws_step
                    ws_step += 1
                    if ws_step == 1:
                        return {"type": "websocket.connect"}
                    return {"type": "websocket.disconnect", "code": 1000}

                async def ws_send(message: dict[str, Any]) -> None:
                    ws_messages.append(message)

                scope = {
                    "type": "websocket",
                    "asgi": {"version": "3.0", "spec_version": "2.3"},
                    "http_version": "1.1",
                    "scheme": "ws",
                    "path": "/ws/subtitles",
                    "raw_path": b"/ws/subtitles",
                    "query_string": b"",
                    "root_path": "",
                    "headers": [(b"host", b"testserver")],
                    "server": ("testserver", 80),
                    "client": ("127.0.0.1", 50001),
                    "subprotocols": [],
                    "state": {},
                }
                with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}):
                    asyncio.run(app(scope, ws_receive, ws_send))
                self.assertEqual(ws_messages[0]["type"], "websocket.accept")
                self.assertEqual(manager.active_client_count("main"), 0)
                self.assertEqual(manager.total_active_clients(), 0)


if __name__ == "__main__":
    unittest.main()
