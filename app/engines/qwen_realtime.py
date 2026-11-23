"""Qwen realtime translation engine with explicit state and strict TLS."""

from __future__ import annotations

import base64
import json
import queue
import random
import ssl
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import certifi
import websocket

from app.audio.pipeline import SAMPLE_RATE, calc_rms
from app.engines.base import (
    EngineErrorEvent,
    EngineState,
    EngineStateEvent,
    RealtimeTranslatorEngine,
    TranscriptDelta,
    TranscriptFinal,
)

DEFAULT_QWEN_MODEL = "qwen3.8-livetranslate-flash-realtime"
DEFAULT_QWEN_PUBLIC_WS_URL = (
    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    f"?model={DEFAULT_QWEN_MODEL}"
)
DEFAULT_QWEN_WS_URL = DEFAULT_QWEN_PUBLIC_WS_URL
PRIVATE_QWEN_WS_HOST_TEMPLATE = (
    "wss://{workspace_id}.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
)
AUDIO_TX_QUEUE_MAXSIZE = 8
CONNECT_TIMEOUT_SEC = 15.0
SETUP_TIMEOUT_SEC = 5.0
SEND_TIMEOUT_SEC = 5.0
HEALTH_CHECK_INTERVAL_SEC = 10.0
HEALTH_PROBE_TIMEOUT_SEC = 4.0
SESSION_FINISH_DRAIN_SEC = 0.25
MIN_RATE_LIMIT_BACKOFF_SEC = 1.0
MAX_CORPUS_WIRE_BYTES = 6144
AUTO_COMMIT_LOW_ENERGY_RMS = 380.0
AUTO_COMMIT_PAUSE_SEC = 2.4
AUTO_COMMIT_MAX_SEC = 3.8

EngineCallback = Callable[[dict[str, Any]], None]
Connector = Callable[..., Any]


TERMINAL_STATES = {EngineState.AUTH_ERROR, EngineState.CLOSED}


def classify_engine_error(
    error: BaseException | str,
    *,
    status_code: int | None = None,
) -> EngineState:
    message = str(error).casefold()
    if status_code in {401, 403} or any(
        token in message
        for token in ("401", "403", "unauthorized", "forbidden", "invalid api key", "auth")
    ):
        return EngineState.AUTH_ERROR
    if status_code == 429 or any(
        token in message
        for token in ("429", "1007", "quota", "insufficient", "rate limit", "balance")
    ):
        return EngineState.QUOTA_ERROR
    return EngineState.RECONNECTING


def _set_model_query(url: str, model: str) -> str:
    parsed = urlparse(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "model"]
    query.append(("model", model))
    return urlunparse(parsed._replace(query=urlencode(query)))


def resolve_ws_url(
    url: str | None,
    api_key: str,
    *,
    model: str = DEFAULT_QWEN_MODEL,
    workspace_id: str = "",
) -> str:
    """Resolve a deterministic TLS endpoint without putting workspace data in headers."""

    candidate = (url or "").strip()
    private_key = (api_key or "").strip().startswith("sk-ws-")
    workspace = workspace_id.strip()

    if candidate:
        parsed = urlparse(candidate)
        if parsed.scheme != "wss" or not parsed.netloc:
            raise ValueError("realtime_url_must_use_wss")
        if not private_key and ".maas.aliyuncs.com" in parsed.netloc.casefold():
            return _set_model_query(DEFAULT_QWEN_PUBLIC_WS_URL, model)
        if "{WorkspaceId}" in candidate or "{workspace_id}" in candidate:
            if not workspace:
                raise ValueError("workspace_id_required")
            candidate = candidate.replace("{WorkspaceId}", workspace).replace(
                "{workspace_id}", workspace
            )
            parsed = urlparse(candidate)
            if parsed.scheme != "wss" or not parsed.netloc:
                raise ValueError("realtime_url_must_use_wss")
        return _set_model_query(candidate, model)

    if private_key:
        if not workspace:
            raise ValueError("workspace_id_required")
        candidate = PRIVATE_QWEN_WS_HOST_TEMPLATE.format(workspace_id=workspace)
        return _set_model_query(candidate, model)
    return _set_model_query(DEFAULT_QWEN_PUBLIC_WS_URL, model)


class QwenRealtimeAdapter(RealtimeTranslatorEngine):
    """Single upstream session with one handshake per successful connection."""

    def __init__(
        self,
        *,
        api_key: str,
        target_lang: str,
        source_lang: str = "auto",
        workspace_id: str = "",
        ws_url: str | None = None,
        model: str = DEFAULT_QWEN_MODEL,
        window_id: str = "",
        channel_id: str = "",
        corpus_phrases: dict[str, str] | list[tuple[str, str]] | None = None,
        hotwords: list[str] | None = None,
        event_callback: EngineCallback | None = None,
        on_delta: Callable[[TranscriptDelta], None] | None = None,
        on_final: Callable[[TranscriptFinal], None] | None = None,
        on_state_change: Callable[[EngineStateEvent], None] | None = None,
        on_error: Callable[[EngineErrorEvent], None] | None = None,
        connector: Connector | None = None,
        ssl_context: ssl.SSLContext | None = None,
        thread_join_timeout: float = 2.0,
        reconnect_base: float = 0.5,
        reconnect_cap: float = 16.0,
        connect_timeout: float = CONNECT_TIMEOUT_SEC,
        setup_timeout: float = SETUP_TIMEOUT_SEC,
        send_timeout: float = SEND_TIMEOUT_SEC,
        health_check_interval: float = HEALTH_CHECK_INTERVAL_SEC,
        health_probe_timeout: float = HEALTH_PROBE_TIMEOUT_SEC,
        recovery_continuity: float = HEALTH_CHECK_INTERVAL_SEC,
        session_finish_drain: float = SESSION_FINISH_DRAIN_SEC,
        enable_health_check: bool = True,
    ) -> None:
        self.api_key = api_key.strip()
        self.source_lang = source_lang or "auto"
        self.target_lang = target_lang
        self.workspace_id = workspace_id.strip()
        self._ws_url_template = (ws_url or "").strip()
        self.ws_url = resolve_ws_url(
            self._ws_url_template,
            self.api_key,
            model=model,
            workspace_id=self.workspace_id,
        )
        self.model = model
        self.window_id = window_id
        self.channel_id = channel_id
        self.corpus_phrases = self._normalize_phrases(corpus_phrases)
        self.hotwords = list(hotwords or [])
        self.event_callback = event_callback
        self.on_delta = on_delta
        self.on_final = on_final
        self.on_state_change = on_state_change
        self.on_error = on_error
        self._connector = connector or websocket.create_connection
        self._thread_join_timeout = thread_join_timeout
        self._reconnect_base = max(0.0, reconnect_base)
        self._reconnect_cap = max(self._reconnect_base, reconnect_cap)
        self._connect_timeout = max(0.1, connect_timeout)
        self._setup_timeout = max(0.1, setup_timeout)
        self._send_timeout = max(0.1, send_timeout)
        self._health_check_interval = max(0.05, health_check_interval)
        self._health_probe_timeout = max(0.05, health_probe_timeout)
        self._recovery_continuity = max(0.0, recovery_continuity)
        self._session_finish_drain = max(0.0, session_finish_drain)
        self._enable_health_check = bool(enable_health_check)
        self._jitter_random = random.Random(f"{self.window_id}:{self.channel_id}:qwen")
        self.ssl_context = ssl_context or ssl.create_default_context(cafile=certifi.where())
        self.ssl_context.verify_mode = ssl.CERT_REQUIRED
        self.ssl_context.check_hostname = True

        self._state = EngineState.IDLE
        self._state_reason = ""
        self._state_lock = threading.RLock()
        self._socket_lock = threading.RLock()
        self._socket: Any | None = None
        self._running = False
        self._stop_event = threading.Event()
        self._finish_drained = threading.Event()
        self._finish_sent = False
        self._reader_thread: threading.Thread | None = None
        self._tx_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue(
            maxsize=AUDIO_TX_QUEUE_MAXSIZE
        )
        self.dropped_audio_frames = 0
        self._audio_energy_lock = threading.Lock()
        self._continuous_speech_sec = 0.0
        self._commit_turn_count = 0
        self._session_update_count = 0
        self._reconnect_attempt = 0
        self._last_backoff_sec = 0.0
        self._turn_started_at = 0.0
        self._ready_since: float | None = None
        self._last_health_at = 0.0
        self._health_probe_started_at: float | None = None
        self._last_failure_reason = ""
        self._corpus_suspended = False

        self._sentence_id = 0
        self._source_text = ""
        self._target_text = ""
        self._has_active_response = False

    @staticmethod
    def _normalize_phrases(
        corpus_phrases: dict[str, str] | list[tuple[str, str]] | None,
    ) -> dict[str, str]:
        if not corpus_phrases:
            return {}
        if isinstance(corpus_phrases, dict):
            return {
                str(source): str(target)
                for source, target in corpus_phrases.items()
                if str(source).strip()
            }
        return {
            str(source): str(target)
            for source, target in corpus_phrases
            if str(source).strip()
        }

    @property
    def current_state(self) -> EngineState:
        with self._state_lock:
            return self._state

    @property
    def session_update_count(self) -> int:
        with self._state_lock:
            return self._session_update_count

    @property
    def current_reason(self) -> str:
        with self._state_lock:
            return self._state_reason

    @property
    def reconnect_attempt(self) -> int:
        with self._state_lock:
            return self._reconnect_attempt

    @property
    def commit_turn_count(self) -> int:
        with self._audio_energy_lock:
            return self._commit_turn_count

    @property
    def corpus_suspended(self) -> bool:
        with self._state_lock:
            return self._corpus_suspended

    @property
    def ssl_options(self) -> dict[str, Any]:
        return {
            "cert_reqs": ssl.CERT_REQUIRED,
            "context": self.ssl_context,
        }

    def state_event(self) -> dict[str, Any]:
        with self._state_lock:
            state = self._state
            reason = self._state_reason
            suspended = self._corpus_suspended
            attempt = self._reconnect_attempt
            backoff = round(self._last_backoff_sec, 3)
        if state == EngineState.READY:
            health_status = "degraded_minimal_corpus" if suspended else "connected"
        elif state in {EngineState.RECONNECTING, EngineState.QUOTA_ERROR} and backoff > 0:
            health_status = "reconnecting(backoff)"
        else:
            health_status = state.value
        return {
            "type": "engine_state",
            "state": state.value,
            "reason": reason,
            "health_status": health_status,
            "corpus_suspended": suspended,
            "reconnect_attempt": attempt,
            "backoff_seconds": backoff,
            "window_id": self.window_id,
            "channel_id": self.channel_id,
        }

    def _safe_reason(self, reason: str) -> str:
        if self.api_key:
            return reason.replace(self.api_key, "[redacted]")
        return reason

    def _set_state(self, state: EngineState, reason: str = "") -> None:
        safe_reason = self._safe_reason(reason)
        with self._state_lock:
            changed = self._state != state or self._state_reason != safe_reason
            self._state = state
            self._state_reason = safe_reason
        if changed:
            structured = EngineStateEvent(
                window_id=self.window_id,
                channel_id=self.channel_id,
                state=state,
                reason=safe_reason,
            )
            self._emit_structured(self.on_state_change, structured)
            self._emit_event(self.state_event())

    @staticmethod
    def _emit_structured(callback: Callable[[Any], None] | None, event: Any) -> None:
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            pass

    def _emit_event(self, event: dict[str, Any]) -> None:
        callback = self.event_callback
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            pass

    def _emit_subtitle(self, *, is_final: bool) -> None:
        now = time.monotonic()
        ttft_ms = (
            round((now - self._turn_started_at) * 1_000, 3)
            if self._turn_started_at and (self._source_text or self._target_text)
            else None
        )
        payload = {
            "type": "subtitle",
            "id": self._sentence_id,
            "sentence_id": self._sentence_id,
            "source_text": self._source_text,
            "target_text": self._target_text,
            "source": self._source_text,
            "target": self._target_text,
            "is_final": is_final,
            "window_id": self.window_id,
            "channel_id": self.channel_id,
            "ttft_ms": ttft_ms,
        }
        if is_final:
            latency_ms = ttft_ms
            self._emit_structured(
                self.on_final,
                TranscriptFinal(
                    window_id=self.window_id,
                    channel_id=self.channel_id,
                    sentence_id=self._sentence_id,
                    source_text=self._source_text,
                    target_text=self._target_text,
                    latency_ms=latency_ms,
                ),
            )
            payload["latency_ms"] = latency_ms
        else:
            self._emit_structured(
                self.on_delta,
                TranscriptDelta(
                    window_id=self.window_id,
                    channel_id=self.channel_id,
                    sentence_id=self._sentence_id,
                    source_text=self._source_text,
                    target_text=self._target_text,
                    ts_ms=int(time.time() * 1_000),
                    ttft_ms=ttft_ms,
                ),
            )
        self._emit_event(payload)

    def connect(self) -> bool:
        if not self.api_key:
            self._set_state(EngineState.AUTH_ERROR, "missing_api_key")
            return False
        with self._state_lock:
            if self._running:
                return True
            self._running = True
            self._stop_event.clear()
            self._finish_drained.clear()
            self._finish_sent = False
            self._reconnect_attempt = 0
            self._last_backoff_sec = 0.0
            self._ready_since = None
            self._health_probe_started_at = None
            self._last_failure_reason = ""
        self._reader_thread = threading.Thread(
            target=self._connection_loop,
            name="qwen-realtime-reader",
            daemon=True,
        )
        self._tx_thread = threading.Thread(
            target=self._audio_sender_loop,
            name="qwen-realtime-sender",
            daemon=True,
        )
        self._reader_thread.start()
        self._tx_thread.start()
        if self._enable_health_check:
            self._health_thread = threading.Thread(
                target=self._health_loop,
                name="qwen-realtime-health",
                daemon=True,
            )
            self._health_thread.start()
        return True

    def live_update_session(
        self,
        source_lang: str,
        target_lang: str,
        corpus_phrases: dict[str, str] | list[tuple[str, str]] | None,
        hotwords: list[str] | None = None,
    ) -> bool:
        self.source_lang = source_lang or "auto"
        self.target_lang = target_lang
        phrases = self._normalize_phrases(corpus_phrases)
        for hotword in hotwords or []:
            value = str(hotword).strip()
            if value:
                phrases.setdefault(value, value)
        self.corpus_phrases = phrases
        self.hotwords = list(hotwords or [])
        if self.current_state != EngineState.READY:
            return True
        return self._send_session_update()

    def update_api_key(self, api_key: str, workspace_id: str | None = None) -> None:
        self.api_key = (api_key or "").strip()
        if workspace_id is not None:
            self.workspace_id = workspace_id.strip()
        self.ws_url = resolve_ws_url(
            self._ws_url_template,
            self.api_key,
            model=self.model,
            workspace_id=self.workspace_id,
        )
        if self._running:
            self._set_state(EngineState.RECONNECTING, "api_key_updated")
            self._close_socket()

    def _connection_loop(self) -> None:
        while not self._stop_event.is_set():
            connecting_reason = (
                f"corpus_suspended: {self._last_failure_reason}"
                if self._corpus_suspended and self._last_failure_reason
                else ""
            )
            self._set_state(EngineState.CONNECTING, connecting_reason)
            self._ready_since = None
            self._health_probe_started_at = None
            self._finish_drained.clear()
            with self._socket_lock:
                self._finish_sent = False
            try:
                socket = self._connector(
                    self.ws_url,
                    header=[f"Authorization: Bearer {self.api_key}"],
                    sslopt=self.ssl_options,
                    timeout=self._connect_timeout,
                    enable_multithread=True,
                )
                if self._stop_event.is_set():
                    try:
                        socket.close()
                    except Exception:
                        pass
                    break
                if hasattr(socket, "settimeout"):
                    socket.settimeout(self._setup_timeout)
                with self._socket_lock:
                    self._socket = socket
                if not self._send_session_update():
                    self._close_socket()
                    break
            except Exception as error:
                state = classify_engine_error(error)
                self._record_failure(str(error))
                self._set_state(state, str(error))
                self._close_socket()
                if state in TERMINAL_STATES:
                    break
            else:
                self._read_until_disconnect()

            if self._stop_event.is_set():
                break
            state = self.current_state
            if state in TERMINAL_STATES:
                break
            reason = self._last_failure_reason or "upstream_disconnected"
            base = self._reconnect_base
            if self._is_rate_limit(reason) or self._is_invalid_parameter(reason):
                base = max(base, MIN_RATE_LIMIT_BACKOFF_SEC)
            delay = min(
                self._reconnect_cap,
                base * (2 ** min(self._reconnect_attempt, 4)),
            )
            delay += self._jitter_random.uniform(0.0, 0.08)
            with self._state_lock:
                self._last_backoff_sec = delay
                self._reconnect_attempt += 1
            if self._is_invalid_parameter(reason):
                self._set_state(EngineState.RECONNECTING, reason)
            elif self._is_rate_limit(reason):
                self._set_state(EngineState.QUOTA_ERROR, reason)
            else:
                self._set_state(EngineState.RECONNECTING, reason)
            if self._stop_event.wait(delay):
                break

    def _read_until_disconnect(self) -> None:
        socket = self._socket
        if socket is None:
            return
        try:
            while not self._stop_event.is_set():
                try:
                    receiver = getattr(socket, "recv_data_frame", None)
                    if callable(receiver):
                        opcode, frame = receiver(control_frame=True)
                        if opcode == websocket.ABNF.OPCODE_PING:
                            pong = getattr(socket, "pong", None)
                            if callable(pong):
                                try:
                                    pong(frame.data)
                                except Exception:
                                    pass
                            self._mark_healthy()
                            continue
                        if opcode == websocket.ABNF.OPCODE_PONG:
                            self._mark_healthy()
                            continue
                        if opcode == websocket.ABNF.OPCODE_CLOSE:
                            return
                        message = frame.data
                    else:
                        message = socket.recv()
                except (TimeoutError, websocket.WebSocketTimeoutException) as error:
                    if self._ready_since is None:
                        raise TimeoutError("setup_timeout") from error
                    continue
                if not message:
                    return
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                self.handle_message(message)
                if self.current_state in TERMINAL_STATES:
                    return
                if self._socket is None:
                    return
        except Exception as error:
            if not self._stop_event.is_set():
                is_generic_disconnect = str(error).casefold() in {
                    "closed",
                    "connection closed",
                    "connection reset by peer",
                }
                if is_generic_disconnect and self._last_failure_reason:
                    return
                state = classify_engine_error(error)
                self._record_failure(str(error))
                self._set_state(state, str(error))
        finally:
            self._finish_drained.set()
            self._close_socket()

    def _send_session_update(self) -> bool:
        phrases = dict(self.corpus_phrases)
        for hotword in self.hotwords:
            value = str(hotword).strip()
            if value:
                phrases.setdefault(value, value)
        if self._corpus_suspended:
            phrases = {}
        phrases = self._fit_phrases_to_wire_budget(phrases)
        translation: dict[str, Any] = {"language": self.target_lang}
        if phrases:
            translation["corpus"] = {"phrases": phrases}
        payload = {
            "type": "session.update",
            "session": {
                "output_modalities": ["text"],
                "translation": translation,
            },
        }
        if not self.model.casefold().startswith("qwen3.8"):
            payload["session"]["turn_detection"] = {
                "type": "server_vad",
                "threshold": 0.45,
                "silence_duration_ms": 240,
                "prefix_padding_ms": 180,
            }
        with self._state_lock:
            if not self._running or self._stop_event.is_set():
                return False
            with self._socket_lock:
                socket = self._socket
                if socket is None:
                    return False
                socket.send(json.dumps(payload, ensure_ascii=False))
                self._session_update_count += 1
                return True

    @staticmethod
    def _fit_phrases_to_wire_budget(phrases: dict[str, str]) -> dict[str, str]:
        fitted = dict(phrases)
        while fitted and len(
            json.dumps(fitted, ensure_ascii=False).encode("utf-8")
        ) > MAX_CORPUS_WIRE_BYTES:
            fitted.popitem()
        return fitted

    @staticmethod
    def _is_invalid_parameter(reason: str) -> bool:
        normalized = reason.casefold()
        if "invalidparameter" in normalized or "invalid parameter" in normalized:
            return True
        return "400" in normalized and any(
            token in normalized
            for token in ("length", "corpus", "input", "invalid", "parameter")
        )

    @staticmethod
    def _is_rate_limit(reason: str) -> bool:
        normalized = reason.casefold()
        return any(
            token in normalized
            for token in ("1007", "429", "rate limit", "too many requests")
        )

    def _record_failure(self, reason: str) -> None:
        safe_reason = self._safe_reason(reason)
        with self._state_lock:
            self._last_failure_reason = safe_reason
            self._ready_since = None
            self._health_probe_started_at = None
            if self._is_invalid_parameter(reason):
                self._corpus_suspended = True

    def _mark_healthy(self) -> None:
        now = time.monotonic()
        with self._state_lock:
            self._last_health_at = now
            self._health_probe_started_at = None
            ready_since = self._ready_since
            if (
                ready_since is not None
                and now - ready_since >= self._recovery_continuity
            ):
                self._reconnect_attempt = 0
                self._last_backoff_sec = 0.0
                self._last_failure_reason = ""

    def _health_loop(self) -> None:
        while not self._stop_event.is_set():
            step = max(
                0.02,
                min(1.0, self._health_check_interval, self._health_probe_timeout),
            )
            if self._stop_event.wait(step):
                break
            if self.current_state != EngineState.READY:
                continue
            now = time.monotonic()
            with self._state_lock:
                socket = self._socket
                ready_since = self._ready_since
                probe_started = self._health_probe_started_at
                last_health = self._last_health_at
            if socket is None or ready_since is None:
                continue
            if (
                probe_started is not None
                and now - probe_started >= self._health_probe_timeout
                and last_health < probe_started
            ):
                reason = "health_probe_timeout"
                self._record_failure(reason)
                self._set_state(EngineState.RECONNECTING, reason)
                self._close_socket()
                continue
            if now - max(last_health, ready_since) < self._health_check_interval:
                continue
            if probe_started is not None:
                continue
            try:
                ping = getattr(socket, "ping", None)
                with self._state_lock:
                    self._health_probe_started_at = now
                if callable(ping):
                    ping()
                else:
                    socket.send(json.dumps({"type": "ping"}))
            except Exception as error:
                reason = f"health_probe_failed: {error}"
                self._record_failure(reason)
                self._set_state(EngineState.RECONNECTING, reason)
                self._close_socket()
                continue

    def handle_message(self, message: str | bytes | dict[str, Any]) -> None:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        if isinstance(message, str):
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                return
        else:
            data = message
        if not isinstance(data, dict):
            return

        event_type = str(data.get("type", ""))
        if event_type == "session.updated":
            now = time.monotonic()
            with self._state_lock:
                self._ready_since = now
                self._last_health_at = now
                self._health_probe_started_at = None
                suspended_reason = (
                    self._last_failure_reason if self._corpus_suspended else ""
                )
                active_socket = self._socket
            if active_socket is not None and hasattr(active_socket, "settimeout"):
                active_socket.settimeout(self._send_timeout)
            reason = (
                f"corpus_suspended: {suspended_reason}"
                if suspended_reason
                else ""
            )
            self._set_state(EngineState.READY, reason)
            return
        if event_type == "session.finished":
            self._finish_drained.set()
            return
        if event_type == "pong":
            self._mark_healthy()
            return
        if event_type == "input_audio_buffer.speech_started":
            self._sentence_id += 1
            self._source_text = ""
            self._target_text = ""
            self._has_active_response = False
            self._turn_started_at = time.monotonic()
            self._emit_subtitle(is_final=False)
            self._mark_healthy()
            return
        if event_type.startswith("conversation.item.input_audio_transcription"):
            if self._sentence_id == 0:
                self._sentence_id = 1
                self._turn_started_at = time.monotonic()
            text = data.get("text")
            transcript = data.get("transcript")
            delta = data.get("delta")
            if text is not None:
                self._source_text = str(text)
            elif transcript is not None:
                self._source_text = str(transcript)
            elif delta is not None:
                self._source_text += str(delta)
            self._emit_subtitle(is_final=False)
            self._mark_healthy()
            return
        if event_type in {"response.text.delta", "response.audio_transcript.delta"}:
            self._has_active_response = True
            self._target_text += str(data.get("delta") or data.get("text") or "")
            self._emit_subtitle(is_final=False)
            self._mark_healthy()
            return
        if event_type in {"response.text.done", "response.audio_transcript.done"}:
            self._has_active_response = True
            text = data.get("text") or data.get("transcript")
            if text is not None:
                self._target_text = str(text)
            self._emit_subtitle(is_final=False)
            self._mark_healthy()
            return
        if event_type == "response.done":
            if self._has_active_response or self._source_text or self._target_text:
                self._has_active_response = False
                self._emit_subtitle(is_final=True)
            self._mark_healthy()
            return
        if event_type == "error":
            error_data = data.get("error", data)
            if isinstance(error_data, dict):
                code = error_data.get("code", "")
                message_text = error_data.get("message", "")
                reason = f"{code}: {message_text}".strip(": ")
                status_code = error_data.get("status_code") or data.get("status_code")
            else:
                reason = str(error_data)
                status_code = data.get("status_code")
            state = classify_engine_error(
                reason,
                status_code=int(status_code) if status_code else None,
            )
            self._record_failure(reason)
            self._set_state(state, reason)
            self._emit_structured(
                self.on_error,
                EngineErrorEvent(
                    window_id=self.window_id,
                    channel_id=self.channel_id,
                    code=str(error_data.get("code", "")) if isinstance(error_data, dict) else "error",
                    message=reason,
                    recoverable=state not in TERMINAL_STATES,
                ),
            )
            self._close_socket()

    def send_audio_chunk(self, pcm_bytes: bytes) -> bool:
        if self.current_state != EngineState.READY or not pcm_bytes:
            return False
        frame = bytes(pcm_bytes)
        commit_after_frame = self._observe_audio_energy(frame)
        try:
            self._audio_queue.put_nowait(frame)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
                self.dropped_audio_frames += 1
            except queue.Empty:
                pass
            try:
                self._audio_queue.put_nowait(frame)
            except queue.Full:
                return False
        if commit_after_frame:
            try:
                self._audio_queue.put_nowait(None)
            except queue.Full:
                try:
                    dropped = self._audio_queue.get_nowait()
                    if dropped is not None:
                        self.dropped_audio_frames += 1
                except queue.Empty:
                    pass
                try:
                    self._audio_queue.put_nowait(None)
                except queue.Full:
                    pass
        return True

    def send_audio(self, pcm_bytes: bytes) -> bool:
        """Backward-compatible name used by the Phase 1 manager and tests."""

        return self.send_audio_chunk(pcm_bytes)

    def _observe_audio_energy(self, pcm_bytes: bytes) -> bool:
        """Return whether the current 50ms frame closes an auto-commit turn."""

        if not self.model.casefold().startswith("qwen3.8"):
            return False
        rms = calc_rms(pcm_bytes)
        duration_sec = max(0.001, len(pcm_bytes) / (SAMPLE_RATE * 2))
        with self._audio_energy_lock:
            if rms < AUTO_COMMIT_LOW_ENERGY_RMS:
                should_commit = (
                    self._continuous_speech_sec >= AUTO_COMMIT_PAUSE_SEC - 1e-6
                )
                self._continuous_speech_sec = 0.0
                return should_commit
            self._continuous_speech_sec += duration_sec
            if self._continuous_speech_sec >= AUTO_COMMIT_MAX_SEC - 1e-6:
                self._continuous_speech_sec = 0.0
                return True
        return False

    def commit_turn(self) -> bool:
        if self.current_state != EngineState.READY:
            return False
        with self._socket_lock:
            socket = self._socket
            if socket is None:
                return False
            try:
                socket.send(json.dumps({"type": "input_audio_buffer.commit"}))
                with self._audio_energy_lock:
                    self._commit_turn_count += 1
                return True
            except Exception:
                self._close_socket()
                return False

    def _audio_sender_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                pcm_bytes = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if pcm_bytes is None:
                self.commit_turn()
                continue
            if self.current_state != EngineState.READY:
                continue
            with self._socket_lock:
                socket = self._socket
                if socket is None:
                    continue
                payload = {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm_bytes).decode("ascii"),
                }
                try:
                    socket.send(json.dumps(payload, separators=(",", ":")))
                except Exception:
                    self._close_socket()

    def _close_socket(self) -> None:
        with self._socket_lock:
            socket, self._socket = self._socket, None
            already_finished = self._finish_sent
            if socket is not None and self._session_update_count and not already_finished:
                self._finish_sent = True
        if socket is None:
            return
        if self._session_update_count and not already_finished:
            try:
                socket.send(json.dumps({"type": "session.finish"}))
            except Exception:
                pass
            if self._session_finish_drain > 0 and not self._stop_event.is_set():
                self._finish_drained.wait(
                    timeout=min(self._session_finish_drain, SESSION_FINISH_DRAIN_SEC)
                )
        try:
            socket.close()
        except Exception:
            pass

    def close(self) -> None:
        with self._state_lock:
            if not self._running and self._state == EngineState.CLOSED:
                return
            self._running = False
        should_drain = False
        with self._socket_lock:
            socket = self._socket
            if socket is not None and self._session_update_count and not self._finish_sent:
                self._finish_sent = True
                try:
                    socket.send(json.dumps({"type": "session.finish"}))
                    should_drain = self._session_finish_drain > 0
                except Exception:
                    pass
        if should_drain:
            self._finish_drained.wait(
                timeout=min(self._session_finish_drain, SESSION_FINISH_DRAIN_SEC)
            )
        self._stop_event.set()
        self._close_socket()
        current = threading.current_thread()
        for thread in (self._reader_thread, self._tx_thread, self._health_thread):
            if thread is not None and thread is not current and thread.is_alive():
                thread.join(timeout=self._thread_join_timeout)
        self._reader_thread = None
        self._tx_thread = None
        self._health_thread = None
        self._set_state(EngineState.CLOSED)


QwenRealtimeEngine = QwenRealtimeAdapter
