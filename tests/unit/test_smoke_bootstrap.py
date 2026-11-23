from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class SmokeBootstrapTests(unittest.TestCase):
    def test_python_and_platform_match_mac_project_contract(self) -> None:
        self.assertGreaterEqual(sys.version_info, (3, 9))
        self.assertEqual(platform.system(), "Darwin")
        self.assertEqual(platform.machine(), "arm64")

    def test_project_declares_python_312_environment(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.12,<3.13"', pyproject)
        self.assertTrue((ROOT / ".venv-v2" / "bin" / "python").is_file())

    def test_required_phase_zero_structure_exists(self) -> None:
        required = [
            ".agents/PROJECT_CONTRACT.md",
            ".agents/decisions/ADR-001-scope-and-cleanup.md",
            ".agents/decisions/ADR-006-mimi-14-parity-and-no-token.md",
            ".env.example",
            "app",
            "config",
            "frontend",
            "glossary_bank",
            "scripts/smoke_e2e.py",
            "scripts/bench_latency.py",
            "src-tauri",
            "tests/core_regression/test_baseline_sanity.py",
        ]
        for relative in required:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).exists())

    def test_core_python_files_compile_without_importing_hardware(self) -> None:
        for relative in [
            "legacy/v1/server.py",
            "legacy/v1/switch_key.py",
            "scripts/smoke_e2e.py",
            "scripts/bench_latency.py",
        ]:
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(path=relative):
                compile(source, relative, "exec")

    def test_standard_library_event_path_has_no_metering_fields(self) -> None:
        event = {
            "type": "subtitle.delta",
            "window_id": "smoke-window",
            "sent_id": 1,
            "source": "你好",
            "target": "Hello",
            "source_hash": hashlib.sha256(b"hello").hexdigest()[:16],
        }
        encoded = json.dumps(event, ensure_ascii=False)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["window_id"], "smoke-window")
        self.assertFalse({"usage", "total_tokens", "cost_rmb"}.intersection(decoded))

    def test_asyncio_is_available(self) -> None:
        async def value() -> int:
            await asyncio.sleep(0)
            return 7

        self.assertEqual(asyncio.run(value()), 7)


if __name__ == "__main__":
    unittest.main()
