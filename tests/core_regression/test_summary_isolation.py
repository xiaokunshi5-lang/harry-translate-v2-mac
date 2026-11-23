from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.bypass.summary_worker import (
    SUMMARY_QUEUE_MAXSIZE,
    SummaryItem,
    SummaryWorker,
)
from app.config_store import DEFAULT_SETTINGS, ConfigStore
from app.main import create_app
from app.session_manager import MAX_OVERLAY_WINDOWS, SessionManager
from tests.unit.test_bootstrap_config import PLACEHOLDER, asgi_request

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FORBIDDEN_FIELDS = {
    "cost",
    "cost_rmb",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "usage",
    "usage_stats",
    "summary_" + "tokens",
}


def _forbidden_fields(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(FORBIDDEN_FIELDS.intersection(value))
        for key, child in value.items():
            normalized = str(key).casefold()
            if "token" in normalized or "usage" in normalized:
                found.add(str(key))
            found.update(_forbidden_fields(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_forbidden_fields(child))
    return found


def _event(sent_id: int, source: str, target: str) -> dict[str, Any]:
    return {
        "type": "subtitle",
        "window_id": "main",
        "source_text": source,
        "target_text": target,
        "source_lang": "en",
        "target_lang": "zh",
        "is_final": True,
        "sent_id": sent_id,
        "source_hash": f"{sent_id:012x}",
    }


class _RecordingClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, message: str) -> None:
        self.messages.append(json.loads(message))

    async def close(self) -> None:
        return None


class _BlockingClient(_RecordingClient):
    def __init__(self, block_type: str = "summary.update") -> None:
        super().__init__()
        self.block_type = block_type
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_text(self, message: str) -> None:
        payload = json.loads(message)
        if payload.get("type") == self.block_type:
            self.started.set()
            await self.release.wait()
        await super().send_text(message)


class SummaryDefaultOffTests(unittest.TestCase):
    def test_default_disabled_zero_overhead_and_zero_fields(self) -> None:
        async def run() -> None:
            model_calls = 0

            async def model(
                utterances: tuple[Any, ...],
                existing: tuple[SummaryItem, ...],
                summary_model: str,
            ) -> Any:
                nonlocal model_calls
                del utterances, existing, summary_model
                model_calls += 1
                return []

            worker = SummaryWorker(
                DEFAULT_SETTINGS,
                api_key_provider=lambda: "sk-test-r15-key",
                model_client=model,
            )
            self.assertFalse(worker.settings.enabled)
            self.assertFalse(worker.running)
            self.assertEqual(worker.active_workers, 0)
            self.assertEqual(worker.queued_count, 0)

            durations: list[float] = []
            for index in range(500):
                started = time.perf_counter_ns()
                accepted = worker.enqueue_final(_event(index + 1, "source", "译文"))
                durations.append((time.perf_counter_ns() - started) / 1_000_000)
                self.assertFalse(accepted)

            self.assertLess(sum(durations) / len(durations), 0.1)
            self.assertEqual(worker.queued_count, 0)
            self.assertEqual(worker.pending_words, 0)
            self.assertEqual(worker.llm_call_count, 0)
            self.assertEqual(worker.network_request_count, 0)
            self.assertEqual(model_calls, 0)
            snapshot = worker.snapshot()
            self.assertFalse(snapshot["enabled"])
            self.assertEqual(snapshot["pinned"], [])
            self.assertEqual(snapshot["archived"], [])
            self.assertFalse(_forbidden_fields(snapshot))

        asyncio.run(run())

    def test_queue_drop_oldest_and_hot_switch_off(self) -> None:
        async def run() -> None:
            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "rules",
                }
            )
            self.assertTrue(worker.start())
            for sent_id in range(1, SUMMARY_QUEUE_MAXSIZE + 8):
                self.assertTrue(
                    worker.enqueue_final(
                        _event(sent_id, f"source {sent_id}", "译文")
                    )
                )
            self.assertEqual(worker.queued_count, SUMMARY_QUEUE_MAXSIZE)
            queued: list[int] = []
            while not worker.queue.empty():
                queued.append(worker.queue.get_nowait().sent_id)
            self.assertEqual(queued[0], 8)
            self.assertEqual(queued[-1], SUMMARY_QUEUE_MAXSIZE + 7)

            worker.enqueue_final(_event(99, "source", "译文"))
            started = time.perf_counter()
            await worker.apply_settings(
                {
                    "summary_enabled": False,
                    "summary_mode": "rules",
                }
            )
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.1)
            self.assertFalse(worker.running)
            self.assertEqual(worker.active_workers, 0)
            self.assertEqual(worker.queued_count, 0)
            self.assertEqual(worker.pending_words, 0)
            self.assertFalse(worker.enqueue_final(_event(100, "late", "迟到")))

        asyncio.run(run())

    def test_pending_buffer_is_bounded_by_count_and_characters(self) -> None:
        async def run() -> None:
            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "rules",
                },
                clock=lambda: 10.0,
            )
            target = "会" * 1000
            for sent_id in range(1, 65):
                worker.enqueue_final(_event(sent_id, "source", target))
            worker._drain_queue()
            self.assertLessEqual(len(worker._buffer), 16)
            self.assertLessEqual(worker._buffer_chars, 16_000)
            self.assertLessEqual(worker.pending_words, 16_000)

        asyncio.run(run())


class SummaryExtractionTests(unittest.TestCase):
    def test_first_summary_waits_for_initial_thirty_second_gate(self) -> None:
        async def run() -> None:
            calls = 0

            async def model(
                utterances: tuple[Any, ...],
                existing: tuple[SummaryItem, ...],
                summary_model: str,
            ) -> list[dict[str, Any]]:
                nonlocal calls
                del utterances, existing, summary_model
                calls += 1
                return [
                    {
                        "level": 5,
                        "text": "达到时间门槛后生成总结。",
                        "category": "decision",
                    }
                ]

            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "bailian",
                    "summary_min_words": 120,
                    "summary_min_interval_sec": 30,
                },
                api_key_provider=lambda: "sk-test-r15-key",
                model_client=model,
                clock=lambda: 10.0,
            )
            worker.enqueue_final(
                _event(1, "Long meeting", "会" * 130),
                created_at=10.0,
            )
            self.assertFalse(await worker.run_once(now=10.0))
            self.assertFalse(await worker.run_once(now=39.9))
            self.assertTrue(await worker.run_once(now=40.0))
            self.assertEqual(calls, 1)

        asyncio.run(run())

    def test_120_word_gate_schema_cleaning_and_priority_buckets(self) -> None:
        async def run() -> None:
            calls = 0
            seen_existing: tuple[SummaryItem, ...] = ()

            async def model(
                utterances: tuple[Any, ...],
                existing: tuple[SummaryItem, ...],
                summary_model: str,
            ) -> list[dict[str, Any]]:
                nonlocal calls, seen_existing
                del utterances, summary_model
                calls += 1
                seen_existing = existing
                return [
                    {
                        "level": 5,
                        "text": "老师指出：项目必须在十二月上线。",
                        "category": "decision",
                    },
                    {
                        "level": 2,
                        "text": "讲师提到：背景情况属于辅助信息。",
                        "category": "detail",
                    },
                    {
                        "level": 4,
                        "text": "The lecturer explains that 目标是增长20%。",
                        "category": "metric",
                    },
                ]

            published: list[dict[str, Any]] = []

            async def publisher(
                event: dict[str, Any],
                session_generation: int,
                generation_epoch: int,
            ) -> None:
                del session_generation, generation_epoch
                published.append(event)

            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "bailian",
                    "summary_model": "qwen-turbo",
                    "summary_min_words": 120,
                    "summary_min_interval_sec": 30,
                },
                api_key_provider=lambda: "sk-test-r15-key",
                model_client=model,
                publisher=publisher,
                clock=lambda: 100.0,
            )
            worker._last_summary_ts = 70.0
            worker.enqueue_final(
                _event(
                    1,
                    "First source",
                    "这是一段用于测试总结门控的中文译文。" * 5,
                ),
                created_at=100.0,
            )
            self.assertFalse(await worker.run_once(now=100.0))
            self.assertEqual(calls, 0)

            worker.enqueue_final(
                _event(
                    2,
                    "Second source",
                    "继续补充到达到总结阈值的中文内容。" * 6,
                ),
                created_at=100.0,
            )
            self.assertTrue(await worker.run_once(now=100.0))
            self.assertEqual(calls, 1)
            self.assertEqual(seen_existing, ())
            snapshot = worker.snapshot()
            self.assertEqual(
                [item["level"] for item in snapshot["pinned"]],
                [5, 4],
            )
            self.assertEqual(
                [item["level"] for item in snapshot["archived"]],
                [2],
            )
            self.assertNotIn("老师指出", snapshot["pinned"][0]["text"])
            self.assertNotIn("讲师提到", snapshot["archived"][0]["text"])
            self.assertIn("目标是增长20%", snapshot["pinned"][1]["text"])
            self.assertEqual(snapshot["engine_used"], "bailian")
            self.assertEqual(published[-1]["type"], "summary.update")
            self.assertFalse(_forbidden_fields(snapshot))
            self.assertFalse(_forbidden_fields(published[-1]))

            worker.enqueue_final(
                _event(3, "More source", "一段新的补充内容。" * 8),
                created_at=106.0,
            )
            self.assertFalse(await worker.run_once(now=106.0))
            self.assertEqual(calls, 1)
            self.assertTrue(worker.snapshot()["pending_words"] > 0)

        asyncio.run(run())

    def test_timeout_and_invalid_payload_fall_back_to_rules(self) -> None:
        async def run() -> None:
            async def slow_model(
                utterances: tuple[Any, ...],
                existing: tuple[SummaryItem, ...],
                summary_model: str,
            ) -> Any:
                del utterances, existing, summary_model
                await asyncio.sleep(1.0)
                return []

            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "bailian",
                    "summary_min_words": 20,
                    "summary_min_interval_sec": 1,
                },
                api_key_provider=lambda: "sk-test-r15-key",
                model_client=slow_model,
                timeout_seconds=0.05,
                clock=lambda: 200.0,
            )
            worker.enqueue_final(
                _event(
                    1,
                    "Decision",
                    "决定：项目必须在十二月上线，目标是增长20%。请提前准备发布计划。",
                ),
                created_at=200.0,
            )
            self.assertTrue(await worker.run_once(now=200.0, force=True))
            snapshot = worker.snapshot()
            self.assertEqual(snapshot["engine_used"], "rules_fallback")
            self.assertGreater(len(snapshot["pinned"]), 0)
            self.assertEqual(worker.rules_summary_count, 1)

            missing_key = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "bailian",
                    "summary_min_words": 20,
                    "summary_min_interval_sec": 1,
                },
                api_key_provider=lambda: "",
                model_client=slow_model,
                clock=lambda: 300.0,
            )
            missing_key.enqueue_final(
                _event(
                    2,
                    "Decision",
                    "决定：先完成离线规则提炼，再准备发布计划。",
                ),
                created_at=300.0,
            )
            self.assertTrue(await missing_key.run_once(now=300.0, force=True))
            self.assertEqual(
                missing_key.snapshot()["engine_used"],
                "rules_fallback",
            )
            self.assertEqual(missing_key.llm_call_count, 0)
            self.assertEqual(missing_key.network_request_count, 0)

        asyncio.run(run())


class SummarySessionTests(unittest.TestCase):
    def test_only_final_main_subtitles_enter_summary_queue(self) -> None:
        async def run() -> None:
            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "rules",
                },
                clock=lambda: 10.0,
            )
            manager = SessionManager(lambda: False, summary_worker=worker)
            manager._finalize_subtitle_event(
                {
                    "type": "subtitle",
                    "source_text": "Draft source",
                    "target_text": "草稿译文",
                    "is_final": False,
                },
                channel="main",
                source_lang="en",
                target_lang="zh",
                generation=0,
            )
            self.assertEqual(worker.queued_count, 0)

            manager._finalize_subtitle_event(
                {
                    "type": "subtitle",
                    "source_text": "Compatibility source",
                    "target_text": "兼容通道译文",
                    "is_final": True,
                },
                channel="en",
                source_lang="zh",
                target_lang="en",
                generation=0,
            )
            self.assertEqual(worker.queued_count, 0)

            manager._finalize_subtitle_event(
                {
                    "type": "subtitle",
                    "source_text": "Final source",
                    "target_text": "最终译文",
                    "is_final": True,
                },
                channel="main",
                source_lang="en",
                target_lang="zh",
                generation=0,
            )
            self.assertEqual(worker.queued_count, 1)
            manager.set_channel_languages("main", "en", "ja")
            self.assertEqual(worker.queued_count, 0)

            disabled = SummaryWorker(DEFAULT_SETTINGS)
            manager.set_summary_worker(disabled)
            manager._finalize_subtitle_event(
                {
                    "type": "subtitle",
                    "source_text": "Another final",
                    "target_text": "最终译文",
                    "is_final": True,
                },
                channel="main",
                source_lang="en",
                target_lang="zh",
                generation=0,
            )
            self.assertEqual(disabled.queued_count, 0)

        asyncio.run(run())

    def test_session_reset_invalidates_inflight_summary_result(self) -> None:
        async def run() -> None:
            entered = asyncio.Event()
            release = asyncio.Event()

            async def slow_model(
                utterances: tuple[Any, ...],
                existing: tuple[SummaryItem, ...],
                summary_model: str,
            ) -> list[dict[str, Any]]:
                del utterances, existing, summary_model
                entered.set()
                await release.wait()
                return [
                    {
                        "level": 5,
                        "text": "老师指出：旧会话的重点不能进入新会话。",
                        "category": "decision",
                    }
                ]

            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "bailian",
                    "summary_min_words": 20,
                    "summary_min_interval_sec": 1,
                },
                api_key_provider=lambda: "sk-test-r15-key",
                model_client=slow_model,
                clock=lambda: 30.0,
            )
            manager = SessionManager(lambda: False, summary_worker=worker)
            manager.bind_loop(asyncio.get_running_loop())
            client = _RecordingClient()
            manager.add_client("main", client)

            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "Old session final.",
                    "target_text": "这是一段属于旧会话并且超过总结阈值的最终译文。",
                    "is_final": True,
                },
            )
            processing = asyncio.create_task(worker.run_once(force=True))
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            await manager.reset_channel_session_async("main")
            release.set()
            self.assertFalse(await processing)
            summary_events = [
                message
                for message in client.messages
                if message.get("type") == "summary.update"
            ]
            self.assertTrue(summary_events)
            self.assertTrue(
                all(
                    message.get("pinned") == []
                    and message.get("archived") == []
                    for message in summary_events
                )
            )
            await manager.stop_all()

        asyncio.run(run())

    def test_reset_waits_for_summary_publish_and_clears_completed_cards(self) -> None:
        async def run() -> None:
            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "rules",
                    "summary_min_words": 20,
                    "summary_min_interval_sec": 1,
                },
                clock=lambda: 70.0,
            )
            manager = SessionManager(lambda: False, summary_worker=worker)
            manager.bind_loop(asyncio.get_running_loop())
            worker.bind_publisher(manager.publish_summary_event)
            client = _BlockingClient()
            manager.add_client("main", client)
            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "Decision final.",
                    "target_text": "决定：项目必须在十二月上线，并完成发布计划。",
                    "is_final": True,
                },
            )

            publishing = asyncio.create_task(worker.run_once(force=True))
            await asyncio.wait_for(client.started.wait(), timeout=0.5)
            resetting = asyncio.create_task(
                manager.reset_channel_session_async("main")
            )
            await asyncio.sleep(0)
            self.assertFalse(resetting.done())
            client.release.set()
            self.assertTrue(await publishing)
            await resetting
            self.assertEqual(worker.snapshot()["pinned"], [])
            self.assertEqual(worker.snapshot()["archived"], [])
            summary_events = [
                message
                for message in client.messages
                if message.get("type") == "summary.update"
            ]
            self.assertGreaterEqual(len(summary_events), 2)
            self.assertEqual(summary_events[-1]["pinned"], [])
            self.assertEqual(summary_events[-1]["archived"], [])

            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "New decision.",
                    "target_text": "结论：新会话继续保留核心决定和发布目标。",
                    "is_final": True,
                },
            )
            self.assertTrue(await worker.run_once(force=True))
            self.assertGreater(len(worker.snapshot()["pinned"]), 0)
            manager.set_channel_languages("main", "en", "ja")
            self.assertEqual(worker.snapshot()["pinned"], [])
            self.assertEqual(worker.snapshot()["archived"], [])
            await manager.stop_all()

        asyncio.run(run())

    def test_disable_waits_for_inflight_summary_publish(self) -> None:
        async def run() -> None:
            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "rules",
                    "summary_min_words": 20,
                    "summary_min_interval_sec": 1,
                },
                clock=lambda: 80.0,
            )
            manager = SessionManager(lambda: False, summary_worker=worker)
            manager.bind_loop(asyncio.get_running_loop())
            worker.bind_publisher(manager.publish_summary_event)
            client = _BlockingClient()
            manager.add_client("main", client)
            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "Publish then disable.",
                    "target_text": "决定：先发布重点总结，再彻底关闭总结任务。",
                    "is_final": True,
                },
            )

            publishing = asyncio.create_task(worker.run_once(force=True))
            await asyncio.wait_for(client.started.wait(), timeout=0.5)
            disabling = asyncio.create_task(
                manager.apply_summary_settings(
                    worker,
                    {
                        "summary_enabled": False,
                        "summary_mode": "rules",
                        "summary_min_words": 20,
                        "summary_min_interval_sec": 1,
                    },
                )
            )
            await asyncio.sleep(0)
            self.assertFalse(disabling.done())
            client.release.set()
            self.assertTrue(await publishing)
            await disabling
            self.assertFalse(worker.settings.enabled)
            message_count = len(client.messages)
            await asyncio.sleep(0.02)
            self.assertEqual(len(client.messages), message_count)
            await manager.stop_all()

        asyncio.run(run())

    def test_summary_update_uses_existing_main_channel_and_never_blocks_final(self) -> None:
        async def run() -> None:
            entered = asyncio.Event()
            release = asyncio.Event()

            async def slow_model(
                utterances: tuple[Any, ...],
                existing: tuple[SummaryItem, ...],
                summary_model: str,
            ) -> list[dict[str, Any]]:
                del utterances, existing, summary_model
                entered.set()
                await release.wait()
                return [
                    {
                        "level": 5,
                        "text": "老师强调：项目必须在十二月上线。",
                        "category": "decision",
                    }
                ]

            worker = SummaryWorker(
                {
                    "summary_enabled": True,
                    "summary_mode": "bailian",
                    "summary_min_words": 20,
                    "summary_min_interval_sec": 1,
                },
                api_key_provider=lambda: "sk-test-r15-key",
                model_client=slow_model,
                clock=lambda: 50.0,
            )
            manager = SessionManager(lambda: False, summary_worker=worker)
            manager.bind_loop(asyncio.get_running_loop())
            worker.bind_publisher(manager.publish_summary_event)
            client = _RecordingClient()
            manager.add_client("main", client)

            started = time.perf_counter()
            await manager.publish_event(
                "main",
                {
                    "type": "subtitle",
                    "source_text": "A final decision.",
                    "target_text": "这是一段超过总结阈值并包含明确决定的最终译文。",
                    "is_final": True,
                },
            )
            elapsed = time.perf_counter() - started
            self.assertLess(elapsed, 0.5)

            processing = asyncio.create_task(worker.run_once(force=True))
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            self.assertFalse(processing.done())
            release.set()
            self.assertTrue(await processing)
            summary_events = [
                message
                for message in client.messages
                if message.get("type") == "summary.update"
            ]
            self.assertEqual(len(summary_events), 1)
            self.assertEqual(summary_events[0]["pinned"][0]["level"], 5)
            self.assertNotIn("老师强调", summary_events[0]["pinned"][0]["text"])
            self.assertFalse(_forbidden_fields(summary_events[0]))
            await manager.stop_all()

        asyncio.run(run())


class SummaryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / ".env.example").write_text(
            f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )
        self.environment_patch = patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""})
        self.environment_patch.start()

    def tearDown(self) -> None:
        self.environment_patch.stop()
        self.temporary.cleanup()

    def test_disabled_routes_do_not_wake_model_and_refresh_conflicts(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        disabled = asyncio.run(asgi_request(application, "/api/summary"))
        self.assertEqual(disabled.status, 200)
        self.assertEqual(
            json.loads(disabled.body),
            {"enabled": False, "pinned": [], "archived": []},
        )
        refresh = asyncio.run(
            asgi_request(application, "/api/summary/refresh", method="POST")
        )
        self.assertEqual(refresh.status, 409)
        self.assertEqual(json.loads(refresh.body)["error"], "summary_disabled")
        worker = application.state.summary_worker
        self.assertEqual(worker.llm_call_count, 0)
        self.assertEqual(worker.network_request_count, 0)

    def test_invalid_summary_config_falls_back_disabled_and_clamped(self) -> None:
        store = ConfigStore(self.root)
        settings = store.update_settings(
            {
                "summary_enabled": "true",
                "summary_mode": "unsafe",
                "summary_model": "unsafe-model",
                "summary_min_words": 1,
                "summary_min_interval_sec": 9999,
            }
        )
        self.assertFalse(settings["summary_enabled"])
        self.assertEqual(settings["summary_mode"], "bailian")
        self.assertEqual(settings["summary_model"], "qwen-turbo")
        self.assertEqual(settings["summary_min_words"], 20)
        self.assertEqual(settings["summary_min_interval_sec"], 600)

    def test_enable_rules_mode_via_config_and_return_structured_snapshot(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        update = asyncio.run(
            asgi_request(
                application,
                "/api/config",
                method="PUT",
                payload={
                    "settings": {
                        "summary_enabled": True,
                        "summary_mode": "rules",
                        "summary_model": "qwen-flash",
                        "summary_min_words": 120,
                        "summary_min_interval_sec": 30,
                    }
                },
            )
        )
        self.assertEqual(update.status, 200)
        enabled = json.loads(update.body)["user_settings"]
        self.assertTrue(enabled["summary_enabled"])
        self.assertEqual(enabled["summary_mode"], "rules")
        self.assertEqual(enabled["summary_model"], "qwen-flash")

        response = asyncio.run(asgi_request(application, "/api/summary"))
        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["mode"], "rules")
        self.assertIn("pinned", payload)
        self.assertIn("archived", payload)
        self.assertFalse(_forbidden_fields(payload))


class R15StaticContractTests(unittest.TestCase):
    def test_defaults_worker_adr_and_native_boundary(self) -> None:
        self.assertEqual(MAX_OVERLAY_WINDOWS, 1)
        self.assertFalse(DEFAULT_SETTINGS["summary_enabled"])
        self.assertEqual(DEFAULT_SETTINGS["summary_mode"], "bailian")
        self.assertEqual(DEFAULT_SETTINGS["summary_model"], "qwen-turbo")
        self.assertEqual(DEFAULT_SETTINGS["summary_min_words"], 120)
        self.assertEqual(DEFAULT_SETTINGS["summary_min_interval_sec"], 30)

        worker_source = (
            ROOT / "app" / "bypass" / "summary_worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn("SUMMARY_QUEUE_MAXSIZE = 64", worker_source)
        self.assertIn("SUMMARY_TIMEOUT_SECONDS = 8.0", worker_source)
        self.assertIn("certifi.where()", worker_source)
        self.assertNotIn("CERT_NONE", worker_source)
        self.assertIn("if not self._settings.enabled:", worker_source)
        self.assertIn("_EN_NARRATION", worker_source)
        self.assertIn("_ZH_NARRATION", worker_source)

        adr = (
            ROOT / ".agents" / "decisions" / "ADR-013-r15-summary-boundary.md"
        ).read_text(encoding="utf-8")
        self.assertIn("fc8d9a2195903cef780c8a295183b21a3561db12", adr)
        self.assertIn(
            "616ae6dd6cac79a910da1ab2ef1290c8b66c8c4940641ed41be50119be104404",
            adr,
        )
        self.assertIn("_EN_NARRATION", adr)
        self.assertIn("_ZH_NARRATION", adr)
        self.assertIn("standard-library", adr)

        tauri_config = (
            ROOT / "src-tauri" / "capabilities" / "summary.json"
        ).read_text(encoding="utf-8")
        self.assertIn('"windows": ["summary"]', tauri_config)
        summary_rs = (
            ROOT / "src-tauri" / "src" / "windows" / "summary.rs"
        ).read_text(encoding="utf-8")
        self.assertIn("SUMMARY_ICON_SIZE: f64 = 48.0", summary_rs)
        self.assertIn("SUMMARY_EXPANDED_WIDTH: f64 = 420.0", summary_rs)
        self.assertIn("summary_geometry.json", summary_rs)
        summary_production_rs = summary_rs.split("#[cfg(test)]", 1)[0]
        self.assertNotIn("user_settings.json", summary_production_rs)

    def test_frontend_default_off_and_pure_overlay(self) -> None:
        widget = (
            FRONTEND
            / "src"
            / "components"
            / "summary"
            / "SummaryWidget.tsx"
        ).read_text(encoding="utf-8")
        styles = (
            FRONTEND / "src" / "styles" / "summary.css"
        ).read_text(encoding="utf-8")
        overlay = (
            FRONTEND / "src" / "windows" / "overlay" / "OverlayWindow.tsx"
        ).read_text(encoding="utf-8")
        monitor = (
            FRONTEND / "src" / "screens" / "MonitorScreens.tsx"
        ).read_text(encoding="utf-8")
        all_frontend = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (FRONTEND / "src").rglob("*")
            if path.is_file() and path.suffix in {".ts", ".tsx", ".css"}
        )
        self.assertIn("if (!enabled) return null", widget)
        self.assertIn("width: 48px", styles)
        self.assertIn("backdrop-filter: blur(20px) saturate(190%)", styles)
        self.assertIn("overflow-x: hidden", styles)
        self.assertIn("isBrowserFallback", overlay)
        self.assertIn('tauriInvoke<void>("summary_set_enabled"', overlay)
        self.assertIn("summary.update", monitor)
        self.assertIn("isTauriRuntime()", widget)
        self.assertNotIn("useSubtitleTail", all_frontend)
        self.assertNotIn("Audio" + "Context", all_frontend)
        self.assertNotIn("<audio", all_frontend)
        self.assertNotIn("highlight_red", all_frontend)

    def test_active_runtime_and_ui_have_no_legacy_summary_field(self) -> None:
        literal = "summary_" + "tokens"
        active_sources = [
            *list((ROOT / "app").rglob("*.py")),
            *list((FRONTEND / "src").rglob("*")),
        ]
        for path in active_sources:
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".css"}:
                continue
            with self.subTest(path=path):
                self.assertNotIn(literal, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
