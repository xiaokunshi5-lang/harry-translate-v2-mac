from __future__ import annotations

import json
import re
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.main import create_app
from app.transcript_exporter import SessionPersistenceManager


def entry(
    sent_id: int,
    *,
    source: str = "Source",
    target: str = "译文",
    patched_target: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "window_id": "main",
        "sent_id": sent_id,
        "source": source,
        "target": target,
        "start_ms": sent_id * 1_000,
        "end_ms": sent_id * 1_000 + 800,
        "channel": "main",
        "target_lang": "zh",
        "source_hash": f"{sent_id:012x}",
    }
    if patched_target is not None:
        payload["patched_target"] = patched_target
    return payload


class SessionExporterTests(unittest.TestCase):
    def test_single_session_routes_are_registered(self) -> None:
        with TemporaryDirectory() as temp:
            application = create_app(Path(temp), enable_audio_capture=False)
            paths = {route.path for route in application.routes}
            self.assertIn("/api/session/save", paths)
            self.assertIn("/api/session/latest", paths)
            self.assertIn("/api/export", paths)
            self.assertIn("/api/sessions/{session_id}", paths)

    def test_stale_timestamp_is_ignored_and_json_stays_clean(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            manager = SessionPersistenceManager(root)
            latest = {
                "id": "session_test_001",
                "clientSavedAt": 2000,
                "created_at": "2026-11-23T00:00:00Z",
                "entries": [entry(index) for index in range(1, 6)],
                "usage_stats": {"total_tokens": 99},
            }
            saved = manager.save(latest)
            self.assertFalse(saved["ignored"])

            old = {
                "id": "session_test_001",
                "clientSavedAt": 1500,
                "entries": [entry(index) for index in range(1, 3)],
            }
            stale = manager.save(old)
            self.assertTrue(stale["ignored"])
            self.assertEqual(stale["reason"], "stale_timestamp")
            stored = manager.load("session_test_001")
            self.assertEqual(len(stored["entries"]), 5)
            self.assertNotIn("usage_stats", json.dumps(stored))
            self.assertEqual(list((root / "exports").glob("*.tmp")), [])

    def test_concurrent_writes_keep_latest_complete_payload(self) -> None:
        with TemporaryDirectory() as temp:
            manager = SessionPersistenceManager(Path(temp))
            barrier = threading.Barrier(10)

            def writer(index: int) -> None:
                barrier.wait()
                manager.save(
                    {
                        "id": "session_concurrent_01",
                        "clientSavedAt": 1000 + index,
                        "entries": [entry(value) for value in range(index + 1)],
                    }
                )

            threads = [threading.Thread(target=writer, args=(index,)) for index in range(10)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            stored = manager.load("session_concurrent_01")
            self.assertEqual(stored["clientSavedAt"], 1009)
            self.assertEqual(len(stored["entries"]), 10)
            self.assertEqual(list((Path(temp) / "exports").glob("*.tmp")), [])

    def test_export_md_srt_json_use_patched_text_and_no_metering(self) -> None:
        with TemporaryDirectory() as temp:
            manager = SessionPersistenceManager(Path(temp))
            manager.save(
                {
                    "id": "session_export_01",
                    "clientSavedAt": 2000,
                    "created_at": "2026-11-23T08:00:00Z",
                    "source_lang": "en",
                    "target_lang": "zh",
                    "entries": [
                        entry(1),
                        entry(2, patched_target="修正后的译文"),
                        entry(3),
                    ],
                    "summary_items": [
                        {"level": 5, "text": "重点", "category": "concept"}
                    ],
                    "usage_stats": {"total_tokens": 1},
                }
            )

            srt = manager.export("session_export_01", "srt")
            self.assertRegex(
                srt.content,
                r"(?m)^1\n\d{2}:\d{2}:\d{2},\d{3} --> "
                r"\d{2}:\d{2}:\d{2},\d{3}\n",
            )
            self.assertEqual(srt.content.count(" --> "), 3)
            markdown = manager.export("session_export_01", "md")
            self.assertIn("修正后的译文", markdown.content)
            self.assertNotIn("usage", markdown.content.casefold())
            raw_json = manager.export("session_export_01", "json")
            decoded = json.loads(raw_json.content)
            self.assertEqual(decoded["id"], "session_export_01")
            self.assertNotIn("usage_stats", json.dumps(decoded))
            self.assertNotRegex(raw_json.content, r"(?i)total_tokens|usage_stats")

    def test_invalid_session_id_is_rejected(self) -> None:
        with TemporaryDirectory() as temp:
            manager = SessionPersistenceManager(Path(temp))
            with self.assertRaises(ValueError):
                manager.save({"id": "../bad", "clientSavedAt": 1, "entries": []})

    def test_srt_time_format_is_exact(self) -> None:
        with TemporaryDirectory() as temp:
            manager = SessionPersistenceManager(Path(temp))
            manager.save(
                {
                    "id": "session_time_001",
                    "clientSavedAt": 1,
                    "entries": [entry(1), entry(2)],
                }
            )
            srt = manager.export("session_time_001", "srt").content
            timestamps = re.findall(
                r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}",
                srt,
            )
            self.assertEqual(len(timestamps), 2)


if __name__ == "__main__":
    unittest.main()
