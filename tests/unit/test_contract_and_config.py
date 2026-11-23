from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / ".agents" / "PROJECT_CONTRACT.md"
CONFIG = ROOT / ".agents" / "workflow.config"
ADR_DIR = ROOT / ".agents" / "decisions"


class ContractAndConfigTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def front_matter_value(self, key: str) -> str:
        text = CONTRACT.read_text(encoding="utf-8")
        match = re.search(rf"^{re.escape(key)}:\s*\"?([^\"\n]*)\"?$", text, re.MULTILINE)
        self.assertIsNotNone(match, f"missing front matter key: {key}")
        return match.group(1).strip()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_contract_is_confirmed_and_has_mac_only_overrides(self) -> None:
        self.assertEqual(self.front_matter_value("contract_status"), "CONFIRMED")
        self.assertEqual(self.front_matter_value("confirmed_by"), "user")
        self.assertEqual(self.front_matter_value("perf_critical"), "true")
        self.assertEqual(self.front_matter_value("audio_sensitive"), "true")
        self.assertEqual(self.front_matter_value("ui_contract_enabled"), "true")

        text = CONTRACT.read_text(encoding="utf-8")
        for required in [
            "P1 稳定性、可复现与数据安全",
            "P2 极速低延迟",
            "P3 Mac 悬浮体验与语义精修",
            "Apple Silicon",
            "macOS 13",
            "逐轮",
            "R09.5",
            "250 词",
            "MAX_OVERLAY_WINDOWS=1",
            "6144 B",
            "qwen3.8*",
            "session.finish",
            "source_font_size",
            "Tauri v2",
            "Mimi 14 项",
            "QuickTermBar",
            "零 Token 计量",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_contract_and_adrs_have_no_template_or_test_placeholders(self) -> None:
        paths = [CONTRACT, *sorted(ADR_DIR.glob("ADR-*.md"))]
        mandatory = {
            "ADR-001-scope-and-cleanup.md",
            "ADR-002-full-18517-glossary.md",
            "ADR-003-quick-term-injection-no-highlight.md",
            "ADR-004-tauri-v2-mimi-sidecar.md",
            "ADR-005-multi-window-parallel-qwen38.md",
            "ADR-006-mimi-14-parity-and-no-token.md",
            "ADR-007-rev01-qwen38-spec.md",
            "ADR-008-r095-single-window-wire-budget.md",
            "ADR-009-r10-r12-phase4-closeout.md",
        }
        self.assertTrue(mandatory.issubset({path.name for path in paths}))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("{{", text)
                self.assertNotIn("}}", text)
                self.assertNotIn("[待实测补齐]", text)
                self.assertNotIn("TODO", text)

    def test_canonical_adrs_exist_with_required_topics(self) -> None:
        expected = {
            "ADR-001-scope-and-cleanup.md": ["原地", "Apple Silicon", "逐轮", "v2.0-mac-ready"],
            "ADR-002-full-18517-glossary.md": ["18,517", "两段式", "900", "fcntl"],
            "ADR-003-quick-term-injection-no-highlight.md": ["QuickTermBar", "promote_term", "标红"],
            "ADR-004-tauri-v2-mimi-sidecar.md": ["bce7164", "Tauri v2", "Sidecar", "hayamimi"],
            "ADR-005-multi-window-parallel-qwen38.md": ["1 至 3", "window_id", "关窗", "R12"],
            "ADR-006-mimi-14-parity-and-no-token.md": ["14 项", "Process Tap", "Token", "零音效"],
            "ADR-009-r10-r12-phase4-closeout.md": [
                "MAX_OVERLAY_WINDOWS = 1",
                "loopback_missing",
                "session_reset",
                "R13",
            ],
        }
        for filename, needles in expected.items():
            path = ADR_DIR / filename
            self.assertTrue(path.is_file(), filename)
            text = path.read_text(encoding="utf-8")
            for needle in needles:
                with self.subTest(path=filename, needle=needle):
                    self.assertIn(needle, text)

    def test_r095_adr_records_the_current_single_window_contract(self) -> None:
        path = ADR_DIR / "ADR-008-r095-single-window-wire-budget.md"
        text = path.read_text(encoding="utf-8")
        for needle in [
            "MAX_OVERLAY_WINDOWS = 1",
            "<= 250",
            "4096 B",
            "6144 B",
            "turn_detection",
            "session.finish",
            "1007/429",
            "本机 `.env`",
        ]:
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_contract_no_longer_requires_full_glossary_or_parallel_windows(self) -> None:
        text = CONTRACT.read_text(encoding="utf-8")
        self.assertNotIn("18,517", text)
        self.assertNotIn("1 至 3 窗均并行", text)
        self.assertNotIn("09~14 后台", text)

    def test_contract_and_adrs_are_tracked_but_local_config_is_ignored(self) -> None:
        tracked = set(self.git("ls-files").stdout.splitlines())
        self.assertIn(".agents/PROJECT_CONTRACT.md", tracked)
        for path in sorted(ADR_DIR.glob("ADR-*.md")):
            self.assertIn(path.relative_to(ROOT).as_posix(), tracked)
        self.assertNotIn(".agents/workflow.config", tracked)
        self.assertEqual(self.git("check-ignore", "-q", ".agents/workflow.config").returncode, 0)

    def test_workflow_config_uses_real_commands(self) -> None:
        text = CONFIG.read_text(encoding="utf-8")
        for expected in [
            'BASE_BRANCH="main"',
            'TEST_CMD=".venv-v2/bin/python -m pytest -q tests/"',
            'CORE_REGRESSION_CMD=".venv-v2/bin/python -m pytest -q tests/core_regression/"',
            'SMOKE_CMD=".venv-v2/bin/python scripts/smoke_e2e.py --mock"',
            'BENCH_CMD=".venv-v2/bin/python scripts/bench_latency.py"',
            'BENCH_THRESHOLD="3"',
            'BENCH_HIGHER_IS_BETTER="0"',
            'PERF_CRITICAL="1"',
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        self.assertNotIn("echo 0", text)
        self.assertNotIn("TEST_CMD=\"\"", text)

    def test_current_docs_have_no_windows_instructions(self) -> None:
        docs = "\n".join(self.read(name) for name in ["README.md", "INSTALL.md", "TUTORIAL.md"])
        for forbidden in [
            "PowerShell",
            ".venv\\Scripts",
            "Win + P",
            "taskkill",
            "WASAPI",
            "msvcrt",
            "NSIS",
            "WebView2",
            ".bat",
            ".ps1",
        ]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, docs)
        tutorial = self.read("TUTORIAL.md")
        self.assertIn("./启动同传系统.command --legacy", tutorial)
        self.assertIn("真实 DashScope", tutorial)
        self.assertIn("单窗双语", tutorial)


if __name__ == "__main__":
    unittest.main()
