"""Three-bucket glossary engine with single-pass interval-locked AC matching."""

from __future__ import annotations

import bisect
import threading
from collections import deque
from collections.abc import Iterable
from typing import Any

PRIORITY_RANK_MAX = 3


def _is_ascii_alnum(ch: str) -> bool:
    code = ord(ch)
    return (48 <= code <= 57) or (65 <= code <= 90) or (97 <= code <= 122)


def _passes_ascii_boundary(text: str, start: int, end: int) -> bool:
    """Protect ASCII words from partial matches (e.g. 'AI' inside 'OpenAI' or 'rain')."""

    if start > 0 and _is_ascii_alnum(text[start]) and _is_ascii_alnum(text[start - 1]):
        return False
    if end < len(text) and _is_ascii_alnum(text[end - 1]) and _is_ascii_alnum(text[end]):
        return False
    return True


def _lower_same_length(text: str) -> str:
    """Lowercase text while strictly preserving 1:1 character index offsets."""

    lowered = text.lower()
    if len(lowered) == len(text):
        return lowered
    chars: list[str] = []
    for ch in text:
        mapped = ch.lower()
        chars.append(mapped if len(mapped) == 1 else ch)
    return "".join(chars)


def infer_lang_from_text(text: str) -> str:
    """Best-effort language tag inference for source='auto' ASR correction."""

    has_cjk = False
    has_ascii_alpha = False
    for ch in text:
        code = ord(ch)
        if 0x3040 <= code <= 0x30FF:
            return "ja"
        if 0xAC00 <= code <= 0xD7AF:
            return "ko"
        if 0x4E00 <= code <= 0x9FFF:
            has_cjk = True
        elif (65 <= code <= 90) or (97 <= code <= 122):
            has_ascii_alpha = True
    if has_cjk:
        return "zh"
    if has_ascii_alpha:
        return "en"
    return ""


def normalize_bucket_and_lang(
    bucket: str | None = None,
    lang_key: str | None = None,
    *,
    direction: str | None = None,
) -> tuple[str, str, str]:
    """Normalize bucket, lang_key and legacy category representation.

    Returns `(bucket, lang_key, legacy_category)`.
    """

    raw_bucket = (bucket or "").strip()
    raw_lang = (lang_key or "").strip().casefold()
    raw_dir = (direction or "").strip().casefold()

    if raw_bucket in {"terms_zh_to_en", "terms_en_to_zh"}:
        pair = "zh->en" if raw_bucket == "terms_zh_to_en" else "en->zh"
        return "terms_trans", pair, raw_bucket

    if raw_dir and raw_bucket in {"", "terms_trans"}:
        if "->" not in raw_dir:
            raise ValueError(f"invalid_direction: {direction}")
        src, tgt = (part.strip() for part in raw_dir.split("->", 1))
        if not src or not tgt or src == tgt:
            raise ValueError(f"invalid_direction: {direction}")
        pair = f"{src}->{tgt}"
        legacy = (
            "terms_zh_to_en"
            if pair == "zh->en"
            else "terms_en_to_zh"
            if pair == "en->zh"
            else "terms_trans"
        )
        return "terms_trans", pair, legacy

    if raw_bucket == "terms_trans" or (not raw_bucket and "->" in raw_lang):
        pair_raw = raw_lang or raw_dir
        if "->" not in pair_raw:
            raise ValueError("direction_required_for_terms_trans")
        src, tgt = (part.strip() for part in pair_raw.split("->", 1))
        if not src or not tgt or src == tgt:
            raise ValueError(f"invalid_direction: {pair_raw}")
        pair = f"{src}->{tgt}"
        legacy = (
            "terms_zh_to_en"
            if pair == "zh->en"
            else "terms_en_to_zh"
            if pair == "en->zh"
            else "terms_trans"
        )
        return "terms_trans", pair, legacy

    if raw_bucket == "asr_fixes":
        lang = raw_lang or "zh"
        return "asr_fixes", lang, "asr_fixes"

    if raw_bucket == "target_fixes":
        lang = raw_lang or "en"
        return "target_fixes", lang, "target_fixes"

    raise ValueError(f"unsupported_glossary_bucket: {bucket}")


class _ACNode:
    __slots__ = ("children", "fail", "outputs")

    def __init__(self) -> None:
        self.children: dict[str, _ACNode] | None = None
        self.fail: _ACNode | None = None
        self.outputs: list[tuple[int, int, str]] | None = None


class _ACTrie:
    """Immutable pure-Python BFS Aho-Corasick trie."""

    __slots__ = ("_root", "has_patterns")

    def __init__(self, patterns: Iterable[tuple[str, str, int]]) -> None:
        root = _ACNode()
        deduped: dict[str, tuple[int, str]] = {}
        for raw_source, raw_target, raw_tier in patterns:
            source = str(raw_source).strip()
            target = str(raw_target).strip()
            if not source or not target or source == target:
                continue
            key = _lower_same_length(source)
            tier = int(raw_tier)
            existing = deduped.get(key)
            if existing is None or tier < existing[0]:
                deduped[key] = (tier, target)

        self.has_patterns = bool(deduped)
        if deduped:
            for key, (tier, target) in deduped.items():
                node = root
                for ch in key:
                    if node.children is None:
                        node.children = {}
                    nxt = node.children.get(ch)
                    if nxt is None:
                        nxt = _ACNode()
                        node.children[ch] = nxt
                    node = nxt
                if node.outputs is None:
                    node.outputs = []
                node.outputs.append((len(key), tier, target))

            queue: deque[_ACNode] = deque()
            if root.children is not None:
                for child in root.children.values():
                    child.fail = root
                    queue.append(child)
            while queue:
                current = queue.popleft()
                if current.children is None:
                    continue
                for ch, child in current.children.items():
                    fallback = current.fail
                    while fallback is not None and (
                        fallback.children is None or ch not in fallback.children
                    ):
                        fallback = fallback.fail
                    child.fail = (
                        fallback.children[ch]
                        if fallback is not None and fallback.children is not None
                        else root
                    )
                    if child.fail.outputs is not None:
                        if child.outputs is None:
                            child.outputs = list(child.fail.outputs)
                        else:
                            child.outputs.extend(child.fail.outputs)
                    queue.append(child)

        self._root = root

    def collect_matches(
        self,
        text: str,
        lowered: str,
        out: list[tuple[int, int, int, int, str]],
    ) -> None:
        """Scan `lowered` once and append boundary-valid candidate intervals."""

        if not self.has_patterns:
            return
        root = self._root
        node = root
        for idx, ch in enumerate(lowered):
            while node is not root and (node.children is None or ch not in node.children):
                node = node.fail if node.fail is not None else root
            if node.children is not None and ch in node.children:
                node = node.children[ch]
            if node.outputs is not None:
                end = idx + 1
                for length, tier, replacement in node.outputs:
                    start = end - length
                    if _passes_ascii_boundary(text, start, end):
                        out.append((tier, start, -length, end, replacement))


_EMPTY_TRIE = _ACTrie(())


class IntervalLockedAutomaton:
    """Dual-layer AC matcher with single-pass `[start, end)` interval locking.

    Static (`P2+P3`) and dynamic (`P0+P1`) layers are stored as separate immutable
    tries so dynamic hot-adds compile in `< 1ms` without touching the 18k-term static
    trie. Both layers scan the original input once, then intervals are resolved
    globally by `(tier_rank, start, -length)` with zero second-pass rescanning.
    """

    def __init__(self) -> None:
        self._static_trie: _ACTrie = _EMPTY_TRIE
        self._dynamic_trie: _ACTrie = _EMPTY_TRIE

    def update_static(self, entries: Iterable[tuple[str, str, int]]) -> None:
        compiled = _ACTrie(entries)
        self._static_trie = compiled

    def update_dynamic(self, entries: Iterable[tuple[str, str, int]]) -> None:
        compiled = _ACTrie(entries)
        self._dynamic_trie = compiled

    def replace_all(self, text: str) -> str:
        if not text:
            return text
        dynamic_trie = self._dynamic_trie
        static_trie = self._static_trie
        if not dynamic_trie.has_patterns and not static_trie.has_patterns:
            return text

        lowered = _lower_same_length(text)
        candidates: list[tuple[int, int, int, int, str]] = []
        dynamic_trie.collect_matches(text, lowered, candidates)
        static_trie.collect_matches(text, lowered, candidates)
        if not candidates:
            return text

        # Fast path when only one candidate matched.
        if len(candidates) == 1:
            _, start, _, end, replacement = candidates[0]
            return f"{text[:start]}{replacement}{text[end:]}"

        # Rank strictly by tier (P0 < P1 < P2 < P3), earliest start, longest match.
        candidates.sort()
        starts: list[int] = []
        chosen: list[tuple[int, int, str]] = []
        for _tier, start, _neg_len, end, replacement in candidates:
            pos = bisect.bisect_left(starts, start)
            if pos > 0 and chosen[pos - 1][1] > start:
                continue
            if pos < len(chosen) and chosen[pos][0] < end:
                continue
            starts.insert(pos, start)
            chosen.insert(pos, (start, end, replacement))

        parts: list[str] = []
        cursor = 0
        for start, end, replacement in chosen:
            if start > cursor:
                parts.append(text[cursor:start])
            parts.append(replacement)
            cursor = end
        if cursor < len(text):
            parts.append(text[cursor:])
        return "".join(parts)


class GlossaryEngine:
    """Three-bucket glossary engine preventing direction inversion and cascades."""

    def __init__(self) -> None:
        self._build_lock = threading.RLock()
        self.asr_fixes: dict[str, IntervalLockedAutomaton] = {}
        self.target_fixes: dict[str, IntervalLockedAutomaton] = {}

        self._static_terms_trans: dict[str, dict[str, tuple[int, str]]] = {}
        self._dynamic_terms_trans: dict[str, dict[str, tuple[int, str]]] = {}
        self._static_asr_map: dict[str, dict[str, tuple[int, str]]] = {}
        self._dynamic_asr_map: dict[str, dict[str, tuple[int, str]]] = {}
        self._static_target_map: dict[str, dict[str, tuple[int, str]]] = {}
        self._dynamic_target_map: dict[str, dict[str, tuple[int, str]]] = {}

    @property
    def terms_trans(self) -> dict[str, dict[str, tuple[int, str]]]:
        with self._build_lock:
            merged: dict[str, dict[str, tuple[int, str]]] = {}
            for pair in set(self._static_terms_trans) | set(self._dynamic_terms_trans):
                bucket: dict[str, tuple[int, str]] = {}
                for source, (priority, target) in self._static_terms_trans.get(pair, {}).items():
                    self._put_ranked(bucket, source, target, priority)
                for source, (priority, target) in self._dynamic_terms_trans.get(pair, {}).items():
                    self._put_ranked(bucket, source, target, priority)
                if bucket:
                    merged[pair] = bucket
            return merged

    def rebuild_static(self, static_entries: Iterable[Any]) -> None:
        asr_grouped: dict[str, list[tuple[str, str, int]]] = {}
        target_grouped: dict[str, list[tuple[str, str, int]]] = {}
        trans_map: dict[str, dict[str, tuple[int, str]]] = {}
        asr_map: dict[str, dict[str, tuple[int, str]]] = {}
        target_map: dict[str, dict[str, tuple[int, str]]] = {}

        for entry in static_entries:
            source = str(entry.source).strip()
            target = str(entry.target).strip()
            if not source or not target or source == target:
                continue
            priority = int(entry.priority)
            bucket = str(entry.bucket)
            lang_key = str(entry.lang_key)
            if bucket == "asr_fixes":
                asr_grouped.setdefault(lang_key, []).append((source, target, priority))
                self._put_ranked(asr_map.setdefault(lang_key, {}), source, target, priority)
            elif bucket == "target_fixes":
                target_grouped.setdefault(lang_key, []).append((source, target, priority))
                self._put_ranked(target_map.setdefault(lang_key, {}), source, target, priority)
            elif bucket == "terms_trans":
                self._put_ranked(trans_map.setdefault(lang_key, {}), source, target, priority)

        with self._build_lock:
            for lang in set(self.asr_fixes) | set(asr_grouped):
                automaton = self.asr_fixes.setdefault(lang, IntervalLockedAutomaton())
                automaton.update_static(asr_grouped.get(lang, ()))
            for lang in set(self.target_fixes) | set(target_grouped):
                automaton = self.target_fixes.setdefault(lang, IntervalLockedAutomaton())
                automaton.update_static(target_grouped.get(lang, ()))
            self._static_terms_trans = trans_map
            self._static_asr_map = asr_map
            self._static_target_map = target_map

    def rebuild_dynamic(self, dynamic_entries: Iterable[Any]) -> None:
        asr_grouped: dict[str, list[tuple[str, str, int]]] = {}
        target_grouped: dict[str, list[tuple[str, str, int]]] = {}
        trans_map: dict[str, dict[str, tuple[int, str]]] = {}
        asr_map: dict[str, dict[str, tuple[int, str]]] = {}
        target_map: dict[str, dict[str, tuple[int, str]]] = {}

        for entry in dynamic_entries:
            source = str(entry.source).strip()
            target = str(entry.target).strip()
            if not source or not target or source == target:
                continue
            priority = int(entry.priority)
            bucket = str(entry.bucket)
            lang_key = str(entry.lang_key)
            if bucket == "asr_fixes":
                asr_grouped.setdefault(lang_key, []).append((source, target, priority))
                self._put_ranked(asr_map.setdefault(lang_key, {}), source, target, priority)
            elif bucket == "target_fixes":
                target_grouped.setdefault(lang_key, []).append((source, target, priority))
                self._put_ranked(target_map.setdefault(lang_key, {}), source, target, priority)
            elif bucket == "terms_trans":
                self._put_ranked(trans_map.setdefault(lang_key, {}), source, target, priority)

        with self._build_lock:
            for lang in set(self.asr_fixes) | set(asr_grouped):
                automaton = self.asr_fixes.setdefault(lang, IntervalLockedAutomaton())
                automaton.update_dynamic(asr_grouped.get(lang, ()))
            for lang in set(self.target_fixes) | set(target_grouped):
                automaton = self.target_fixes.setdefault(lang, IntervalLockedAutomaton())
                automaton.update_dynamic(target_grouped.get(lang, ()))
            self._dynamic_terms_trans = trans_map
            self._dynamic_asr_map = asr_map
            self._dynamic_target_map = target_map

    @staticmethod
    def _put_ranked(
        bucket: dict[str, tuple[int, str]],
        source: str,
        target: str,
        priority: int,
    ) -> None:
        existing = bucket.get(source)
        if existing is None or priority < existing[0]:
            bucket[source] = (priority, target)

    def apply_fixes(
        self,
        source_text: str,
        target_text: str,
        *,
        source_lang: str = "auto",
        target_lang: str = "",
    ) -> tuple[str, str]:
        """Apply `asr_fixes[src]` ONLY to `source_text` and `target_fixes[tgt]` ONLY to `target_text`."""

        fixed_source = source_text
        fixed_target = target_text

        if source_text:
            src = (
                infer_lang_from_text(source_text)
                if source_lang in {"", "auto"}
                else source_lang.strip().casefold()
            )
            if src:
                automaton = self.asr_fixes.get(src)
                if automaton is not None:
                    fixed_source = automaton.replace_all(source_text)

        if target_text:
            tgt = target_lang.strip().casefold()
            if tgt:
                automaton = self.target_fixes.get(tgt)
                if automaton is not None:
                    fixed_target = automaton.replace_all(target_text)

        return fixed_source, fixed_target

    def get_corpus_phrases(
        self,
        *,
        source_lang: str = "auto",
        target_lang: str = "",
        limit: int = 900,
    ) -> dict[str, str]:
        """Return cloud `corpus.phrases` sorted by P0 > P1 > P2 > P3."""

        cap = min(max(int(limit), 1), 900)
        src = (source_lang or "auto").strip().casefold()
        tgt = (target_lang or "").strip().casefold()

        with self._build_lock:
            dyn_trans = dict(self._dynamic_terms_trans)
            stat_trans = dict(self._static_terms_trans)
            dyn_asr = dict(self._dynamic_asr_map)
            stat_asr = dict(self._static_asr_map)
            dyn_target = dict(self._dynamic_target_map)
            stat_target = dict(self._static_target_map)

        ranked: list[tuple[int, str, str]] = []

        # 1. Translation glossary terms matching the active direction.
        for trans_map in (dyn_trans, stat_trans):
            for pair, entries in trans_map.items():
                pair_src, pair_tgt = pair.split("->", 1)
                if tgt and pair_tgt != tgt:
                    continue
                if src not in {"", "auto"} and pair_src != src:
                    continue
                for source, (priority, target) in entries.items():
                    ranked.append((priority, source, target))

        # 2. ASR fixes for the source language.
        for lang_map in (dyn_asr, stat_asr):
            for lang, entries in lang_map.items():
                if src not in {"", "auto"}:
                    if lang != src:
                        continue
                elif tgt and lang == tgt:
                    continue
                for source, (priority, target) in entries.items():
                    ranked.append((priority, source, target))

        # 3. Target fixes for the target language.
        if tgt:
            for lang_map in (dyn_target, stat_target):
                for source, (priority, target) in lang_map.get(tgt, {}).items():
                    ranked.append((priority, source, target))

        ranked.sort(key=lambda item: (item[0], item[1]))
        phrases: dict[str, str] = {}
        for _priority, source, target in ranked:
            if source not in phrases:
                phrases[source] = target
                if len(phrases) >= cap:
                    break
        return phrases
