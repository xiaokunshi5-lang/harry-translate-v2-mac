"""Audio capture and 50 ms PCM pipeline primitives."""

from app.audio.backend import (
    AudioCaptureBackend,
    AudioDataCallback,
    AudioDeviceError,
    AudioDeviceInfo,
    PyAudioBackend,
    SoundDeviceBackend,
    create_audio_backend,
    is_loopback_device_name,
)
from app.audio.pipeline import (
    AUDIOOP_BACKEND,
    AdaptiveFarFieldAGC,
    CHUNK_BYTES,
    CHUNK_DURATION_MS,
    CHUNK_SAMPLES,
    SAMPLE_RATE,
    TX_QUEUE_MAXSIZE,
    AudioPipeline,
    CaptureFailure,
    calc_rms,
    downmix_to_mono,
)

__all__ = [
    "AUDIOOP_BACKEND",
    "AdaptiveFarFieldAGC",
    "CHUNK_BYTES",
    "CHUNK_DURATION_MS",
    "CHUNK_SAMPLES",
    "SAMPLE_RATE",
    "TX_QUEUE_MAXSIZE",
    "AudioCaptureBackend",
    "AudioDataCallback",
    "AudioDeviceError",
    "AudioDeviceInfo",
    "AudioPipeline",
    "CaptureFailure",
    "PyAudioBackend",
    "SoundDeviceBackend",
    "calc_rms",
    "create_audio_backend",
    "downmix_to_mono",
    "is_loopback_device_name",
]
