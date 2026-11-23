from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TAURI_BINARY = ROOT / "src-tauri" / "target" / "debug" / "harry-translate-mac"
PLACEHOLDER = "sk-your-dashscope-api-key-here"
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FULLSCREEN_FIXTURE = ROOT / "tests" / "smoke" / "fullscreen_fixture.swift"
WINDOW_PROBE_SCRIPT = r"""
import CoreGraphics
import Foundation

let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
    as? [[String: Any]] ?? []
let rows = windows.compactMap { item -> [String: Any]? in
    let owner = item[kCGWindowOwnerName as String] as? String ?? ""
    let pid = item[kCGWindowOwnerPID as String] as? Int ?? -1
    let name = item[kCGWindowName as String] as? String ?? ""
    let layer = item[kCGWindowLayer as String] as? Int ?? -1
    guard owner.contains("harry-translate-mac")
        || owner.contains("fullscreen-fixture")
        || name.contains("R155S Fullscreen Fixture") else {
        return nil
    }
    return [
        "owner": owner,
        "pid": pid,
        "name": name,
        "layer": layer,
    ]
}
let data = try JSONSerialization.data(withJSONObject: rows, options: [])
print(String(data: data, encoding: .utf8) ?? "[]")
"""


def _read_ready(process: subprocess.Popen[str], timeout: float = 15.0) -> dict[str, Any]:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        readable, _, _ = select.select([process.stdout.fileno()], [], [], 0.25)
        if not readable:
            continue
        line = process.stdout.readline().strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "sidecar_ready":
            return payload
    stderr = process.stderr.read() if process.stderr and process.poll() is not None else ""
    raise AssertionError(f"sidecar did not become ready: {stderr}")


def _read_process_event(
    process: subprocess.Popen[str],
    expected_event: str,
    timeout: float,
) -> dict[str, Any]:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise AssertionError(
                f"process exited before event {expected_event!r} "
                f"({process.returncode}): {stderr}"
            )
        readable, _, _ = select.select([process.stdout.fileno()], [], [], 0.25)
        if not readable:
            continue
        line = process.stdout.readline().strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("event") == expected_event:
            return payload
    stderr = process.stderr.read() if process.poll() is not None and process.stderr else ""
    raise AssertionError(f"timed out waiting for event {expected_event!r}: {stderr}")


def _wait_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with LOCAL_OPENER.open(url, timeout=0.5) as response:
                return json.loads(response.read())
        except Exception as error:  # noqa: BLE001
            last_error = error
            time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {url}: {last_error}")


def _wait_overlay_registration(
    state_path: Path,
    overlay_process: subprocess.Popen[str],
    timeout: float = 8.0,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if overlay_process.poll() is not None:
            stderr = overlay_process.stderr.read() if overlay_process.stderr else ""
            raise AssertionError(
                f"overlay exited before registration ({overlay_process.returncode}): {stderr}"
            )
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            control_url = state.get("overlay_control_url")
            if isinstance(control_url, str) and control_url:
                try:
                    with LOCAL_OPENER.open(f"{control_url}/health", timeout=0.3) as response:
                        if response.status == 200:
                            return control_url
                except Exception:  # noqa: BLE001
                    pass
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise AssertionError("overlay control service did not register")


def _wait_exit(process: subprocess.Popen[str], timeout: float = 5.0) -> int:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise AssertionError(f"process {process.pid} did not exit within {timeout}s") from error
    return int(process.returncode or 0)


def _terminate(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _window_snapshot() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["swift", "-e", WINDOW_PROBE_SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)


def _wait_fullscreen_windows(
    overlay_pid: int,
    fixture_pid: int,
    timeout: float = 8.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout
    last_windows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last_windows = _window_snapshot()
        overlay_windows = [
            window for window in last_windows if int(window["pid"]) == overlay_pid
        ]
        fixture_windows = [
            window for window in last_windows if int(window["pid"]) == fixture_pid
        ]
        if fixture_windows and any(
            int(window["layer"]) == 1000 for window in overlay_windows
        ):
            return last_windows, overlay_windows, fixture_windows
        time.sleep(0.2)
    raise AssertionError(
        "full-screen fixture or overlay did not appear at layer 1000 within "
        f"{timeout}s; last windows={last_windows}"
    )


def _build_fullscreen_fixture(root: Path) -> Path:
    binary = root / "fullscreen-fixture"
    result = subprocess.run(
        [
            "xcrun",
            "swiftc",
            str(FULLSCREEN_FIXTURE),
            "-o",
            str(binary),
            "-framework",
            "AppKit",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return binary


def _set_summary_enabled(base_url: str, enabled: bool) -> None:
    body = json.dumps({"settings": {"summary_enabled": enabled}}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/config",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with LOCAL_OPENER.open(request, timeout=3) as response:
        if response.status != 200:
            raise AssertionError(f"summary enable failed: HTTP {response.status}")


def _start_overlay(
    root: Path,
    environment: dict[str, str],
    backend_pid: int,
    backend_port: int,
    instance_token: str,
) -> tuple[subprocess.Popen[str], str]:
    overlay_environment = environment.copy()
    overlay_environment.update(
        {
            "HARRY_PROJECT_ROOT": str(root),
            "HARRY_ATTACH_BACKEND_URL": f"http://127.0.0.1:{backend_port}",
            "HARRY_OWNER_PID": str(backend_pid),
            "HARRY_INSTANCE_TOKEN": instance_token,
            "HARRY_NO_OPEN_SETTINGS": "1",
        }
    )
    process = subprocess.Popen(
        [str(TAURI_BINARY)],
        cwd=ROOT,
        env=overlay_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=None,
        text=True,
    )
    control_url = _wait_overlay_registration(
        root / ".agents" / "runtime_state.json",
        process,
    )
    return process, control_url


def _assert_panel(
    panel: dict[str, Any],
    expected_class: str | None,
    *,
    expected_size: tuple[float, float] | None = None,
) -> None:
    required_bits = (1 << 0) | (1 << 8) | (1 << 18)
    collection_behavior = int(panel["collectionBehavior"])
    if collection_behavior & required_bits != required_bits:
        raise AssertionError(f"missing full-screen collection bits: {collection_behavior}")
    if int(panel["windowLevel"]) != 1000:
        raise AssertionError(f"unexpected window level: {panel['windowLevel']}")
    if expected_class is not None and expected_class not in str(panel["className"]):
        raise AssertionError(f"unexpected panel class: {panel['className']}")
    if not bool(panel["visible"]):
        raise AssertionError(f"{panel['label']} panel is not visible")
    if expected_size is not None:
        actual_size = (float(panel["frameWidth"]), float(panel["frameHeight"]))
        if actual_size != expected_size:
            raise AssertionError(f"unexpected {panel['label']} size: {actual_size}")


def main() -> int:
    if not TAURI_BINARY.is_file():
        raise AssertionError(f"missing Tauri binary: {TAURI_BINARY}")

    evidence: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="harry-r155s-native-smoke-") as temporary:
        root = Path(temporary)
        (root / "config").mkdir()
        (root / ".env.example").write_text(
            f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )
        state_path = root / ".agents" / "runtime_state.json"
        environment = os.environ.copy()
        environment.update(
            {
                "DASHSCOPE_API_KEY": PLACEHOLDER,
                "PYTHONUNBUFFERED": "1",
                "HARRY_NO_OPEN_SETTINGS": "1",
            }
        )
        backend: subprocess.Popen[str] | None = None
        overlay: subprocess.Popen[str] | None = None
        second_overlay: subprocess.Popen[str] | None = None
        restarted_overlay: subprocess.Popen[str] | None = None
        fullscreen_fixture: subprocess.Popen[str] | None = None
        kill_backend: subprocess.Popen[str] | None = None
        kill_overlay: subprocess.Popen[str] | None = None
        try:
            backend = subprocess.Popen(
                [
                    str(ROOT / ".venv-v2" / "bin" / "python"),
                    "-m",
                    "app.main",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--runtime-port-file",
                    str(root / ".agents" / "runtime_port"),
                    "--project-root",
                    str(root),
                    "--no-audio-capture",
                ],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            ready = _read_ready(backend)
            base_url = f"http://127.0.0.1:{ready['port']}"
            _set_summary_enabled(base_url, True)

            deadline = time.monotonic() + 5
            instance_token = ""
            while time.monotonic() < deadline:
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    instance_token = str(state.get("instance_token") or "")
                except (OSError, UnicodeError, json.JSONDecodeError):
                    instance_token = ""
                if instance_token:
                    break
                time.sleep(0.05)
            if not instance_token:
                raise AssertionError("backend instance token missing")

            overlay, control_url = _start_overlay(
                root,
                environment,
                backend.pid,
                int(ready["port"]),
                instance_token,
            )
            deadline = time.monotonic() + 8
            diagnostics: dict[str, Any] = {}
            while time.monotonic() < deadline:
                diagnostics = _wait_json(f"{control_url}/diagnostics")
                if diagnostics.get("summary") is not None:
                    break
                time.sleep(0.1)
            _assert_panel(diagnostics["overlay"], "HarrySubtitlePanel")
            _assert_panel(
                diagnostics["summary"],
                None,
                expected_size=(48.0, 48.0),
            )
            evidence["diagnostics"] = diagnostics

            for cycle in range(3):
                _set_summary_enabled(base_url, False)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    close_diagnostics = _wait_json(f"{control_url}/diagnostics")
                    if close_diagnostics.get("summary") is None:
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError(
                        f"summary panel did not close cleanly on cycle {cycle + 1}"
                    )
                if overlay.poll() is not None:
                    raise AssertionError("overlay exited while closing summary")

                # The setting change reaches the overlay through the main
                # session channel before React can request a new summary window.
                time.sleep(0.5)
                _set_summary_enabled(base_url, True)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    reopened_diagnostics = _wait_json(f"{control_url}/diagnostics")
                    if reopened_diagnostics.get("summary") is not None:
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError(
                        f"summary panel did not reopen on cycle {cycle + 1}"
                    )
                _assert_panel(
                    reopened_diagnostics["summary"],
                    None,
                    expected_size=(48.0, 48.0),
                )

            fixture_binary = _build_fullscreen_fixture(root)
            fullscreen_fixture = subprocess.Popen(
                [str(fixture_binary)],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            _read_process_event(
                fullscreen_fixture,
                expected_event="fullscreen_entered",
                timeout=15.0,
            )
            fullscreen_windows, overlay_windows, fixture_windows = (
                _wait_fullscreen_windows(overlay.pid, fullscreen_fixture.pid)
            )
            if not fixture_windows:
                raise AssertionError("full-screen fixture window is not onscreen")
            if not overlay_windows:
                raise AssertionError("overlay disappeared from the full-screen Space")
            if not any(int(window["layer"]) == 1000 for window in overlay_windows):
                raise AssertionError("overlay lost screen-saver window level")
            evidence["fullscreen"] = {
                "overlay_windows": overlay_windows,
                "fixture_windows": fixture_windows,
            }
            _terminate(fullscreen_fixture)
            time.sleep(1.0)

            hide_request = urllib.request.Request(
                f"{control_url}/hide",
                data=b"",
                method="POST",
            )
            with LOCAL_OPENER.open(hide_request, timeout=3) as response:
                if response.status != 200:
                    raise AssertionError(f"hide returned HTTP {response.status}")
            hidden_health = _wait_json(f"{control_url}/health")
            if hidden_health.get("visible") is not False:
                raise AssertionError(f"hide did not update health: {hidden_health}")

            second_overlay, _ = _start_overlay(
                root,
                environment,
                backend.pid,
                int(ready["port"]),
                instance_token,
            )
            evidence["duplicate_returncode"] = _wait_exit(second_overlay)
            if overlay.poll() is not None:
                raise AssertionError("primary overlay exited after duplicate launch")
            shown_health = _wait_json(f"{control_url}/health")
            if shown_health.get("visible") is not True:
                raise AssertionError(
                    f"duplicate launch did not restore visible health: {shown_health}"
                )
            evidence["duplicate_visible_health"] = shown_health

            shutdown_request = urllib.request.Request(
                f"{control_url}/shutdown",
                data=b"",
                method="POST",
            )
            with LOCAL_OPENER.open(shutdown_request, timeout=3) as response:
                if response.status != 202:
                    raise AssertionError(f"shutdown returned HTTP {response.status}")
            evidence["control_shutdown_returncode"] = _wait_exit(overlay)

            restarted_overlay, restarted_control = _start_overlay(
                root,
                environment,
                backend.pid,
                int(ready["port"]),
                instance_token,
            )
            _wait_json(f"{restarted_control}/diagnostics")

            backend_shutdown = urllib.request.Request(
                f"{base_url}/api/system/shutdown",
                data=b"",
                method="POST",
            )
            with LOCAL_OPENER.open(backend_shutdown, timeout=3) as response:
                if response.status != 200:
                    raise AssertionError(
                        f"backend shutdown returned HTTP {response.status}"
                    )
            evidence["backend_shutdown_returncode"] = _wait_exit(backend)
            evidence["backend_shutdown_overlay_returncode"] = _wait_exit(
                restarted_overlay
            )
            if evidence["backend_shutdown_returncode"] != 0:
                raise AssertionError("backend shutdown did not exit cleanly")
            if evidence["backend_shutdown_overlay_returncode"] != 0:
                raise AssertionError("overlay did not exit cleanly during backend shutdown")

            kill_backend = subprocess.Popen(
                [
                    str(ROOT / ".venv-v2" / "bin" / "python"),
                    "-m",
                    "app.main",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--runtime-port-file",
                    str(root / ".agents" / "runtime_port"),
                    "--project-root",
                    str(root),
                    "--no-audio-capture",
                ],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            kill_ready = _read_ready(kill_backend)
            deadline = time.monotonic() + 5
            kill_token = ""
            while time.monotonic() < deadline:
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    kill_token = str(state.get("instance_token") or "")
                except (OSError, UnicodeError, json.JSONDecodeError):
                    kill_token = ""
                if kill_token:
                    break
                time.sleep(0.05)
            if not kill_token:
                raise AssertionError("replacement backend instance token missing")
            kill_overlay, kill_control = _start_overlay(
                root,
                environment,
                kill_backend.pid,
                int(kill_ready["port"]),
                kill_token,
            )
            _wait_json(f"{kill_control}/diagnostics")
            os.kill(kill_backend.pid, signal.SIGKILL)
            kill_backend.wait(timeout=3)
            evidence["backend_death_returncode"] = _wait_exit(kill_overlay)
            if evidence["backend_death_returncode"] != 0:
                raise AssertionError("overlay did not exit cleanly after backend death")
        finally:
            _terminate(fullscreen_fixture)
            _terminate(kill_overlay)
            _terminate(kill_backend)
            _terminate(restarted_overlay)
            _terminate(second_overlay)
            _terminate(overlay)
            _terminate(backend)

    print(json.dumps({"ok": True, "evidence": evidence}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
