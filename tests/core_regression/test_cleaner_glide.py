from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import time
import unittest
from pathlib import Path
from typing import Any

from app.session_manager import MAX_OVERLAY_WINDOWS, SessionManager
from app.transcript_cleaner import (
    DANGLING,
    SingleSessionCleaner,
    assign_para,
    clean_speech,
    mend_fragment,
)

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FORBIDDEN_KEYS = {
    "usage",
    "usage_stats",
    "input_tokens",
    "output_tokens",
    "summary_tokens",
    "total_tokens",
}


class _RecordingClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.messages.append(json.loads(message))

    async def close(self) -> None:
        return None


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(FORBIDDEN_KEYS.intersection(value))
        for child in value.values():
            found.update(_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


class EchoCleanerRulesTests(unittest.TestCase):
    def test_fillers_and_repeat_guards_match_verified_source(self) -> None:
        self.assertEqual(clean_speech("Uhm, I uhh think so."), "I think so.")
        self.assertEqual(
            clean_speech("we need to we need to ship"),
            "we need to ship",
        )
        self.assertEqual(clean_speech("The the project starts."), "The project starts.")
        self.assertEqual(
            clean_speech("that that is very very correct"),
            "that that is very very correct",
        )

    def test_fragment_prefix_and_near_slip_rules_are_conservative(self) -> None:
        self.assertEqual(clean_speech("proj project scope"), "project scope")
        self.assertEqual(clean_speech("translatr translate now"), "translate now")
        self.assertEqual(clean_speech("the cat cat sat"), "the cat sat")
        self.assertEqual(clean_speech("OpenAI API"), "OpenAI API")

    def test_every_verified_dangling_word_repairs_false_period(self) -> None:
        expected = {
            "of",
            "the",
            "a",
            "an",
            "to",
            "and",
            "or",
            "but",
            "than",
            "between",
            "into",
            "whose",
            "whether",
            "because",
            "our",
            "your",
            "their",
            "its",
        }
        self.assertEqual(DANGLING, expected)
        for word in sorted(expected):
            with self.subTest(word=word):
                merged, previous, following = mend_fragment(
                    f"We talked about {word}.",
                    "Project scope.",
                    gap_ms=100.0,
                )
                self.assertTrue(merged)
                self.assertEqual(previous, f"We talked about {word}")
                self.assertEqual(following, "project scope.")

    def test_quotes_abbreviations_and_long_gaps_do_not_merge(self) -> None:
        self.assertEqual(
            mend_fragment('We talked about "the."', "Project scope.", 100.0)[0],
            False,
        )
        self.assertEqual(
            mend_fragment("We spoke with Mr.", "Project scope.", 100.0)[0],
            False,
        )
        self.assertEqual(
            mend_fragment("We talked about the.", "Project scope.", 301.0)[0],
            False,
        )
        merged, previous, following = mend_fragment(
            "We talked about the.",
            "Project scope.",
            None,
            protected_terms=("Project",),
        )
        self.assertTrue(merged)
        self.assertEqual(previous, "We talked about the")
        self.assertEqual(following, "Project scope.")

    def test_paragraph_soft_and_hard_boundaries(self) -> None:
        sentences = [f"Sentence {index}." for index in range(1, 8)]
        groups = assign_para(sentences)
        self.assertEqual([len(group) for group in groups], [6, 1])

        no_false_soft_break = [f"Sentence {index}." for index in range(1, 6)]
        no_false_soft_break.append("This ends with the.")
        self.assertEqual(len(assign_para(no_false_soft_break)), 1)

        hard_cap = ["No punctuation here"] * 12
        self.assertEqual([len(group) for group in assign_para(hard_cap)], [12])

        long_sentence = "x" * 521 + "."
        self.assertEqual([len(group) for group in assign_para([long_sentence])], [1])

    def test_single_session_merge_and_reset(self) -> None:
        now = [10.0]
        cleaner = SingleSessionCleaner(clock=lambda: now[0])
        first = cleaner.finalize_utterance(
            "We talked about the.",
            "我们谈到了。",
            sent_id=1,
            source_lang="en",
            target_lang="zh",
        )
        self.assertEqual(first[0], "We talked about the")

        now[0] += 0.2
        second = cleaner.finalize_utterance(
            "Project scope.",
            "项目范围。",
            sent_id=2,
            source_lang="en",
            target_lang="zh",
        )
        self.assertEqual(second[0], "We talked about the project scope.")
        self.assertEqual(second[1], "我们谈到了。项目范围。")
        self.assertEqual(second[2], 1)
        self.assertEqual(second[3], "p1")

        cleaner.reset()
        reset = cleaner.finalize_utterance(
            "Fresh sentence.",
            "新句子。",
            sent_id=1,
            source_lang="en",
            target_lang="zh",
        )
        self.assertIsNone(reset[2])
        self.assertEqual(reset[3], "p1")


class EchoCleanerPerformanceTests(unittest.TestCase):
    def test_echo_sync_cleaner_sla(self) -> None:
        corpus = [
            f"Uhm, the the project item {index} costs about the." for index in range(100)
        ]
        durations_ms: list[float] = []
        previous = ""
        for text in corpus:
            started = time.perf_counter_ns()
            cleaned = clean_speech(text, "en")
            _merged, _previous, following = mend_fragment(
                previous,
                cleaned,
                gap_ms=100.0,
            )
            assign_para([following])
            durations_ms.append((time.perf_counter_ns() - started) / 1_000_000)
            previous = cleaned

        p99 = statistics.quantiles(durations_ms, n=100, method="inclusive")[98]
        self.assertLess(p99, 1.0)


class SingleWindowFinalProtocolTests(unittest.TestCase):
    def test_final_events_stamp_monotonic_ids_hash_and_merge(self) -> None:
        manager = SessionManager(lambda: False)
        client = _RecordingClient()
        manager.add_client("main", client)

        async def run() -> None:
            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "We talked about the.",
                    "target_text": "我们谈到了。",
                    "is_final": True,
                },
            )
            await manager.publish_event(
                "main",
                {
                    "type": "subtitle.final",
                    "source_text": "Project scope.",
                    "target_text": "项目范围。",
                    "is_final": True,
                },
            )
            manager.reset_channel_session("main")
            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "Fresh start.",
                    "target_text": "重新开始。",
                    "is_final": True,
                },
            )

        asyncio.run(run())
        finals = [message for message in client.messages if message.get("is_final")]
        self.assertEqual([event["sent_id"] for event in finals], [1, 2, 1])
        self.assertEqual(finals[1]["merged_with_prev_id"], 1)
        self.assertEqual(
            finals[1]["source"],
            "We talked about the project scope.",
        )
        for event in finals:
            self.assertEqual(event["window_id"], "main")
            self.assertEqual(event["audio_source"], "microphone")
            self.assertEqual(event["para_id"], "p1")
            self.assertRegex(event["source_hash"], r"^[0-9a-f]{12}$")
            self.assertEqual(
                event["source_hash"],
                hashlib.sha256(event["source"].encode("utf-8")).hexdigest()[:12],
            )
            self.assertEqual(MAX_OVERLAY_WINDOWS, 1)
            self.assertFalse(_forbidden_keys(event))

    def test_delta_is_not_cleaned_or_delayed(self) -> None:
        manager = SessionManager(lambda: False)
        client = _RecordingClient()
        manager.add_client("main", client)

        async def run() -> None:
            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "Uhm, the the project",
                    "target_text": "项目",
                    "is_final": False,
                },
            )

        asyncio.run(run())
        event = client.messages[0]
        self.assertEqual(event["source_text"], "Uhm, the the project")
        self.assertNotIn("sent_id", event)
        self.assertNotIn("source_hash", event)


class R13StaticContractTests(unittest.TestCase):
    def test_frontend_pipeline_files_and_no_usage_tail_or_audio(self) -> None:
        required = [
            FRONTEND / "src/lib/subtitles/subtitlePipeline.ts",
            FRONTEND / "src/windows/overlay/timeline/Timeline.tsx",
            FRONTEND / "src/windows/overlay/timeline/timelineScroll.ts",
            FRONTEND / "src/windows/overlay/timeline/overlayModel.ts",
        ]
        for path in required:
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file())

        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (FRONTEND / "src").rglob("*")
            if path.is_file() and path.suffix in {".ts", ".tsx", ".css"}
        )
        self.assertNotIn("useSubtitleTail", source)
        self.assertNotIn("AudioContext", source)
        self.assertNotIn("<audio", source)
        self.assertNotIn("usage_stats", source)
        self.assertIn("data-window-id", source)
        self.assertIn("data-sent-id", source)
        self.assertIn("data-source-hash", source)
        self.assertIn("data-para-id", source)
        self.assertIn("lcp-stable", source)
        self.assertIn("lcp-tail", source)
        timeline_scroll = (
            FRONTEND / "src/windows/overlay/timeline/timelineScroll.ts"
        ).read_text(encoding="utf-8")
        self.assertRegex(timeline_scroll, r"TIMELINE_GLIDE_MS\s*=\s*180")
        self.assertIn("preserveTimelineAnchor", source)
        self.assertIn("回到实时", source)

    def test_notice_and_adr_preserve_source_provenance(self) -> None:
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("Samoyed123-yed/echo-translate", notices)
        self.assertIn(
            "fc8d9a2195903cef780c8a295183b21a3561db12",
            notices,
        )
        self.assertIn("Copyright (c) 2026 Echo Translate contributors", notices)

        adr = (
            ROOT
            / ".agents/decisions/ADR-010-rev02-rev03-echo-cleaner.md"
        ).read_text(encoding="utf-8")
        self.assertIn("97e20b01d1af5d978b96445440b8da64e1fa32ab75ee9a123d65dab5942fd53d", adr)
        self.assertIn("does not use a regular expression", adr)
        self.assertIn("no abbreviation set", adr)

    def test_single_window_and_zero_metering_contract_remain_fixed(self) -> None:
        manager_source = (ROOT / "app/session_manager.py").read_text(encoding="utf-8")
        self.assertIn("MAX_OVERLAY_WINDOWS = 1", manager_source)
        self.assertIn("FORBIDDEN_BUSINESS_KEYS", manager_source)
        self.assertNotIn("MAX_OVERLAY_WINDOWS = 2", manager_source)
        self.assertEqual(MAX_OVERLAY_WINDOWS, 1)


if __name__ == "__main__":
    unittest.main()
