from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "sk-your-dashscope-api-key-here"


class SummaryWidgetUiGateTests(unittest.TestCase):
    def test_default_off_zero_dom_and_three_state_interaction(self) -> None:
        node_modules = ROOT / "frontend" / "node_modules" / "playwright-core"
        self.assertTrue(node_modules.is_dir(), "run npm ci in frontend before pytest")
        output_dir = ROOT / ".tmp" / "r15-artifacts"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
                encoding="utf-8",
            )
            runtime_file = root / ".agents" / "runtime_port"
            environment = os.environ.copy()
            environment["DASHSCOPE_API_KEY"] = PLACEHOLDER
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "app.main",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--runtime-port-file",
                    str(runtime_file),
                    "--project-root",
                    str(root),
                    "--no-audio-capture",
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready = self._read_ready(process)
                base_url = f"http://127.0.0.1:{ready['port']}"
                result = subprocess.run(
                    [
                        "node",
                        str(ROOT / "tests" / "smoke" / "summary_widget_ui.mjs"),
                        base_url,
                        str(output_dir),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertEqual(len(payload["results"]), 2)
                for viewport in ("desktop", "narrow"):
                    self.assertTrue(
                        (output_dir / f"{viewport}-summary.png").is_file(),
                        f"missing {viewport} screenshot",
                    )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

    @staticmethod
    def _read_ready(process: subprocess.Popen[str]) -> dict[str, Any]:
        assert process.stdout is not None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            readable, _, _ = select.select(
                [process.stdout.fileno()],
                [],
                [],
                max(0.0, deadline - time.monotonic()),
            )
            if not readable:
                break
            line = process.stdout.readline().strip()
            if not line:
                continue
            candidate = json.loads(line)
            if candidate.get("event") == "sidecar_ready":
                return candidate
        error = process.stderr.read() if process.poll() is not None else ""
        raise AssertionError(f"sidecar did not become ready: {error}")


if __name__ == "__main__":
    unittest.main()
