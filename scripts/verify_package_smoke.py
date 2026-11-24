#!/usr/bin/env python3
"""Verify the local sidecar contract and package metadata without secrets."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE_BUNDLE_ROOT = (
    ROOT / "src-tauri" / "target" / "aarch64-apple-darwin" / "release" / "bundle"
)


def classify_signature_details(details: str) -> str:
    normalized = details.replace("\r", "\n")
    if "Authority=Developer ID Application:" in normalized:
        return "developer_id"
    if re.search(r"Signature=(?:adhoc|ad hoc)", normalized, re.IGNORECASE):
        return "adhoc_local"
    if "TeamIdentifier=not set" in normalized and "Identifier=" in normalized:
        return "adhoc_local"
    if "code object is not signed at all" in normalized:
        return "unsigned_local"
    return "unknown"


def command_output(command: list[str], timeout: float = 20.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, str(error)
    return result.returncode, f"{result.stdout}\n{result.stderr}".strip()


def find_release_artifact(directory: Path, suffix: str) -> Path | None:
    if not directory.is_dir():
        return None
    candidates = sorted(
        (path for path in directory.iterdir() if path.name.endswith(suffix)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def has_stapled_ticket(target: Path | None) -> bool:
    if target is None or not target.exists():
        return False
    code, _output = command_output(
        ["xcrun", "stapler", "validate", str(target)],
        timeout=30,
    )
    return code == 0


def gatekeeper_assessment(target: Path | None, target_type: str) -> tuple[bool, bool]:
    if target is not None and target.exists():
        code, output = command_output(
            ["spctl", "-a", "-vvv", "-t", target_type, str(target)],
            timeout=30,
        )
    else:
        return False, False
    accepted = code == 0
    notarized_marker = "Notarized Developer ID" in output
    return accepted, notarized_marker


def inspect_signature(
    *,
    app: Path | None,
    dmg: Path | None,
    binary: Path | None,
) -> dict[str, object]:
    target = app or dmg or binary
    details = ""
    raw_mode = "unavailable"
    target_kind = "missing"
    if target is not None and target.exists():
        target_kind = "app" if target == app else "dmg" if target == dmg else "sidecar"
        _code, details = command_output(
            ["codesign", "-dv", "--verbose=4", str(target)],
            timeout=20,
        )
        raw_mode = classify_signature_details(details)

    app_stapled = has_stapled_ticket(app)
    dmg_stapled = has_stapled_ticket(dmg)
    app_accepted, app_notarized_marker = gatekeeper_assessment(app, "install")
    dmg_accepted, dmg_notarized_marker = gatekeeper_assessment(dmg, "open")
    app_notarized = app_stapled or app_notarized_marker
    dmg_notarized = dmg_stapled or dmg_notarized_marker
    if app is not None and dmg is not None:
        product_notarized = app_notarized and dmg_notarized
        stapled = app_stapled and dmg_stapled
        gatekeeper_accepted = app_accepted and dmg_accepted
    elif app is not None:
        product_notarized = app_notarized
        stapled = app_stapled
        gatekeeper_accepted = app_accepted
    elif dmg is not None:
        product_notarized = dmg_notarized
        stapled = dmg_stapled
        gatekeeper_accepted = dmg_accepted
    else:
        product_notarized = False
        stapled = False
        gatekeeper_accepted = False

    if raw_mode == "developer_id":
        signature_mode = "developer_id_notarized" if product_notarized else "developer_id_unnotarized"
    else:
        signature_mode = raw_mode

    return {
        "signature_mode": signature_mode,
        "notarized": signature_mode == "developer_id_notarized",
        "has_stapled_ticket": stapled,
        "app_stapled": app_stapled,
        "dmg_stapled": dmg_stapled,
        "app_gatekeeper_accepted": app_accepted,
        "dmg_gatekeeper_accepted": dmg_accepted,
        "gatekeeper_accepted": gatekeeper_accepted,
        "signing_target": target_kind,
    }


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
    parser.add_argument("--app", type=Path)
    parser.add_argument("--dmg", type=Path)
    args = parser.parse_args(argv)
    errors = check_static()
    for label, path in (("app", args.app), ("dmg", args.dmg), ("binary", args.binary)):
        if path is not None and not path.exists():
            errors.append(f"requested {label} artifact does not exist: {path}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    app = args.app or find_release_artifact(RELEASE_BUNDLE_ROOT / "macos", ".app")
    dmg = args.dmg or find_release_artifact(RELEASE_BUNDLE_ROOT / "dmg", ".dmg")
    binary = args.binary
    if binary is None and app is None and dmg is None:
        candidate = (
            ROOT
            / "src-tauri"
            / "binaries"
            / "harry-sidecar-aarch64-apple-darwin"
        )
        binary = candidate if candidate.exists() else None
    signature = inspect_signature(app=app, dmg=dmg, binary=binary)
    print(f"PACKAGE-SIGNING {json.dumps(signature, ensure_ascii=False)}")
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
