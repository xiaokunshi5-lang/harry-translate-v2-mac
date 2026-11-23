from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from app.glossary import GlossaryStore


ROOT = Path(__file__).resolve().parents[2]


class GlossaryPriorityTests(unittest.TestCase):
    def test_curated_packs_load_synchronously_within_wire_budget(self) -> None:
        started = time.perf_counter()
        store = GlossaryStore(ROOT)
        init_ms = (time.perf_counter() - started) * 1000

        self.assertLess(init_ms, 20)
        self.assertEqual(store.loading_stage, "all_packs_ready")
        self.assertTrue(store.wait_until_ready(5))
        self.assertEqual(store.loading_stage, "all_packs_ready")

        packs = store.get_packs()["packs"]
        self.assertEqual(
            [pack["id"] for pack in packs],
            ["00_base_emergency", "01_curated_everyday"],
        )
        self.assertEqual(sum(pack["terms_count"] for pack in packs), 250)
        self.assertTrue(next(pack for pack in packs if pack["id"].startswith("00"))["locked"])
        phrases = store.get_budgeted_corpus_phrases(
            source_lang="zh",
            target_lang="en",
            max_bytes=4096,
        )
        self.assertLessEqual(
            len(json.dumps(phrases, ensure_ascii=False).encode("utf-8")),
            4096,
        )
        self.assertEqual(store.get_quota_status()["cloud_cap"], 250)
        self.assertLessEqual(store.get_quota_status()["wire_bytes"], 6144)
        store.stop_background_load()

    def test_priority_hard_cap_and_glossary_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            (sandbox / "config").mkdir()
            shutil.copyfile(ROOT / "config" / "glossary.json", sandbox / "config" / "glossary.json")
            (sandbox / "glossary_bank").symlink_to(ROOT / "glossary_bank")

            store = GlossaryStore(sandbox)
            self.assertTrue(store.wait_until_ready(5))
            pack = next(
                item for item in store.get_packs()["packs"] if item["id"].startswith("01")
            )
            self.assertTrue(store.toggle_pack(pack["id"], False)["quota"])
            closed = store.get_active_terms(category="all", pack_id=pack["id"])
            self.assertEqual(closed["total"], 0)
            self.assertNotIn(pack["id"], store.get_corpus_phrases())

            # P0 session terms remain first and are never trimmed before lower tiers.
            store.add_session_term(
                "反向传播",
                "Session Backprop",
                bucket="terms_trans",
                lang_key="zh->en",
            )
            phrases = store.get_budgeted_corpus_phrases(
                source_lang="zh",
                target_lang="en",
                max_bytes=4096,
            )
            self.assertEqual(next(iter(phrases)), "反向传播")
            self.assertEqual(phrases["反向传播"], "Session Backprop")

            oversized = store.get_budgeted_corpus_phrases(
                source_lang="zh",
                target_lang="en",
                max_bytes=99_999,
                hard_cap=99_999,
            )
            self.assertLessEqual(
                len(json.dumps(oversized, ensure_ascii=False).encode("utf-8")),
                6144,
            )
            self.assertTrue(
                all(len(source) <= 48 for source in oversized)
            )

            store.set_corpus_policy(enabled=False, wire_budget_bytes=4096)
            self.assertEqual(
                store.get_budgeted_corpus_phrases(
                    source_lang="zh",
                    target_lang="en",
                ),
                {},
            )

            with self.assertRaises(PermissionError):
                store.toggle_pack("00_base_emergency", False)
            with self.assertRaises(KeyError):
                store.toggle_pack("missing_pack", True)
            store.stop_background_load()


if __name__ == "__main__":
    unittest.main()
