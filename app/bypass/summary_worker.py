"""Default-off, bounded and non-blocking key-point summary worker."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import ssl
import time
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import certifi

from app.config_store import KeyValidator

SUMMARY_QUEUE_MAXSIZE = 64
SUMMARY_BUFFER_MAX_UTTERANCES = 64
SUMMARY_BUFFER_MAX_CHARS = 16_000
SUMMARY_TICK_SECONDS = 0.5
DEFAULT_SUMMARY_MODEL = "qwen-turbo"
SUPPORTED_SUMMARY_MODELS = frozenset({"qwen-turbo", "qwen-flash"})
SUMMARY_MODES = frozenset({"bailian", "rules"})
SUMMARY_TIMEOUT_SECONDS = 8.0
DASHSCOPE_TEXT_ENDPOINT = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SCRIPT_UNIT_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]"
)
_LATIN_WORD_RE = re.compile(r"[A-Za-z\u00c0-\u024f]+(?:['’-][A-Za-z\u00c0-\u024f]+)*")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])|[\r\n]+")

_EN_NARRATION = re.compile(
    r"^\s*(?:in (?:this|the) (?:lecture|class|session),?\s*)?(?:the\s+)?"
    r"(?:lecturer|professor|instructor|teacher|speaker)"
    r"\s+(?:\w+(?:ly)?\s+){0,2}?"
    r"(?:explains?|explained|says?|said|notes?|noted|points? out|pointed out|"
    r"emphasi[sz]es|emphasi[sz]ed|stresses|stressed|clarifies|clarified|"
    r"mentions?|mentioned|highlights?|highlighted|argues?|argued|shows?|"
    r"showed|reminds? (?:students|us|the class))\s*(?:that\s+|:\s*)",
    re.I,
)
_ZH_NARRATION = re.compile(
    r"^\s*(?:课上|本节课|这节课)?[，,]?\s*"
    r"(?:老师|讲师|教授|讲者|主讲人)[^，。：:；]{0,6}?"
    r"(?:指出|强调|提到|说明|解释|讲到|认为|表示|提醒|说)(?:了|道)?"
    r"\s*[，,：:]\s*"
)

_DECISION_WORDS = (
    "决定",
    "确定",
    "结论",
    "必须",
    "需要",
    "计划",
    "截止",
    "上线",
    "批准",
    "同意",
    "decision",
    "decided",
    "must",
    "deadline",
    "launch",
    "approve",
    "required",
)
_RISK_WORDS = (
    "风险",
    "问题",
    "阻塞",
    "失败",
    "依赖",
    "风险项",
    "risk",
    "issue",
    "blocker",
    "failure",
    "dependency",
)
_METRIC_WORDS = (
    "增长",
    "下降",
    "占比",
    "成本",
    "预算",
    "指标",
    "目标",
    "提升",
    "降低",
    "revenue",
    "growth",
    "cost",
    "budget",
    "target",
)


def _normalized_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _direct_statement(text: str) -> str:
    cleaned = _normalized_text(text)
    cleaned = _ZH_NARRATION.sub("", cleaned, count=1)
    cleaned = _EN_NARRATION.sub("", cleaned, count=1)
    return cleaned.strip()


def _word_units(text: str) -> int:
    script_units = len(_SCRIPT_UNIT_RE.findall(text))
    latin_units = len(_LATIN_WORD_RE.findall(text))
    number_units = len(_NUMBER_RE.findall(text))
    return script_units + latin_units + number_units


def _sentence_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for part in _SENTENCE_SPLIT_RE.split(text):
        cleaned = _direct_statement(part)
        if len(cleaned) >= 6:
            candidates.append(cleaned[:440])
    return candidates


def _rule_level(text: str) -> tuple[int, str]:
    folded = text.casefold()
    score = 2
    category = "detail"
    if any(word in folded for word in _DECISION_WORDS):
        score += 2
        category = "decision"
    if _NUMBER_RE.search(text) or any(word in folded for word in _METRIC_WORDS):
        score += 2
        category = "metric"
    if any(word in folded for word in _RISK_WORDS):
        score += 1
        if category == "detail":
            category = "risk"
    if len(_SCRIPT_UNIT_RE.findall(text)) >= 28 or len(text) >= 80:
        score += 1
    return min(5, max(1, score)), category


@dataclasses.dataclass(slots=True, frozen=True)
class SummarySettings:
    enabled: bool = False
    mode: str = "bailian"
    model: str = DEFAULT_SUMMARY_MODEL
    min_words: int = 120
    min_interval_sec: int = 30

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any] | None) -> "SummarySettings":
        source = settings if isinstance(settings, Mapping) else {}
        mode = str(source.get("summary_mode") or "bailian").strip().casefold()
        if mode not in SUMMARY_MODES:
            mode = "bailian"
        model = str(source.get("summary_model") or DEFAULT_SUMMARY_MODEL).strip()
        if model not in SUPPORTED_SUMMARY_MODELS:
            model = DEFAULT_SUMMARY_MODEL
        try:
            min_words = int(source.get("summary_min_words", 120))
        except (TypeError, ValueError):
            min_words = 120
        try:
            min_interval_sec = int(source.get("summary_min_interval_sec", 30))
        except (TypeError, ValueError):
            min_interval_sec = 30
        return cls(
            enabled=source.get("summary_enabled") is True,
            mode=mode,
            model=model,
            min_words=min(max(min_words, 20), 2000),
            min_interval_sec=min(max(min_interval_sec, 1), 600),
        )


@dataclasses.dataclass(slots=True, frozen=True)
class SummaryUtterance:
    window_id: str
    sent_id: int
    source_lang: str
    target_lang: str
    source_text: str
    target_text: str
    created_at: float
    generation: int


@dataclasses.dataclass(slots=True, frozen=True)
class SummaryItem:
    level: int
    text: str
    category: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "text": self.text,
            "category": self.category,
        }


SummaryModelClient = Callable[
    [tuple[SummaryUtterance, ...], tuple[SummaryItem, ...], str],
    Awaitable[Any],
]
SummaryPublisher = Callable[[dict[str, Any], int, int], Awaitable[Any]]


def _parse_summary_payload(payload: Any) -> list[SummaryItem] | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, Mapping):
        for key in ("items", "notes", "key_points", "points"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                payload = candidate
                break
        else:
            return None
    if not isinstance(payload, list):
        return None

    items: list[SummaryItem] = []
    seen: set[str] = set()
    for candidate in payload:
        if not isinstance(candidate, Mapping):
            continue
        text = _direct_statement(
            candidate.get("text")
            or candidate.get("zh")
            or candidate.get("note")
            or ""
        )
        if not text:
            continue
        try:
            level = int(candidate.get("level", candidate.get("importance", 3)))
        except (TypeError, ValueError):
            level = 3
        level = min(5, max(1, level))
        category = _normalized_text(candidate.get("category") or "detail")[:40]
        dedupe_key = text.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        items.append(
            SummaryItem(
                level=level,
                text=text[:440],
                category=category or "detail",
            )
        )
    return items or None


class SummaryWorker:
    """Own the only bounded summary queue and background consumer."""

    def __init__(
        self,
        settings: Mapping[str, Any] | SummarySettings | None = None,
        *,
        publisher: SummaryPublisher | None = None,
        api_key_provider: Callable[[], str] | None = None,
        model_client: SummaryModelClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        tick_seconds: float = SUMMARY_TICK_SECONDS,
        timeout_seconds: float = SUMMARY_TIMEOUT_SECONDS,
    ) -> None:
        self._settings = (
            settings
            if isinstance(settings, SummarySettings)
            else SummarySettings.from_mapping(settings)
        )
        self._publisher = publisher
        self._api_key_provider = api_key_provider or (lambda: "")
        self._model_client = model_client or self._request_llm_summary
        self._clock = clock
        self._tick_seconds = max(0.01, float(tick_seconds))
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self.queue: asyncio.Queue[SummaryUtterance] = asyncio.Queue(
            maxsize=SUMMARY_QUEUE_MAXSIZE
        )
        self._task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()
        self._settings_epoch = 0
        self._buffer: list[SummaryUtterance] = []
        self._pending_words = 0
        self._buffer_chars = 0
        self._last_summary_ts: float | None = (
            self._clock() if self._settings.enabled else None
        )
        self._generation = 0
        self._generation_epoch = 0
        self._pinned: list[SummaryItem] = []
        self._archived: list[SummaryItem] = []
        self._engine_used = ""
        self.llm_call_count = 0
        self.network_request_count = 0
        self.rules_summary_count = 0
        self.summaries_emitted = 0

    @property
    def settings(self) -> SummarySettings:
        return self._settings

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def active_workers(self) -> int:
        return 1 if self.running else 0

    @property
    def queued_count(self) -> int:
        return self.queue.qsize()

    @property
    def pending_words(self) -> int:
        return self._pending_words

    def bind_publisher(self, publisher: SummaryPublisher) -> None:
        self._publisher = publisher

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._settings.enabled,
            "mode": self._settings.mode,
            "model": self._settings.model,
            "engine_used": self._engine_used,
            "pending_words": self._pending_words,
            "pinned": [item.as_dict() for item in self._pinned],
            "archived": [item.as_dict() for item in self._archived],
        }

    def is_publication_current(
        self,
        session_generation: int,
        generation_epoch: int,
    ) -> bool:
        return (
            self._settings.enabled
            and session_generation == self._generation
            and generation_epoch == self._generation_epoch
        )

    def invalidate_session(self) -> int:
        cleared = self.clear_pending()
        self.clear_results()
        return cleared

    def enqueue_final(
        self,
        event: Mapping[str, Any],
        *,
        generation: int = 0,
        created_at: float | None = None,
    ) -> bool:
        if not self._settings.enabled:
            return False
        if not isinstance(event, Mapping):
            return False
        if str(event.get("window_id") or "main") != "main":
            return False
        source = str(event.get("source_text") or event.get("source") or "")
        target = str(event.get("target_text") or event.get("target") or "")
        if not source and not target:
            return False
        try:
            sent_id = int(event.get("sent_id") or 0)
        except (TypeError, ValueError):
            sent_id = 0
        parsed_generation = int(generation)
        if parsed_generation < self._generation:
            return False
        if parsed_generation > self._generation:
            self._generation = parsed_generation
        utterance = SummaryUtterance(
            window_id="main",
            sent_id=max(0, sent_id),
            source_lang=str(event.get("source_lang") or "auto"),
            target_lang=str(event.get("target_lang") or "zh"),
            source_text=source,
            target_text=target,
            created_at=self._clock() if created_at is None else float(created_at),
            generation=parsed_generation,
        )
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(utterance)
        except asyncio.QueueFull:
            return False
        return True

    def set_enabled(self, enabled: bool) -> bool:
        was_enabled = self._settings.enabled
        next_settings = dataclasses.replace(self._settings, enabled=bool(enabled))
        if next_settings == self._settings:
            return self.running
        self._settings = next_settings
        self._settings_epoch += 1
        if not next_settings.enabled:
            self.clear_pending()
            self.clear_results()
            self._cancel_task()
            return False
        if not was_enabled:
            self._last_summary_ts = self._clock()
        self.start()
        return self.running

    async def apply_settings(
        self,
        settings: Mapping[str, Any] | SummarySettings,
    ) -> SummarySettings:
        next_settings = (
            settings
            if isinstance(settings, SummarySettings)
            else SummarySettings.from_mapping(settings)
        )
        was_enabled = self._settings.enabled
        changed = next_settings != self._settings
        if changed:
            self._settings_epoch += 1
            self._settings = next_settings
        if not next_settings.enabled:
            await self.stop()
            self.clear_results()
        else:
            if not was_enabled:
                self._last_summary_ts = self._clock()
            self.start()
        return next_settings

    def start(self) -> bool:
        if not self._settings.enabled:
            return False
        if self.running:
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._task = loop.create_task(self._run_loop(), name="r15-summary-worker")
        return True

    async def stop(self) -> None:
        self.clear_pending()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _cancel_task(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()

    def clear_pending(self) -> int:
        self._generation_epoch += 1
        cleared = 0
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            cleared += 1
        cleared += len(self._buffer)
        self._buffer.clear()
        self._pending_words = 0
        self._buffer_chars = 0
        return cleared

    def clear_results(self) -> None:
        self._pinned.clear()
        self._archived.clear()
        self._engine_used = ""
        self._last_summary_ts = None

    async def refresh(self) -> dict[str, Any]:
        if not self._settings.enabled:
            return self.snapshot()
        await self.run_once(force=True)
        return self.snapshot()

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    def _drain_queue(self) -> None:
        while True:
            try:
                utterance = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if utterance.generation < self._generation:
                continue
            self._buffer.append(utterance)
            text = (
                utterance.target_text
                if utterance.target_text
                else utterance.source_text
            )
            self._pending_words += _word_units(text)
            self._buffer_chars += len(utterance.source_text) + len(
                utterance.target_text
            )
            self._trim_buffer()

    def _trim_buffer(self) -> None:
        while self._buffer and (
            len(self._buffer) > SUMMARY_BUFFER_MAX_UTTERANCES
            or self._buffer_chars > SUMMARY_BUFFER_MAX_CHARS
        ):
            removed = self._buffer.pop(0)
            removed_text = removed.target_text or removed.source_text
            self._pending_words = max(
                0,
                self._pending_words - _word_units(removed_text),
            )
            self._buffer_chars = max(
                0,
                self._buffer_chars
                - len(removed.source_text)
                - len(removed.target_text),
            )

    def _api_key_available(self) -> bool:
        try:
            api_key = str(self._api_key_provider() or "").strip()
        except Exception:
            return False
        valid, _reason = KeyValidator.is_valid_api_key(api_key)
        return valid

    async def _extract(
        self,
        utterances: tuple[SummaryUtterance, ...],
    ) -> tuple[list[SummaryItem] | None, str]:
        if self._settings.mode == "bailian":
            if not self._api_key_available():
                return self._rules_extract(utterances), "rules_fallback"
            self.llm_call_count += 1
            settings_epoch = self._settings_epoch
            try:
                result = await asyncio.wait_for(
                    self._model_client(
                        utterances,
                        tuple(self._pinned),
                        self._settings.model,
                    ),
                    timeout=self._timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                result = None
            if (
                settings_epoch != self._settings_epoch
                or not self._settings.enabled
            ):
                return None, ""
            items = _parse_summary_payload(result)
            if items:
                return items, "bailian"
            return self._rules_extract(utterances), "rules_fallback"
        return self._rules_extract(utterances), "rules"

    def _rules_extract(
        self,
        utterances: Sequence[SummaryUtterance],
    ) -> list[SummaryItem]:
        items: list[SummaryItem] = []
        seen: set[str] = set()
        for utterance in utterances:
            summary_text = utterance.target_text or utterance.source_text
            for sentence in _sentence_candidates(summary_text):
                dedupe_key = sentence.casefold()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                level, category = _rule_level(sentence)
                items.append(
                    SummaryItem(
                        level=level,
                        text=sentence,
                        category=category,
                    )
                )
        items.sort(
            key=lambda item: (item.level, len(item.text)),
            reverse=True,
        )
        self.rules_summary_count += 1
        return items[:30]

    @staticmethod
    def _merge_pinned(
        existing: Sequence[SummaryItem],
        incoming: Sequence[SummaryItem],
    ) -> list[SummaryItem]:
        merged: list[SummaryItem] = []
        seen: set[str] = set()
        for item in [*incoming, *existing]:
            if item.level < 4:
                continue
            key = item.text.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged[:20]

    async def _publish_snapshot(
        self,
        session_generation: int,
        generation_epoch: int,
    ) -> None:
        if self._publisher is None:
            return
        event = {"type": "summary.update", **self.snapshot()}
        try:
            await self._publisher(
                event,
                session_generation,
                generation_epoch,
            )
        except Exception:
            return

    async def run_once(
        self,
        *,
        now: float | None = None,
        force: bool = False,
    ) -> bool:
        if not self._settings.enabled:
            return False
        async with self._run_lock:
            if not self._settings.enabled:
                return False
            self._drain_queue()
            if not self._buffer:
                return False
            current = self._clock() if now is None else float(now)
            interval_ready = (
                self._last_summary_ts is None
                or current - self._last_summary_ts >= self._settings.min_interval_sec
            )
            if (
                not force
                and (
                    self._pending_words < self._settings.min_words
                    or not interval_ready
                )
            ):
                return False

            utterances = tuple(self._buffer)
            settings_epoch = self._settings_epoch
            generation_epoch = self._generation_epoch
            batch_generation = utterances[-1].generation
            items, engine_used = await self._extract(utterances)
            if (
                items is None
                or settings_epoch != self._settings_epoch
                or generation_epoch != self._generation_epoch
                or batch_generation != self._generation
                or not self._settings.enabled
            ):
                return False

            self._pinned = self._merge_pinned(self._pinned, items)
            archived = [item for item in items if item.level <= 3]
            archived.sort(key=lambda item: item.level, reverse=True)
            self._archived = archived[:40]
            self._engine_used = engine_used
            self._last_summary_ts = current
            self._buffer.clear()
            self._pending_words = 0
            self._buffer_chars = 0
            self.summaries_emitted += 1
            await self._publish_snapshot(batch_generation, generation_epoch)
            return True

    def _request_llm_summary_sync(
        self,
        utterances: tuple[SummaryUtterance, ...],
        existing: tuple[SummaryItem, ...],
        model: str,
    ) -> Any:
        api_key = str(self._api_key_provider() or "").strip()
        valid, _reason = KeyValidator.is_valid_api_key(api_key)
        if not valid:
            return None
        transcript = "\n".join(
            (
                f"[{utterance.sent_id}] "
                f"source: {utterance.source_text}\n"
                f"target: {utterance.target_text}"
            )
            for utterance in utterances
        )
        prompt = {
            "task": "meeting_key_point_summary",
            "rules": [
                "Return one valid JSON array only.",
                "Each item must contain level, text, and category.",
                "level must be an integer from 1 to 5.",
                "4-5 means important or core; 1-3 means supporting detail.",
                "State knowledge directly. Do not say the lecturer/teacher said it.",
                "Do not invent facts, numbers, decisions, or action items.",
                "Use the target language unless a proper noun must remain unchanged.",
            ],
            "existing_pinned": [item.as_dict() for item in existing],
            "transcript": transcript,
        }
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You extract meeting key points. Return only a compact "
                            "JSON array with level, text, and category."
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
        return content

    async def _request_llm_summary(
        self,
        utterances: tuple[SummaryUtterance, ...],
        existing: tuple[SummaryItem, ...],
        model: str,
    ) -> Any:
        try:
            return await asyncio.to_thread(
                self._request_llm_summary_sync,
                utterances,
                existing,
                model,
            )
        except Exception:
            return None
