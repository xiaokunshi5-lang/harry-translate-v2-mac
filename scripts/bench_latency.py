#!/usr/bin/env python3
"""Measure 50ms PCM slicing, gated routing and concurrent JSON fan-out."""

from __future__ import annotations

import asyncio
import gc
import json
import math
import sys
import time
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audio import AudioPipeline  # noqa: E402
from app.engines import EngineState  # noqa: E402
from app.session_manager import SessionManager  # noqa: E402


SAMPLE_RATE = 16_000
CHUNK_SIZE = 800
FRAMES_PER_BATCH = 100
BATCHES = 100
# Repeat each 100-frame batch to keep P99 above macOS timer and scheduler noise.
FRAME_REPETITIONS = 10


def make_pcm_frames() -> list[bytes]:
    frames: list[bytes] = []
    for frame_index in range(FRAMES_PER_BATCH):
        samples = array(
            "h",
            (
                ((frame_index * 17 + sample_index * 13) % 65_536) - 32_768
                for sample_index in range(CHUNK_SIZE)
            ),
        )
        if sys.byteorder != "little":
            samples.byteswap()
        frames.append(samples.tobytes())
    return frames


def percentile_99(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(0.99 * len(ordered)) - 1)
    return ordered[index]


async def measure() -> float:
    frames = make_pcm_frames()
    manager = SessionManager(lambda: False)
    engine = _BenchEngine()
    manager.register_engine("en", engine)
    manager.add_client("en", _BenchClient())
    pipeline = AudioPipeline()
    serialized_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=3)
    samples_ms: list[float] = []

    for warmup_index in range(50):
        for _ in range(FRAME_REPETITIONS):
            for pcm in frames:
                pipeline.slice_pcm_frames(pcm)
                manager.route_audio_frame("en", pcm)
            event = {
                "type": "subtitle",
                "id": -warmup_index,
                "source": "source",
                "target": "target",
                "is_final": False,
            }
            if serialized_queue.full():
                serialized_queue.get_nowait()
            await serialized_queue.put(json.dumps(event, separators=(",", ":")))
            await manager.broadcast_to_channel("en", event)

    for batch_index in range(BATCHES):
        started = time.process_time_ns()
        for _ in range(FRAME_REPETITIONS):
            for pcm in frames:
                pipeline.slice_pcm_frames(pcm)
                manager.route_audio_frame("en", pcm)
            event = {
                "type": "subtitle",
                "id": batch_index,
                "source": "source",
                "target": "target",
                "is_final": False,
            }
            if serialized_queue.full():
                serialized_queue.get_nowait()
            await serialized_queue.put(json.dumps(event, separators=(",", ":")))
            await manager.broadcast_to_channel("en", event)
        elapsed_ms = (time.process_time_ns() - started) / 1_000_000
        samples_ms.append(elapsed_ms / (FRAMES_PER_BATCH * FRAME_REPETITIONS))

    return percentile_99(samples_ms)


class _BenchEngine:
    current_state = EngineState.READY
    session_update_count = 1

    @staticmethod
    def send_audio(_pcm_bytes: bytes) -> bool:
        return True

    @staticmethod
    def close() -> None:
        return None

    @staticmethod
    def state_event() -> dict[str, str]:
        return {"type": "engine_state", "state": "ready", "reason": ""}


class _BenchClient:
    @staticmethod
    async def send_text(_message: str) -> None:
        return None

    @staticmethod
    async def close() -> None:
        return None


def main() -> int:
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        p99_ms = asyncio.run(measure())
    finally:
        if gc_was_enabled:
            gc.enable()
    print(f"{p99_ms:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
