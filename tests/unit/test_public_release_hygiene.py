from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()


class PublicReleaseHygieneTests(unittest.TestCase):
    def test_no_private_workspace_host_or_personal_home_path_is_tracked(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout.split(b"\0")
        patterns = [
            re.compile(rb"llm-[a-z0-9]{8,}\.cn-beijing\.maas\.aliyuncs\.com"),
            re.compile(rb"/Users/[A-Za-z0-9._-]+/"),
        ]

        findings: list[str] = []
        for raw_name in tracked:
            if not raw_name:
                continue
            path = ROOT / raw_name.decode("utf-8")
            if path.resolve() == SELF or not path.is_file():
                continue
            payload = path.read_bytes()
            if b"\0" in payload[:4096]:
                continue
            for pattern in patterns:
                if pattern.search(payload):
                    findings.append(f"{path.relative_to(ROOT)}: {pattern.pattern!r}")

        self.assertEqual(findings, [])

    def test_secret_file_is_ignored_and_example_is_safe_and_portable(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout.split(b"\0")
        self.assertNotIn(b".env", tracked)

        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn(
            "DASHSCOPE_API_KEY=sk-your-dashscope-api-key-here",
            example,
        )
        self.assertIn("QWEN_REALTIME_WS_URL=\n", example)
        self.assertIn("CHUNK_SIZE=800", example)
        self.assertNotIn("maas.aliyuncs.com", example)


if __name__ == "__main__":
    unittest.main()
