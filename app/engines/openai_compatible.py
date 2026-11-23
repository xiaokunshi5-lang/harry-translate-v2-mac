"""Credential-free skeleton for a future OpenAI-compatible realtime provider."""

from __future__ import annotations

import threading

from app.engines.base import EngineState, RealtimeTranslatorEngine


class OpenAICompatibleAdapter(RealtimeTranslatorEngine):
    """Contract-only adapter; R06 deliberately adds no provider SDK."""

    def __init__(
        self,
        *,
        api_key: str = "",
        source_lang: str = "auto",
        target_lang: str,
        corpus_phrases: dict[str, str] | None = None,
        hotwords: list[str] | None = None,
        **_: object,
    ) -> None:
        self.api_key = api_key
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.corpus_phrases = dict(corpus_phrases or {})
        self.hotwords = list(hotwords or [])
        self._state = EngineState.IDLE
        self._session_update_count = 0
        self._lock = threading.RLock()

    @property
    def current_state(self) -> EngineState:
        with self._lock:
            return self._state

    @property
    def session_update_count(self) -> int:
        with self._lock:
            return self._session_update_count

    def connect(self) -> bool:
        with self._lock:
            self._state = EngineState.AUTH_ERROR
        return False

    def close(self) -> None:
        with self._lock:
            self._state = EngineState.CLOSED

    def live_update_session(
        self,
        source_lang: str,
        target_lang: str,
        corpus_phrases: dict[str, str] | list[tuple[str, str]] | None,
        hotwords: list[str] | None = None,
    ) -> bool:
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.corpus_phrases = dict(corpus_phrases or {})
        self.hotwords = list(hotwords or [])
        return False

    def update_api_key(self, api_key: str, workspace_id: str | None = None) -> None:
        self.api_key = api_key

    def send_audio_chunk(self, pcm_bytes: bytes) -> bool:
        return False

    def commit_turn(self) -> bool:
        return False
