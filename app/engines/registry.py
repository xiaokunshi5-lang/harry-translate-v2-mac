"""Provider catalog validation and engine factory registration."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.engines.base import RealtimeTranslatorEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "config" / "provider_catalog.json"


class UnsupportedLanguagePairError(ValueError):
    """A request that maps to HTTP 400 before touching an active session."""

    def __init__(
        self,
        provider: str,
        source_lang: str,
        target_lang: str,
        message: str = "unsupported_language_pair",
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.code = "unsupported_language_pair"
        self.http_status = 400

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "provider": self.provider,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "status": self.http_status,
        }


class EngineRegistry:
    """Load a safe provider matrix and create only registered engine types."""

    def __init__(self, catalog_path: Path | str = DEFAULT_CATALOG_PATH) -> None:
        self.catalog_path = Path(catalog_path).resolve()
        self._catalog = self._load_catalog(self.catalog_path)
        self._factories: dict[str, Callable[..., RealtimeTranslatorEngine]] = {}

    @staticmethod
    def _load_catalog(path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            catalog = json.load(handle)
        if not isinstance(catalog, dict) or not isinstance(catalog.get("providers"), dict):
            raise ValueError("provider_catalog_must_contain_providers")
        return catalog

    def register(
        self,
        provider_id: str,
        factory: Callable[..., RealtimeTranslatorEngine],
    ) -> None:
        if provider_id not in self._catalog["providers"]:
            raise ValueError(f"unknown_provider: {provider_id}")
        if not callable(factory):
            raise TypeError("engine_factory_must_be_callable")
        self._factories[provider_id] = factory

    def provider_config(self, provider_id: str) -> dict[str, Any]:
        if provider_id not in self._catalog["providers"]:
            raise UnsupportedLanguagePairError(
                provider_id,
                "",
                "",
                message="unknown_provider",
            )
        provider = self._catalog["providers"][provider_id]
        if not isinstance(provider, dict):
            raise ValueError(f"provider_catalog_entry_invalid: {provider_id}")
        return provider

    def validate_language_pair(
        self,
        provider_id: str,
        source_lang: str,
        target_lang: str,
    ) -> None:
        provider = self.provider_config(provider_id)
        sources = set(provider.get("source_languages", []))
        targets = set(provider.get("target_languages", []))
        if source_lang not in sources or target_lang not in targets:
            raise UnsupportedLanguagePairError(provider_id, source_lang, target_lang)

    def create_engine(
        self,
        provider_id: str,
        *args: Any,
        source_lang: str,
        target_lang: str,
        **kwargs: Any,
    ) -> RealtimeTranslatorEngine:
        self.validate_language_pair(provider_id, source_lang, target_lang)
        factory = self._factories.get(provider_id)
        if factory is None:
            raise ValueError(f"engine_factory_not_registered: {provider_id}")
        provider = self.provider_config(provider_id)
        kwargs.setdefault("model", provider.get("model", ""))
        return factory(
            *args,
            source_lang=source_lang,
            target_lang=target_lang,
            **kwargs,
        )

    def get_catalog(self) -> dict[str, Any]:
        return deepcopy(self._catalog)
