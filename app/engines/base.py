"""Provider-neutral realtime translation contracts without metering fields."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EngineState(StrEnum):
    """The only states exposed by realtime translation engines."""

    IDLE = "idle"
    CONNECTING = "connecting"
    READY = "ready"
    RECONNECTING = "reconnecting"
    AUTH_ERROR = "auth_error"
    QUOTA_ERROR = "quota_error"
    CLOSED = "closed"


def parse_engine_state(value: EngineState | str) -> EngineState:
    """Reject state strings that are not part of the public seven-state contract."""

    try:
        return value if isinstance(value, EngineState) else EngineState(str(value))
    except ValueError as error:
        raise ValueError(f"unsupported_engine_state: {value}") from error


@dataclass(frozen=True, slots=True)
class TranscriptDelta:
    window_id: str
    channel_id: str
    sentence_id: int
    source_text: str
    target_text: str
    ts_ms: int
    ttft_ms: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptFinal:
    window_id: str
    channel_id: str
    sentence_id: int
    source_text: str
    target_text: str
    latency_ms: float | None = None


@dataclass(frozen=True, slots=True)
class EngineStateEvent:
    window_id: str
    channel_id: str
    state: EngineState
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class EngineErrorEvent:
    window_id: str
    channel_id: str
    code: str
    message: str
    recoverable: bool


class RealtimeTranslatorEngine(ABC):
    """Control and audio data plane shared by realtime providers.

    Callback implementations must not be able to break the engine receive loop.
    Concrete engines are responsible for invoking callbacks defensively.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Start connecting and return whether startup was accepted."""

    @abstractmethod
    def close(self) -> None:
        """Stop the engine and release the upstream socket."""

    @abstractmethod
    def live_update_session(
        self,
        source_lang: str,
        target_lang: str,
        corpus_phrases: dict[str, str] | list[tuple[str, str]] | None,
        hotwords: list[str] | None = None,
    ) -> bool:
        """Update the active session without requiring a full reconnect."""

    @abstractmethod
    def update_api_key(self, api_key: str, workspace_id: str | None = None) -> None:
        """Replace credentials for subsequent connections."""

    @abstractmethod
    def send_audio_chunk(self, pcm_bytes: bytes) -> bool:
        """Queue one already-framed PCM chunk for the upstream session."""

    @abstractmethod
    def commit_turn(self) -> bool:
        """Ask the upstream provider to close the current turn."""

    @property
    @abstractmethod
    def current_state(self) -> EngineState:
        """Return the current public engine state."""

    @property
    @abstractmethod
    def session_update_count(self) -> int:
        """Return the number of session.update frames sent on this engine."""

    def state_event(self) -> dict[str, Any]:
        """Return a backward-compatible state event for the legacy WebSocket."""

        return {
            "type": "engine_state",
            "state": self.current_state.value,
            "reason": "",
        }
