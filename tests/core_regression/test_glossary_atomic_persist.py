from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.engines.base import EngineState
from app.glossary import GlossaryPersistError, GlossaryStore
from app.main import create_app
from tests.unit.test_bootstrap_config import PLACEHOLDER, asgi_request

ROOT = Path(__file__).resolve().parents[2]


class MockRealtimeSession:
    def __init__(self, source_lang: str, target_lang: str) -> None:
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.current_state = EngineState.READY
        self.session_update_count = 1
        self.reconnect_count = 0
        self.live_updates: list[dict[str, str]] = []

    def connect(self) -> bool:
        return True

    def send_audio(self, _pcm_bytes: bytes) -> bool:
        return True

    def close(self) -> None:
        self.current_state = EngineState.CLOSED

    def state_event(self) -> dict[str, Any]:
        return {"type": "engine_state", "state": self.current_state.value, "reason": ""}

    def live_update_session(
        self,
        source_lang: str,
        target_lang: str,
        corpus_phrases: dict[str, str] | list[tuple[str, str]] | None,
        _hotwords: list[str] | None = None,
    ) -> bool:
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.session_update_count += 1
        self.live_updates.append(dict(corpus_phrases or {}))
        return True


class GlossaryAtomicPersistTests(unittest.TestCase):
    def _create_sandbox(self, temporary: str) -> Path:
        sandbox = Path(temporary)
        (sandbox / "config").mkdir()
        shutil.copyfile(ROOT / "config" / "glossary.json", sandbox / "config" / "glossary.json")
        (sandbox / "glossary_bank").symlink_to(ROOT / "glossary_bank")
        (sandbox / ".env.example").write_text(
            f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )
        return sandbox

    def test_all_direction_session_inject_live_push_promote_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = self._create_sandbox(temporary)
            application = create_app(sandbox, enable_audio_capture=False)
            glossary: GlossaryStore = application.state.glossary_store
            self.assertTrue(glossary.wait_until_ready(5))

            try:
                manager = application.state.session_manager
                en_engine = MockRealtimeSession("zh", "en")
                zh_engine = MockRealtimeSession("en", "zh")
                manager.register_engine("en", en_engine)
                manager.register_engine("zh", zh_engine)

                # Pre-existing unsaved P0 session term.
                add_altman = asyncio.run(
                    asgi_request(
                        application,
                        "/api/glossary/session/add",
                        method="POST",
                        payload={
                            "source": "奥特曼",
                            "target": "Sam Altman",
                            "bucket": "terms_trans",
                            "lang_key": "zh->en",
                        },
                    )
                )
                self.assertEqual(add_altman.status, 200)

                # Inject English ASR fix + zh->en example term without reconnecting WS.
                add_asr = asyncio.run(
                    asgi_request(
                        application,
                        "/api/glossary/session/add",
                        method="POST",
                        payload={
                            "source": "gen ai",
                            "target": "Generative AI",
                            "bucket": "asr_fixes",
                            "lang_key": "en",
                        },
                    )
                )
                self.assertEqual(add_asr.status, 200)
                self.assertIn("zh", json.loads(add_asr.body)["pushed_channels"])

                add_jensen = asyncio.run(
                    asgi_request(
                        application,
                        "/api/glossary/session/add",
                        method="POST",
                        payload={
                            "source": "黄仁勋",
                            "target": "Jason Huang",
                            "bucket": "terms_trans",
                            "lang_key": "zh->en",
                        },
                    )
                )
                self.assertEqual(add_jensen.status, 200)
                self.assertIn("en", json.loads(add_jensen.body)["pushed_channels"])
                self.assertEqual(en_engine.reconnect_count, 0)
                self.assertEqual(zh_engine.reconnect_count, 0)
                self.assertEqual(en_engine.live_updates[-1].get("黄仁勋"), "Jason Huang")
                self.assertEqual(zh_engine.live_updates[-1].get("gen ai"), "Generative AI")

                # Promote ONLY "黄仁勋"; "奥特曼" must remain in P0 session.
                promote = asyncio.run(
                    asgi_request(
                        application,
                        "/api/glossary/promote-term",
                        method="POST",
                        payload={
                            "source": "黄仁勋",
                            "bucket": "terms_trans",
                            "lang_key": "zh->en",
                        },
                    )
                )
                self.assertEqual(promote.status, 200)
                session_sources = {
                    item["source"] for item in json.loads(promote.body)["session_items"]
                }
                self.assertIn("奥特曼", session_sources)
                self.assertNotIn("黄仁勋", session_sources)

                # Clear session must perform ZERO disk writes.
                with patch.object(
                    glossary,
                    "_persist_to_disk_locked",
                    side_effect=AssertionError("clear_session must not write disk"),
                ):
                    cleared = asyncio.run(
                        asgi_request(
                            application,
                            "/api/glossary/session/clear",
                            method="POST",
                            payload={},
                        )
                    )
                self.assertEqual(cleared.status, 200)
                self.assertEqual(json.loads(cleared.body)["cleared"], 2)
            finally:
                glossary.stop_background_load()

            # Restart GlossaryStore and confirm "黄仁勋" persisted while P0 terms did not.
            restarted = GlossaryStore(sandbox)
            try:
                self.assertTrue(restarted.wait_until_ready(5))
                phrases = restarted.get_corpus_phrases(source_lang="zh", target_lang="en")
                self.assertEqual(phrases.get("黄仁勋"), "Jason Huang")
                self.assertNotIn("奥特曼", phrases)
                self.assertEqual(restarted.list_session_terms(), [])
            finally:
                restarted.stop_background_load()

    def test_concurrent_10_threads_with_permission_error_exponential_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = self._create_sandbox(temporary)
            store = GlossaryStore(sandbox)
            self.assertTrue(store.wait_until_ready(5))
            try:
                for idx in range(10):
                    store.add_session_term(
                        f"并发术语{idx}",
                        f"ConcurrentTerm{idx}",
                        bucket="terms_trans",
                        lang_key="zh->en",
                    )

                real_replace = os.replace
                attempts_lock = threading.Lock()
                replace_calls = 0
                sleep_calls: list[float] = []

                def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
                    nonlocal replace_calls
                    with attempts_lock:
                        replace_calls += 1
                        call_num = replace_calls
                    if call_num <= 2:
                        raise PermissionError("simulated windows AV lock")
                    real_replace(src, dst)

                barrier = threading.Barrier(10)
                errors: list[BaseException] = []

                def worker(idx: int) -> None:
                    try:
                        barrier.wait(timeout=3)
                        store.promote_term(
                            f"并发术语{idx}",
                            bucket="terms_trans",
                            lang_key="zh->en",
                        )
                    except BaseException as exc:
                        errors.append(exc)

                with (
                    patch("app.glossary.store.os.replace", side_effect=flaky_replace),
                    patch("app.glossary.store.time.sleep", side_effect=sleep_calls.append),
                ):
                    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join(timeout=10)

                self.assertEqual(errors, [])
                self.assertEqual(sleep_calls[:2], [0.02, 0.04])
                persisted = json.loads(
                    (sandbox / "config" / "glossary.json").read_text(encoding="utf-8")
                )
                for idx in range(10):
                    self.assertEqual(
                        persisted["terms_zh_to_en"].get(f"并发术语{idx}"),
                        f"ConcurrentTerm{idx}",
                    )
                leftover_tmp = list((sandbox / "config").glob(".glossary_*.tmp"))
                self.assertEqual(leftover_tmp, [])
            finally:
                store.stop_background_load()

    def test_unrecoverable_persist_error_returns_http_500_and_cleans_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = self._create_sandbox(temporary)
            application = create_app(sandbox, enable_audio_capture=False)
            glossary: GlossaryStore = application.state.glossary_store
            self.assertTrue(glossary.wait_until_ready(5))
            try:
                glossary.add_session_term(
                    "落盘失败词",
                    "PersistFailTerm",
                    bucket="terms_trans",
                    lang_key="zh->en",
                )
                with (
                    patch("app.glossary.store.os.replace", side_effect=PermissionError("locked")),
                    patch("app.glossary.store.time.sleep"),
                ):
                    response = asyncio.run(
                        asgi_request(
                            application,
                            "/api/glossary/promote-term",
                            method="POST",
                            payload={
                                "source": "落盘失败词",
                                "bucket": "terms_trans",
                                "lang_key": "zh->en",
                            },
                        )
                    )
                self.assertEqual(response.status, 500)
                payload = json.loads(response.body)
                self.assertEqual(payload["error"], "glossary_persist_failed")
                self.assertEqual(list((sandbox / "config").glob(".glossary_*.tmp")), [])
                # Session entry remains intact on failed persist.
                self.assertEqual(len(glossary.list_session_terms()), 1)
                self.assertTrue(issubclass(GlossaryPersistError, RuntimeError))
            finally:
                glossary.stop_background_load()

    def test_static_hygiene_no_top_level_lock_import_and_pure_subtitles(self) -> None:
        store_src = (ROOT / "app" / "glossary" / "store.py").read_text(encoding="utf-8")
        tree = ast.parse(store_src)
        top_level_imports: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.add(node.module)
        self.assertNotIn("fcntl", top_level_imports)
        self.assertNotIn("msvcrt", top_level_imports)

        quick_bar = (
            ROOT / "frontend" / "src" / "components" / "glossary" / "QuickTermBar.tsx"
        ).read_text(encoding="utf-8")
        for direction in ("asr_fixes", "terms_trans", "target_fixes", "zh->en", "en->zh", "zh->fr"):
            self.assertIn(direction, quick_bar)

        monitor = (
            ROOT / "frontend" / "src" / "screens" / "MonitorScreens.tsx"
        ).read_text(encoding="utf-8")
        for forbidden in ("untranslated", "highlight-error", "red-badge", "wavy"):
            self.assertNotIn(forbidden, monitor)


if __name__ == "__main__":
    unittest.main()
