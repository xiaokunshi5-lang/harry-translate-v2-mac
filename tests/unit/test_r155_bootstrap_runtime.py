from __future__ import annotations

import hashlib
import http.server
import json
import os
import select
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from app.main import frontend_fingerprint

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "sk-your-dashscope-api-key-here"


class BootstrapRuntimeTests(unittest.TestCase):
    def test_prepare_only_never_acquires_or_removes_the_launch_lock(self) -> None:
        bootstrap_source = ROOT / "scripts" / "bootstrap_macos.sh"
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            (sandbox / "scripts").mkdir()
            shutil.copyfile(
                bootstrap_source,
                sandbox / "scripts" / "bootstrap_macos.sh",
            )
            shutil.copyfile(ROOT / "requirements.lock", sandbox / "requirements.lock")
            (sandbox / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\n",
                encoding="utf-8",
            )
            lock_dir = sandbox / ".agents" / "launch.lock"
            lock_dir.mkdir(parents=True)
            (lock_dir / "pid").write_text("999999\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["HARRY_BOOTSTRAP_PREPARE_ONLY"] = "1"
            environment["HARRY_BOOTSTRAP_SKIP_SYNC"] = "1"
            result = subprocess.run(
                ["bash", "scripts/bootstrap_macos.sh"],
                cwd=sandbox,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(lock_dir.is_dir())
            self.assertEqual(
                (lock_dir / "pid").read_text(encoding="utf-8"),
                "999999\n",
            )

    def test_dependency_fingerprint_skips_uv_until_inputs_change(self) -> None:
        bootstrap_source = ROOT / "scripts" / "bootstrap_macos.sh"
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            (sandbox / "scripts").mkdir()
            (sandbox / ".agents").mkdir()
            (sandbox / "fake-bin").mkdir()
            shutil.copyfile(
                bootstrap_source,
                sandbox / "scripts" / "bootstrap_macos.sh",
            )
            shutil.copyfile(ROOT / "requirements.lock", sandbox / "requirements.lock")
            (sandbox / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\n",
                encoding="utf-8",
            )
            pyproject = sandbox / "pyproject.toml"
            uv_lock = sandbox / "uv.lock"
            pyproject.write_text("[project]\nname = \"fingerprint-test\"\n", encoding="utf-8")
            uv_lock.write_text("version = 1\n", encoding="utf-8")

            venv_python = sandbox / ".venv-v2" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(ROOT / ".venv-v2" / "bin" / "python")

            marker = sandbox / "uv-called"
            fake_uv = sandbox / "fake-bin" / "uv"
            fake_uv.write_text(
                f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 0\n",
                encoding="utf-8",
            )
            fake_uv.chmod(0o755)

            digest = hashlib.sha256()
            for name, path in [
                ("uv.lock", uv_lock),
                ("pyproject.toml", pyproject),
            ]:
                digest.update(f"{name}\n".encode("utf-8"))
                digest.update(
                    f"{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode("utf-8")
                )
            (sandbox / ".agents" / ".venv-v2.lockhash").write_text(
                f"{digest.hexdigest()}\n",
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["HARRY_BOOTSTRAP_PREPARE_ONLY"] = "1"
            environment["PATH"] = f"{sandbox / 'fake-bin'}:{environment['PATH']}"
            first = subprocess.run(
                ["bash", "scripts/bootstrap_macos.sh"],
                cwd=sandbox,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertFalse(marker.exists())
            self.assertIn("跳过 uv sync", first.stdout)

            pyproject.write_text(
                "[project]\nname = \"fingerprint-test\"\nversion = \"2\"\n",
                encoding="utf-8",
            )
            second = subprocess.run(
                ["bash", "scripts/bootstrap_macos.sh"],
                cwd=sandbox,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertTrue(marker.exists())
            updated_hash = (
                sandbox / ".agents" / ".venv-v2.lockhash"
            ).read_text(encoding="utf-8").strip()
            self.assertNotEqual(updated_hash, digest.hexdigest())

    def test_runtime_state_reuses_only_verified_current_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            (sandbox / "scripts").mkdir()
            (sandbox / ".agents").mkdir()
            (sandbox / "config").mkdir()
            (sandbox / "frontend" / "dist").mkdir(parents=True)
            (sandbox / "frontend" / "dist" / "index.html").write_text(
                "<html>fingerprint</html>",
                encoding="utf-8",
            )
            (sandbox / "app").mkdir()
            (sandbox / "app" / "main.py").write_text("", encoding="utf-8")
            shutil.copyfile(
                ROOT / "scripts" / "bootstrap_macos.sh",
                sandbox / "scripts" / "bootstrap_macos.sh",
            )
            shutil.copyfile(ROOT / "requirements.lock", sandbox / "requirements.lock")
            (sandbox / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\n",
                encoding="utf-8",
            )
            venv_python = sandbox / ".venv-v2" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(ROOT / ".venv-v2" / "bin" / "python")

            fingerprint = frontend_fingerprint(sandbox / "frontend" / "dist")
            backend_pid = os.getpid()

            class HealthHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    if not self.path.startswith("/api/health?fast=1"):
                        self.send_error(404)
                        return
                    body = json.dumps(
                        {
                            "service": "harry-translate-v2",
                            "pid": backend_pid,
                            "project_root": str(sandbox.resolve()),
                            "frontend_fingerprint": fingerprint,
                            "fast": True,
                            "port": self.server.server_address[1],
                        }
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, _format: str, *_args: Any) -> None:
                    return

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            backend_port = int(server.server_address[1])
            state = {
                "service": "harry-translate-v2",
                "pid": backend_pid,
                "port": backend_port,
                "project_root": str(sandbox.resolve()),
                "frontend_fingerprint": fingerprint,
                "overlay_control_url": None,
            }
            (sandbox / ".agents" / "runtime_state.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )
            (sandbox / ".agents" / "runtime_port").write_text(
                f"{backend_port}\n",
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["HARRY_BOOTSTRAP_SKIP_SYNC"] = "1"
            process = subprocess.Popen(
                ["bash", "scripts/bootstrap_macos.sh", "--no-open-settings"],
                cwd=sandbox,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert process.stdout is not None
                deadline = time.monotonic() + 5
                output = ""
                while time.monotonic() < deadline:
                    readable, _, _ = select.select(
                        [process.stdout.fileno()],
                        [],
                        [],
                        max(0.0, deadline - time.monotonic()),
                    )
                    if not readable:
                        break
                    line = process.stdout.readline()
                    if not line:
                        break
                    output += line
                    if "复用已确认的最新版 V2 后端" in output:
                        break
                if "复用已确认的最新版 V2 后端" not in output:
                    diagnostics = ""
                    if process.poll() is not None and process.stderr is not None:
                        diagnostics = process.stderr.read()
                    self.fail(
                        "bootstrap did not reuse verified runtime state\n"
                        f"stdout:\n{output}\nstderr:\n{diagnostics}"
                    )
                self.assertTrue((sandbox / ".agents" / "runtime_state.json").is_file())
                self.assertEqual(
                    (sandbox / ".agents" / "runtime_port").read_text(encoding="utf-8"),
                    f"{backend_port}\n",
                )
                self.assertIsNone(process.poll())
                lock_deadline = time.monotonic() + 1
                while (
                    (sandbox / ".agents" / "launch.lock").exists()
                    and time.monotonic() < lock_deadline
                ):
                    time.sleep(0.02)
                self.assertFalse((sandbox / ".agents" / "launch.lock").exists())

                second_process = subprocess.Popen(
                    ["bash", "scripts/bootstrap_macos.sh", "--no-open-settings"],
                    cwd=sandbox,
                    env=environment,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    assert second_process.stdout is not None
                    second_deadline = time.monotonic() + 5
                    second_output = ""
                    while time.monotonic() < second_deadline:
                        readable, _, _ = select.select(
                            [second_process.stdout.fileno()],
                            [],
                            [],
                            max(0.0, second_deadline - time.monotonic()),
                        )
                        if not readable:
                            break
                        line = second_process.stdout.readline()
                        if not line:
                            break
                        second_output += line
                        if "复用已确认的最新版 V2 后端" in second_output:
                            break
                    self.assertIn("复用已确认的最新版 V2 后端", second_output)
                    self.assertIsNone(second_process.poll())
                finally:
                    second_process.terminate()
                    try:
                        second_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        second_process.kill()
                        second_process.wait(timeout=3)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=3)

    def test_cargo_fingerprint_skips_desktop_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            for relative in [
                "scripts",
                ".agents",
                "fake-bin",
                "frontend/dist",
                "src-tauri/src",
                "src-tauri/capabilities",
                "src-tauri/target/debug",
            ]:
                (sandbox / relative).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(
                ROOT / "scripts" / "bootstrap_macos.sh",
                sandbox / "scripts" / "bootstrap_macos.sh",
            )
            shutil.copyfile(ROOT / "requirements.lock", sandbox / "requirements.lock")
            (sandbox / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\n",
                encoding="utf-8",
            )
            (sandbox / "app").mkdir()
            (sandbox / "app" / "main.py").write_text("", encoding="utf-8")
            fp_inputs = [
                sandbox / "frontend" / "dist" / "index.html",
                sandbox / "src-tauri" / "src" / "lib.rs",
                sandbox / "src-tauri" / "build.rs",
                sandbox / "src-tauri" / "Cargo.toml",
                sandbox / "src-tauri" / "Cargo.lock",
                sandbox / "src-tauri" / "tauri.conf.json",
                sandbox / "src-tauri" / "capabilities" / "default.json",
            ]
            for path in fp_inputs:
                path.write_text(f"fixture:{path.name}\n", encoding="utf-8")

            venv_python = sandbox / ".venv-v2" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(ROOT / ".venv-v2" / "bin" / "python")
            tauri_bin = sandbox / "src-tauri" / "target" / "debug" / "harry-translate-mac"
            tauri_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            tauri_bin.chmod(0o755)
            marker = sandbox / "cargo-called"
            fake_cargo = sandbox / "fake-bin" / "cargo"
            fake_cargo.write_text(
                f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 0\n",
                encoding="utf-8",
            )
            fake_cargo.chmod(0o755)

            hasher = hashlib.sha256()
            fingerprint_paths = [
                sandbox / "src-tauri" / "src",
                sandbox / "src-tauri" / "build.rs",
                sandbox / "src-tauri" / "Cargo.toml",
                sandbox / "src-tauri" / "Cargo.lock",
                sandbox / "src-tauri" / "tauri.conf.json",
                sandbox / "src-tauri" / "capabilities",
                sandbox / "frontend" / "dist",
            ]
            for path in fingerprint_paths:
                files = (
                    sorted(
                        (
                            candidate
                            for candidate in path.rglob("*")
                            if candidate.is_file() and candidate.name != ".DS_Store"
                        ),
                        key=lambda candidate: candidate.as_posix(),
                    )
                    if path.is_dir()
                    else [path]
                )
                for file_path in files:
                    relative = file_path.relative_to(sandbox).as_posix()
                    hasher.update(f"FILE:{relative}\n".encode("utf-8"))
                    hasher.update(
                        f"{hashlib.sha256(file_path.read_bytes()).hexdigest()}\n".encode(
                            "utf-8"
                        )
                    )
            (sandbox / ".agents" / "cargo-build.fingerprint").write_text(
                f"{hasher.hexdigest()}\n",
                encoding="utf-8",
            )

            fingerprint = frontend_fingerprint(sandbox / "frontend" / "dist")
            backend_pid = os.getpid()

            class DesktopHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    if self.path.startswith("/api/health?fast=1"):
                        payload = {
                            "service": "harry-translate-v2",
                            "pid": backend_pid,
                            "project_root": str(sandbox.resolve()),
                            "frontend_fingerprint": fingerprint,
                            "fast": True,
                            "port": self.server.server_address[1],
                        }
                    elif self.path == "/api/overlay/status":
                        payload = {
                            "service": "harry-translate-v2",
                            "running": True,
                            "registered": True,
                            "visible": True,
                        }
                    else:
                        self.send_error(404)
                        return
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self) -> None:  # noqa: N802
                    if self.path != "/api/overlay/launch":
                        self.send_error(404)
                        return
                    body = b'{"ok":true,"action":"shown"}'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, _format: str, *_args: Any) -> None:
                    return

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DesktopHandler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            backend_port = int(server.server_address[1])
            runtime_state = {
                "service": "harry-translate-v2",
                "pid": backend_pid,
                "port": backend_port,
                "project_root": str(sandbox.resolve()),
                "frontend_fingerprint": fingerprint,
                "overlay_control_url": "http://127.0.0.1:43210",
            }
            (sandbox / ".agents" / "runtime_state.json").write_text(
                json.dumps(runtime_state),
                encoding="utf-8",
            )
            (sandbox / ".agents" / "runtime_port").write_text(
                f"{backend_port}\n",
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["HARRY_BOOTSTRAP_SKIP_SYNC"] = "1"
            environment["PATH"] = f"{sandbox / 'fake-bin'}:{environment['PATH']}"
            process = subprocess.Popen(
                ["bash", "scripts/bootstrap_macos.sh", "--desktop"],
                cwd=sandbox,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert process.stdout is not None
                deadline = time.monotonic() + 5
                output = ""
                while time.monotonic() < deadline:
                    readable, _, _ = select.select(
                        [process.stdout.fileno()],
                        [],
                        [],
                        max(0.0, deadline - time.monotonic()),
                    )
                    if not readable:
                        break
                    line = process.stdout.readline()
                    if not line:
                        break
                    output += line
                    if "检测到已注册的唯一悬浮窗" in output:
                        break
                self.assertIn("Rust/前端构建指纹未变化，跳过 cargo build", output)
                self.assertIn("检测到已注册的唯一悬浮窗", output)
                self.assertFalse(marker.exists())
                self.assertIsNone(process.poll())
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=3)

    def test_desktop_waits_for_tauri_but_not_reused_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            for relative in [
                "scripts",
                ".agents",
                "fake-bin",
                "frontend/dist",
                "src-tauri/target/debug",
            ]:
                (sandbox / relative).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(
                ROOT / "scripts" / "bootstrap_macos.sh",
                sandbox / "scripts" / "bootstrap_macos.sh",
            )
            shutil.copyfile(ROOT / "requirements.lock", sandbox / "requirements.lock")
            (sandbox / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\n",
                encoding="utf-8",
            )
            (sandbox / "app").mkdir()
            (sandbox / "app" / "main.py").write_text("", encoding="utf-8")
            (sandbox / "frontend" / "dist" / "index.html").write_text(
                "<html>desktop wait test</html>",
                encoding="utf-8",
            )
            venv_python = sandbox / ".venv-v2" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.symlink_to(ROOT / ".venv-v2" / "bin" / "python")

            started = sandbox / "tauri-started"
            release = sandbox / "tauri-release"
            tauri_bin = sandbox / "src-tauri" / "target" / "debug" / "harry-translate-mac"
            tauri_bin.write_text(
                "#!/bin/sh\n"
                f"touch {shlex.quote(str(started))}\n"
                f"while [[ ! -f {shlex.quote(str(release))} ]]; do sleep 0.05; done\n"
                "exit 0\n",
                encoding="utf-8",
            )
            tauri_bin.chmod(0o755)
            fake_cargo = sandbox / "fake-bin" / "cargo"
            fake_cargo.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_cargo.chmod(0o755)

            fingerprint = frontend_fingerprint(sandbox / "frontend" / "dist")
            backend_pid = os.getpid()

            class DesktopHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    if self.path.startswith("/api/health?fast=1"):
                        payload = {
                            "service": "harry-translate-v2",
                            "pid": backend_pid,
                            "project_root": str(sandbox.resolve()),
                            "frontend_fingerprint": fingerprint,
                            "fast": True,
                            "port": self.server.server_address[1],
                        }
                    elif self.path == "/api/overlay/status":
                        registered = started.exists()
                        payload = {
                            "service": "harry-translate-v2",
                            "running": registered,
                            "registered": registered,
                            "visible": registered,
                        }
                    else:
                        self.send_error(404)
                        return
                    body = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, _format: str, *_args: Any) -> None:
                    return

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DesktopHandler)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            backend_port = int(server.server_address[1])
            state = {
                "service": "harry-translate-v2",
                "pid": backend_pid,
                "port": backend_port,
                "project_root": str(sandbox.resolve()),
                "frontend_fingerprint": fingerprint,
                "overlay_control_url": None,
            }
            (sandbox / ".agents" / "runtime_state.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )
            (sandbox / ".agents" / "runtime_port").write_text(
                f"{backend_port}\n",
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["HARRY_BOOTSTRAP_SKIP_SYNC"] = "1"
            environment["PATH"] = f"{sandbox / 'fake-bin'}:{environment['PATH']}"
            process = subprocess.Popen(
                ["bash", "scripts/bootstrap_macos.sh", "--desktop"],
                cwd=sandbox,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not started.exists():
                    time.sleep(0.02)
                self.assertTrue(started.exists(), "fake Tauri did not start")
                self.assertIsNone(process.poll(), "launcher exited before Tauri")

                release.touch()
                try:
                    return_code = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.fail("desktop launcher kept waiting on the reused backend")
                self.assertEqual(return_code, 0)
                self.assertTrue((sandbox / ".agents" / "runtime_state.json").is_file())
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
