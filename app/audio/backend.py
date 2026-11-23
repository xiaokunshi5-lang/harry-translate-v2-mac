"""Lazy, structured audio capture backends for the V2 Mac pipeline."""

from __future__ import annotations

import importlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from app.audio.pipeline import CHUNK_SAMPLES, SAMPLE_RATE

AudioDataCallback = Callable[[bytes], None]

LOOPBACK_KEYWORDS = (
    "blackhole",
    "loopback",
    "soundflower",
    "stereo mix",
    "立体声混音",
    "virtual",
    "wave out",
    "what u hear",
)

MAC_PRIVACY_HINT = (
    "打开“系统设置 > 隐私与安全性 > 麦克风”，允许启动器访问麦克风后重试。"
)


@dataclass(frozen=True, slots=True)
class AudioDeviceInfo:
    """Normalized description of an input-capable audio device."""

    index: int
    name: str
    max_input_channels: int
    default_samplerate: float
    is_loopback: bool = False


class AudioDeviceError(RuntimeError):
    """Structured audio failure that callers can surface without crashing."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if not self.hint:
            return f"{self.code}: {self.message}"
        return f"{self.code}: {self.message} ({self.hint})"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def is_loopback_device_name(name: str) -> bool:
    """Return whether a device name matches known virtual loopback devices."""

    if not name:
        return False
    normalized = name.casefold()
    return any(keyword in normalized for keyword in LOOPBACK_KEYWORDS)


def _wrap_backend_error(
    backend_name: str,
    error: BaseException,
    *,
    device: int | None = None,
) -> AudioDeviceError:
    if isinstance(error, AudioDeviceError):
        return error

    raw_message = str(error).strip() or error.__class__.__name__
    normalized = raw_message.casefold()
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        missing_module = getattr(error, "name", backend_name)
        code = f"{backend_name}_unavailable"
        message = f"{backend_name} 音频后端不可用（缺少 {missing_module}）"
        hint = "执行 uv sync --frozen 安装锁定依赖后重试。"
    elif any(token in normalized for token in ("permission", "denied", "not allowed")):
        code = "mic_permission_denied"
        message = "macOS 拒绝了麦克风访问"
        hint = MAC_PRIVACY_HINT
    elif device is not None and any(
        token in normalized for token in ("invalid", "device", "sample rate", "channel")
    ):
        code = "invalid_audio_device"
        message = f"无法打开音频设备 {device}: {raw_message}"
        hint = MAC_PRIVACY_HINT
    else:
        code = "audio_stream_error"
        message = f"{backend_name} 音频流失败: {raw_message}"
        hint = MAC_PRIVACY_HINT
    return AudioDeviceError(code, message, hint)


class AudioCaptureBackend(ABC):
    """Common capture interface used by later Sidecar/session layers."""

    name: str

    @abstractmethod
    def list_input_devices(self) -> list[AudioDeviceInfo]:
        """Return all input-capable devices without opening a stream."""

    @abstractmethod
    def open_stream(
        self,
        samplerate: int = SAMPLE_RATE,
        blocksize: int = CHUNK_SAMPLES,
        channels: int = 1,
        device: int | None = None,
        callback: AudioDataCallback | None = None,
    ) -> None:
        """Open an explicitly requested stream; never called during import."""

    @abstractmethod
    def read(self, frames: int) -> bytes:
        """Read PCM frames from a blocking stream."""

    @abstractmethod
    def close(self) -> None:
        """Close the stream and release the physical device."""

    @abstractmethod
    def is_active(self) -> bool:
        """Return whether the underlying stream is currently active."""


class SoundDeviceBackend(AudioCaptureBackend):
    """Preferred zero-compile backend backed by bundled PortAudio wheels."""

    name = "sounddevice"

    def __init__(self) -> None:
        self._module: Any | None = None
        self._stream: Any | None = None
        self.last_overflowed = False

    def _load_module(self) -> Any:
        if self._module is None:
            try:
                self._module = importlib.import_module("sounddevice")
            except (ImportError, OSError) as error:
                raise _wrap_backend_error(self.name, error) from error
        return self._module

    def is_available(self) -> bool:
        try:
            self._load_module()
        except AudioDeviceError:
            return False
        return True

    def list_input_devices(self) -> list[AudioDeviceInfo]:
        module = self._load_module()
        try:
            raw_devices = module.query_devices()
            devices: list[AudioDeviceInfo] = []
            for index, raw_device in enumerate(raw_devices):
                channels = int(raw_device.get("max_input_channels", 0))
                if channels <= 0:
                    continue
                name = str(raw_device.get("name", f"Input {index}"))
                devices.append(
                    AudioDeviceInfo(
                        index=index,
                        name=name,
                        max_input_channels=channels,
                        default_samplerate=float(
                            raw_device.get("default_samplerate", SAMPLE_RATE)
                        ),
                        is_loopback=is_loopback_device_name(name),
                    )
                )
            return devices
        except AudioDeviceError:
            raise
        except Exception as error:
            raise _wrap_backend_error(self.name, error) from error

    def open_stream(
        self,
        samplerate: int = SAMPLE_RATE,
        blocksize: int = CHUNK_SAMPLES,
        channels: int = 1,
        device: int | None = None,
        callback: AudioDataCallback | None = None,
    ) -> None:
        module = self._load_module()
        self.close()

        stream_callback: Callable[..., None] | None = None
        if callback is not None:

            def on_audio(
                indata: Any,
                _frames: int,
                _time_info: Any,
                _status: Any,
            ) -> None:
                callback(bytes(indata))

            stream_callback = on_audio

        try:
            self._stream = module.RawInputStream(
                samplerate=samplerate,
                blocksize=blocksize,
                channels=channels,
                dtype="int16",
                device=device,
                callback=stream_callback,
            )
            self._stream.start()
        except Exception as error:
            self._stream = None
            raise _wrap_backend_error(self.name, error, device=device) from error

    def read(self, frames: int) -> bytes:
        if self._stream is None:
            raise AudioDeviceError(
                "audio_stream_not_open",
                "音频流尚未打开",
                "先调用 open_stream()，再读取 PCM 数据。",
            )
        try:
            result = self._stream.read(frames)
            if isinstance(result, tuple):
                data, overflowed = result
                self.last_overflowed = bool(overflowed)
                return bytes(data)
            return bytes(result)
        except Exception as error:
            raise _wrap_backend_error(self.name, error) from error

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def is_active(self) -> bool:
        if self._stream is None:
            return False
        try:
            return bool(self._stream.active)
        except Exception:
            return False


class PyAudioBackend(AudioCaptureBackend):
    """Optional compatibility backend; PyAudio is never imported at module load."""

    name = "pyaudio"

    def __init__(self) -> None:
        self._module: Any | None = None
        self._audio: Any | None = None
        self._stream: Any | None = None

    def _load_module(self) -> Any:
        if self._module is None:
            try:
                self._module = importlib.import_module("pyaudio")
            except (ImportError, OSError) as error:
                raise _wrap_backend_error(self.name, error) from error
        return self._module

    def is_available(self) -> bool:
        try:
            self._load_module()
        except AudioDeviceError:
            return False
        return True

    def _ensure_audio(self) -> Any:
        module = self._load_module()
        if self._audio is None:
            try:
                self._audio = module.PyAudio()
            except Exception as error:
                raise _wrap_backend_error(self.name, error) from error
        return self._audio

    def list_input_devices(self) -> list[AudioDeviceInfo]:
        audio = self._ensure_audio()
        try:
            devices: list[AudioDeviceInfo] = []
            for index in range(int(audio.get_device_count())):
                raw_device = audio.get_device_info_by_index(index)
                channels = int(raw_device.get("maxInputChannels", 0))
                if channels <= 0:
                    continue
                name = str(raw_device.get("name", f"Input {index}"))
                devices.append(
                    AudioDeviceInfo(
                        index=index,
                        name=name,
                        max_input_channels=channels,
                        default_samplerate=float(
                            raw_device.get("defaultSampleRate", SAMPLE_RATE)
                        ),
                        is_loopback=is_loopback_device_name(name),
                    )
                )
            return devices
        except Exception as error:
            raise _wrap_backend_error(self.name, error) from error

    def open_stream(
        self,
        samplerate: int = SAMPLE_RATE,
        blocksize: int = CHUNK_SAMPLES,
        channels: int = 1,
        device: int | None = None,
        callback: AudioDataCallback | None = None,
    ) -> None:
        module = self._load_module()
        self.close()
        audio = self._ensure_audio()

        stream_callback: Callable[..., tuple[None, int]] | None = None
        if callback is not None:

            def on_audio(
                in_data: bytes,
                _frame_count: int,
                _time_info: Any,
                _status_flags: int,
            ) -> tuple[None, int]:
                callback(bytes(in_data))
                return (None, module.paContinue)

            stream_callback = on_audio

        try:
            self._stream = audio.open(
                format=module.paInt16,
                channels=channels,
                rate=samplerate,
                input=True,
                input_device_index=device,
                frames_per_buffer=blocksize,
                stream_callback=stream_callback,
                start=stream_callback is None,
            )
            if stream_callback is not None:
                self._stream.start_stream()
        except Exception as error:
            self._stream = None
            raise _wrap_backend_error(self.name, error, device=device) from error

    def read(self, frames: int) -> bytes:
        if self._stream is None:
            raise AudioDeviceError(
                "audio_stream_not_open",
                "音频流尚未打开",
                "先调用 open_stream()，再读取 PCM 数据。",
            )
        try:
            return bytes(self._stream.read(frames, exception_on_overflow=False))
        except TypeError:
            try:
                return bytes(self._stream.read(frames))
            except Exception as error:
                raise _wrap_backend_error(self.name, error) from error
        except Exception as error:
            raise _wrap_backend_error(self.name, error) from error

    def close(self) -> None:
        stream, self._stream = self._stream, None
        audio, self._audio = self._audio, None
        if stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass

    def is_active(self) -> bool:
        if self._stream is None:
            return False
        try:
            return bool(self._stream.is_active())
        except Exception:
            return False


def create_audio_backend(preferred: str = "auto") -> AudioCaptureBackend:
    """Create a backend without touching hardware until open_stream() is called."""

    choice = (preferred or "auto").strip().casefold()
    if choice == "auto":
        choice = (os.environ.get("AUDIO_BACKEND") or "auto").strip().casefold()

    sounddevice_backend = SoundDeviceBackend()
    pyaudio_backend = PyAudioBackend()

    if choice in {"auto", "sounddevice"}:
        if choice == "sounddevice" or sounddevice_backend.is_available():
            return sounddevice_backend
    if choice in {"auto", "pyaudio"}:
        if choice == "pyaudio" or pyaudio_backend.is_available():
            return pyaudio_backend

    if choice not in {"auto", "sounddevice", "pyaudio"}:
        raise AudioDeviceError(
            "invalid_audio_backend",
            f"未知音频后端: {choice}",
            "可选值为 auto、sounddevice 或 pyaudio。",
        )
    raise AudioDeviceError(
        "audio_backend_unavailable",
        "没有可用的音频采集后端",
        "执行 uv sync --frozen 安装 sounddevice；仅兼容旧环境时才手动安装 PyAudio。",
    )
