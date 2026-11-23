from __future__ import annotations

import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
REQUIREMENTS_LOCK = ROOT / "requirements.lock"


class DependencyLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        cls.uv_lock = UV_LOCK.read_text(encoding="utf-8")
        cls.requirements_lock = REQUIREMENTS_LOCK.read_text(encoding="utf-8")

    def test_runtime_dependencies_are_exact_and_zero_compile(self) -> None:
        dependencies = self.project["project"]["dependencies"]
        self.assertEqual(
            dependencies,
            [
                "certifi==2026.11.20",
                "fastapi==0.165.0",
                "pydantic==2.14.4",
                "python-dotenv==1.2.4",
                "sounddevice==0.5.6",
                "uvicorn==0.54.0",
                "websocket-client==1.9.2",
                "websockets==17.4",
            ],
        )
        core_text = "\n".join(dependencies).casefold()
        self.assertNotIn("pyaudio", core_text)
        self.assertIn("sounddevice==0.5.6", core_text)
        self.assertIn("certifi==2026.11.20", core_text)
        self.assertIn("websockets==17.4", core_text)

    def test_python_and_lock_files_are_present(self) -> None:
        self.assertEqual(self.project["project"]["requires-python"], ">=3.12,<3.13")
        self.assertTrue(UV_LOCK.is_file())
        self.assertTrue(REQUIREMENTS_LOCK.is_file())
        self.assertIn('name = "sounddevice"', self.uv_lock)
        self.assertIn('version = "0.5.6"', self.uv_lock)
        self.assertIn("sounddevice==0.5.6", self.requirements_lock)
        self.assertIn("certifi==2026.11.20", self.requirements_lock)

    def test_requirements_lock_has_sha256_for_every_package(self) -> None:
        package_starts = [
            line
            for line in self.requirements_lock.splitlines()
            if line and not line.startswith((" ", "#", "-"))
        ]
        self.assertGreater(len(package_starts), 0)
        blocks = self.requirements_lock.split("\n\n")
        for block in blocks:
            lines = [line for line in block.splitlines() if line and not line.startswith("#")]
            if not lines or any(line.startswith("-") for line in lines):
                continue
            with self.subTest(package=lines[0]):
                self.assertIn("==", lines[0])
                self.assertIn("--hash=sha256:", block)

    def test_locked_export_excludes_removed_dependencies(self) -> None:
        locked_names = {
            line.split("==", 1)[0].casefold()
            for line in self.requirements_lock.splitlines()
            if "==" in line and not line.lstrip().startswith("#")
        }
        self.assertNotIn("pyaudio", locked_names)
        self.assertIn("sounddevice", locked_names)
        self.assertIn("certifi", locked_names)
        self.assertIn("websockets", locked_names)


if __name__ == "__main__":
    unittest.main()
