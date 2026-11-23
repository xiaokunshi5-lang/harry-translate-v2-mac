"""Client-aware session gating, concurrency and controller lifecycle."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import random
import re
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.engines.base import EngineState, RealtimeTranslatorEngine
from app.engines.registry import EngineRegistry
from app.transcript_cleaner import SingleSessionCleaner

SUPPORTED_CHANNELS = ("main", "en", "zh")
MAX_OVERLAY_WINDOWS = 1
EVENT_QUEUE_MAXSIZE = 64
FORBIDDEN_BUSINESS_KEYS = {
    "cost",
    "cost_rmb",
    "input_tokens",
    "output_tokens",
    "summary_" + "tokens",
    "total_tokens",
    "usage",
    "usage_stats",
}

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff]")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")
_LATIN_RE = re.compile(r"[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u1e00-\u1eff]")
_DIGIT_RE = re.compile(r"[0-9]")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_LATIN_TARGET_LANGS = {"en", "fr", "es", "it", "de", "pt"}


def _has_letter(text: str) -> bool:
    return any(char.isalpha() for char in text)


def _is_acronym_or_technical_token(text: str) -> bool:
    trimmed = text.strip()
    if not trimmed:
        return False
    if _DIGIT_RE.search(trimmed):
        return True
    ascii_letters = "".join(_ASCII_LETTER_RE.findall(trimmed))
    return (
        2 <= len(ascii_letters) <= 16
        and ascii_letters == ascii_letters.upper()
        and not _CJK_RE.search(trimmed)
    )


def sanitize_target_for_lang(raw_trans: str, raw_source: str, target_lang: str) -> str:
    """Suppress transient same-language echo while preserving valid target scripts."""

    if not raw_trans:
        return ""
    lang = (target_lang or "").strip().casefold().split("-")[0]

    if lang == "zh":
        if (
            _CJK_RE.search(raw_trans)
            or not _has_letter(raw_trans)
            or _CJK_RE.search(raw_source)
        ):
            return raw_trans
        if _is_acronym_or_technical_token(raw_trans):
            return raw_trans
        return ""

    if lang in _LATIN_TARGET_LANGS:
        if _LATIN_RE.search(raw_trans):
            return raw_trans
        if _CJK_RE.search(raw_trans):
            return ""
        return raw_trans

    if lang == "ja":
        if _KANA_RE.search(raw_trans) or (
            _CJK_RE.search(raw_trans) and not _HANGUL_RE.search(raw_trans)
        ):
            return raw_trans
        if not _has_letter(raw_trans) or _is_acronym_or_technical_token(raw_trans):
            return raw_trans
        return ""

    if lang == "ko":
        if (
            _HANGUL_RE.search(raw_trans)
            or not _has_letter(raw_trans)
            or _is_acronym_or_technical_token(raw_trans)
        ):
            return raw_trans
        return ""

    return raw_trans


def _apply_purity_to_event(
    event: dict[str, Any],
    *,
    target_lang: str,
) -> dict[str, Any]:
    if event.get("type") not in {"subtitle", "subtitle.delta", "subtitle.final"}:
        return event
    has_target_text = "target_text" in event
    has_target = "target" in event
    if not has_target_text and not has_target:
        return event
    source_text = str(
        event["source_text"] if "source_text" in event else event.get("source") or ""
    )
    raw_target = str(
        event["target_text"] if has_target_text else event.get("target") or ""
    )
    if not raw_target:
        return event
    cleaned = sanitize_target_for_lang(
        raw_target,
        source_text,
        str(event.get("target_lang") or target_lang),
    )
    if cleaned == raw_target:
        return event
    updated = dict(event)
    if has_target_text:
        updated["target_text"] = cleaned
    if has_target:
        updated["target"] = cleaned
    return updated


class RealtimeEngine(Protocol):
    session_update_count: int

    @property
    def current_state(self) -> EngineState: ...

    def connect(self) -> bool: ...

    def send_audio(self, pcm_bytes: bytes) -> bool: ...

    def close(self) -> None: ...

    def state_event(self) -> dict[str, Any]: ...


class ClientSocket(Protocol):
    async def send_text(self, message: str) -> None: ...

    async def close(self) -> None: ...


def assert_business_event(event: dict[str, Any]) -> None:
    """Reject metering keys from every business event before it reaches clients."""

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            forbidden = FORBIDDEN_BUSINESS_KEYS.intersection(value)
            if forbidden:
                raise ValueError(f"forbidden business fields: {sorted(forbidden)}")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(event)


def _direction_matches_session(
    bucket: str | None,
    lang_key: str | None,
    session_src: str,
    session_tgt: str,
) -> bool:
    if not bucket:
        return True
    src_norm = (session_src or "auto").strip().casefold()
    tgt_norm = (session_tgt or "").strip().casefold()
    key_norm = (lang_key or "").strip().casefold()
    if bucket == "terms_trans":
        if "->" not in key_norm:
            return True
        pair_src, pair_tgt = key_norm.split("->", 1)
        if tgt_norm and pair_tgt != tgt_norm:
            return False
        return src_norm in {"", "auto"} or pair_src == src_norm
    if bucket == "asr_fixes":
        if src_norm not in {"", "auto"}:
            return key_norm == src_norm
        return not tgt_norm or key_norm != tgt_norm
    if bucket == "target_fixes":
        return not tgt_norm or key_norm == tgt_norm
    return True


def _apply_glossary_to_event(
    glossary_engine: Any,
    event: dict[str, Any],
    *,
    source_lang: str,
    target_lang: str,
) -> dict[str, Any]:
    if event.get("type") not in {"subtitle", "subtitle.delta", "subtitle.final"}:
        return event
    source_text = str(
        event["source_text"] if "source_text" in event else event.get("source") or ""
    )
    target_text = str(
        event["target_text"] if "target_text" in event else event.get("target") or ""
    )
    if not source_text and not target_text:
        return event
    fixed_source, fixed_target = glossary_engine.apply_fixes(
        source_text,
        target_text,
        source_lang=str(event.get("source_lang") or source_lang),
        target_lang=str(event.get("target_lang") or target_lang),
    )
    if fixed_source == source_text and fixed_target == target_text:
        return event
    updated = dict(event)
    if "source_text" in updated:
        updated["source_text"] = fixed_source
    if "source" in updated:
        updated["source"] = fixed_source
    if "target_text" in updated:
        updated["target_text"] = fixed_target
    if "target" in updated:
        updated["target"] = fixed_target
    return updated


class SessionManager:
    """Own connected clients and gate every audio frame before upstream send."""

    def __init__(
        self,
        needs_setup: Callable[[], bool],
        *,
        glossary_engine: Any | None = None,
        transcript_cleaner: SingleSessionCleaner | None = None,
        patch_worker: Any | None = None,
        summary_worker: Any | None = None,
        audio_source: str = "microphone",
    ) -> None:
        self._needs_setup = needs_setup
        self._glossary_engine = glossary_engine
        self._transcript_cleaner = transcript_cleaner or SingleSessionCleaner()
        self._patch_worker = patch_worker
        self._summary_worker = summary_worker
        self._audio_source = audio_source or "microphone"
        self._next_sent_id = 0
        self._channel_langs: dict[str, tuple[str, str]] = {
            "main": ("auto", "zh"),
            "en": ("auto", "en"),
            "zh": ("auto", "zh"),
        }
        self.connected_clients: dict[str, set[ClientSocket]] = {
            channel: set() for channel in SUPPORTED_CHANNELS
        }
        self._engines: dict[str, RealtimeEngine] = {}
        self._controllers: dict[str, SessionController] = {}
        self._active_channel: str | None = None
        self._client_lock = threading.RLock()
        self._event_lock = threading.RLock()
        self._events: dict[str, deque[dict[str, Any]]] = {
            channel: deque(maxlen=3) for channel in SUPPORTED_CHANNELS
        }
        self._preloop_events: dict[str, deque[tuple[int | None, dict[str, Any]]]] = {
            channel: deque() for channel in SUPPORTED_CHANNELS
        }
        self._channel_generations: dict[str, int] = {
            channel: 0 for channel in SUPPORTED_CHANNELS
        }
        self._event_queues: dict[
            str, asyncio.Queue[tuple[int | None, dict[str, Any]]]
        ] = {}
        self._event_workers: set[asyncio.Task[Any]] = set()
        self._broadcast_locks = {
            channel: asyncio.Lock() for channel in SUPPORTED_CHANNELS
        }
        self._loop: asyncio.AbstractEventLoop | None = None
        self.paused = False
        self.removed_client_count = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        if self._event_queues:
            return
        for channel in SUPPORTED_CHANNELS:
            event_queue: asyncio.Queue[tuple[int | None, dict[str, Any]]] = (
                asyncio.Queue(maxsize=EVENT_QUEUE_MAXSIZE)
            )
            self._event_queues[channel] = event_queue
            worker = asyncio.create_task(self._event_worker(channel, event_queue))
            self._event_workers.add(worker)
            while self._preloop_events[channel]:
                self._enqueue_event(channel, self._preloop_events[channel].popleft())

    def add_client(self, channel: str, client: ClientSocket) -> int:
        if channel not in self.connected_clients:
            raise ValueError(f"unsupported channel: {channel}")
        with self._client_lock:
            self.connected_clients[channel].add(client)
            return len(self.connected_clients[channel])

    def disconnect_client(self, channel: str, client: ClientSocket) -> int:
        if channel not in self.connected_clients:
            return 0
        with self._client_lock:
            clients = self.connected_clients[channel]
            if client in clients:
                clients.discard(client)
                self.removed_client_count += 1
            return len(clients)

    def active_client_count(self, channel: str) -> int:
        with self._client_lock:
            return len(self.connected_clients.get(channel, set()))

    def total_active_clients(self) -> int:
        with self._client_lock:
            return sum(len(clients) for clients in self.connected_clients.values())

    def needs_setup(self) -> bool:
        try:
            return bool(self._needs_setup())
        except Exception:
            return True

    def register_engine(self, channel: str, engine: RealtimeEngine) -> None:
        if channel not in self.connected_clients:
            raise ValueError(f"unsupported channel: {channel}")
        self._engines[channel] = engine

    def unregister_engine(self, channel: str, engine: RealtimeEngine) -> None:
        if self._engines.get(channel) is engine:
            self._engines.pop(channel, None)

    def register_controller(self, channel: str, controller: SessionController) -> None:
        if channel not in self.connected_clients:
            raise ValueError(f"unsupported channel: {channel}")
        self._controllers[channel] = controller

    def controller(self, channel: str) -> SessionController | None:
        return self._controllers.get(channel)

    @property
    def active_channel(self) -> str | None:
        return self._active_channel

    async def activate_channel(self, channel: str) -> None:
        """Close every other upstream engine before activating one channel."""

        if channel not in self.connected_clients:
            raise ValueError(f"unsupported channel: {channel}")
        for other_channel, controller in list(self._controllers.items()):
            if other_channel != channel:
                await controller.stop()
        self._active_channel = channel

    async def deactivate_channel_if_idle(self, channel: str) -> None:
        if self.active_client_count(channel) == 0 and self._active_channel == channel:
            self._active_channel = None

    def should_stream_audio(self, channel: str) -> bool:
        if self.paused or self.active_client_count(channel) == 0:
            return False
        if self.needs_setup():
            return False
        engine = self._engines.get(channel)
        return engine is not None and engine.current_state == EngineState.READY

    def route_audio_frame(self, channel: str, pcm_bytes: bytes) -> bool:
        if not self.should_stream_audio(channel):
            return False
        engine = self._engines.get(channel)
        if engine is None:
            return False
        return bool(engine.send_audio(pcm_bytes))

    def current_state_event(self, channel: str) -> dict[str, Any]:
        engine = self._engines.get(channel)
        if engine is None:
            return {
                "type": "engine_state",
                "state": EngineState.IDLE.value,
                "health_status": EngineState.IDLE.value,
                "reason": "needs_setup" if self.needs_setup() else "waiting_for_client",
            }
        return engine.state_event()

    async def broadcast_to_channel(
        self,
        channel: str,
        event: dict[str, Any],
        *,
        timeout: float = 0.25,
    ) -> int:
        assert_business_event(event)
        with self._client_lock:
            clients = list(self.connected_clients.get(channel, set()))
        if not clients:
            return 0
        message = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        results = await asyncio.gather(
            *[
                self._send_safe(channel, client, message, timeout=timeout)
                for client in clients
            ],
            return_exceptions=True,
        )
        return sum(1 for result in results if result is True)

    async def _send_safe(
        self,
        channel: str,
        client: ClientSocket,
        message: str,
        *,
        timeout: float,
    ) -> bool:
        try:
            await asyncio.wait_for(client.send_text(message), timeout=timeout)
            return True
        except Exception:
            self.disconnect_client(channel, client)
            try:
                await asyncio.wait_for(client.close(), timeout=0.05)
            except Exception:
                pass
            return False

    def set_glossary_engine(self, engine: Any | None) -> None:
        self._glossary_engine = engine

    def set_patch_worker(self, patch_worker: Any | None) -> None:
        self._patch_worker = patch_worker

    def set_summary_worker(self, summary_worker: Any | None) -> None:
        self._summary_worker = summary_worker

    async def apply_summary_settings(
        self,
        worker: Any,
        settings: dict[str, Any],
    ) -> Any:
        async with self._broadcast_locks["main"]:
            return await worker.apply_settings(settings)

    def set_audio_source(self, audio_source: str) -> None:
        normalized = (audio_source or "").strip()
        if normalized:
            self._audio_source = normalized

    @property
    def audio_source(self) -> str:
        return self._audio_source

    def set_channel_languages(
        self,
        channel: str,
        source_lang: str,
        target_lang: str,
    ) -> None:
        if channel in self.connected_clients:
            previous = self._channel_langs.get(channel)
            next_languages = (source_lang or "auto", target_lang)
            self._channel_langs[channel] = next_languages
            if previous is not None and previous != next_languages:
                self.clear_pending_patches()
                self.clear_pending_summaries()

    def channel_generation(self, channel: str) -> int:
        with self._event_lock:
            return self._channel_generations.get(channel, 0)

    def reset_channel_session(self, channel: str) -> int:
        """Synchronous non-concurrent reset used by direct unit scenarios."""

        if channel not in self.connected_clients:
            return 0
        with self._event_lock:
            next_gen = self._channel_generations.get(channel, 0) + 1
            self._channel_generations[channel] = next_gen
            self._reset_transcript_state_unlocked()
            self._events[channel].clear()
            self._preloop_events[channel].clear()
            event_queue = self._event_queues.get(channel)
            if event_queue is not None:
                while not event_queue.empty():
                    try:
                        event_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            return next_gen

    async def reset_channel_session_async(self, channel: str) -> int:
        """Atomically invalidate pending patches before releasing channel state."""

        if channel not in self.connected_clients:
            return 0
        async with self._broadcast_locks[channel]:
            generation = self.reset_channel_session(channel)
            await self._broadcast_cleared_summary_locked(channel)
            return generation

    def reset_transcript_state(self) -> None:
        """Synchronous non-concurrent reset; production paths use the async form."""

        with self._event_lock:
            self._next_sent_id = 0
            self._transcript_cleaner.reset()
            self.clear_pending_patches()
            self.clear_pending_summaries()
            generation = self._channel_generations.get("main", 0) + 1
            self._channel_generations["main"] = generation

    async def reset_transcript_state_async(self, channel: str = "main") -> int:
        if channel not in self.connected_clients:
            return 0
        async with self._broadcast_locks[channel]:
            with self._event_lock:
                generation = self._channel_generations.get(channel, 0) + 1
                self._channel_generations[channel] = generation
                self._reset_transcript_state_unlocked()
            await self._broadcast_cleared_summary_locked(channel)
            return generation

    def _reset_transcript_state_unlocked(self) -> None:
        self._next_sent_id = 0
        self._transcript_cleaner.reset()
        self.clear_pending_patches()
        self.clear_pending_summaries()

    def clear_pending_patches(self) -> int:
        worker = self._patch_worker
        clear = getattr(worker, "clear_pending", None)
        if not callable(clear):
            return 0
        try:
            return int(clear() or 0)
        except Exception:
            return 0

    def clear_pending_summaries(self) -> int:
        worker = self._summary_worker
        clear = getattr(worker, "invalidate_session", None)
        if not callable(clear):
            return 0
        try:
            return int(clear() or 0)
        except Exception:
            return 0

    async def _broadcast_cleared_summary_locked(self, channel: str) -> int:
        if channel != "main":
            return 0
        worker = self._summary_worker
        settings = getattr(worker, "settings", None)
        snapshot = getattr(worker, "snapshot", None)
        if getattr(settings, "enabled", False) is not True or not callable(snapshot):
            return 0
        event = {"type": "summary.update", **snapshot()}
        return await self.broadcast_to_channel(channel, event)

    def push_glossary_update(
        self,
        glossary_store: Any,
        *,
        bucket: str | None = None,
        lang_key: str | None = None,
    ) -> list[str]:
        updated: list[str] = []
        for channel, engine in list(self._engines.items()):
            if engine.current_state != EngineState.READY:
                continue
            live_update = getattr(engine, "live_update_session", None)
            if not callable(live_update):
                continue
            default_src, default_tgt = self._channel_langs.get(channel, ("auto", channel))
            src = str(getattr(engine, "source_lang", "") or default_src)
            tgt = str(getattr(engine, "target_lang", "") or default_tgt)
            if not _direction_matches_session(bucket, lang_key, src, tgt):
                continue
            phrases = glossary_store.get_budgeted_corpus_phrases(
                source_lang=src,
                target_lang=tgt,
            )
            if live_update(src, tgt, phrases):
                updated.append(channel)
        return updated

    async def publish_event(
        self,
        channel: str,
        event: dict[str, Any],
        *,
        generation: int | None = None,
    ) -> None:
        assert_business_event(event)
        async with self._broadcast_locks[channel]:
            with self._event_lock:
                current_generation = self._channel_generations.get(channel, 0)
                if generation is not None and generation != current_generation:
                    return
                effective_generation = current_generation
            engine = self._engines.get(channel)
            default_src, default_tgt = self._channel_langs.get(
                channel,
                ("auto", channel),
            )
            src = str(getattr(engine, "source_lang", "") or default_src)
            tgt = str(getattr(engine, "target_lang", "") or default_tgt)
            if self._glossary_engine is not None:
                event = _apply_glossary_to_event(
                    self._glossary_engine,
                    event,
                    source_lang=src,
                    target_lang=tgt,
                )
            event = _apply_purity_to_event(event, target_lang=tgt)
            event = self._finalize_subtitle_event(
                event,
                channel=channel,
                source_lang=src,
                target_lang=tgt,
                generation=effective_generation,
            )
            with self._event_lock:
                self._events[channel].append(dict(event))
            await self.broadcast_to_channel(channel, event)

    def _finalize_subtitle_event(
        self,
        event: dict[str, Any],
        *,
        channel: str,
        source_lang: str,
        target_lang: str,
        generation: int,
    ) -> dict[str, Any]:
        is_final = event.get("is_final") is True or event.get("type") == "subtitle.final"
        if event.get("type") not in {"subtitle", "subtitle.final"} or not is_final:
            return event
        if event.get("sent_id") is not None and event.get("source_hash"):
            return event

        source = str(
            event["source_text"] if "source_text" in event else event.get("source") or ""
        )
        target = str(
            event["target_text"] if "target_text" in event else event.get("target") or ""
        )
        with self._event_lock:
            self._next_sent_id += 1
            sent_id = self._next_sent_id
            (
                cleaned_source,
                cleaned_target,
                merged_with_prev_id,
                para_id,
            ) = self._transcript_cleaner.finalize_utterance(
                source,
                target,
                sent_id=sent_id,
                source_lang=source_lang,
                target_lang=target_lang,
            )

        updated = dict(event)
        updated.update(
            {
                "type": "subtitle",
                "window_id": "main",
                "channel_id": "main",
                "audio_source": str(
                    event.get("audio_source") or self._audio_source
                ),
                "source": cleaned_source,
                "target": cleaned_target,
                "source_text": cleaned_source,
                "target_text": cleaned_target,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "is_final": True,
                "sent_id": sent_id,
                "source_hash": hashlib.sha256(
                    cleaned_source.encode("utf-8")
                ).hexdigest()[:12],
                "para_id": para_id,
            }
        )
        if merged_with_prev_id is None:
            updated.pop("merged_with_prev_id", None)
        else:
            updated["merged_with_prev_id"] = merged_with_prev_id
        self._enqueue_patch_task(
            updated,
            channel=channel,
            source_lang=source_lang,
            target_lang=target_lang,
            generation=generation,
        )
        self._enqueue_summary_task(
            updated,
            channel=channel,
            generation=generation,
        )
        return updated

    def _enqueue_patch_task(
        self,
        event: dict[str, Any],
        *,
        channel: str,
        source_lang: str,
        target_lang: str,
        generation: int,
    ) -> None:
        worker = self._patch_worker
        enqueue = getattr(worker, "enqueue_final_utterance", None)
        if not callable(enqueue) or channel != "main":
            return
        merged_with_prev_id = event.get("merged_with_prev_id")
        patch_sent_id = (
            merged_with_prev_id
            if isinstance(merged_with_prev_id, int)
            else event.get("sent_id")
        )
        try:
            enqueue(
                window_id="main",
                sent_id=int(patch_sent_id),
                channel="main",
                source_lang=source_lang,
                target_lang=target_lang,
                source_text=str(event.get("source_text") or ""),
                target_text=str(event.get("target_text") or ""),
                source_hash=str(event.get("source_hash") or ""),
                generation=generation,
            )
        except Exception:
            # Correction ingress must never affect final subtitle delivery.
            return

    def _enqueue_summary_task(
        self,
        event: dict[str, Any],
        *,
        channel: str,
        generation: int,
    ) -> None:
        if channel != "main":
            return
        worker = self._summary_worker
        settings = getattr(worker, "settings", None)
        if getattr(settings, "enabled", False) is not True:
            return
        enqueue = getattr(worker, "enqueue_final", None)
        if not callable(enqueue):
            return
        try:
            enqueue(event, generation=generation)
        except Exception:
            # Summary ingress must never affect final subtitle delivery.
            return

    async def publish_patch_event(
        self,
        event: dict[str, Any],
        generation: int,
        *,
        channel: str = "main",
    ) -> int:
        """Broadcast one optimistic-lock patch through the single main channel."""

        assert_business_event(event)
        async with self._broadcast_locks[channel]:
            with self._event_lock:
                if generation != self._channel_generations.get(channel, 0):
                    return 0
            return await self.broadcast_to_channel(channel, event)

    async def publish_summary_event(
        self,
        event: dict[str, Any],
        session_generation: int,
        generation_epoch: int,
        *,
        channel: str = "main",
    ) -> int:
        """Publish a summary only while its session and invalidation token match."""

        if channel != "main":
            return 0
        async with self._broadcast_locks[channel]:
            with self._event_lock:
                if session_generation != self._channel_generations.get(channel, 0):
                    return 0
            worker = self._summary_worker
            current = getattr(worker, "is_publication_current", None)
            if not callable(current) or not current(
                session_generation,
                generation_epoch,
            ):
                return 0
            return await self.broadcast_to_channel(channel, event)

    def publish_from_thread(
        self,
        channel: str,
        event: dict[str, Any],
        *,
        generation: int | None = None,
    ) -> None:
        assert_business_event(event)
        with self._event_lock:
            if generation is not None and generation != self._channel_generations.get(
                channel, 0
            ):
                return
            loop = self._loop
            if loop is None or not loop.is_running():
                self._preloop_events[channel].append((generation, dict(event)))
                return
        loop.call_soon_threadsafe(
            self._enqueue_event,
            channel,
            (generation, dict(event)),
        )

    def _enqueue_event(
        self,
        channel: str,
        item: tuple[int | None, dict[str, Any]],
    ) -> None:
        generation, _event = item
        with self._event_lock:
            if generation is not None and generation != self._channel_generations.get(
                channel, 0
            ):
                return
        event_queue = self._event_queues.get(channel)
        if event_queue is None:
            self._preloop_events[channel].append(item)
            return
        if event_queue.full():
            try:
                event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            event_queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def _event_worker(
        self,
        channel: str,
        event_queue: asyncio.Queue[tuple[int | None, dict[str, Any]]],
    ) -> None:
        while True:
            generation, event = await event_queue.get()
            with self._event_lock:
                if generation is not None and generation != self._channel_generations.get(
                    channel, 0
                ):
                    continue
            await self.publish_event(channel, event, generation=generation)

    def pending_events(self, channel: str) -> list[dict[str, Any]]:
        with self._event_lock:
            return list(self._events[channel])

    async def stop_all(self) -> None:
        for controller in list(self._controllers.values()):
            await controller.stop()
        workers = list(self._event_workers)
        for task in workers:
            task.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._event_workers.clear()


class SessionController:
    """Single lifecycle entry that creates at most one upstream session."""

    def __init__(
        self,
        channel: str,
        manager: SessionManager,
        engine_factory: Callable[[], RealtimeEngine],
    ) -> None:
        self.channel = channel
        self.manager = manager
        self.engine_factory = engine_factory
        self.engine: RealtimeEngine | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> bool:
        async with self._lock:
            return await self._start_unlocked()

    async def _start_unlocked(self) -> bool:
        if self.manager.active_client_count(self.channel) == 0:
            return False
        if self.manager.needs_setup():
            return False
        reusable_states = {
            EngineState.CONNECTING,
            EngineState.READY,
            EngineState.RECONNECTING,
        }
        if self.engine is not None and self.engine.current_state in reusable_states:
            return True
        await self._close_engine()
        await self.manager.reset_transcript_state_async(self.channel)
        engine = self.engine_factory()
        self.engine = engine
        self.manager.register_engine(self.channel, engine)
        await asyncio.to_thread(engine.connect)
        return engine.current_state not in {EngineState.AUTH_ERROR, EngineState.CLOSED}

    async def reload_session(self) -> bool:
        async with self._lock:
            await self._close_engine()
            await self.manager.reset_channel_session_async(self.channel)
            if self.manager.active_client_count(self.channel) > 0:
                await self.manager.broadcast_to_channel(
                    self.channel,
                    {"type": "session_reset", "channel": self.channel},
                )
            return await self._start_unlocked()

    async def stop(self) -> None:
        async with self._lock:
            await self._close_engine()

    async def stop_if_idle(self) -> None:
        async with self._lock:
            if self.manager.active_client_count(self.channel) == 0:
                await self._close_engine()

    async def _close_engine(self) -> None:
        engine, self.engine = self.engine, None
        if engine is None:
            return
        self.manager.unregister_engine(self.channel, engine)
        await asyncio.to_thread(engine.close)

    @property
    def session_update_count(self) -> int:
        return self.engine.session_update_count if self.engine is not None else 0


class TokenBucketLimiter:
    """Thread-safe shared RPM guard for connection creation and reconnects."""

    def __init__(
        self,
        rpm: int = 10,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rpm <= 0:
            raise ValueError("rpm_must_be_positive")
        self.capacity = float(rpm)
        self.refill_per_second = float(rpm) / 60.0
        self._tokens = float(rpm)
        self._updated_at = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)
        self._updated_at = now

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_seconds = (1.0 - self._tokens) / self.refill_per_second
            await self._sleep(max(0.001, wait_seconds))


@dataclasses.dataclass(slots=True)
class SessionSlot:
    """One active window identity with an optional independent upstream engine."""

    window_id: str
    provider: str
    audio_source: str
    source_lang: str
    target_lang: str
    engine: RealtimeTranslatorEngine | None = None
    clients: set[ClientSocket] = dataclasses.field(default_factory=set)


class SessionPool:
    """Compatibility pool restricted to the single main overlay window."""

    MAX_WINDOWS = MAX_OVERLAY_WINDOWS

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        needs_setup: Callable[[], bool] = lambda: False,
        rpm: int = 10,
        stagger_base_sec: float = 0.15,
        jitter_sec: float = 0.08,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        glossary_engine: Any | None = None,
    ) -> None:
        if not 0.0 <= jitter_sec <= 0.08:
            raise ValueError("jitter_must_not_exceed_80ms")
        self.registry = registry
        self._needs_setup = needs_setup
        self._slots: dict[str, SessionSlot] = {}
        self._lock = threading.RLock()
        self._limiter = TokenBucketLimiter(rpm, sleep=sleep)
        self._stagger_base_sec = max(0.0, stagger_base_sec)
        self._jitter_sec = max(0.0, jitter_sec)
        self._event_callback = event_callback
        self._sleep = sleep
        self._glossary_engine = glossary_engine
        self.removed_client_count = 0

    def needs_setup(self) -> bool:
        try:
            return bool(self._needs_setup())
        except Exception:
            return True

    def window_ids(self) -> list[str]:
        with self._lock:
            return list(self._slots)

    def slot(self, window_id: str) -> SessionSlot | None:
        with self._lock:
            return self._slots.get(window_id)

    def register_window(
        self,
        window_id: str,
        *,
        audio_source: str,
        source_lang: str,
        target_lang: str,
        provider: str = "qwen",
    ) -> SessionSlot:
        if not window_id.strip():
            raise ValueError("window_id_required")
        self.registry.validate_language_pair(provider, source_lang, target_lang)
        with self._lock:
            if window_id in self._slots:
                raise ValueError("window_already_registered")
            if len(self._slots) >= self.MAX_WINDOWS:
                raise ValueError("window_limit_reached")
            slot = SessionSlot(
                window_id=window_id,
                provider=provider,
                audio_source=audio_source,
                source_lang=source_lang,
                target_lang=target_lang,
            )
            self._slots[window_id] = slot
            return slot

    def add_client(self, window_id: str, client: ClientSocket) -> int:
        with self._lock:
            slot = self._require_slot(window_id)
            slot.clients.add(client)
            return len(slot.clients)

    async def remove_client(self, window_id: str, client: ClientSocket) -> int:
        close_engine = False
        with self._lock:
            slot = self._slots.get(window_id)
            if slot is None:
                return 0
            if client in slot.clients:
                slot.clients.discard(client)
                self.removed_client_count += 1
            if not slot.clients and slot.engine is not None:
                close_engine = True
            count = len(slot.clients)
        if close_engine:
            await self.stop_window(window_id)
        return count

    def should_stream_audio(self, window_id: str) -> bool:
        if self.needs_setup():
            return False
        with self._lock:
            slot = self._slots.get(window_id)
            return bool(
                slot
                and slot.clients
                and slot.engine is not None
                and slot.engine.current_state == EngineState.READY
            )

    def route_audio_frame(self, window_id: str, pcm_bytes: bytes) -> bool:
        if not self.should_stream_audio(window_id):
            return False
        with self._lock:
            slot = self._slots.get(window_id)
            engine = slot.engine if slot is not None else None
        if engine is None:
            return False
        send = getattr(engine, "send_audio_chunk", None) or getattr(engine, "send_audio", None)
        return bool(send(pcm_bytes)) if callable(send) else False

    async def start_window(self, window_id: str) -> bool:
        if self.needs_setup():
            return False
        with self._lock:
            slot = self._require_slot(window_id)
            if not slot.clients:
                return False
            if slot.engine is not None and slot.engine.current_state in {
                EngineState.CONNECTING,
                EngineState.READY,
                EngineState.RECONNECTING,
            }:
                return True
            provider = slot.provider
            source_lang = slot.source_lang
            target_lang = slot.target_lang
            window_index = list(self._slots).index(window_id)

        if window_index:
            jitter = random.Random(f"{window_id}:initial").uniform(0.0, self._jitter_sec)
            await self._sleep(window_index * self._stagger_base_sec + jitter)
        await self._limiter.acquire()
        engine = self._create_engine(window_id, provider, source_lang, target_lang)
        with self._lock:
            slot = self._require_slot(window_id)
            old_engine = slot.engine
            slot.engine = engine
        if old_engine is not None:
            await asyncio.to_thread(old_engine.close)
        await asyncio.to_thread(engine.connect)
        return engine.current_state not in {
            EngineState.AUTH_ERROR,
            EngineState.CLOSED,
        }

    async def switch_engine(
        self,
        window_id: str,
        *,
        provider: str,
        source_lang: str,
        target_lang: str,
    ) -> RealtimeTranslatorEngine:
        # Validation must happen before any current engine is closed.
        self.registry.validate_language_pair(provider, source_lang, target_lang)
        await self._limiter.acquire()
        replacement = self._create_engine(window_id, provider, source_lang, target_lang)
        with self._lock:
            slot = self._require_slot(window_id)
            old_engine = slot.engine
            slot.provider = provider
            slot.source_lang = source_lang
            slot.target_lang = target_lang
            slot.engine = replacement
        if old_engine is not None:
            await asyncio.to_thread(old_engine.close)
        if slot.clients and not self.needs_setup():
            await asyncio.to_thread(replacement.connect)
        return replacement

    async def reconnect_windows(self, window_ids: list[str]) -> None:
        for index, window_id in enumerate(window_ids):
            delay = index * self._stagger_base_sec
            if self._jitter_sec:
                delay += (index % 3) * self._jitter_sec / 3
            if delay:
                await self._sleep(delay)
            with self._lock:
                slot = self._slots.get(window_id)
                if slot is None or not slot.clients:
                    continue
                provider = slot.provider
                source_lang = slot.source_lang
                target_lang = slot.target_lang
            await self._limiter.acquire()
            replacement = self._create_engine(window_id, provider, source_lang, target_lang)
            with self._lock:
                slot = self._slots.get(window_id)
                if slot is None:
                    replacement.close()
                    continue
                old_engine = slot.engine
                slot.engine = replacement
            if old_engine is not None:
                await asyncio.to_thread(old_engine.close)
            await asyncio.to_thread(replacement.connect)

    async def stop_window(self, window_id: str) -> None:
        with self._lock:
            slot = self._slots.get(window_id)
            if slot is None:
                return
            engine, slot.engine = slot.engine, None
        if engine is not None:
            await asyncio.to_thread(engine.close)

    async def close_window(self, window_id: str) -> None:
        with self._lock:
            slot = self._slots.pop(window_id, None)
        if slot is not None:
            slot.clients.clear()
            if slot.engine is not None:
                await asyncio.to_thread(slot.engine.close)

    async def close_all(self) -> None:
        for window_id in self.window_ids():
            await self.close_window(window_id)

    def active_client_count(self, window_id: str) -> int:
        with self._lock:
            slot = self._slots.get(window_id)
            return len(slot.clients) if slot is not None else 0

    def total_active_clients(self) -> int:
        with self._lock:
            return sum(len(slot.clients) for slot in self._slots.values())

    def _require_slot(self, window_id: str) -> SessionSlot:
        slot = self._slots.get(window_id)
        if slot is None:
            raise ValueError(f"unknown_window: {window_id}")
        return slot

    def push_glossary_update(
        self,
        glossary_store: Any,
        *,
        bucket: str | None = None,
        lang_key: str | None = None,
    ) -> list[str]:
        updated: list[str] = []
        with self._lock:
            slots = list(self._slots.values())
        for slot in slots:
            engine = slot.engine
            if engine is None or engine.current_state != EngineState.READY:
                continue
            if not _direction_matches_session(
                bucket, lang_key, slot.source_lang, slot.target_lang
            ):
                continue
            phrases = glossary_store.get_budgeted_corpus_phrases(
                source_lang=slot.source_lang,
                target_lang=slot.target_lang,
            )
            if engine.live_update_session(slot.source_lang, slot.target_lang, phrases):
                updated.append(slot.window_id)
        return updated

    def _create_engine(
        self,
        window_id: str,
        provider: str,
        source_lang: str,
        target_lang: str,
    ) -> RealtimeTranslatorEngine:
        def on_event(event: dict[str, Any]) -> None:
            processed = (
                _apply_glossary_to_event(
                    self._glossary_engine,
                    event,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                if self._glossary_engine is not None
                else event
            )
            if self._event_callback is not None:
                try:
                    self._event_callback(window_id, processed)
                except Exception:
                    pass

        return self.registry.create_engine(
            provider,
            window_id=window_id,
            channel_id=window_id,
            source_lang=source_lang,
            target_lang=target_lang,
            event_callback=on_event,
        )
