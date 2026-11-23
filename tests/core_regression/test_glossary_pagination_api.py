from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import create_app


ROOT = Path(__file__).resolve().parents[2]


class GlossaryPaginationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(ROOT, enable_audio_capture=False)
        self.glossary = self.app.state.glossary_store
        if self.glossary is None:
            self.fail("glossary store was not initialized")

    def tearDown(self) -> None:
        glossary = self.glossary
        if glossary is not None:
            glossary.stop_background_load()

    def test_three_categories_are_paginated(self) -> None:
        app = self.app
        glossary = app.state.glossary_store
        self.assertIsNotNone(glossary)
        self.assertTrue(glossary.wait_until_ready(5))

        result = glossary.get_active_terms(category="all", limit=100)
        self.assertEqual(len(result["items"]), 100)
        self.assertEqual(result["total"], 299)
        self.assertEqual(result["limit"], 100)
        self.assertEqual(result["offset"], 0)
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 50_000)

        for category, expected in {
            "terms_en_to_zh": 99,
            "asr_fixes": 83,
            "terms_zh_to_en": 117,
        }.items():
            with self.subTest(category=category):
                payload = glossary.get_active_terms(category=category, limit=50)
                self.assertEqual(payload["total"], expected)
                self.assertEqual(len(payload["items"]), min(50, expected))

    def test_packs_and_locked_toggle(self) -> None:
        glossary = self.glossary
        self.assertIsNotNone(glossary)
        self.assertTrue(glossary.wait_until_ready(5))
        packs = glossary.get_packs()["packs"]
        self.assertEqual(len(packs), 2)
        locked = next(pack for pack in packs if pack["id"].startswith("00"))
        self.assertTrue(locked["locked"])
        with self.assertRaises(PermissionError):
            glossary.toggle_pack(locked["id"], False)

    def test_api_routes_are_registered(self) -> None:
        paths = {
            route.path for route in self.app.routes if isinstance(route, APIRoute)
        }
        self.assertIn("/api/glossary/packs", paths)
        self.assertIn("/api/glossary/packs/{pack_id}/toggle", paths)
        self.assertIn("/api/glossary/terms/active", paths)
        self.assertIn("/api/glossary/session", paths)
        self.assertIn("/api/glossary/session/add", paths)
        self.assertIn("/api/glossary/promote-term", paths)
        self.assertIn("/api/glossary/session/clear", paths)


if __name__ == "__main__":
    unittest.main()
