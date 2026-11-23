"""Structured system-audio capture availability checks."""

from __future__ import annotations

from dataclasses import dataclass

from app.audio.backend import AudioDeviceError, create_audio_backend, is_loopback_device_name


@dataclass(frozen=True, slots=True)
class SystemAudioProbe:
    """Pure inspection result shared by API and UI without opening a stream."""

    backend: str
    loopback_available: bool
    loopback_devices: list[dict[str, object]]
    input_devices: list[dict[str, object]]
    status: str
    hint: str = ""


def probe_system_audio(backend_name: str = "auto") -> SystemAudioProbe:
    backend = create_audio_backend(backend_name)
    try:
        devices = backend.list_input_devices()
    except AudioDeviceError as error:
        return SystemAudioProbe(
            backend=backend.name,
            loopback_available=False,
            loopback_devices=[],
            input_devices=[],
            status="loopback_missing",
            hint=error.hint or error.message,
        )

    loopbacks = [
        {
            "index": device.index,
            "name": device.name,
            "channels": device.max_input_channels,
            "default_samplerate": device.default_samplerate,
        }
        for device in devices
        if device.is_loopback or is_loopback_device_name(device.name)
    ]
    inputs = [
        {
            "index": device.index,
            "name": device.name,
            "channels": device.max_input_channels,
            "default_samplerate": device.default_samplerate,
        }
        for device in devices
    ]
    hint = "" if loopbacks else "安装 BlackHole 2ch 后，在音频 MIDI 设置中创建多输出设备。"
    return SystemAudioProbe(
        backend=backend.name,
        loopback_available=bool(loopbacks),
        loopback_devices=loopbacks,
        input_devices=inputs,
        status="available" if loopbacks else "loopback_missing",
        hint=hint,
    )
