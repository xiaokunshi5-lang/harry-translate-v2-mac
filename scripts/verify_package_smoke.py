#!/usr/bin/env python3
"""Verify the local sidecar contract and package metadata without secrets."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_static() -> list[str]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_sidecar.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return [result.stderr.strip() or "build_sidecar --check failed"]
    config_path = ROOT / "src-tauri" / "tauri.macos.conf.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if config.get("bundle", {}).get("targets") != ["dmg", "app"]:
        errors.append("Tauri bundle targets must be dmg and app")
    return errors


def wait_for_ready(stream_queue: queue.Queue[str], timeout: float = 10.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = stream_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "sidecar_ready" and int(payload.get("port", 0)) > 0:
            return payload
    raise TimeoutError("sidecar_ready line was not received")


def fetch_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def run_lifecycle_smoke(binary: Path | None = None) -> None:
    with tempfile.TemporaryDirectory(prefix="harry-r16-sidecar-") as temp:
        runtime_port = Path(temp) / ".agents" / "runtime_port"
        project_root = Path(temp) / "project"
        project_root.mkdir(parents=True, exist_ok=True)
        command = [
            str(binary) if binary is not None else sys.executable,
            *( [] if binary is not None else ["-m", "app.main"] ),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--runtime-port-file",
            str(runtime_port),
            "--project-root",
            str(project_root),
            "--no-audio-capture",
        ]
        environment = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1"}
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        lines: queue.Queue[str] = queue.Queue()

        def pump_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line.strip())

        threading.Thread(target=pump_stdout, daemon=True).start()
        try:
            ready = wait_for_ready(lines)
            port = int(ready["port"])
            health = fetch_json(f"http://127.0.0.1:{port}/api/health?fast=1")
            if health.get("status") != "ok":
                raise AssertionError(f"health failed: {health}")
            save_payload = {
                "id": "session_package_smoke",
                "clientSavedAt": int(time.time() * 1000),
                "entries": [
                    {
                        "window_id": "main",
                        "sent_id": 1,
                        "source": "package smoke",
                        "target": "打包冒烟",
                    }
                ],
            }
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/session/save",
                data=json.dumps(save_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                saved = json.loads(response.read().decode("utf-8"))
            if saved.get("ignored"):
                raise AssertionError(f"save unexpectedly ignored: {saved}")
            shutdown = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/system/shutdown",
                data=b"",
                method="POST",
            )
            urllib.request.urlopen(shutdown, timeout=3).read()
            process.wait(timeout=5)
            if process.returncode != 0:
                raise AssertionError(f"sidecar exited with {process.returncode}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            stderr = process.stderr.read() if process.stderr is not None else ""
            if process.returncode not in (0, -15) and stderr:
                print(stderr, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--lifecycle", action="store_true")
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args(argv)
    errors = check_static()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("PACKAGE-CHECK PASS")
    if args.check and not args.lifecycle:
        return 0
    try:
        run_lifecycle_smoke(args.binary)
    except Exception as error:
        print(f"ERROR: package lifecycle smoke failed: {error}", file=sys.stderr)
        return 2
    print("PACKAGE-LIFECYCLE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
