"""Glossary storage, 3-bucket interval-locked engine and atomic persistence."""

from app.glossary.engine import (
    GlossaryEngine,
    IntervalLockedAutomaton,
    infer_lang_from_text,
    normalize_bucket_and_lang,
)
from app.glossary.store import (
    CATEGORIES,
    QUERY_CATEGORIES,
    GlossaryEntry,
    GlossaryPersistError,
    GlossaryStore,
)

__all__ = [
    "CATEGORIES",
    "QUERY_CATEGORIES",
    "GlossaryEngine",
    "GlossaryEntry",
    "GlossaryPersistError",
    "GlossaryStore",
    "IntervalLockedAutomaton",
    "infer_lang_from_text",
    "normalize_bucket_and_lang",
]
