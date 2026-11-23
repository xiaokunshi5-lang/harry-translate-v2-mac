from __future__ import annotations

import array
import json
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.audio import pipeline
from app.engines.base import EngineState
from app.engines.qwen_realtime import QwenRealtimeAdapter

ROOT = Path(__file__).resolve().parents[2]


def pcm(sample: int, count: int = pipeline.CHUNK_SAMPLES) -> bytes:
    values = array.array("h", [sample] * count)
    return values.tobytes()


def peak_abs(raw: bytes) -> int:
    values = array.array("h")
    values.frombytes(raw)
    return max((abs(value) for value in values), default=0)


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
        self.closed = False

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def close(self) -> None:
        self.closed = True


class AdaptiveFarFieldAGCTests(unittest.TestCase):
    def test_golden_parameters_and_noise_floor_bypass(self) -> None:
        agc = pipeline.AdaptiveFarFieldAGC()
        self.assertEqual(agc.target_rms, 1200.0)
        self.assertEqual(agc.max_gain, 3.2)
        self.assertEqual(agc.noise_floor, 240.0)
        self.assertEqual(agc.soft_knee, 280.0)
        self.assertEqual(agc.alpha, 0.30)
        self.assertEqual(agc.soft_limit, 31500.0)

        quiet = pcm(120)
        self.assertEqual(agc.process(quiet), quiet)

    def test_weak_speech_is_boosted_but_never_exceeds_max_gain(self) -> None:
        agc = pipeline.AdaptiveFarFieldAGC()
        source = pcm(400)
        output = agc.process(source)
        self.assertLessEqual(peak_abs(output), peak_abs(source) * 3.2)
        self.assertGreaterEqual(pipeline.calc_rms(output), pipeline.calc_rms(source))

    def test_peak_is_soft_limited_with_audioop_and_array_fallback(self) -> None:
        source = pcm(32000)
        with patch.object(pipeline, "_audioop", None):
            output = pipeline.AdaptiveFarFieldAGC().process(source)
        self.assertLessEqual(peak_abs(output), 31500)

    def test_pipeline_can_bypass_agc_for_digital_loopback_frames(self) -> None:
        audio = pipeline.AudioPipeline(enable_agc=True)
        source = pcm(400)
        self.assertEqual(audio.process_frame(source, enable_agc=False), source)
        self.assertNotEqual(audio.process_frame(source, enable_agc=True), source)

    def test_single_frame_agc_hot_path_stays_below_half_millisecond_p99(self) -> None:
        agc = pipeline.AdaptiveFarFieldAGC()
        samples = [pcm(400 + index) for index in range(20)]
        timings: list[float] = []
        for _ in range(3):
            for sample in samples:
                started = time.perf_counter_ns()
                agc.process(sample)
                timings.append((time.perf_counter_ns() - started) / 1_000_000)
        timings.sort()
        self.assertLess(timings[max(0, int(len(timings) * 0.99) - 1)], 0.5)


class QwenSafeCommitTests(unittest.TestCase):
    def _engine(self, socket: FakeSocket) -> QwenRealtimeAdapter:
        engine = QwenRealtimeAdapter(
            api_key="sk-test1234567890abcd",
            target_lang="zh",
            ws_url="wss://example.invalid/realtime",
            connector=lambda *_args, **_kwargs: socket,
            enable_health_check=False,
            session_finish_drain=0.0,
        )
        engine._running = True
        engine._socket = socket
        engine._state = EngineState.READY
        engine._session_update_count = 1
        return engine

    def _start_sender(self, engine: QwenRealtimeAdapter) -> threading.Thread:
        thread = threading.Thread(target=engine._audio_sender_loop, daemon=True)
        thread.start()
        return thread

    def test_qwen38_update_has_no_turn_detection(self) -> None:
        socket = FakeSocket()
        engine = self._engine(socket)
        self.assertTrue(engine._send_session_update())
        update = next(item for item in socket.sent if item["type"] == "session.update")
        self.assertNotIn("turn_detection", update["session"])
        engine._stop_event.set()

    def test_pause_after_2_4_seconds_queues_commit_after_audio(self) -> None:
        socket = FakeSocket()
        engine = self._engine(socket)
        sender = self._start_sender(engine)
        high = pcm(500)
        for _ in range(48):
            self.assertTrue(engine.send_audio_chunk(high))
        self.assertTrue(engine.send_audio_chunk(pcm(0)))
        wait_for(lambda: any(item.get("type") == "input_audio_buffer.commit" for item in socket.sent))
        append_index = next(
            index
            for index, item in enumerate(socket.sent)
            if item.get("type") == "input_audio_buffer.append"
        )
        commit_index = next(
            index
            for index, item in enumerate(socket.sent)
            if item.get("type") == "input_audio_buffer.commit"
        )
        self.assertGreater(commit_index, append_index)
        self.assertGreaterEqual(engine.commit_turn_count, 1)
        engine._stop_event.set()
        sender.join(timeout=1)

    def test_continuous_3_8_seconds_queues_commit(self) -> None:
        socket = FakeSocket()
        engine = self._engine(socket)
        sender = self._start_sender(engine)
        high = pcm(500)
        for _ in range(76):
            self.assertTrue(engine.send_audio_chunk(high))
        wait_for(lambda: engine.commit_turn_count >= 1)
        self.assertTrue(
            any(item.get("type") == "input_audio_buffer.commit" for item in socket.sent)
        )
        engine._stop_event.set()
        sender.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
