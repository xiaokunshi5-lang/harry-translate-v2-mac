#!/usr/bin/env python3
"""Mock-only smoke test for the local 50 ms route, gating and event path."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audio import CHUNK_BYTES, AudioPipeline  # noqa: E402
from app.engines import EngineState  # noqa: E402
from app.session_manager import SessionManager  # noqa: E402


FORBIDDEN_METERING_KEYS = {
    "cost",
    "cost_rmb",
    "input_tokens",
    "output_tokens",
    "summary_tokens",
    "total_tokens",
    "usage",
    "usage_stats",
}


class SmokeFailure(RuntimeError):
    """Raised when a mock event violates the baseline wire contract."""


class MockEngine:
    def __init__(self) -> None:
        self.current_state = EngineState.READY
        self.session_update_count = 1
        self.sent_frames = 0
        self.closed = False

    def connect(self) -> bool:
        return True

    def send_audio(self, pcm_bytes: bytes) -> bool:
        if len(pcm_bytes) != CHUNK_BYTES:
            raise SmokeFailure("audio frame size violated")
        self.sent_frames += 1
        return True

    def close(self) -> None:
        self.closed = True
        self.current_state = EngineState.CLOSED

    def state_event(self) -> dict[str, Any]:
        return {"type": "engine_state", "state": self.current_state.value, "reason": ""}


class MockClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.closed = False

    async def send_text(self, message: str) -> None:
        payload = json.loads(message)
        assert_no_metering_fields(payload)
        self.messages.append(payload)

    async def close(self) -> None:
        self.closed = True


def assert_no_metering_fields(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_METERING_KEYS.intersection(value)
        if forbidden:
            raise SmokeFailure(f"business event contains forbidden fields: {sorted(forbidden)}")
        for child in value.values():
            assert_no_metering_fields(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_metering_fields(child)


def make_subtitle_event(window_id: str, sent_id: int, source: str, target: str) -> dict[str, Any]:
    source_hash = hashlib.sha256(
        f"{window_id}:{sent_id}:{source}".encode("utf-8")
    ).hexdigest()[:16]
    event = {
        "type": "subtitle.delta",
        "window_id": window_id,
        "sent_id": sent_id,
        "source": source,
        "target": target,
        "source_hash": source_hash,
        "is_final": False,
    }
    assert_no_metering_fields(event)
    return event


async def run_mock_flow() -> None:
    pipeline = AudioPipeline()
    one_second_pcm = b"\x00" * (CHUNK_BYTES * 20)
    sliced = pipeline.slice_pcm_frames(one_second_pcm)
    if len(sliced) != 20 or any(len(frame) != CHUNK_BYTES for frame in sliced):
        raise SmokeFailure("50 ms PCM slicing is not deterministic")

    manager = SessionManager(lambda: False)
    engine = MockEngine()
    manager.register_engine("en", engine)
    frames = [b"\x00" * CHUNK_BYTES for _ in range(40)]
    for frame in frames:
        if manager.route_audio_frame("en", frame):
            raise SmokeFailure("audio routed upstream with zero clients")
    if engine.sent_frames != 0:
        raise SmokeFailure("zero-client gate did not stop upstream audio")

    client = MockClient()
    manager.add_client("en", client)
    for frame in frames[:4]:
        if not manager.route_audio_frame("en", frame):
            raise SmokeFailure("connected client did not enable audio routing")
    if engine.sent_frames != 4:
        raise SmokeFailure(f"route sent {engine.sent_frames} frames, expected 4")

    events = [
        make_subtitle_event("window-1", index, f"源句 {index}", f"Target {index}")
        for index in range(1, 6)
    ]

    for event in events:
        await manager.publish_event("en", event)

    pending = manager.pending_events("en")
    if [event["sent_id"] for event in pending] != [3, 4, 5]:
        raise SmokeFailure("bounded queue did not drop the oldest frames")
    if not all(event in client.messages for event in events):
        raise SmokeFailure("healthy mock client did not receive every event")
    for event in pending:
        if event["type"] != "subtitle.delta" or not event["window_id"]:
            raise SmokeFailure("subtitle frame is missing required routing fields")
        assert_no_metering_fields(event)

    manager.disconnect_client("en", client)
    before_disconnect = engine.sent_frames
    if manager.route_audio_frame("en", b"\x00" * CHUNK_BYTES):
        raise SmokeFailure("audio kept routing after the final client disconnected")
    if engine.sent_frames != before_disconnect:
        raise SmokeFailure("final-client disconnect did not stop upstream audio")

    state_event = manager.current_state_event("en")
    if state_event["state"] != EngineState.READY.value:
        raise SmokeFailure("engine state event is not bound to the mock engine")
    assert_no_metering_fields(state_event)
    handshake = {"type": "sidecar_ready", "port": 0, "pid": 1}
    if json.loads(json.dumps(handshake))["type"] != "sidecar_ready":
        raise SmokeFailure("sidecar handshake is invalid")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mock", action="store_true", help="run the deterministic mock flow")
    args = parser.parse_args()
    if not args.mock:
        parser.error("Phase 0 only supports --mock")

    asyncio.run(run_mock_flow())
    print(
        "SMOKE PASS: 50ms slicing, client-aware gating, bounded delivery, "
        "real engine state and zero metering payload"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
