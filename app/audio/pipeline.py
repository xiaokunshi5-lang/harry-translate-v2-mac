"""Deterministic 50 ms PCM slicing and bounded audio queue primitives."""

from __future__ import annotations

import array
import math
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

try:
    import audioop as _audioop

    AUDIOOP_BACKEND = "audioop"
except ImportError:
    try:
        import audioop_lts as _audioop  # type: ignore[import-not-found,no-redef]

        AUDIOOP_BACKEND = "audioop_lts"
    except ImportError:
        _audioop = None  # type: ignore[assignment]
        AUDIOOP_BACKEND = "array"

SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 800
CHUNK_BYTES = CHUNK_SAMPLES * 2
CHUNK_DURATION_MS = CHUNK_SAMPLES * 1_000 / SAMPLE_RATE
TX_QUEUE_MAXSIZE = 8

StreamingAllowedHook = Callable[[], bool]
CaptureErrorHook = Callable[["CaptureFailure"], None]


def _even_pcm(raw_pcm: bytes | bytearray | memoryview) -> bytes:
    data = bytes(raw_pcm)
    if len(data) % 2:
        return data[:-1]
    return data


def downmix_to_mono(
    raw_pcm: bytes | bytearray | memoryview,
    channels: int,
) -> bytes:
    """Downmix interleaved signed 16-bit PCM to mono with three-tier fallback."""

    data = bytes(raw_pcm)
    if channels <= 1 or not data:
        return data
    if channels == 2 and _audioop is not None:
        try:
            return _audioop.tomono(_even_pcm(data), 2, 0.5, 0.5)
        except Exception:
            pass

    frame_bytes = channels * 2
    aligned_length = len(data) - (len(data) % frame_bytes)
    if not aligned_length:
        return b""
    samples = array.array("h")
    samples.frombytes(data[:aligned_length])
    mono = array.array(
        "h",
        (
            round(sum(samples[offset : offset + channels]) / channels)
            for offset in range(0, len(samples), channels)
        ),
    )
    return mono.tobytes()


def calc_rms(raw_pcm: bytes | bytearray | memoryview) -> int:
    """Calculate signed 16-bit mono RMS using audioop, audioop_lts, or array."""

    data = _even_pcm(raw_pcm)
    if not data:
        return 0
    if _audioop is not None:
        try:
            return int(_audioop.rms(data, 2))
        except Exception:
            pass

    samples = array.array("h")
    samples.frombytes(data)
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    return int(round(math.sqrt(mean_square)))


class AdaptiveFarFieldAGC:
    """Gentle physical-microphone AGC with noise gating and soft limiting."""

    def __init__(
        self,
        *,
        target_rms: float = 1200.0,
        max_gain: float = 3.2,
        noise_floor: float = 240.0,
        soft_knee: float = 280.0,
        alpha: float = 0.30,
        soft_limit: float = 31500.0,
    ) -> None:
        self.target_rms = float(target_rms)
        self.max_gain = float(max_gain)
        self.noise_floor = float(noise_floor)
        self.soft_knee = float(soft_knee)
        self.alpha = float(alpha)
        self.soft_limit = float(soft_limit)
        self.last_gain = 1.0

    def reset(self) -> None:
        self.last_gain = 1.0

    @staticmethod
    def _peak(raw_pcm: bytes) -> int:
        if _audioop is not None:
            try:
                return int(_audioop.max(raw_pcm, 2))
            except Exception:
                pass
        samples = array.array("h")
        samples.frombytes(raw_pcm)
        return max((abs(sample) for sample in samples), default=0)

    @staticmethod
    def _multiply(raw_pcm: bytes, gain: float) -> bytes:
        if _audioop is not None:
            try:
                return _audioop.mul(raw_pcm, 2, gain)
            except Exception:
                pass
        samples = array.array("h")
        samples.frombytes(raw_pcm)
        for index, sample in enumerate(samples):
            samples[index] = max(-32768, min(32767, int(sample * gain)))
        return samples.tobytes()

    def process(self, raw_pcm: bytes) -> bytes:
        """Process one aligned 16-bit mono PCM frame without allowing clipping."""

        if not raw_pcm:
            return raw_pcm
        if len(raw_pcm) % 2:
            raw_pcm = raw_pcm[:-1]
            if not raw_pcm:
                return raw_pcm

        rms = calc_rms(raw_pcm)
        peak = self._peak(raw_pcm)

        if rms < self.noise_floor:
            self.last_gain = (1.0 - self.alpha) * self.last_gain + self.alpha
            return raw_pcm
        if rms < self.soft_knee:
            ratio = (rms - self.noise_floor) / max(self.soft_knee - self.noise_floor, 1.0)
            full_gain = min(self.max_gain, self.target_rms / self.soft_knee)
            target_gain = 1.0 + ratio * (full_gain - 1.0)
        elif rms <= 2500.0:
            target_gain = min(self.max_gain, max(1.0, self.target_rms / max(rms, 1.0)))
        else:
            target_gain = 1.0

        if peak > 0:
            target_gain = min(target_gain, self.soft_limit / peak)
        gain = (1.0 - self.alpha) * self.last_gain + self.alpha * target_gain
        if peak > 0:
            gain = min(gain, self.soft_limit / peak)
        self.last_gain = gain

        if abs(gain - 1.0) < 1e-4:
            return raw_pcm
        return self._multiply(raw_pcm, gain)

    process_frame = process
    __call__ = process


@dataclass(frozen=True, slots=True)
class CaptureFailure:
    """Structured capture-loop failure state delivered after the retry threshold."""

    consecutive_errors: int
    last_error: str
    backoff_seconds: float


class AudioPipeline:
    """Slice arbitrary PCM buffers and enqueue complete frames without blocking."""

    def __init__(
        self,
        *,
        tx_queue_maxsize: int = TX_QUEUE_MAXSIZE,
        streaming_allowed: StreamingAllowedHook | None = None,
        capture_error_hook: CaptureErrorHook | None = None,
        error_threshold: int = 3,
        enable_agc: bool = False,
        agc: AdaptiveFarFieldAGC | None = None,
    ) -> None:
        if tx_queue_maxsize <= 0:
            raise ValueError("tx_queue_maxsize must be positive")
        if error_threshold <= 0:
            raise ValueError("error_threshold must be positive")

        self.tx_queue: queue.Queue[bytes] = queue.Queue(maxsize=tx_queue_maxsize)
        self.dropped_frames = 0
        self.error_threshold = error_threshold
        self._streaming_allowed = streaming_allowed
        self._capture_error_hook = capture_error_hook
        self._agc_enabled = bool(enable_agc)
        self._agc = agc or AdaptiveFarFieldAGC()
        self._paused = threading.Event()
        self._buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._consecutive_capture_errors = 0

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def pause(self) -> None:
        self.set_paused(True)

    def resume(self) -> None:
        self.set_paused(False)

    def set_streaming_allowed_hook(
        self,
        hook: StreamingAllowedHook | None,
    ) -> None:
        self._streaming_allowed = hook

    def set_agc_enabled(self, enabled: bool) -> None:
        self._agc_enabled = bool(enabled)

    def process_frame(
        self,
        frame: bytes | bytearray | memoryview,
        *,
        enable_agc: bool | None = None,
    ) -> bytes:
        """Apply AGC only when the caller marks the frame as physical mic input."""

        data = bytes(frame)
        should_process = self._agc_enabled if enable_agc is None else bool(enable_agc)
        return self._agc.process(data) if should_process else data

    def is_streaming_allowed(self) -> bool:
        if self.paused:
            return False
        if self._streaming_allowed is None:
            return True
        try:
            return bool(self._streaming_allowed())
        except Exception:
            return False

    def slice_pcm_frames(
        self,
        raw_pcm: bytes | bytearray | memoryview,
        *,
        flush: bool = False,
    ) -> list[bytes]:
        """Accumulate arbitrary chunks and emit only complete 1600-byte frames."""

        data = bytes(raw_pcm)
        frames: list[bytes] = []
        with self._buffer_lock:
            if len(data) == CHUNK_BYTES and not self._buffer:
                frames.append(data)
                if flush:
                    self._buffer.clear()
                return frames
            if data:
                self._buffer.extend(data)
            if len(self._buffer) % 2:
                del self._buffer[-1]
            while len(self._buffer) >= CHUNK_BYTES:
                frames.append(bytes(self._buffer[:CHUNK_BYTES]))
                del self._buffer[:CHUNK_BYTES]
            if flush:
                del self._buffer[:]
        return frames

    def push_tx_frame(self, frame: bytes | bytearray | memoryview) -> bool:
        """Drop the oldest queued frame when full; never block the capture path."""

        if not self.is_streaming_allowed():
            return False
        data = bytes(frame)
        if len(data) != CHUNK_BYTES:
            raise ValueError(f"TX frame must be exactly {CHUNK_BYTES} bytes")

        with self._queue_lock:
            if self.tx_queue.full():
                try:
                    self.tx_queue.get_nowait()
                    self.dropped_frames += 1
                except queue.Empty:
                    pass
            try:
                self.tx_queue.put_nowait(data)
            except queue.Full:
                return False
        return True

    def ingest_pcm(self, raw_pcm: bytes | bytearray | memoryview) -> int:
        """Slice a capture buffer and enqueue every complete frame if allowed."""

        queued = 0
        for frame in self.slice_pcm_frames(raw_pcm):
            if self.push_tx_frame(frame):
                queued += 1
        return queued

    def pop_tx_frame(self, *, block: bool = False) -> bytes | None:
        try:
            if block:
                return self.tx_queue.get()
            return self.tx_queue.get_nowait()
        except queue.Empty:
            return None

    def drain_tx_frames(self) -> list[bytes]:
        frames: list[bytes] = []
        while True:
            frame = self.pop_tx_frame()
            if frame is None:
                return frames
            frames.append(frame)

    def record_capture_error(self, error: BaseException) -> CaptureFailure | None:
        """Track capture failures and report a structured backoff state."""

        self._consecutive_capture_errors += 1
        if self._consecutive_capture_errors < self.error_threshold:
            return None
        failure = CaptureFailure(
            consecutive_errors=self._consecutive_capture_errors,
            last_error=str(error),
            backoff_seconds=min(2.0, 0.05 * (2 ** (self._consecutive_capture_errors - 1))),
        )
        self._consecutive_capture_errors = 0
        if self._capture_error_hook is not None:
            try:
                self._capture_error_hook(failure)
            except Exception:
                pass
        return failure

    def record_capture_success(self) -> None:
        self._consecutive_capture_errors = 0

    def capture_until_stopped(
        self,
        read_frames: Callable[[int], bytes],
        stop_event: threading.Event,
        *,
        max_frames: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> int:
        """Run a blocking capture loop with bounded failure logging/backoff."""

        captured = 0
        while not stop_event.is_set():
            if max_frames is not None and captured >= max_frames:
                break
            try:
                raw_pcm = read_frames(CHUNK_SAMPLES)
            except Exception as error:
                failure = self.record_capture_error(error)
                if failure is not None:
                    sleep(failure.backoff_seconds)
                else:
                    sleep(0.005)
                continue

            self.record_capture_success()
            self.ingest_pcm(raw_pcm)
            captured += 1
        return captured

    def clear(self) -> None:
        with self._buffer_lock:
            self._buffer.clear()
        with self._queue_lock:
            while True:
                try:
                    self.tx_queue.get_nowait()
                except queue.Empty:
                    break

    def debug_state(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "streaming_allowed": self.is_streaming_allowed(),
            "buffered_bytes": len(self._buffer),
            "queue_size": self.tx_queue.qsize(),
            "queue_maxsize": self.tx_queue.maxsize,
            "dropped_frames": self.dropped_frames,
            "audioop_backend": AUDIOOP_BACKEND,
            "agc_enabled": self._agc_enabled,
            "agc_last_gain": self._agc.last_gain,
        }
