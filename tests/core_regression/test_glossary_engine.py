from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from pathlib import Path

from app.glossary import (
    GlossaryEngine,
    GlossaryEntry,
    GlossaryStore,
    IntervalLockedAutomaton,
)
from app.session_manager import SessionManager

ROOT = Path(__file__).resolve().parents[2]


class GlossaryEngineTests(unittest.TestCase):
    def test_direction_isolation(self) -> None:
        config_data = json.loads((ROOT / "config" / "glossary.json").read_text(encoding="utf-8"))
        reflexive_count = sum(
            1
            for section in ("asr_fixes", "terms_zh_to_en", "terms_en_to_zh")
            for key, value in config_data.get(section, {}).items()
            if str(key).strip() == str(value).strip()
        )
        self.assertEqual(reflexive_count, 0)
        self.assertEqual(
            set(config_data.get("_enabled_packs", [])),
            {"00_base_emergency", "01_curated_everyday"},
        )

        engine = GlossaryEngine()
        engine.rebuild_dynamic(
            [
                GlossaryEntry("反向穿破", "反向传播", "asr_fixes", 1, "permanent", "permanent", "asr_fixes", "zh"),
                GlossaryEntry("gen ai", "Generative AI", "asr_fixes", 1, "permanent", "permanent", "asr_fixes", "en"),
                GlossaryEntry("反向传播", "Backpropagation", "terms_zh_to_en", 1, "permanent", "permanent", "terms_trans", "zh->en"),
                GlossaryEntry("黄某", "Someone", "terms_zh_to_en", 1, "permanent", "permanent", "terms_trans", "zh->en"),
                GlossaryEntry("Open AI", "OpenAI", "target_fixes", 1, "permanent", "permanent", "target_fixes", "en"),
                GlossaryEntry("大语言模", "大语言模型", "target_fixes", 1, "permanent", "permanent", "target_fixes", "zh"),
            ]
        )

        src_zh, tgt_en = engine.apply_fixes(
            "我们研究反向穿破",
            "We study Backpropagation and Open AI with 黄某",
            source_lang="zh",
            target_lang="en",
        )
        self.assertEqual(src_zh, "我们研究反向传播")
        self.assertEqual(tgt_en, "We study Backpropagation and OpenAI with 黄某")

        src_en, tgt_zh = engine.apply_fixes(
            "We use gen ai",
            "我们使用大语言模和Open AI",
            source_lang="en",
            target_lang="zh",
        )
        self.assertEqual(src_en, "We use Generative AI")
        self.assertEqual(tgt_zh, "我们使用大语言模型和Open AI")

        # Empty or unknown language passthrough must never crash.
        self.assertEqual(engine.apply_fixes("", "", source_lang="unknown", target_lang="xx"), ("", ""))
        self.assertEqual(
            engine.apply_fixes("Bonjour", "Bonjour", source_lang="unknown", target_lang="xx"),
            ("Bonjour", "Bonjour"),
        )

    def test_interval_lock_prevents_cascading_and_protects_ascii_boundaries(self) -> None:
        store = GlossaryStore(ROOT)
        try:
            self.assertTrue(store.wait_until_ready(5))
            # Build the curated automaton plus synthetic volume patterns, then inject P0 override.
            automaton = IntervalLockedAutomaton()
            static_patterns = [
                (entry.source, entry.target, entry.priority)
                for entry in store._snapshot_enabled_pack_entries()
            ]
            self.assertGreaterEqual(len(static_patterns), 249)
            static_patterns.extend(
                [
                    ("Huang", "黄某", 2),
                    ("AI", "人工智能", 3),
                    *[
                        (f"批量压力词{index}", f"StressTerm{index}", 3)
                        for index in range(1_000)
                    ],
                ]
            )
            automaton.update_static(static_patterns)

            hot_started = time.perf_counter()
            automaton.update_dynamic([("黄仁勋", "Jason Huang", 0)])
            hot_add_ms = (time.perf_counter() - hot_started) * 1000
            self.assertLess(hot_add_ms, 5.0)

            sample = "黄仁勋在OpenAI大会谈rain和AI技术"
            expected = "Jason Huang在OpenAI大会谈rain和人工智能技术"
            started = time.perf_counter()
            for _ in range(1_000):
                result = automaton.replace_all(sample)
                self.assertEqual(result, expected)
            avg_ms = ((time.perf_counter() - started) * 1000) / 1_000
            self.assertLess(avg_ms, 0.5)

            # Curated GlossaryStore hot session add and apply_fixes timing.
            add_started = time.perf_counter()
            store.add_session_term("黄仁勋", "Jason Huang", bucket="asr_fixes", lang_key="zh")
            store_hot_ms = (time.perf_counter() - add_started) * 1000
            self.assertLess(store_hot_ms, 5.0)
            fixed_src, fixed_tgt = store.apply_fixes(
                "黄仁勋谈落拉",
                "OpenAI is great",
                source_lang="auto",
                target_lang="en",
            )
            self.assertEqual(fixed_src, "Jason Huang谈LoRA")
            self.assertEqual(fixed_tgt, "OpenAI is great")
        finally:
            store.stop_background_load()

    def test_priority_overlap_longest_match_and_concurrent_swap(self) -> None:
        automaton = IntervalLockedAutomaton()
        automaton.update_static(
            [
                ("A B", "X", 3),
                ("B", "C", 3),
                ("A", "B", 3),
                ("人工智能体", "AI Agent", 2),
                ("人工智能", "AI", 2),
            ]
        )
        automaton.update_dynamic([("B C", "P0_BC", 0)])

        # Overlapping P0 ("B C" at [2,5)) beats earlier P3 ("A B" at [0,3)); "A" then matches [0,1).
        self.assertEqual(automaton.replace_all("A B C"), "B P0_BC")
        # Same-tier longest match ("A B" -> "X") beats shorter "A".
        self.assertEqual(automaton.replace_all("A B"), "X")
        # Single-pass no-cascade on non-overlapping "A, B" -> "B, C" (NOT "C, C").
        self.assertEqual(automaton.replace_all("A, B"), "B, C")
        # Same-tier longest match wins over shorter prefix.
        self.assertEqual(automaton.replace_all("测试人工智能体"), "测试AI Agent")

        errors: list[BaseException] = []
        stop = threading.Event()

        def reader() -> None:
            try:
                while not stop.is_set():
                    out = automaton.replace_all("测试人工智能体与A B C")
                    self.assertIn("AI Agent", out)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=reader)
        thread.start()
        for step in range(30):
            automaton.update_static([("人工智能体", "AI Agent", 2), (f"词{step}", f"T{step}", 3)])
        stop.set()
        thread.join(timeout=2)
        self.assertEqual(errors, [])

    def test_session_manager_applies_glossary_without_metering(self) -> None:
        engine = GlossaryEngine()
        engine.rebuild_dynamic(
            [
                GlossaryEntry("落拉", "LoRA", "asr_fixes", 1, "permanent", "permanent", "asr_fixes", "zh"),
                GlossaryEntry("Open AI", "OpenAI", "target_fixes", 1, "permanent", "permanent", "target_fixes", "en"),
            ]
        )
        manager = SessionManager(lambda: False, glossary_engine=engine)
        manager.set_channel_languages("en", "zh", "en")

        class CaptureClient:
            def __init__(self) -> None:
                self.items: list[dict[str, object]] = []

            async def send_text(self, message: str) -> None:
                self.items.append(json.loads(message))

            async def close(self) -> None:
                return None

        client = CaptureClient()
        manager.add_client("en", client)
        asyncio.run(
            manager.publish_event(
                "en",
                {
                    "type": "subtitle",
                    "id": 1,
                    "source_text": "我们讨论落拉",
                    "target_text": "We discuss Open AI",
                    "is_final": True,
                },
            )
        )
        self.assertEqual(len(client.items), 1)
        self.assertEqual(client.items[0]["source_text"], "我们讨论LoRA")
        self.assertEqual(client.items[0]["target_text"], "We discuss OpenAI")
        self.assertNotIn("usage", client.items[0])


if __name__ == "__main__":
    unittest.main()
