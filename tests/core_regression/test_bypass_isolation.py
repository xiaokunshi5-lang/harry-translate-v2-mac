from __future__ import annotations

import asyncio
import hashlib
import json
import time
import unittest
from pathlib import Path
from typing import Any

from app.bypass.patch_worker import (
    CORRECTION_QUEUE_MAXSIZE,
    PatchWorker,
    detect_untranslated_words,
    plausible_correction,
)
from app.config_store import DEFAULT_SETTINGS, ConfigStore
from app.session_manager import MAX_OVERLAY_WINDOWS, SessionManager

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
FORBIDDEN_PATCH_KEYS = FORBIDDEN_KEYS | {
    "highlight_red",
    "underline",
    "suspect_badge",
    "untranslated_words",
}


class _RecordingClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.messages.append(json.loads(message))

    async def close(self) -> None:
        return None


class _BlockingClient(_RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_text(self, message: str) -> None:
        self.started.set()
        await self.release.wait()
        await super().send_text(message)


class _FakeGlossary:
    terms_trans = {"en->zh": {"OpenAI": (0, "OpenAI")}}

    def apply_fixes(
        self,
        source_text: str,
        target_text: str,
        *,
        source_lang: str = "auto",
        target_lang: str = "",
    ) -> tuple[str, str]:
        del source_lang, target_lang
        return (
            source_text.replace("teh", "the"),
            target_text.replace("项木", "项目"),
        )

    def get_corpus_phrases(
        self,
        *,
        source_lang: str = "auto",
        target_lang: str = "",
        limit: int = 900,
    ) -> dict[str, str]:
        del source_lang, target_lang, limit
        return {"OpenAI": "OpenAI", "LockTerm": "锁定术语"}


class _PatchRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, event: dict[str, Any], generation: int) -> None:
        del generation
        self.events.append(event)


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


class Rev04GuardrailTests(unittest.TestCase):
    def test_plausible_correction_matches_verified_formula_and_limits(self) -> None:
        self.assertTrue(
            plausible_correction(
                "The epidemic model is ready.",
                "The epistemic model is ready.",
            )
        )
        expansion = "The epidemic model is ready. " + " ".join(
            f"extra{index}" for index in range(20)
        )
        self.assertFalse(
            plausible_correction("The epidemic model is ready.", expansion)
        )
        self.assertFalse(
            plausible_correction(
                "The OpenAI model is ready.",
                "The model is ready.",
                protected_terms=("OpenAI",),
            )
        )
        self.assertFalse(plausible_correction("OpenAI", "微软"))
        self.assertTrue(plausible_correction("teh", "the"))
        self.assertTrue(plausible_correction("recieve", "receive"))
        self.assertFalse(
            plausible_correction(
                "This is a test.",
                "That was a movie.",
            )
        )
        self.assertFalse(plausible_correction("hello there", "world earth"))

    def test_untranslated_words_use_verified_zh_rules(self) -> None:
        self.assertEqual(
            detect_untranslated_words(
                "系统 still uses legacy fallback",
                "zh",
            ),
            ["still", "uses", "legacy", "fallback"],
        )
        self.assertEqual(
            detect_untranslated_words(
                "系统 leaves OpenAI and API untranslated",
                "zh",
                protected_terms=("OpenAI",),
            ),
            ["leaves", "and", "untranslated"],
        )
        self.assertEqual(
            detect_untranslated_words("这里有两个中文残留块", "en"),
            ["这里有两个中文残留块"],
        )


class BypassIsolationTests(unittest.TestCase):
    def test_queue_isolation_and_mode_switch(self) -> None:
        async def run() -> None:
            started = asyncio.Event()
            release = asyncio.Event()
            model_calls = 0

            async def blocking_model(
                task: Any,
                protected_terms: tuple[str, ...],
                model: str,
            ) -> dict[str, str]:
                nonlocal model_calls
                del protected_terms, model
                model_calls += 1
                started.set()
                await release.wait()
                return {
                    "corrected_source": task.source_text,
                    "corrected_target": task.target_text,
                }

            worker = PatchWorker(
                {
                    "correction_enabled": True,
                    "correction_mode": "llm",
                    "correction_model": "qwen-turbo",
                    "correction_gap_ms": 1500,
                },
                api_key_provider=lambda: "sk-test-r14-key",
                model_client=blocking_model,
                publisher=_PatchRecorder(),
                clock=lambda: 100.0,
            )
            manager = SessionManager(lambda: False, patch_worker=worker)
            client = _RecordingClient()
            manager.add_client("main", client)

            manager._finalize_subtitle_event(
                {
                    "type": "subtitle",
                    "source_text": "Blocking source.",
                    "target_text": "阻塞译文。",
                    "is_final": True,
                },
                channel="main",
                source_lang="en",
                target_lang="zh",
                generation=0,
            )
            tick = asyncio.create_task(worker.run_once(now=102.0))
            await asyncio.wait_for(started.wait(), timeout=0.5)

            durations: list[float] = []
            for index in range(10):
                began = time.perf_counter_ns()
                accepted = worker.enqueue_final_utterance(
                    window_id="main",
                    sent_id=index + 2,
                    channel="main",
                    source_lang="en",
                    target_lang="zh",
                    source_text=f"Queued source {index}.",
                    target_text="排队译文。",
                    source_hash=f"{index + 100:012x}",
                    created_at=102.0,
                )
                durations.append((time.perf_counter_ns() - began) / 1_000_000)
                self.assertTrue(accepted)

            main_chain_started = time.perf_counter()
            await asyncio.wait_for(
                manager.publish_event(
                    "main",
                    {
                        "type": "subtitle",
                        "source_text": "Live source.",
                        "target_text": "实时译文。",
                        "is_final": False,
                    },
                ),
                timeout=0.5,
            )
            main_chain_elapsed = time.perf_counter() - main_chain_started
            self.assertLess(max(durations), 0.5)
            self.assertLess(main_chain_elapsed, 0.5)

            release.set()
            await tick
            self.assertEqual(model_calls, 1)

            disabled = PatchWorker(
                {
                    "correction_enabled": False,
                    "correction_mode": "llm",
                },
                api_key_provider=lambda: "sk-test-r14-key",
                model_client=blocking_model,
            )
            self.assertFalse(disabled.start())
            for index in range(20):
                self.assertFalse(
                    disabled.enqueue_final_utterance(
                        window_id="main",
                        sent_id=index + 1,
                        channel="main",
                        source_lang="en",
                        target_lang="zh",
                        source_text="disabled",
                        target_text="关闭",
                        source_hash=f"{index + 1:012x}",
                    )
                )
            self.assertFalse(await disabled.run_once(now=999.0))
            self.assertEqual(disabled.llm_call_count, 0)
            self.assertEqual(disabled.network_request_count, 0)

            rules_patches = _PatchRecorder()
            rules = PatchWorker(
                {
                    "correction_enabled": True,
                    "correction_mode": "rules",
                },
                glossary_engine=_FakeGlossary(),
                api_key_provider=lambda: "sk-test-r14-key",
                model_client=blocking_model,
                publisher=rules_patches,
                clock=lambda: 200.0,
            )
            for index in range(20):
                rules.enqueue_final_utterance(
                    window_id="main",
                    sent_id=index + 1,
                    channel="main",
                    source_lang="en",
                    target_lang="zh",
                    source_text=f"teh source {index}",
                    target_text="项木。",
                    source_hash=f"{index + 1:012x}",
                    created_at=200.0,
                )
            for index in range(20):
                await rules.run_once(now=202.0 + index)
            self.assertEqual(rules.llm_call_count, 0)
            self.assertEqual(rules.network_request_count, 0)
            self.assertGreater(len(rules_patches.events), 0)

        asyncio.run(run())

    def test_queue_drops_oldest_and_is_hard_bounded(self) -> None:
        async def run() -> None:
            worker = PatchWorker(
                {
                    "correction_enabled": True,
                    "correction_mode": "rules",
                },
                clock=lambda: 10.0,
            )
            for sent_id in range(1, 41):
                worker.enqueue_final_utterance(
                    window_id="main",
                    sent_id=sent_id,
                    channel="main",
                    source_lang="en",
                    target_lang="zh",
                    source_text=f"source {sent_id}",
                    target_text="译文。",
                    source_hash=f"{sent_id:012x}",
                )
            self.assertEqual(worker.queue.qsize(), CORRECTION_QUEUE_MAXSIZE)
            queued_ids: list[int] = []
            while not worker.queue.empty():
                queued_ids.append(worker.queue.get_nowait().sent_id)
            self.assertEqual(queued_ids[0], 9)
            self.assertEqual(queued_ids[-1], 40)

        asyncio.run(run())

    def test_hot_switch_off_stops_worker_and_clears_queue(self) -> None:
        async def run() -> None:
            worker = PatchWorker(
                {
                    "correction_enabled": True,
                    "correction_mode": "rules",
                },
                clock=lambda: 10.0,
            )
            self.assertTrue(worker.start())
            worker.enqueue_final_utterance(
                window_id="main",
                sent_id=1,
                channel="main",
                source_lang="en",
                target_lang="zh",
                source_text="source",
                target_text="译文。",
                source_hash="111111111111",
            )
            await worker.apply_settings(
                {
                    "correction_enabled": False,
                    "correction_mode": "rules",
                }
            )
            self.assertFalse(worker.running)
            self.assertEqual(worker.queue.qsize(), 0)

        asyncio.run(run())

    def test_protected_terms_include_ranked_asr_and_target_fix_entries(self) -> None:
        async def run() -> None:
            worker = PatchWorker(
                {
                    "correction_enabled": True,
                    "correction_mode": "rules",
                },
                glossary_engine=_FakeGlossary(),
                clock=lambda: 10.0,
            )
            worker.enqueue_final_utterance(
                window_id="main",
                sent_id=1,
                channel="main",
                source_lang="en",
                target_lang="zh",
                source_text="OpenAI source",
                target_text="锁定术语。",
                source_hash="111111111111",
            )
            task = worker.queue.get_nowait()
            protected = worker._protected_terms(task)
            self.assertIn("OpenAI", protected)
            self.assertIn("LockTerm", protected)
            self.assertIn("锁定术语", protected)

        asyncio.run(run())

    def test_untranslated_urgent_and_plausible_guardrail(self) -> None:
        async def run() -> None:
            patches = _PatchRecorder()
            model_calls = 0

            async def model(
                task: Any,
                protected_terms: tuple[str, ...],
                correction_model: str,
            ) -> dict[str, str]:
                nonlocal model_calls
                del protected_terms, correction_model
                model_calls += 1
                if task.sent_id == 2:
                    return {
                        "corrected_source": task.source_text,
                        "corrected_target": (
                            "正常译文。"
                            + " ".join(f"hallucinated{index}" for index in range(20))
                        ),
                    }
                if task.sent_id == 1:
                    return {
                        "corrected_source": task.source_text,
                        "corrected_target": task.target_text.replace("役", "译"),
                    }
                return {
                    "corrected_source": task.source_text,
                    "corrected_target": task.target_text.replace("leave", "留下"),
                }

            worker = PatchWorker(
                {
                    "correction_enabled": True,
                    "correction_mode": "llm",
                    "correction_gap_ms": 1500,
                },
                api_key_provider=lambda: "sk-test-r14-key",
                model_client=model,
                publisher=patches,
                clock=lambda: 50.0,
            )
            worker.enqueue_final_utterance(
                window_id="main",
                sent_id=1,
                channel="main",
                source_lang="en",
                target_lang="zh",
                source_text="Normal sentence.",
                target_text="正常役文。",
                source_hash="111111111111",
                created_at=50.0,
            )
            worker.enqueue_final_utterance(
                window_id="main",
                sent_id=3,
                channel="main",
                source_lang="en",
                target_lang="zh",
                source_text="Please leave this untranslated.",
                target_text="请 leave this untranslated",
                source_hash="333333333333",
                created_at=50.0,
            )
            worker.enqueue_final_utterance(
                window_id="main",
                sent_id=2,
                channel="main",
                source_lang="en",
                target_lang="zh",
                source_text="Hallucination check.",
                target_text="正常译文。",
                source_hash="222222222222",
                created_at=50.0,
            )

            processed = await worker.run_once(now=50.0)
            self.assertTrue(processed)
            self.assertEqual(patches.events[-1]["sent_id"], 3)
            self.assertEqual(worker.urgent_promoted_count, 1)

            while await worker.run_once(now=52.0):
                pass
            self.assertEqual(model_calls, 3)
            self.assertEqual(worker.rejected_hallucination_count, 1)
            self.assertEqual(len(patches.events), 2)
            self.assertEqual(
                patches.events[0]["new_source_hash"],
                hashlib.sha256(
                    patches.events[0]["patched_source"].encode("utf-8")
                ).hexdigest()[:12],
            )
            for event in patches.events:
                self.assertEqual(event["type"], "transcript.patch")
                self.assertEqual(event["window_id"], "main")
                self.assertRegex(event["source_hash"], r"^[0-9a-f]{12}$")
                self.assertFalse(FORBIDDEN_PATCH_KEYS.intersection(event))
                self.assertNotIn("red", json.dumps(event).casefold())
                self.assertNotIn("badge", json.dumps(event).casefold())
                self.assertFalse(_forbidden_keys(event))

        asyncio.run(run())


class SessionPatchIntegrationTests(unittest.TestCase):
    def test_worker_patch_is_broadcast_once_to_main_clients(self) -> None:
        async def run() -> None:
            worker = PatchWorker(
                {
                    "correction_enabled": True,
                    "correction_mode": "rules",
                    "correction_gap_ms": 0,
                },
                glossary_engine=_FakeGlossary(),
                clock=lambda: 30.0,
            )
            manager = SessionManager(lambda: False, patch_worker=worker)
            manager.bind_loop(asyncio.get_running_loop())
            worker.bind_publisher(manager.publish_patch_event)
            client = _RecordingClient()
            manager.add_client("main", client)

            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "teh source.",
                    "target_text": "项木。",
                    "is_final": True,
                },
            )
            await worker.run_once(now=30.0)
            patches = [
                message
                for message in client.messages
                if message.get("type") == "transcript.patch"
            ]
            self.assertEqual(len(patches), 1)
            self.assertEqual(patches[0]["window_id"], "main")
            self.assertEqual(patches[0]["sent_id"], 1)
            self.assertEqual(patches[0]["patched_source"], "the source.")
            self.assertEqual(patches[0]["patched_target"], "项目。")
            self.assertFalse(FORBIDDEN_PATCH_KEYS.intersection(patches[0]))
            await manager.stop_all()

        asyncio.run(run())

    def test_merged_final_enqueues_card_identity_and_reset_generation(self) -> None:
        async def run() -> None:
            worker = PatchWorker(
                {
                    "correction_enabled": True,
                    "correction_mode": "rules",
                },
                glossary_engine=_FakeGlossary(),
                clock=lambda: 20.0,
            )
            manager = SessionManager(lambda: False, patch_worker=worker)
            manager.bind_loop(asyncio.get_running_loop())
            client = _RecordingClient()
            manager.add_client("main", client)

            await asyncio.wait_for(
                manager.publish_event(
                    "main",
                    {
                        "type": "subtitle",
                        "source_text": "We talked about the.",
                        "target_text": "我们谈到了。",
                        "is_final": True,
                    },
                ),
                timeout=0.5,
            )
            await asyncio.wait_for(
                manager.publish_event(
                    "main",
                    {
                        "type": "subtitle.final",
                        "source_text": "Project scope.",
                        "target_text": "项目范围。",
                        "is_final": True,
                    },
                ),
                timeout=0.5,
            )
            self.assertEqual(worker.queue.qsize(), 2)
            first = worker.queue.get_nowait()
            second = worker.queue.get_nowait()
            self.assertEqual(first.sent_id, 1)
            self.assertEqual(second.sent_id, 1)
            self.assertNotEqual(first.source_hash, second.source_hash)

            generation = manager.channel_generation("main")
            patch = {
                "type": "transcript.patch",
                "window_id": "main",
                "sent_id": 1,
                "source_hash": second.source_hash,
                "patched_source": "We talked about the project scope.",
                "patched_target": "我们谈到了项目范围。",
                "new_source_hash": "abcdef123456",
            }
            reset_generation = await manager.reset_channel_session_async("main")
            self.assertEqual(reset_generation, generation + 1)
            delivered = await manager.publish_patch_event(
                patch,
                generation,
            )
            self.assertEqual(delivered, 0)
            self.assertFalse(
                any(message.get("type") == "transcript.patch" for message in client.messages)
            )
            await manager.stop_all()

        asyncio.run(run())

    def test_new_session_start_always_advances_generation(self) -> None:
        async def run() -> None:
            manager = SessionManager(lambda: False)
            manager.bind_loop(asyncio.get_running_loop())
            manager.add_client("main", _RecordingClient())
            before = manager.channel_generation("main")
            after = await manager.reset_transcript_state_async("main")
            self.assertEqual(after, before + 1)
            self.assertGreater(manager.channel_generation("main"), before)
            await manager.stop_all()

        asyncio.run(run())

    def test_reset_waits_for_inflight_patch_before_invalidating_generation(self) -> None:
        async def run() -> None:
            manager = SessionManager(lambda: False)
            manager.bind_loop(asyncio.get_running_loop())
            client = _BlockingClient()
            manager.add_client("main", client)
            generation = manager.channel_generation("main")
            patch = {
                "type": "transcript.patch",
                "window_id": "main",
                "sent_id": 1,
                "source_hash": "111111111111",
                "patched_source": "Patched.",
                "patched_target": "已修补。",
                "new_source_hash": "222222222222",
            }
            patch_task = asyncio.create_task(
                manager.publish_patch_event(patch, generation)
            )
            await asyncio.wait_for(client.started.wait(), timeout=0.5)
            reset_task = asyncio.create_task(
                manager.reset_channel_session_async("main")
            )
            await asyncio.sleep(0)
            self.assertFalse(reset_task.done())
            client.release.set()
            self.assertEqual(await patch_task, 1)
            self.assertEqual(await reset_task, generation + 1)
            self.assertEqual(client.messages[0]["type"], "transcript.patch")
            await manager.stop_all()

        asyncio.run(run())


class R14StaticContractTests(unittest.TestCase):
    def test_config_adr_and_worker_contract_are_present(self) -> None:
        self.assertEqual(MAX_OVERLAY_WINDOWS, 1)
        self.assertTrue(DEFAULT_SETTINGS["correction_enabled"])
        self.assertEqual(DEFAULT_SETTINGS["correction_mode"], "llm")
        self.assertEqual(DEFAULT_SETTINGS["correction_model"], "qwen-turbo")
        self.assertEqual(DEFAULT_SETTINGS["correction_gap_ms"], 1500)

        adr = (
            ROOT / ".agents/decisions/ADR-011-rev04-echo-guardrails.md"
        ).read_text(encoding="utf-8")
        self.assertIn("fc8d9a2195903cef780c8a295183b21a3561db12", adr)
        self.assertIn("616ae6dd6cac79a910da1ab2ef1290c8b66c8c4940641ed41be50119be104404", adr)
        self.assertIn("new_words <= max(3, round(len(corrected_tokens) * 0.3))", adr)
        self.assertIn("httpx", adr)
        self.assertIn("standard", adr)

        session_source = (ROOT / "app/session_manager.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("await patch_worker", session_source)
        self.assertIn("MAX_OVERLAY_WINDOWS = 1", session_source)
        worker_source = (ROOT / "app/bypass/patch_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("CORRECTION_QUEUE_MAXSIZE = 32", worker_source)
        self.assertIn("asyncio.Queue", worker_source)
        self.assertIn("certifi.where()", worker_source)
        self.assertNotIn("CERT_NONE", worker_source)

    def test_frontend_patch_contract_is_pure_and_silent(self) -> None:
        pipeline = (
            FRONTEND / "src/lib/subtitles/subtitlePipeline.ts"
        ).read_text(encoding="utf-8")
        timeline = (
            FRONTEND / "src/windows/overlay/timeline/Timeline.tsx"
        ).read_text(encoding="utf-8")
        styles = (
            FRONTEND / "src/windows/overlay/timeline/timeline.css"
        ).read_text(encoding="utf-8")
        settings_panel = (
            FRONTEND / "src/components/settings/GlossaryInsightsPanel.tsx"
        ).read_text(encoding="utf-8")
        monitor = (
            FRONTEND / "src/screens/MonitorScreens.tsx"
        ).read_text(encoding="utf-8")
        frontend_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (FRONTEND / "src").rglob("*")
            if path.is_file() and path.suffix in {".ts", ".tsx", ".css"}
        )
        self.assertIn("applySubtitlePatch", pipeline)
        self.assertIn("stalePatchDropped", pipeline)
        self.assertIn("150", timeline)
        self.assertIn("overflow-wrap", styles)
        self.assertIn("correction_mode", settings_panel)
        self.assertIn("qwen-flash", settings_panel)
        self.assertIn("correction_enabled", settings_panel)
        self.assertIn("patchVersion > 0", monitor)
        self.assertNotIn("useSubtitleTail", frontend_source)
        self.assertNotIn("AudioContext", frontend_source)
        self.assertNotIn("<audio", frontend_source)
        self.assertNotIn("suspect_badge", frontend_source)
        self.assertNotIn("highlight_red", frontend_source)

    def test_config_store_normalizes_correction_switch(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            (root / ".env.example").write_text(
                "DASHSCOPE_API_KEY=sk-your-dashscope-api-key-here\n",
                encoding="utf-8",
            )
            store = ConfigStore(root)
            settings = store.update_settings(
                {
                    "correction_enabled": "false",
                    "correction_mode": "unknown",
                    "correction_model": "unsafe-model",
                    "correction_gap_ms": 99999,
                }
            )
            self.assertFalse(settings["correction_enabled"])
            self.assertEqual(settings["correction_mode"], "llm")
            self.assertEqual(settings["correction_model"], "qwen-turbo")
            self.assertEqual(settings["correction_gap_ms"], 10000)


if __name__ == "__main__":
    unittest.main()
