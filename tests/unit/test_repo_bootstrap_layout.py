from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RepositoryBootstrapLayoutTests(unittest.TestCase):
    def test_required_directories_exist(self) -> None:
        required = [
            "app/audio",
            "app/bypass",
            "app/engines",
            "app/glossary",
            "config",
            "docs/workflows",
            "frontend",
            "glossary_bank",
            "legacy/v1",
            "scripts",
            "src-tauri",
            "tests/unit",
        ]
        for relative in required:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).is_dir())

    def test_workflow_assets_and_entrypoints_exist(self) -> None:
        required = [
            "启动同传系统.command",
            "start.sh",
            "requirements.txt",
            "scripts/init_project_contract.sh",
            "scripts/capture_baseline.sh",
            "scripts/watchdog_gate.sh",
            "scripts/fix_attempt.sh",
            "scripts/rollback.sh",
            "docs/workflows/计划工作流.md",
            "docs/workflows/项目契约模板.md",
            "docs/MAC_EXECUTION_PLAN.md",
        ]
        for relative in required:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).is_file())

        for relative in [
            "启动同传系统.command",
            "start.sh",
            "scripts/rollback.sh",
        ]:
            with self.subTest(executable=relative):
                self.assertTrue(os.access(ROOT / relative, os.X_OK))

    def test_ignore_and_line_ending_contract_is_mac_only(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

        for pattern in [
            ".env",
            ".venv/",
            ".venv-*/",
            ".DS_Store",
            "node_modules/",
            "src-tauri/target/",
            ".tmp/",
            "exports/*",
            "config/user_settings.json",
        ]:
            with self.subTest(ignore=pattern):
                self.assertIn(pattern, ignore)

        self.assertIn("*.command text eol=lf", attributes)
        self.assertIn("*.py text eol=lf", attributes)
        self.assertNotIn("*.bat", attributes)
        self.assertNotIn("*.ps1", attributes)

    def test_shell_scripts_parse(self) -> None:
        scripts = sorted((ROOT / "scripts").glob("*.sh"))
        scripts.extend([ROOT / "start.sh", ROOT / "启动同传系统.command"])
        result = subprocess.run(
            ["bash", "-n", *map(str, scripts)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_windows_only_entrypoints_were_created(self) -> None:
        windows_files = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and ".venv" not in path.parts
            and not any(part.startswith(".venv-") for part in path.parts)
            and ".tmp" not in path.parts
            and "node_modules" not in path.parts
            and "target" not in path.parts
            and path.suffix.lower() in {".bat", ".ps1"}
        ]
        self.assertEqual(windows_files, [])


if __name__ == "__main__":
    unittest.main()
