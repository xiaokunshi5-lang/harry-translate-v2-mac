from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROLLBACK = ROOT / "scripts" / "rollback.sh"


class RollbackScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / ".tmp").mkdir(parents=True, exist_ok=True)
        self.repo = Path(tempfile.mkdtemp(prefix="rollback-test-", dir=ROOT / ".tmp"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Rollback Test")
        self.git("config", "user.email", "rollback-test@local")
        (self.repo / ".gitignore").write_text(
            ".env\n.venv*/\n.tmp/\n.pytest_cache/\n",
            encoding="utf-8",
        )
        (self.repo / "tracked.txt").write_text("C1\n", encoding="utf-8")
        self.git("add", ".gitignore", "tracked.txt")
        self.git("commit", "-q", "-m", "C1")
        self.git("tag", "c1")
        (self.repo / "tracked.txt").write_text("C2\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-q", "-m", "C2")
        self.git("tag", "c2")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def rollback(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROLLBACK), *args],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )

    def prepare_protected_assets(self) -> None:
        (self.repo / "scratch.txt").write_text("remove me\n", encoding="utf-8")
        (self.repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        protected = {
            ".env": "SECRET=keep\n",
            ".venv/keep.txt": "venv\n",
            ".venv-v2/keep.txt": "venv-v2\n",
            ".tmp/keep.txt": "tmp\n",
            "config/user_settings.json": "{}\n",
            "config/glossary.json": "{}\n",
            "glossary_bank/custom.json": "{}\n",
            "exports/note.md": "note\n",
            "logs/runtime.log": "log\n",
            "node_modules/keep.js": "node\n",
            "src-tauri/target/keep.bin": "target\n",
        }
        for relative, content in protected.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def assert_protected_assets(self) -> None:
        expected = {
            ".env": "SECRET=keep\n",
            ".venv/keep.txt": "venv\n",
            ".venv-v2/keep.txt": "venv-v2\n",
            ".tmp/keep.txt": "tmp\n",
            "config/user_settings.json": "{}\n",
            "config/glossary.json": "{}\n",
            "glossary_bank/custom.json": "{}\n",
            "exports/note.md": "note\n",
            "logs/runtime.log": "log\n",
            "node_modules/keep.js": "node\n",
            "src-tauri/target/keep.bin": "target\n",
        }
        for relative, content in expected.items():
            with self.subTest(path=relative):
                self.assertEqual(
                    (self.repo / relative).read_text(encoding="utf-8"),
                    content,
                )

    def test_usage_requires_an_explicit_target(self) -> None:
        result = self.rollback()
        self.assertEqual(result.returncode, 1)
        self.assertIn("用法:", result.stdout)
        self.assertEqual(self.git("rev-parse", "--short", "HEAD"), self.git("rev-parse", "--short", "c2"))

    def test_list_reports_tags_and_rescue_branches(self) -> None:
        result = self.rollback("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("c1", result.stdout)
        self.assertIn("c2", result.stdout)
        self.assertIn("Rescue Branches", result.stdout)

    def test_dry_run_does_not_mutate_worktree(self) -> None:
        self.prepare_protected_assets()
        before = self.git("rev-parse", "HEAD")
        result = self.rollback("c1", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        self.assertTrue((self.repo / "scratch.txt").exists())
        self.assertEqual(self.git("branch", "--list", "rescue/*"), "")

    def test_rollback_preserves_local_assets_and_creates_rescue_branches(self) -> None:
        self.prepare_protected_assets()
        result = self.rollback("c1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.git("rev-parse", "c1"))
        self.assertEqual(
            (self.repo / "tracked.txt").read_text(encoding="utf-8"),
            "C1\n",
        )
        self.assertFalse((self.repo / "scratch.txt").exists())
        self.assert_protected_assets()
        branches = self.git("branch", "--list", "rescue/*")
        self.assertIn("rescue/head_", branches)
        self.assertIn("rescue/wip_", branches)


if __name__ == "__main__":
    unittest.main()
