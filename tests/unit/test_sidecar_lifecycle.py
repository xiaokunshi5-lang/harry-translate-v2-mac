from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from typing import Any

from app.main import bind_server_socket

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "sk-your-dashscope-api-key-here"
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_ready(process: subprocess.Popen[str], timeout_sec: float = 12.0) -> dict[str, Any]:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout_sec
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
        payload = json.loads(line)
        if payload.get("event") == "sidecar_ready":
            return payload
    stderr = process.stderr.read() if process.stderr and process.poll() is not None else ""
    raise AssertionError(f"sidecar_ready not received before timeout: {stderr}")


class SidecarLifecycleTests(unittest.TestCase):
    def test_dynamic_port_fallback_when_preferred_port_occupied_and_stdin_eof_exit(self) -> None:
        occupier = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupier.bind(("127.0.0.1", 0))
        occupier.listen(1)
        busy_port = occupier.getsockname()[1]

        try:
            reserved = bind_server_socket(host="127.0.0.1", preferred_port=busy_port, max_scan=10)
            self.assertNotEqual(reserved.getsockname()[1], busy_port)
            reserved.close()

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "config").mkdir()
                (root / ".env.example").write_text(
                    f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
                    encoding="utf-8",
                )
                runtime_file = root / ".agents" / "runtime_port"
                env = os.environ.copy()
                env["DASHSCOPE_API_KEY"] = PLACEHOLDER
                env["PYTHONUNBUFFERED"] = "1"

                started_at = time.monotonic()
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
                        "--watch-stdin",
                        "--no-audio-capture",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    ready = _read_ready(process)
                    elapsed_ready = time.monotonic() - started_at
                    self.assertLess(elapsed_ready, 5.0)
                    self.assertGreater(ready["port"], 0)
                    self.assertNotEqual(ready["port"], busy_port)

                    with LOCAL_OPENER.open(
                        f"http://127.0.0.1:{ready['port']}/api/health",
                        timeout=3,
                    ) as resp:
                        self.assertEqual(resp.status, 200)
                        health = json.loads(resp.read())
                        self.assertEqual(health["status"], "ok")

                    assert process.stdin is not None
                    eof_at = time.monotonic()
                    process.stdin.close()
                    process.wait(timeout=3.0)
                    self.assertLess(time.monotonic() - eof_at, 2.5)
                    self.assertIsNotNone(process.poll())
                    self.assertFalse(runtime_file.exists())
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
        finally:
            occupier.close()

    def test_system_shutdown_endpoint_terminates_sidecar_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            (root / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
                encoding="utf-8",
            )
            runtime_file = root / ".agents" / "runtime_port"
            env = os.environ.copy()
            env["DASHSCOPE_API_KEY"] = PLACEHOLDER
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
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready = _read_ready(process)
                req = urllib.request.Request(
                    f"http://127.0.0.1:{ready['port']}/api/system/shutdown",
                    data=b"{}",
                    method="POST",
                )
                with LOCAL_OPENER.open(req, timeout=3) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertTrue(json.loads(resp.read())["ok"])
                process.wait(timeout=4.0)
                self.assertIsNotNone(process.poll())
                self.assertFalse(runtime_file.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=3)

    def test_zero_non_persistent_data_store_and_zero_pywebview_across_active_code(self) -> None:
        scan_dirs = [ROOT / "app", ROOT / "frontend" / "src", ROOT / "src-tauri" / "src"]
        hits_non_persistent = 0
        hits_pywebview = 0
        for base in scan_dirs:
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".rs", ".css"}:
                    continue
                content = path.read_text(encoding="utf-8")
                hits_non_persistent += content.count("nonPersistentDataStore")
                hits_pywebview += content.count("pywebview")
        self.assertEqual(hits_non_persistent, 0)
        self.assertEqual(hits_pywebview, 0)


if __name__ == "__main__":
    unittest.main()
