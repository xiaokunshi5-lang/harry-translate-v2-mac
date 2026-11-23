"""Bounded, non-blocking silent correction worker for finalized subtitles."""

from __future__ import annotations

import asyncio
import dataclasses
import difflib
import hashlib
import json
import re
import ssl
import time
import urllib.request
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

import certifi

from app.config_store import KeyValidator
from app.transcript_cleaner import COMMON_WORDS, clean_speech

CORRECTION_QUEUE_MAXSIZE = 32
CORRECTION_TICK_SECONDS = 0.5
DEFAULT_CORRECTION_GAP_MS = 1500
DEFAULT_CORRECTION_MODEL = "qwen-turbo"
SUPPORTED_CORRECTION_MODELS = frozenset({"qwen-turbo", "qwen-flash"})
CORRECTION_MODES = frozenset({"llm", "rules"})
CORRECTION_TIMEOUT_SECONDS = 4.0
DASHSCOPE_TEXT_ENDPOINT = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)

_WORDS_RE = re.compile(r"[\w'’-]+")
_CAPITALIZED_ASCII_RE = re.compile(r"\b[A-Z][A-Za-z]*\b")
_LOWERCASE_ASCII_WORD_RE = re.compile(r"[a-z][a-z'-]{2,}")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SOURCE_SCRIPT_RUN_RE = re.compile(
    r"(?:[\u3040-\u30ff\u31f0-\u31ff]{2,})"
    r"|(?:[\u3400-\u4dbf\u4e00-\u9fff]{2,})"
    r"|(?:[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]{2,})"
)
_LATIN_TARGET_LANGS = frozenset({"en", "fr", "es", "it", "de"})


def _normalized_lang(value: str) -> str:
    return (value or "").strip().casefold().split("-")[0]


def _bounded_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _words(text: str) -> list[str]:
    """Match the verified upstream tokenization exactly."""

    return _WORDS_RE.findall(text.lower())


@dataclasses.dataclass(slots=True, frozen=True)
class CorrectionSettings:
    enabled: bool = True
    mode: str = "llm"
    model: str = DEFAULT_CORRECTION_MODEL
    gap_ms: int = DEFAULT_CORRECTION_GAP_MS

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any] | None) -> "CorrectionSettings":
        source = settings if isinstance(settings, Mapping) else {}
        mode = str(source.get("correction_mode") or "llm").strip().casefold()
        if mode not in CORRECTION_MODES:
            mode = "llm"
        model = str(
            source.get("correction_model") or DEFAULT_CORRECTION_MODEL
        ).strip()
        if model not in SUPPORTED_CORRECTION_MODELS:
            model = DEFAULT_CORRECTION_MODEL
        try:
            gap_ms = int(
                source.get("correction_gap_ms", DEFAULT_CORRECTION_GAP_MS)
            )
        except (TypeError, ValueError):
            gap_ms = DEFAULT_CORRECTION_GAP_MS
        gap_ms = min(max(gap_ms, 0), 10_000)
        return cls(
            enabled=source.get("correction_enabled") is True,
            mode=mode,
            model=model,
            gap_ms=gap_ms,
        )


@dataclasses.dataclass(slots=True)
class PatchTask:
    window_id: str
    sent_id: int
    channel: str
    source_lang: str
    target_lang: str
    source_text: str
    target_text: str
    source_hash: str
    created_at: float
    urgent: bool = False
    generation: int = 0


ModelClient = Callable[
    [PatchTask, tuple[str, ...], str],
    Awaitable[Mapping[str, Any] | None],
]
PatchPublisher = Callable[[dict[str, Any], int], Awaitable[None]]


def _contains_protected_term(text: str, terms: Iterable[str]) -> bool:
    folded = text.casefold()
    return any(term.casefold() in folded for term in terms if term)


def _shares_lexical_anchor(original: str, corrected: str) -> bool:
    if _CJK_RE.search(original) or _CJK_RE.search(corrected):
        original_chars = {
            char.casefold()
            for char in original
            if char.isalnum()
        }
        corrected_chars = {
            char.casefold()
            for char in corrected
            if char.isalnum()
        }
        if not original_chars or not corrected_chars:
            return False
        shared = len(original_chars & corrected_chars)
        return shared / min(len(original_chars), len(corrected_chars)) >= 0.35

    original_words = _words(original)
    corrected_words = _words(corrected)
    original_content = set(original_words) - COMMON_WORDS
    corrected_content = set(corrected_words) - COMMON_WORDS
    if original_content or corrected_content:
        if original_content & corrected_content:
            return True
        if len(original_words) == 1 and len(corrected_words) == 1:
            return (
                difflib.SequenceMatcher(
                    None,
                    original_words[0],
                    corrected_words[0],
                    autojunk=False,
                ).ratio()
                >= 0.6
            )
        return False
    return (
        difflib.SequenceMatcher(
            None,
            original_words,
            corrected_words,
            autojunk=False,
        ).ratio()
        >= 0.5
    )


def plausible_correction(
    original: str,
    corrected: str,
    protected_terms: Iterable[str] = (),
) -> bool:
    """Apply the verified EchoTranslate rewrite limits plus local term locks."""

    original = _bounded_text(original)
    corrected = _bounded_text(corrected)
    if not corrected or corrected == original:
        return False
    original_words = _words(original)
    corrected_words = _words(corrected)
    if (
        not original_words
        or not corrected_words
        or len(corrected_words) > len(original_words) + 3
        or len(corrected_words) < max(1, len(original_words) * 0.35)
    ):
        return False
    if len(corrected) > len(original) * 1.35 + 12:
        return False
    opcodes = difflib.SequenceMatcher(
        None,
        original_words,
        corrected_words,
        autojunk=False,
    ).get_opcodes()
    new_words = sum(
        corrected_end - corrected_start
        for tag, _original_start, _original_end, corrected_start, corrected_end in opcodes
        if tag in {"replace", "insert"}
    )
    if new_words > max(3, round(len(corrected_words) * 0.3)):
        return False
    if _contains_protected_term(original, protected_terms) and any(
        term and term.casefold() in original.casefold() and term.casefold() not in corrected.casefold()
        for term in protected_terms
    ):
        return False
    return _shares_lexical_anchor(original, corrected)


def detect_untranslated_words(
    target_text: str,
    target_lang: str,
    protected_terms: Iterable[str] = (),
) -> list[str]:
    """Detect residue for background urgency only; never expose this result."""

    text = str(target_text or "")
    if not text:
        return []
    terms = tuple(
        sorted(
            {str(term).strip() for term in protected_terms if str(term).strip()},
            key=len,
            reverse=True,
        )
    )
    for term in terms:
        text = re.sub(re.escape(term), " ", text, flags=re.IGNORECASE)

    lang = _normalized_lang(target_lang)
    if lang == "zh":
        text = _CAPITALIZED_ASCII_RE.sub(" ", text)
        return _LOWERCASE_ASCII_WORD_RE.findall(text)

    if lang in _LATIN_TARGET_LANGS:
        return _SOURCE_SCRIPT_RUN_RE.findall(text)

    if lang in {"ja", "ko"}:
        text = _CAPITALIZED_ASCII_RE.sub(" ", text)
        return _LOWERCASE_ASCII_WORD_RE.findall(text)

    return []


def _extract_json_object(content: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


class PatchWorker:
    """Own the only bounded correction queue and background consumer."""

    def __init__(
        self,
        settings: Mapping[str, Any] | CorrectionSettings | None = None,
        *,
        glossary_engine: Any | None = None,
        publisher: PatchPublisher | None = None,
        api_key_provider: Callable[[], str] | None = None,
        model_client: ModelClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        tick_seconds: float = CORRECTION_TICK_SECONDS,
        timeout_seconds: float = CORRECTION_TIMEOUT_SECONDS,
    ) -> None:
        if isinstance(settings, CorrectionSettings):
            self._settings = settings
        else:
            self._settings = CorrectionSettings.from_mapping(settings)
        self._glossary_engine = glossary_engine
        self._publisher = publisher
        self._api_key_provider = api_key_provider or (lambda: "")
        self._model_client = model_client or self._request_llm_correction
        self._clock = clock
        self._tick_seconds = max(0.01, float(tick_seconds))
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self.queue: asyncio.Queue[PatchTask] = asyncio.Queue(
            maxsize=CORRECTION_QUEUE_MAXSIZE
        )
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._settings_epoch = 0
        self.rejected_hallucination_count = 0
        self.patches_emitted = 0
        self.llm_call_count = 0
        self.network_request_count = 0
        self.urgent_promoted_count = 0

    @property
    def settings(self) -> CorrectionSettings:
        return self._settings

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def bind_publisher(self, publisher: PatchPublisher) -> None:
        self._publisher = publisher

    def set_glossary_engine(self, glossary_engine: Any | None) -> None:
        self._glossary_engine = glossary_engine

    def update_settings(
        self,
        settings: Mapping[str, Any] | CorrectionSettings,
    ) -> CorrectionSettings:
        next_settings = (
            settings
            if isinstance(settings, CorrectionSettings)
            else CorrectionSettings.from_mapping(settings)
        )
        if next_settings != self._settings:
            self._settings_epoch += 1
        self._settings = next_settings
        if not next_settings.enabled:
            self.clear_pending()
        return next_settings

    async def apply_settings(
        self,
        settings: Mapping[str, Any] | CorrectionSettings,
    ) -> CorrectionSettings:
        next_settings = self.update_settings(settings)
        if next_settings.enabled:
            self.start()
        else:
            await self.stop()
        return next_settings

    def start(self) -> bool:
        if not self._settings.enabled:
            return False
        if self.running:
            return True
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(
            self._run_loop(),
            name="r14-patch-worker",
        )
        return True

    async def stop(self) -> None:
        task, self._task = self._task, None
        self.clear_pending()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def clear_pending(self) -> int:
        cleared = 0
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return cleared
            cleared += 1

    def enqueue_final_utterance(
        self,
        *,
        window_id: str,
        sent_id: int,
        channel: str,
        source_lang: str,
        target_lang: str,
        source_text: str,
        target_text: str,
        source_hash: str,
        created_at: float | None = None,
        generation: int = 0,
    ) -> bool:
        """Synchronous, non-blocking ingress. Never awaits correction work."""

        if not self._settings.enabled or window_id != "main" or channel != "main":
            return False
        source = str(source_text or "")
        target = str(target_text or "")
        if not source and not target:
            return False
        task = PatchTask(
            window_id=window_id,
            sent_id=int(sent_id),
            channel=channel,
            source_lang=str(source_lang or "auto"),
            target_lang=str(target_lang or "zh"),
            source_text=source,
            target_text=target,
            source_hash=str(source_hash or ""),
            created_at=self._clock() if created_at is None else created_at,
            generation=int(generation),
        )
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(task)
        except asyncio.QueueFull:
            return False
        return True

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed correction never terminates the worker or main stream.
                continue

    def _protected_terms(self, task: PatchTask) -> tuple[str, ...]:
        engine = self._glossary_engine
        if engine is None:
            return ()
        try:
            terms_map = engine.terms_trans
        except Exception:
            return ()
        terms: set[str] = set()
        source_lang = _normalized_lang(task.source_lang)
        target_lang = _normalized_lang(task.target_lang)
        for pair, entries in terms_map.items():
            if "->" not in pair:
                continue
            pair_source, pair_target = pair.split("->", 1)
            if target_lang and pair_target != target_lang:
                continue
            if source_lang not in {"", "auto"} and pair_source != source_lang:
                continue
            for source, (priority, target) in entries.items():
                if int(priority) <= 1:
                    terms.add(str(source))
                    terms.add(str(target))
        try:
            ranked = engine.get_corpus_phrases(
                source_lang=task.source_lang,
                target_lang=task.target_lang,
                limit=900,
            )
        except Exception:
            ranked = {}
        for source, target in ranked.items():
            terms.add(str(source))
            terms.add(str(target))
        return tuple(sorted(terms, key=len, reverse=True))

    def _rules_correction(
        self,
        task: PatchTask,
    ) -> tuple[str, str]:
        source = clean_speech(task.source_text, task.source_lang)
        target = clean_speech(task.target_text, task.target_lang)
        engine = self._glossary_engine
        if engine is not None:
            try:
                source, target = engine.apply_fixes(
                    source,
                    target,
                    source_lang=task.source_lang,
                    target_lang=task.target_lang,
                )
            except Exception:
                pass
        return source, target

    def _api_key_available(self) -> bool:
        try:
            api_key = str(self._api_key_provider() or "").strip()
        except Exception:
            return False
        valid, _reason = KeyValidator.is_valid_api_key(api_key)
        return valid

    async def _prepare_correction(
        self,
        task: PatchTask,
        protected_terms: tuple[str, ...],
    ) -> tuple[str, str] | None:
        if self._settings.mode == "rules" or not self._api_key_available():
            return self._rules_correction(task)

        self.llm_call_count += 1
        settings_epoch = self._settings_epoch
        try:
            result = await asyncio.wait_for(
                self._model_client(task, protected_terms, self._settings.model),
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        if settings_epoch != self._settings_epoch or not self._settings.enabled:
            return None
        if not isinstance(result, Mapping):
            return None
        source = result.get("corrected_source", result.get("source"))
        target = result.get("corrected_target", result.get("target"))
        if not isinstance(source, str) or not isinstance(target, str):
            return None
        return _bounded_text(source), _bounded_text(target)

    async def _process_task(self, task: PatchTask) -> bool:
        protected_terms = self._protected_terms(task)
        proposed = await self._prepare_correction(task, protected_terms)
        if proposed is None:
            return False
        corrected_source, corrected_target = proposed
        checks = (
            (task.source_text, corrected_source),
            (task.target_text, corrected_target),
        )
        rejected = False
        changed = False
        for original, corrected in checks:
            if corrected == original:
                continue
            changed = True
            if not plausible_correction(
                original,
                corrected,
                protected_terms=protected_terms,
            ):
                rejected = True
                break
        if rejected:
            self.rejected_hallucination_count += 1
            return False
        if not changed:
            return False

        new_source_hash = hashlib.sha256(
            corrected_source.encode("utf-8")
        ).hexdigest()[:12]
        patch = {
            "type": "transcript.patch",
            "window_id": "main",
            "sent_id": task.sent_id,
            "source_hash": task.source_hash,
            "patched_source": corrected_source,
            "patched_target": corrected_target,
            "new_source_hash": new_source_hash,
        }
        if self._publisher is None:
            return False
        try:
            await self._publisher(patch, task.generation)
        except Exception:
            return False
        self.patches_emitted += 1
        return True

    async def run_once(self, *, now: float | None = None) -> bool:
        if not self._settings.enabled:
            return False
        current = self._clock() if now is None else now
        pending: list[PatchTask] = []
        while True:
            try:
                pending.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if not pending:
            return False

        eligible: list[PatchTask] = []
        waiting: list[PatchTask] = []
        for task in pending:
            protected_terms = self._protected_terms(task)
            residue = detect_untranslated_words(
                task.target_text,
                task.target_lang,
                protected_terms=protected_terms,
            )
            if len(residue) >= 2:
                if not task.urgent:
                    self.urgent_promoted_count += 1
                task.urgent = True
            age_ms = max(0.0, (current - task.created_at) * 1000)
            if task.urgent or age_ms >= self._settings.gap_ms:
                eligible.append(task)
            else:
                waiting.append(task)

        if not eligible:
            for task in waiting:
                self._requeue(task)
            return False

        eligible.sort(key=lambda task: (not task.urgent, task.created_at, task.sent_id))
        selected = eligible.pop(0)
        for task in waiting + eligible:
            self._requeue(task)
        await self._process_task(selected)
        return True

    def _requeue(self, task: PatchTask) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(task)
        except asyncio.QueueFull:
            pass

    def _request_llm_correction_sync(
        self,
        task: PatchTask,
        protected_terms: tuple[str, ...],
        model: str,
    ) -> Mapping[str, Any] | None:
        api_key = str(self._api_key_provider() or "").strip()
        valid, _reason = KeyValidator.is_valid_api_key(api_key)
        if not valid:
            return None
        prompt = {
            "task": "silent_asr_correction",
            "rules": [
                "Correct only obvious ASR homophones, omissions, or broken fragments.",
                "Do not explain, summarize, expand, or invent content.",
                "Preserve language, meaning, names, numbers, and protected terms.",
                "Return only JSON with corrected_source and corrected_target.",
            ],
            "source_lang": task.source_lang,
            "target_lang": task.target_lang,
            "protected_terms": list(protected_terms[:32]),
            "source": task.source_text,
            "target": task.target_text,
        }
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a conservative subtitle correction engine. "
                            "Return one compact JSON object only."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    },
                ],
                "temperature": 0.0,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            DASHSCOPE_TEXT_ENDPOINT,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        context = ssl.create_default_context(cafile=certifi.where())
        self.network_request_count += 1
        with urllib.request.urlopen(
            request,
            timeout=self._timeout_seconds,
            context=context,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            return None
        return _extract_json_object(content)

    async def _request_llm_correction(
        self,
        task: PatchTask,
        protected_terms: tuple[str, ...],
        model: str,
    ) -> Mapping[str, Any] | None:
        try:
            return await asyncio.to_thread(
                self._request_llm_correction_sync,
                task,
                protected_terms,
                model,
            )
        except Exception:
            return None
