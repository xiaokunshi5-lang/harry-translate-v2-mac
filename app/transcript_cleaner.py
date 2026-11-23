"""Pure, low-latency transcript cleanup adapted from Echo Translate (MIT)."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterable

FILLER_RE = re.compile(r"\b(?:u+h+m*|u+m+|e+r+m+|h+m+|m{2,})\b[,.]?\s*", re.IGNORECASE)
WORD_EDGE_RE = re.compile(r"^[^\w'’-]+|[^\w'’-]+$", re.UNICODE)
END_PUNCTUATION_RE = re.compile(r"[.?!;:,]$")
SENTENCE_END_RE = re.compile(r"""([A-Za-z']+)?([.?!…])["')\]]?$""")
FALSE_PERIOD_RE = re.compile(r"([A-Za-z']+)\.$")
PREVIOUS_TERMINAL_RE = re.compile(r"""[.?!:;]["')\]]?$""")
CAPITALIZED_WORD_RE = re.compile(r"^[A-Z][a-z]")
STANDALONE_I_RE = re.compile(r"^I\b")
FIRST_DIGIT_RE = re.compile(r"^\d")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
CJK_JOIN_EDGE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff，。！？；：、]")

COMMON_WORDS = frozenset(
    (
        "a an the and or but if so as at by for from in into onto of off on out "
        "over to up with without is are was were be been being am do does did done "
        "have has had having will would shall should can could may might must i you "
        "he she it we they me him her us them my your his its our their this that "
        "these those there here where when what which who whom whose why how than "
        "then them they also just only very more most much many some same such each "
        "both all any few other another into one two three four five six seven eight "
        "nine ten first last next well like line time form from were wear where "
        "whether weather not no yes now new know knew see seen say said make made take "
        "took get got give gave go went come came use used need needs want think thought "
        "mean means way ways part parts case cases point points kind kinds work works "
        "look looks call called set sets"
    ).split()
)

KEEP_REPEAT = frozenset({"that", "had", "is", "very", "really", "so", "no", "bye", "ha"})
DANGLING = frozenset(
    "of the a an to and or but than between into whose whether because our your their its".split()
)

PARA_MAX_ROWS = 6
PARA_MAX_CHARS = 520
PARA_HARD_ROWS = 12
PARA_HARD_CHARS = 1000
MAX_MERGE_GAP_MS = 300.0

_LATIN_CLEAN_LANGS = frozenset({"en", "auto"})


def _safe_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_word(value: str) -> str:
    return WORD_EDGE_RE.sub("", value).lower().replace("’", "'")


def _ends_clause(value: str) -> bool:
    return bool(END_PUNCTUATION_RE.search(value))


def _starts_uppercase(value: str) -> bool:
    return bool(value) and value[0].isupper()


def _language_key(lang: str) -> str:
    return (lang or "").strip().casefold().split("-")[0]


def _levenshtein(left: str, right: str, limit: int = 4) -> int:
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        best = left_index
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
            best = min(best, current[right_index])
        if best > limit:
            return limit + 1
        previous = current
    return previous[len(right)]


def _is_inflection(left: str, right: str) -> bool:
    short, long = (
        (left, right) if len(left) <= len(right) else (right, left)
    )
    if not long.startswith(short):
        return False
    suffix = long[len(short) :]
    return suffix in {"s", "es", "d", "ed", "ing", "er", "ers", "ly", "'s", "n't"}


def _repeat_key(words: list[str]) -> str:
    normalized = [_normalize_word(word) for word in words]
    if not all(normalized):
        return ""
    return " ".join(normalized)


def _transfer_leading_capital(retained: str, donor: str) -> str:
    if _starts_uppercase(donor) and not _starts_uppercase(retained):
        return retained[:1].upper() + retained[1:]
    return retained


def clean_speech(text: str, lang: str = "en") -> str:
    """Apply the verified synchronous cleanup rules without I/O or model calls."""

    raw = _safe_text(text)
    if not raw:
        return ""
    language = _language_key(lang)
    if language not in _LATIN_CLEAN_LANGS or CJK_RE.search(raw):
        return raw

    text_without_fillers = FILLER_RE.sub("", raw)
    tokens = [token for token in text_without_fillers.split(" ") if token]

    for phrase_size in range(4, 0, -1):
        index = 0
        while index + (2 * phrase_size) <= len(tokens):
            first = tokens[index : index + phrase_size]
            second = tokens[index + phrase_size : index + (2 * phrase_size)]
            if _ends_clause(first[-1]) and not first[-1].endswith(","):
                index += 1
                continue
            first_key = _repeat_key(first)
            second_key = _repeat_key(second)
            if not first_key or first_key != second_key:
                index += 1
                continue
            if phrase_size == 1 and (
                first_key in KEEP_REPEAT or FIRST_DIGIT_RE.match(first_key)
            ):
                index += 1
                continue
            tokens[index + phrase_size] = _transfer_leading_capital(
                second[0], first[0]
            )
            del tokens[index : index + phrase_size]
            index = max(0, index - 1)

    index = 0
    while index + 1 < len(tokens):
        left_token = tokens[index]
        right_token = tokens[index + 1]
        left = _normalize_word(left_token)
        right = _normalize_word(right_token)
        if not left or not right or left == right:
            index += 1
            continue
        if _ends_clause(left_token) and not left_token.endswith("-"):
            index += 1
            continue

        fragment = (
            left_token.endswith("-")
            and len(left) > 1
            and right.startswith(left.rstrip("-"))
        ) or (
            len(left) >= 3
            and len(right) - len(left) >= 2
            and right.startswith(left)
            and left not in COMMON_WORDS
        )
        near_slip = (
            len(left) >= 4
            and len(right) >= 4
            and left not in COMMON_WORDS
            and right not in COMMON_WORDS
            and not _is_inflection(left, right)
            and _levenshtein(
                left,
                right,
                2 if max(len(left), len(right)) >= 7 else 1,
            )
            <= (2 if max(len(left), len(right)) >= 7 else 1)
        )
        if not fragment and not near_slip:
            index += 1
            continue

        tokens[index + 1] = _transfer_leading_capital(right_token, left_token)
        del tokens[index]
        index = max(0, index - 1)

    return " ".join(tokens).strip()


def _sentence_ends(text: str) -> bool:
    if not text:
        return True
    match = SENTENCE_END_RE.search(text)
    if not match:
        return False
    final_word = (match.group(1) or "").lower()
    return not final_word or final_word not in DANGLING


def assign_para(
    sentences: list[str],
    max_sentences: int = PARA_MAX_ROWS,
    max_chars: int = PARA_MAX_CHARS,
) -> list[list[str]]:
    """Split finalized sentences at sentence boundaries using Echo soft/hard caps."""

    if max_sentences <= 0 or max_chars <= 0:
        raise ValueError("paragraph_limits_must_be_positive")
    paragraphs: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for sentence in sentences:
        normalized = _safe_text(sentence)
        if not normalized:
            continue
        current.append(normalized)
        current_chars += len(normalized)
        soft_full = len(current) >= max_sentences or current_chars >= max_chars
        hard_full = len(current) >= PARA_HARD_ROWS or current_chars >= PARA_HARD_CHARS
        if hard_full or (soft_full and _sentence_ends(normalized)):
            paragraphs.append(current)
            current = []
            current_chars = 0
    if current:
        paragraphs.append(current)
    return paragraphs


def mend_fragment(
    prev_text: str,
    next_text: str,
    gap_ms: float | None = None,
    *,
    protected_terms: Iterable[str] = (),
) -> tuple[bool, str, str]:
    """Repair a dangling-word false period and join the next fragment."""

    previous = _safe_text(prev_text)
    following = _safe_text(next_text)
    if not previous or not following:
        return False, previous, following
    if gap_ms is not None and gap_ms > MAX_MERGE_GAP_MS:
        return False, previous, following

    match = FALSE_PERIOD_RE.search(previous)
    if not match or match.group(1).lower() not in DANGLING:
        return False, previous, following

    previous = previous[:-1]
    previous_has_terminal = bool(PREVIOUS_TERMINAL_RE.search(previous))
    protected = any(
        term
        and term[:1].isupper()
        and following.startswith(term)
        for term in protected_terms
    )
    if (
        not previous_has_terminal
        and CAPITALIZED_WORD_RE.match(following)
        and not STANDALONE_I_RE.match(following)
        and not protected
    ):
        following = following[:1].lower() + following[1:]
    return True, previous, following


def _strip_false_period(text: str) -> str:
    match = FALSE_PERIOD_RE.search(text)
    if match and match.group(1).lower() in DANGLING:
        return text[:-1]
    return text


def _join_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    separator = (
        ""
        if CJK_JOIN_EDGE_RE.search(left[-1]) and CJK_JOIN_EDGE_RE.search(right[0])
        else " "
    )
    return f"{left}{separator}{right}".strip()


class SingleSessionCleaner:
    """Stateful cleaner owned by the single active main session."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._paragraph_sentences: list[str] = []
        self._paragraph_id = 1
        self._last_pre_mend_source = ""
        self._last_output_source = ""
        self._last_output_target = ""
        self._last_final_at: float | None = None
        self._last_sent_id: int | None = None
        self._next_sent_id = 0

    def finalize_utterance(
        self,
        source: str,
        target: str,
        *,
        sent_id: int | None = None,
        source_lang: str = "en",
        target_lang: str = "zh",
        gap_ms: float | None = None,
        protected_terms: Iterable[str] = (),
    ) -> tuple[str, str, int | None, str]:
        """Return cleaned source/target, merged predecessor id, and paragraph id."""

        with self._lock:
            now = self._clock()
            cleaned_source = clean_speech(source, source_lang)
            cleaned_target = clean_speech(target, target_lang)
            if gap_ms is None and self._last_final_at is not None:
                gap_ms = max(0.0, (now - self._last_final_at) * 1_000)

            merged_with_prev_id: int | None = None
            if self._last_pre_mend_source and cleaned_source:
                should_merge, previous, following = mend_fragment(
                    self._last_pre_mend_source,
                    cleaned_source,
                    gap_ms,
                    protected_terms=protected_terms,
                )
                if should_merge and self._last_sent_id is not None:
                    merged_with_prev_id = self._last_sent_id
                    cleaned_source = _join_text(previous, following)
                    cleaned_target = _join_text(
                        self._last_output_target,
                        cleaned_target,
                    )
                    if self._paragraph_sentences:
                        self._paragraph_sentences.pop()

            output_source = _strip_false_period(cleaned_source)
            self._paragraph_sentences.append(output_source)
            paragraphs = assign_para(self._paragraph_sentences)
            if len(paragraphs) > 1:
                self._paragraph_id += len(paragraphs) - 1
                self._paragraph_sentences = list(paragraphs[-1])
            paragraph_id = f"p{self._paragraph_id}"

            if sent_id is None:
                self._next_sent_id += 1
                sent_id = self._next_sent_id
            else:
                self._next_sent_id = max(self._next_sent_id, int(sent_id))

            self._last_pre_mend_source = cleaned_source
            self._last_output_source = output_source
            self._last_output_target = cleaned_target
            self._last_final_at = now
            self._last_sent_id = int(sent_id)
            return (
                output_source,
                cleaned_target,
                merged_with_prev_id,
                paragraph_id,
            )

    def reset(self) -> None:
        with self._lock:
            self._paragraph_sentences.clear()
            self._paragraph_id = 1
            self._last_pre_mend_source = ""
            self._last_output_source = ""
            self._last_output_target = ""
            self._last_final_at = None
            self._last_sent_id = None
            self._next_sent_id = 0
