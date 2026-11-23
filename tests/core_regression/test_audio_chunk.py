from __future__ import annotations

import array
import importlib
import statistics
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from app.audio import backend, pipeline


def make_pcm(samples: list[int]) -> bytes:
    values = array.array("h", samples)
    return values.tobytes()


class AudioSlicingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = pipeline.AudioPipeline()

    def test_one_second_stream_produces_twenty_50ms_frames(self) -> None:
        pcm = make_pcm([0] * pipeline.SAMPLE_RATE)
        self.assertEqual(len(pcm), 32_000)

        frames = self.audio.slice_pcm_frames(pcm)

        self.assertEqual(len(frames), 20)
        for frame in frames:
            self.assertEqual(len(frame), pipeline.CHUNK_BYTES)
            self.assertEqual(len(frame) // 2, pipeline.CHUNK_SAMPLES)
            self.assertEqual(
                round(pipeline.CHUNK_SAMPLES / pipeline.SAMPLE_RATE * 1_000),
                50,
            )

    def test_irregular_driver_buffers_are_accumulated_without_loss(self) -> None:
        pcm = bytes((index % 251 for index in range(32_000)))
        sizes = [2, 4, 1_596, 8, 1_600, 2_400, 4_110]
        frames: list[bytes] = []
        offset = 0
        chunk_index = 0
        while offset < len(pcm):
            size = sizes[chunk_index % len(sizes)]
            frames.extend(self.audio.slice_pcm_frames(pcm[offset : offset + size]))
            offset += size
            chunk_index += 1
        frames.extend(self.audio.slice_pcm_frames(b"", flush=True))

        self.assertEqual(len(frames), 20)
        self.assertEqual(b"".join(frames), pcm)
        self.assertEqual(self.audio.debug_state()["buffered_bytes"], 0)

    def test_odd_tail_is_discarded_safely_on_flush(self) -> None:
        frames = self.audio.slice_pcm_frames(b"\x01\x02\x03", flush=True)
        self.assertEqual(frames, [])
        self.assertEqual(self.audio.debug_state()["buffered_bytes"], 0)

    def test_flush_emits_complete_frame_before_discarding_remainder(self) -> None:
        complete_frame = b"\x01\x00" * pipeline.CHUNK_SAMPLES
        frames = self.audio.slice_pcm_frames(complete_frame + b"\xff\x00", flush=True)
        self.assertEqual(frames, [complete_frame])
        self.assertEqual(self.audio.debug_state()["buffered_bytes"], 0)

    def test_odd_tail_does_not_shift_the_next_buffer(self) -> None:
        first_frame = b"\x01\x00" * pipeline.CHUNK_SAMPLES
        second_frame = b"\x02\x00" * pipeline.CHUNK_SAMPLES
        frames = self.audio.slice_pcm_frames(first_frame + b"\xff")
        frames.extend(self.audio.slice_pcm_frames(second_frame))

        self.assertEqual(frames, [first_frame, second_frame])
        self.assertEqual(self.audio.debug_state()["buffered_bytes"], 0)

    def test_queue_keeps_only_latest_eight_frames(self) -> None:
        push_times: list[float] = []
        for index in range(1, 51):
            frame = bytes([index]) + (b"\x00" * (pipeline.CHUNK_BYTES - 1))
            started = time.perf_counter_ns()
            self.assertTrue(self.audio.push_tx_frame(frame))
            push_times.append((time.perf_counter_ns() - started) / 1_000_000)

        queued = [frame[0] for frame in self.audio.drain_tx_frames()]
        p99 = statistics.quantiles(push_times, n=100, method="inclusive")[98]
        self.assertLess(p99, 2.0)
        self.assertLessEqual(len(queued), pipeline.TX_QUEUE_MAXSIZE)
        self.assertEqual(queued, list(range(43, 51)))
        self.assertEqual(self.audio.dropped_frames, 42)

    def test_paused_or_unsubscribed_pipeline_does_not_enqueue(self) -> None:
        active = False
        self.audio.set_streaming_allowed_hook(lambda: active)
        frame = b"\x00" * pipeline.CHUNK_BYTES
        self.assertFalse(self.audio.push_tx_frame(frame))
        active = True
        self.audio.pause()
        self.assertFalse(self.audio.push_tx_frame(frame))
        self.audio.resume()
        self.assertTrue(self.audio.push_tx_frame(frame))

    def test_capture_errors_back_off_after_threshold(self) -> None:
        failures: list[pipeline.CaptureFailure] = []
        self.audio = pipeline.AudioPipeline(
            capture_error_hook=failures.append,
            error_threshold=2,
        )
        self.assertIsNone(self.audio.record_capture_error(RuntimeError("one")))
        failure = self.audio.record_capture_error(RuntimeError("two"))
        self.assertIsNotNone(failure)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].last_error, "two")
        self.assertGreater(failures[0].backoff_seconds, 0)


class AudioMathFallbackTests(unittest.TestCase):
    def test_array_fallback_rms_matches_audioop_within_one(self) -> None:
        audioop = importlib.import_module("audioop")
        samples = [0, 1, -1, 512, -512, 1_000, -2_000, 32_000, -32_000]
        pcm = make_pcm(samples * 128)
        expected = audioop.rms(pcm, 2)
        with (
            patch.object(pipeline, "_audioop", None),
            patch.object(pipeline, "AUDIOOP_BACKEND", "array"),
        ):
            actual = pipeline.calc_rms(pcm)
        self.assertLessEqual(abs(actual - expected), 1)

    def test_array_fallback_downmix_matches_mono_average(self) -> None:
        samples = [1_000, 3_000, -500, 500, 32_000, 31_998]
        with patch.object(pipeline, "_audioop", None):
            mixed = pipeline.downmix_to_mono(make_pcm(samples), 2)
        self.assertEqual(list(array.array("h", mixed)), [2_000, 0, 31_999])

    def test_audioop_lts_import_path_is_declared(self) -> None:
        source = (pipeline.__file__ or "")
        self.assertTrue(source.endswith("pipeline.py"))
        self.assertIn(pipeline.AUDIOOP_BACKEND, {"audioop", "audioop_lts", "array"})


class AudioBackendTests(unittest.TestCase):
    def test_module_import_does_not_require_optional_backends(self) -> None:
        module = importlib.reload(backend)
        self.assertTrue(hasattr(module, "SoundDeviceBackend"))
        self.assertTrue(hasattr(module, "PyAudioBackend"))

    def test_auto_factory_prefers_mock_sounddevice(self) -> None:
        fake_sounddevice = types.SimpleNamespace()
        with patch.dict(sys.modules, {"sounddevice": fake_sounddevice}):
            selected = backend.create_audio_backend("auto")
        self.assertIsInstance(selected, backend.SoundDeviceBackend)

    def test_missing_pyaudio_is_structured_and_lazy(self) -> None:
        candidate = backend.PyAudioBackend()
        with patch.dict(sys.modules, {"pyaudio": None}):
            with self.assertRaises(backend.AudioDeviceError) as raised:
                candidate.list_input_devices()
        self.assertEqual(raised.exception.code, "pyaudio_unavailable")
        self.assertIn("uv sync --frozen", raised.exception.hint)

    def test_invalid_sounddevice_is_wrapped(self) -> None:
        class RawInputStream:
            def __init__(self, **_kwargs: object) -> None:
                raise RuntimeError("Invalid device index")

        fake_sounddevice = types.SimpleNamespace(RawInputStream=RawInputStream)
        candidate = backend.SoundDeviceBackend()
        with patch.dict(sys.modules, {"sounddevice": fake_sounddevice}):
            with self.assertRaises(backend.AudioDeviceError) as raised:
                candidate.open_stream(device=99)
        self.assertEqual(raised.exception.code, "invalid_audio_device")

    def test_pyaudio_backend_keeps_instance_alive_until_stream_opens(self) -> None:
        class FakeStream:
            @staticmethod
            def is_active() -> bool:
                return False

            @staticmethod
            def close() -> None:
                return None

            @staticmethod
            def stop_stream() -> None:
                return None

        class FakeAudio:
            def __init__(self) -> None:
                self.terminated = False
                self.open_called = False

            @staticmethod
            def get_device_count() -> int:
                return 0

            def open(self, **_kwargs: object) -> FakeStream:
                if self.terminated:
                    raise RuntimeError("PortAudio not initialized")
                self.open_called = True
                return FakeStream()

            def terminate(self) -> None:
                self.terminated = True

        fake_module = types.SimpleNamespace(
            PyAudio=FakeAudio,
            paInt16=8,
            paContinue=0,
        )
        candidate = backend.PyAudioBackend()
        with patch.dict(sys.modules, {"pyaudio": fake_module}):
            candidate.open_stream(device=0)

        self.assertTrue(candidate._audio.open_called)
        self.assertFalse(candidate._audio.terminated)
        candidate.close()

    def test_device_listing_marks_loopback_and_skips_outputs(self) -> None:
        class FakeSoundDevice:
            @staticmethod
            def query_devices() -> list[dict[str, object]]:
                return [
                    {
                        "name": "MacBook Pro Microphone",
                        "max_input_channels": 1,
                        "default_samplerate": 48_000,
                    },
                    {
                        "name": "BlackHole 2ch",
                        "max_input_channels": 2,
                        "default_samplerate": 48_000,
                    },
                    {
                        "name": "MacBook Pro Speakers",
                        "max_input_channels": 0,
                        "default_samplerate": 48_000,
                    },
                ]

        candidate = backend.SoundDeviceBackend()
        with patch.dict(sys.modules, {"sounddevice": FakeSoundDevice()}):
            devices = candidate.list_input_devices()
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].name, "MacBook Pro Microphone")
        self.assertFalse(devices[0].is_loopback)
        self.assertTrue(devices[1].is_loopback)

    def test_capture_loop_recovers_after_transient_error(self) -> None:
        audio = pipeline.AudioPipeline()
        calls = 0

        def read_frames(_frames: int) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient")
            return b"\x00" * pipeline.CHUNK_BYTES

        stop = threading.Event()
        sleep_calls: list[float] = []

        def sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        captured = audio.capture_until_stopped(
            read_frames,
            stop,
            max_frames=1,
            sleep=sleep,
        )
        self.assertEqual(captured, 1)
        self.assertGreaterEqual(len(sleep_calls), 1)
        self.assertEqual(audio.tx_queue.qsize(), 1)


if __name__ == "__main__":
    unittest.main()
