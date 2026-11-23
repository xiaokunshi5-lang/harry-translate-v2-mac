from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

from app.engines.base import (
    EngineErrorEvent,
    EngineState,
    EngineStateEvent,
    RealtimeTranslatorEngine,
    TranscriptDelta,
    TranscriptFinal,
    parse_engine_state,
)
from app.engines.openai_compatible import OpenAICompatibleAdapter
from app.engines.registry import EngineRegistry, UnsupportedLanguagePairError

ROOT = Path(__file__).resolve().parents[2]


class EngineAbstractionTests(unittest.TestCase):
    def test_abc_contract_rejects_incomplete_subclasses(self) -> None:
        class IncompleteEngine(RealtimeTranslatorEngine):
            pass

        with self.assertRaises(TypeError):
            IncompleteEngine()

    def test_event_contracts_have_no_metering_fields(self) -> None:
        forbidden = {
            "cost",
            "cost_rmb",
            "input_tokens",
            "output_tokens",
            "summary_tokens",
            "total_tokens",
            "usage",
            "usage_stats",
        }
        for event_type in (
            TranscriptDelta,
            TranscriptFinal,
            EngineStateEvent,
            EngineErrorEvent,
        ):
            with self.subTest(event=event_type.__name__):
                fields = {field.name for field in dataclasses.fields(event_type)}
                self.assertFalse(fields & forbidden)

    def test_seven_state_contract_rejects_unknown_strings(self) -> None:
        self.assertEqual({state.value for state in EngineState}, {
            "idle",
            "connecting",
            "ready",
            "reconnecting",
            "auth_error",
            "quota_error",
            "closed",
        })
        self.assertEqual(parse_engine_state("ready"), EngineState.READY)
        with self.assertRaises(ValueError):
            parse_engine_state("pretending_ready")

    def test_openai_compatible_skeleton_obeys_contract_without_sdk(self) -> None:
        engine = OpenAICompatibleAdapter(target_lang="zh", source_lang="en")
        self.assertFalse(engine.connect())
        self.assertEqual(engine.current_state, EngineState.AUTH_ERROR)
        self.assertFalse(engine.send_audio_chunk(b"\x00" * 1_600))
        self.assertEqual(engine.session_update_count, 0)
        engine.close()
        self.assertEqual(engine.current_state, EngineState.CLOSED)


class EngineRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = EngineRegistry(ROOT / "config" / "provider_catalog.json")

    def test_required_eight_languages_and_auto_are_declared(self) -> None:
        catalog = self.registry.get_catalog()
        qwen = catalog["providers"]["qwen"]
        expected = {"zh", "en", "fr", "es", "it", "ja", "ko", "de"}
        self.assertTrue(expected.issubset(qwen["target_languages"]))
        self.assertTrue(expected | {"auto"} <= set(qwen["source_languages"]))
        self.assertEqual(qwen["source_detection"], "automatic")
        self.assertIn("workspace_id_is_required_in_the_private_maas_hostname", qwen["limitations"])

    def test_valid_pairs_create_engine_and_invalid_pairs_fail_before_creation(self) -> None:
        created: list[OpenAICompatibleAdapter] = []

        def factory(**kwargs: object) -> OpenAICompatibleAdapter:
            engine = OpenAICompatibleAdapter(**kwargs)  # type: ignore[arg-type]
            created.append(engine)
            return engine

        self.registry.register("qwen", factory)
        engine = self.registry.create_engine(
            "qwen",
            source_lang="auto",
            target_lang="fr",
        )
        self.assertIs(engine, created[0])

        with self.assertRaises(UnsupportedLanguagePairError) as raised:
            self.registry.create_engine(
                "qwen",
                source_lang="xx",
                target_lang="yy",
            )
        self.assertEqual(raised.exception.http_status, 400)
        self.assertEqual(len(created), 1)

    def test_unknown_provider_returns_structured_400_error(self) -> None:
        with self.assertRaises(UnsupportedLanguagePairError) as raised:
            self.registry.validate_language_pair("missing", "en", "zh")
        self.assertEqual(raised.exception.code, "unsupported_language_pair")
        self.assertEqual(raised.exception.as_dict()["error"], "unsupported_language_pair")


if __name__ == "__main__":
    unittest.main()
