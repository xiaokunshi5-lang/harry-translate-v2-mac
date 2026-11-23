"""Curated glossary store with 3-bucket isolation, wire budgets, and atomic persist."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.glossary.engine import GlossaryEngine, normalize_bucket_and_lang

CATEGORIES = (
    "asr_fixes",
    "terms_zh_to_en",
    "terms_en_to_zh",
)
QUERY_CATEGORIES = (
    *CATEGORIES,
    "terms_trans",
    "target_fixes",
)
DEFAULT_CLOUD_CAP = 250
MAX_CLOUD_CAP = 900
DEFAULT_CORPUS_WIRE_BYTES = 4096
MAX_CORPUS_WIRE_BYTES = 6144
MAX_HOTWORD_CHARS = 48
CURATED_PACK_IDS = ("00_base_emergency", "01_curated_everyday")
LOCK_RETRY_DELAYS: tuple[float, ...] = (0.02, 0.04, 0.08, 0.16, 0.32)


class GlossaryPersistError(RuntimeError):
    """Raised when atomic persistence to `config/glossary.json` fails."""


def _acquire_file_lock(fd: int) -> None:
    """Acquire an exclusive advisory/byte-range lock with lazy OS imports."""

    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _release_file_lock(fd: int) -> None:
    """Release an exclusive file lock with lazy OS imports."""

    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    source: str
    target: str
    category: str
    priority: int
    scope: str
    pack_id: str
    bucket: str = "asr_fixes"
    lang_key: str = "zh"

    def as_item(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "category": self.category,
            "priority": self.priority,
            "scope": self.scope,
            "pack_id": self.pack_id,
            "bucket": self.bucket,
            "lang_key": self.lang_key,
        }


class GlossaryStore:
    """Load the locked emergency pack and curated everyday pack synchronously."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        cloud_cap: int = DEFAULT_CLOUD_CAP,
        wire_budget_bytes: int = DEFAULT_CORPUS_WIRE_BYTES,
        glossary_enabled: bool = True,
        start_background_load: bool = True,
        retry_delays: tuple[float, ...] = LOCK_RETRY_DELAYS,
    ) -> None:
        self._root = Path(project_root).resolve()
        self._bank_dir = self._root / "glossary_bank"
        self._config_path = self._root / "config" / "glossary.json"
        self._cloud_cap = min(max(int(cloud_cap), 1), MAX_CLOUD_CAP)
        self._wire_budget_bytes = self._clamp_wire_budget(wire_budget_bytes)
        self._glossary_enabled = bool(glossary_enabled)
        self._retry_delays = tuple(retry_delays)
        self._lock = threading.RLock()
        self._loading_stage = "all_packs_ready"
        self._session_entries: dict[tuple[str, str, str], GlossaryEntry] = {}
        self._permanent_entries: dict[tuple[str, str, str], GlossaryEntry] = {}
        self._pack_entries: dict[tuple[str, str, str, str], GlossaryEntry] = {}
        self._static_entries: dict[tuple[str, str, str], GlossaryEntry] = {}
        self._static_sorted: list[GlossaryEntry] = []
        self._packs: dict[str, dict[str, Any]] = {}
        self._enabled_packs: set[str] = set()
        self._errors: list[str] = []
        self._engine = GlossaryEngine()
        self.init_ms = 0.0

        started = time.perf_counter()
        self._load_permanent()
        self._load_packs(CURATED_PACK_IDS)
        self._enabled_packs = self._read_enabled_packs()
        self._rebuild_static_cache()
        self._rebuild_static_engine()
        self._rebuild_dynamic_engine()
        self.init_ms = (time.perf_counter() - started) * 1000
        # Kept as a constructor compatibility flag during the single-window migration.
        _ = start_background_load

    @property
    def engine(self) -> GlossaryEngine:
        return self._engine

    @property
    def loading_stage(self) -> str:
        with self._lock:
            return self._loading_stage

    @property
    def init_ms_value(self) -> float:
        return self.init_ms

    @staticmethod
    def _clamp_wire_budget(value: int) -> int:
        return min(max(int(value), 0), MAX_CORPUS_WIRE_BYTES)

    def set_corpus_policy(self, *, enabled: bool, wire_budget_bytes: int) -> None:
        with self._lock:
            self._glossary_enabled = bool(enabled)
            self._wire_budget_bytes = self._clamp_wire_budget(wire_budget_bytes)

    def start_background_load(self) -> None:
        with self._lock:
            self._loading_stage = "all_packs_ready"

    def stop_background_load(self) -> None:
        return None

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        _ = timeout
        return self.loading_stage == "all_packs_ready"

    def _read_enabled_packs(self) -> set[str]:
        pack_ids = {path.stem for path in self._bank_dir.glob("*.json")}
        try:
            data = self._read_json(self._config_path)
            configured = data.get("_enabled_packs")
            if isinstance(configured, list):
                configured_ids = {str(item) for item in configured}
                return pack_ids.intersection(configured_ids)
        except Exception as error:
            self._record_error("glossary_config", error)
        return set(pack_ids)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("glossary_json_object_required")
        return data

    def _read_config_for_mutation(self) -> dict[str, Any]:
        if not self._config_path.is_file():
            return {
                "_version": "1.0",
                "description": "Harry Translate 专用专业术语与错别字纠偏语料库",
                "asr_fixes": {},
                "terms_zh_to_en": {},
                "terms_en_to_zh": {},
                "_enabled_packs": sorted(self._enabled_packs),
            }
        data = self._read_json(self._config_path)
        data.setdefault("asr_fixes", {})
        data.setdefault("terms_zh_to_en", {})
        data.setdefault("terms_en_to_zh", {})
        return data

    def _record_error(self, pack_id: str, error: BaseException) -> None:
        with self._lock:
            self._errors.append(f"{pack_id}: {type(error).__name__}")

    def _load_permanent(self) -> None:
        try:
            data = self._read_json(self._config_path)
        except Exception as error:
            self._record_error("config/glossary.json", error)
            return

        # 1. Flat ASR fixes (default zh, or nested dict[lang, dict])
        raw_asr = data.get("asr_fixes", {})
        if isinstance(raw_asr, dict):
            for key, value in raw_asr.items():
                if isinstance(value, dict):
                    for src, tgt in value.items():
                        self._add_raw_entry(
                            {"source": src, "target": tgt},
                            "asr_fixes",
                            "permanent",
                            1,
                            "permanent",
                            bucket="asr_fixes",
                            lang_key=str(key),
                        )
                else:
                    self._add_raw_entry(
                        {"source": key, "target": value},
                        "asr_fixes",
                        "permanent",
                        1,
                        "permanent",
                        bucket="asr_fixes",
                        lang_key="zh",
                    )

        # 2. Optional multi-language ASR fixes
        raw_asr_by_lang = data.get("asr_fixes_by_lang", {})
        if isinstance(raw_asr_by_lang, dict):
            for lang_key, mapping in raw_asr_by_lang.items():
                if not isinstance(mapping, dict):
                    continue
                for src, tgt in mapping.items():
                    self._add_raw_entry(
                        {"source": src, "target": tgt},
                        "asr_fixes",
                        "permanent",
                        1,
                        "permanent",
                        bucket="asr_fixes",
                        lang_key=str(lang_key),
                    )

        # 3. Legacy flat translation directions
        for legacy_category, direction in (
            ("terms_zh_to_en", "zh->en"),
            ("terms_en_to_zh", "en->zh"),
        ):
            mapping = data.get(legacy_category, {})
            if isinstance(mapping, dict):
                for src, tgt in mapping.items():
                    self._add_raw_entry(
                        {"source": src, "target": tgt},
                        legacy_category,
                        "permanent",
                        1,
                        "permanent",
                        bucket="terms_trans",
                        lang_key=direction,
                    )

        # 4. General multi-direction translation pairs
        raw_terms_trans = data.get("terms_trans", {})
        if isinstance(raw_terms_trans, dict):
            for direction, mapping in raw_terms_trans.items():
                if not isinstance(mapping, dict):
                    continue
                try:
                    bucket, norm_lang, legacy_cat = normalize_bucket_and_lang(
                        "terms_trans", str(direction)
                    )
                except ValueError:
                    continue
                for src, tgt in mapping.items():
                    self._add_raw_entry(
                        {"source": src, "target": tgt},
                        legacy_cat,
                        "permanent",
                        1,
                        "permanent",
                        bucket=bucket,
                        lang_key=norm_lang,
                    )

        # 5. Target-language normalization fixes
        raw_target_fixes = data.get("target_fixes", {})
        if isinstance(raw_target_fixes, dict):
            for lang_key, mapping in raw_target_fixes.items():
                if not isinstance(mapping, dict):
                    continue
                for src, tgt in mapping.items():
                    self._add_raw_entry(
                        {"source": src, "target": tgt},
                        "target_fixes",
                        "permanent",
                        1,
                        "permanent",
                        bucket="target_fixes",
                        lang_key=str(lang_key),
                    )

    def _load_packs(self, pack_ids: Iterable[str]) -> None:
        allowed_pack_ids = set(pack_ids)
        for path in sorted(self._bank_dir.glob("*.json")):
            if path.stem not in allowed_pack_ids:
                continue
            try:
                data = self._read_json(path)
                pack = {
                    "id": path.stem,
                    "name": str(data.get("name") or path.stem.replace("_", " ")),
                    "icon": str(data.get("icon") or "Book"),
                    "tier": str(data.get("tier") or ("base" if path.stem[:2] == "00" else "bank")),
                    "priority": int(data.get("priority") or int(path.stem[:2])),
                    "locked": bool(data.get("locked", False)),
                    "default_enabled": bool(data.get("default_enabled", True)),
                    "counts_by_category": {
                        category: len(data.get(category, {})) for category in CATEGORIES
                    },
                    "terms_count": sum(
                        len(data.get(category, {})) for category in CATEGORIES
                    ),
                }
                with self._lock:
                    self._packs[path.stem] = pack
                priority = 2 if path.stem in CURATED_PACK_IDS else 3
                scope = "base" if path.stem == "00_base_emergency" else "bank"
                for category in CATEGORIES:
                    for source, target in data.get(category, {}).items():
                        bucket, lang_key, legacy_cat = normalize_bucket_and_lang(category)
                        self._add_raw_entry(
                            {"source": source, "target": target},
                            legacy_cat,
                            path.stem,
                            priority,
                            scope,
                            bucket=bucket,
                            lang_key=lang_key,
                        )
            except Exception as error:
                self._record_error(path.stem, error)

    def _add_raw_entry(
        self,
        term: Any,
        category: str,
        pack_id: str,
        priority: int,
        scope: str,
        *,
        bucket: str | None = None,
        lang_key: str | None = None,
    ) -> None:
        if not isinstance(term, dict):
            return
        source = str(term.get("source") or term.get("wrong") or "").strip()
        target = str(term.get("target") or term.get("correct") or "").strip()
        # Filter reflexive (key == value) entries so no-op pairs never pollute matching.
        if not source or not target or source == target:
            return
        norm_bucket, norm_lang, norm_cat = normalize_bucket_and_lang(
            bucket or category, lang_key
        )
        entry = GlossaryEntry(
            source=source,
            target=target,
            category=norm_cat,
            priority=priority,
            scope=scope,
            pack_id=pack_id,
            bucket=norm_bucket,
            lang_key=norm_lang,
        )
        with self._lock:
            if scope == "permanent":
                self._permanent_entries[(norm_bucket, norm_lang, source)] = entry
            elif scope == "session":
                self._session_entries[(norm_bucket, norm_lang, source)] = entry
            else:
                self._pack_entries[(pack_id, norm_bucket, norm_lang, source)] = entry

    def _snapshot_enabled_pack_entries(self) -> list[GlossaryEntry]:
        with self._lock:
            enabled = self._enabled_packs or {
                pack_id for pack_id, pack in self._packs.items() if pack["default_enabled"]
            }
            entries = [
                entry
                for entry in self._pack_entries.values()
                if entry.pack_id in enabled
            ]
        entries.sort(key=lambda item: (item.priority, item.pack_id, item.bucket, item.lang_key, item.source))
        return entries

    def _rebuild_static_engine(self) -> None:
        self._engine.rebuild_static(self._snapshot_enabled_pack_entries())

    def _rebuild_dynamic_engine(self) -> None:
        with self._lock:
            dynamic_snapshot = [
                *self._session_entries.values(),
                *self._permanent_entries.values(),
            ]
        self._engine.rebuild_dynamic(dynamic_snapshot)

    def _rebuild_static_cache(self) -> None:
        enabled = self._enabled_packs or {
            pack_id for pack_id, pack in self._packs.items() if pack["default_enabled"]
        }
        pack_entries = [
            entry for entry in self._pack_entries.values() if entry.pack_id in enabled
        ]
        pack_entries.sort(key=lambda item: (item.priority, item.pack_id))
        static_cache: dict[tuple[str, str, str], GlossaryEntry] = {}
        for entry in pack_entries:
            static_cache.setdefault((entry.bucket, entry.lang_key, entry.source), entry)
        self._static_entries = static_cache
        self._static_sorted = sorted(
            static_cache.values(),
            key=lambda entry: (
                entry.priority,
                entry.pack_id,
                entry.bucket,
                entry.lang_key,
                entry.source,
            ),
        )

    def _iter_tier_buckets(self) -> list[GlossaryEntry]:
        with self._lock:
            dynamic: dict[tuple[str, str, str], GlossaryEntry] = {}
            for entry in self._session_entries.values():
                dynamic.setdefault((entry.bucket, entry.lang_key, entry.source), entry)
            for entry in self._permanent_entries.values():
                dynamic.setdefault((entry.bucket, entry.lang_key, entry.source), entry)
            static_sorted = self._static_sorted
        dynamic_sorted = sorted(
            dynamic.values(),
            key=lambda entry: (
                entry.priority,
                entry.pack_id,
                entry.bucket,
                entry.lang_key,
                entry.source,
            ),
        )
        tail = [
            entry
            for entry in static_sorted
            if (entry.bucket, entry.lang_key, entry.source) not in dynamic
        ]
        return [*dynamic_sorted, *tail]

    def _active_entries(
        self,
        *,
        category: str = "all",
        scope: str = "all",
        pack_id: str = "",
        query: str = "",
    ) -> list[GlossaryEntry]:
        entries = self._iter_tier_buckets()
        if category != "all":
            if category == "terms_trans":
                entries = [entry for entry in entries if entry.bucket == "terms_trans"]
            elif category == "target_fixes":
                entries = [entry for entry in entries if entry.bucket == "target_fixes"]
            else:
                entries = [entry for entry in entries if entry.category == category]
        if scope != "all":
            entries = [entry for entry in entries if entry.scope == scope]
        if pack_id:
            entries = [entry for entry in entries if entry.pack_id == pack_id]
        needle = query.strip().casefold()
        if needle:
            entries = [
                entry
                for entry in entries
                if needle in entry.source.casefold() or needle in entry.target.casefold()
            ]
        return entries

    def get_active_terms(
        self,
        *,
        category: str = "all",
        scope: str = "all",
        pack_id: str = "",
        query: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        entries = self._active_entries(
            category=category, scope=scope, pack_id=pack_id, query=query
        )
        safe_limit = min(max(int(limit), 1), 500)
        safe_offset = max(int(offset), 0)
        counts: dict[str, int] = {name: 0 for name in QUERY_CATEGORIES}
        for entry in self._iter_tier_buckets():
            if entry.category in counts:
                counts[entry.category] += 1
            if entry.bucket == "terms_trans" and entry.category != "terms_trans":
                counts["terms_trans"] += 1
        return {
            "total": len(entries),
            "limit": safe_limit,
            "offset": safe_offset,
            "counts_by_category": counts,
            "loading_stage": self.loading_stage,
            "items": [entry.as_item() for entry in entries[safe_offset : safe_offset + safe_limit]],
            "quota": self.get_quota_status(),
        }

    def add_session_term(
        self,
        source: str,
        target: str,
        *,
        bucket: str = "terms_trans",
        lang_key: str = "zh->en",
        direction: str | None = None,
    ) -> GlossaryEntry:
        clean_source = str(source).strip()
        clean_target = str(target).strip()
        if not clean_source or not clean_target:
            raise ValueError("term_source_and_target_required")
        if clean_source == clean_target:
            raise ValueError("reflexive_term_rejected")
        norm_bucket, norm_lang, norm_cat = normalize_bucket_and_lang(
            bucket, lang_key, direction=direction
        )
        entry = GlossaryEntry(
            source=clean_source,
            target=clean_target,
            category=norm_cat,
            priority=0,
            scope="session",
            pack_id="session",
            bucket=norm_bucket,
            lang_key=norm_lang,
        )
        with self._lock:
            self._session_entries[(norm_bucket, norm_lang, clean_source)] = entry
            dynamic_snapshot = [
                *self._session_entries.values(),
                *self._permanent_entries.values(),
            ]
        # Hot-rebuild only the lightweight P0+P1 trie (< 1ms, 0 disk write).
        self._engine.rebuild_dynamic(dynamic_snapshot)
        return entry

    def list_session_terms(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = sorted(
                self._session_entries.values(),
                key=lambda item: (item.bucket, item.lang_key, item.source),
            )
        return [entry.as_item() for entry in entries]

    def promote_term(
        self,
        source: str,
        target: str | None = None,
        *,
        bucket: str = "terms_trans",
        lang_key: str = "zh->en",
        direction: str | None = None,
    ) -> GlossaryEntry:
        clean_source = str(source).strip()
        if not clean_source:
            raise ValueError("term_source_required")
        norm_bucket, norm_lang, norm_cat = normalize_bucket_and_lang(
            bucket, lang_key, direction=direction
        )

        with self._lock:
            key = (norm_bucket, norm_lang, clean_source)
            existing_session = self._session_entries.get(key)
            resolved_target = (
                str(target).strip()
                if target is not None
                else (existing_session.target if existing_session is not None else "")
            )
            if not resolved_target:
                raise KeyError(clean_source)
            if clean_source == resolved_target:
                raise ValueError("reflexive_term_rejected")

            data = self._read_config_for_mutation()
            if norm_bucket == "asr_fixes":
                if norm_lang == "zh":
                    section = data.setdefault("asr_fixes", {})
                    if not isinstance(section, dict):
                        section = {}
                        data["asr_fixes"] = section
                    section[clean_source] = resolved_target
                else:
                    by_lang = data.setdefault("asr_fixes_by_lang", {})
                    if not isinstance(by_lang, dict):
                        by_lang = {}
                        data["asr_fixes_by_lang"] = by_lang
                    by_lang.setdefault(norm_lang, {})[clean_source] = resolved_target
            elif norm_bucket == "terms_trans":
                if norm_lang == "zh->en":
                    data.setdefault("terms_zh_to_en", {})[clean_source] = resolved_target
                elif norm_lang == "en->zh":
                    data.setdefault("terms_en_to_zh", {})[clean_source] = resolved_target
                else:
                    trans_section = data.setdefault("terms_trans", {})
                    if not isinstance(trans_section, dict):
                        trans_section = {}
                        data["terms_trans"] = trans_section
                    trans_section.setdefault(norm_lang, {})[clean_source] = resolved_target
            else:
                target_section = data.setdefault("target_fixes", {})
                if not isinstance(target_section, dict):
                    target_section = {}
                    data["target_fixes"] = target_section
                target_section.setdefault(norm_lang, {})[clean_source] = resolved_target

            # Persist atomically under cross-platform file lock BEFORE updating memory.
            self._persist_to_disk_locked(data)

            promoted = GlossaryEntry(
                source=clean_source,
                target=resolved_target,
                category=norm_cat,
                priority=1,
                scope="permanent",
                pack_id="permanent",
                bucket=norm_bucket,
                lang_key=norm_lang,
            )
            self._permanent_entries[key] = promoted
            self._session_entries.pop(key, None)
            dynamic_snapshot = [
                *self._session_entries.values(),
                *self._permanent_entries.values(),
            ]
        self._engine.rebuild_dynamic(dynamic_snapshot)
        return promoted

    def clear_session(self) -> int:
        with self._lock:
            cleared = len(self._session_entries)
            if cleared == 0:
                return 0
            self._session_entries.clear()
            dynamic_snapshot = list(self._permanent_entries.values())
        # Zero disk writes on session clear.
        self._engine.rebuild_dynamic(dynamic_snapshot)
        return cleared

    def apply_fixes(
        self,
        source_text: str,
        target_text: str,
        *,
        source_lang: str = "auto",
        target_lang: str = "",
    ) -> tuple[str, str]:
        return self._engine.apply_fixes(
            source_text,
            target_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )

    def get_hotwords(self) -> dict[str, str]:
        entries = self._iter_tier_buckets()
        return {entry.source: entry.target for entry in entries[: self._cloud_cap]}

    def get_corpus_phrases(
        self,
        *,
        source_lang: str = "auto",
        target_lang: str = "",
        limit: int | None = None,
    ) -> dict[str, str]:
        cap = min(max(int(limit or self._cloud_cap), 1), MAX_CLOUD_CAP)
        if target_lang or source_lang not in {"", "auto"}:
            return self._engine.get_corpus_phrases(
                source_lang=source_lang,
                target_lang=target_lang,
                limit=cap,
            )
        entries = self._iter_tier_buckets()
        return {entry.source: entry.target for entry in entries[:cap]}

    @staticmethod
    def _phrases_wire_bytes(phrases: dict[str, str]) -> int:
        return len(json.dumps(phrases, ensure_ascii=False).encode("utf-8"))

    def get_budgeted_corpus_phrases(
        self,
        *,
        source_lang: str = "auto",
        target_lang: str = "",
        max_bytes: int = DEFAULT_CORPUS_WIRE_BYTES,
        hard_cap: int = MAX_CORPUS_WIRE_BYTES,
    ) -> dict[str, str]:
        """Return P0 > P1 > P2 terms that fit the strict UTF-8 wire budget."""

        with self._lock:
            if not self._glossary_enabled:
                return {}
            configured_budget = self._wire_budget_bytes
        safe_hard_cap = min(max(int(hard_cap), 0), MAX_CORPUS_WIRE_BYTES)
        safe_budget = min(max(int(max_bytes), 0), safe_hard_cap, configured_budget)
        if safe_budget <= 2:
            return {}

        candidates = (
            self._engine.get_corpus_phrases(
                source_lang=source_lang,
                target_lang=target_lang,
                limit=MAX_CLOUD_CAP,
            )
            if target_lang or source_lang not in {"", "auto"}
            else {
                entry.source: entry.target
                for entry in self._iter_tier_buckets()[:MAX_CLOUD_CAP]
            }
        )
        phrases: dict[str, str] = {}
        for source, target in candidates.items():
            if not source or not target or len(source) > MAX_HOTWORD_CHARS:
                continue
            source = str(source).strip()
            target = str(target).strip()
            if not source or not target or source in phrases:
                continue
            candidate = dict(phrases)
            candidate[source] = target
            if self._phrases_wire_bytes(candidate) <= safe_budget:
                phrases = candidate
        return phrases

    def check_conflict(self, source: str) -> dict[str, Any] | None:
        matches = [entry for entry in self._active_entries() if entry.source == source]
        return matches[0].as_item() if matches else None

    def get_quota_status(self) -> dict[str, int]:
        with self._lock:
            session_count = len(self._session_entries)
            dynamic_keys = {
                *self._session_entries.keys(),
                *self._permanent_entries.keys(),
            }
            new_dynamic = sum(1 for key in dynamic_keys if key not in self._static_entries)
            active_count = len(self._static_entries) + new_dynamic
            wire_budget = self._wire_budget_bytes
        wire_phrases = self.get_budgeted_corpus_phrases(max_bytes=wire_budget)
        return {
            "cloud_cap": self._cloud_cap,
            "provider_limit": 1000,
            "active_terms": active_count,
            "local_terms": active_count,
            "session_terms": session_count,
            "wire_bytes": self._phrases_wire_bytes(wire_phrases),
            "wire_budget_bytes": wire_budget,
        }

    def get_packs(self) -> dict[str, Any]:
        with self._lock:
            packs = [dict(pack) for pack in self._packs.values()]
            enabled = set(self._enabled_packs)
            errors = list(self._errors)
        for pack in packs:
            pack["enabled"] = pack["id"] in enabled or pack["locked"]
        return {
            "packs": sorted(packs, key=lambda pack: pack["priority"]),
            "quota": self.get_quota_status(),
            "loading_stage": self.loading_stage,
            "errors": errors,
        }

    def toggle_pack(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            pack = self._packs.get(pack_id)
            if pack is None:
                raise KeyError(pack_id)
            if pack["locked"]:
                raise PermissionError("locked_pack")
            previous = set(self._enabled_packs)
            if enabled:
                self._enabled_packs.add(pack_id)
            else:
                self._enabled_packs.discard(pack_id)
            try:
                self._write_enabled_packs()
            except Exception:
                self._enabled_packs = previous
                raise
            self._rebuild_static_cache()
            static_snapshot = self._snapshot_enabled_pack_entries()
        self._engine.rebuild_static(static_snapshot)
        return self.get_packs()

    def _write_enabled_packs(self) -> None:
        data = self._read_config_for_mutation()
        data["_enabled_packs"] = sorted(self._enabled_packs)
        self._persist_to_disk_locked(data)

    def _persist_to_disk_locked(self, data: dict[str, Any]) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._config_path.with_name(f"{self._config_path.name}.lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        temp_name = ""
        try:
            if os.fstat(lock_fd).st_size == 0:
                os.write(lock_fd, b"\0")
            _acquire_file_lock(lock_fd)
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    dir=self._config_path.parent,
                    prefix=".glossary_",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temp_name = handle.name
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())

                last_error: BaseException | None = None
                total_attempts = 1 + len(self._retry_delays)
                for attempt in range(total_attempts):
                    try:
                        os.replace(temp_name, self._config_path)
                        temp_name = ""
                        last_error = None
                        break
                    except (PermissionError, OSError) as error:
                        last_error = error
                        if attempt < len(self._retry_delays):
                            time.sleep(self._retry_delays[attempt])
                if last_error is not None:
                    raise GlossaryPersistError(
                        f"glossary_persist_failed: {last_error}"
                    ) from last_error
            finally:
                _release_file_lock(lock_fd)
        finally:
            if temp_name:
                try:
                    Path(temp_name).unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
