from __future__ import annotations

import asyncio
import json
import os
import select
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from websocket import create_connection

from app.config_store import ConfigStore, KeyValidator
from app.main import PortBindingError, bind_server_socket, create_app

ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = "sk-your-dashscope-api-key-here"
VALID_KEY = "sk-test1234567890abcd"


@dataclass
class AsgiResponse:
    status: int
    body: bytes


async def asgi_request(
    application: Any,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> AsgiResponse:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    headers = [(b"host", b"testserver")]
    if payload is not None:
        headers.append((b"content-type", b"application/json"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 50000),
        "state": {},
    }
    sent_request = False
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if sent_request:
            return {"type": "http.disconnect"}
        sent_request = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await application(scope, receive, send)
    status = next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return AsgiResponse(status=status, body=response_body)


class ConfigStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment_patch = patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""})
        self.environment_patch.start()
        (self.root / "config").mkdir()
        (self.root / ".env.example").write_text(
            f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.environment_patch.stop()
        self.temporary.cleanup()

    def test_missing_env_is_created_with_owner_only_permissions(self) -> None:
        store = ConfigStore(self.root)
        self.assertTrue(store.env_path.is_file())
        self.assertEqual(stat.S_IMODE(store.env_path.stat().st_mode), 0o600)
        self.assertTrue(store.needs_setup)
        self.assertEqual(store.key_mask, "")

    def test_existing_env_is_never_overwritten(self) -> None:
        env_path = self.root / ".env"
        original = "DASHSCOPE_API_KEY=sk-existing987654321\nWORKSPACE_ID=space-1\n"
        env_path.write_text(original, encoding="utf-8")
        ConfigStore(self.root)
        self.assertEqual(env_path.read_text(encoding="utf-8"), original)
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    def test_known_placeholders_and_control_characters_are_rejected(self) -> None:
        for value in [
            "",
            "   ",
            PLACEHOLDER,
            "在此填入你的Key",
            "your_api_key",
            "sk-xxx",
            "changeme",
            "short",
            "sk-valid\nwith-newline",
            "sk-中文key123",
        ]:
            with self.subTest(value=value):
                valid, reason = KeyValidator.is_valid_api_key(value)
                self.assertFalse(valid)
                self.assertTrue(reason)

    def test_valid_key_is_masked_and_redacted_from_responses(self) -> None:
        store = ConfigStore(self.root)
        self.assertEqual(store.set_api_key(VALID_KEY), "sk-****abcd")

        application = create_app(self.root)
        config_response = asyncio.run(asgi_request(application, "/api/config"))
        self.assertEqual(config_response.status, 200)
        self.assertNotIn(VALID_KEY.encode("utf-8"), config_response.body)
        config_payload = json.loads(config_response.body)
        self.assertEqual(config_payload["key_mask"], "sk-****abcd")
        self.assertFalse(config_payload["needs_setup"])

    def test_secret_api_rejects_placeholders_and_accepts_valid_key(self) -> None:
        application = create_app(self.root)
        rejected = asyncio.run(
            asgi_request(
                application,
                "/api/config/secrets",
                method="POST",
                payload={"api_key": PLACEHOLDER},
            )
        )
        self.assertEqual(rejected.status, 400)
        self.assertNotIn(PLACEHOLDER.encode("utf-8"), rejected.body)

        accepted = asyncio.run(
            asgi_request(
                application,
                "/api/config/secrets",
                method="POST",
                payload={"api_key": VALID_KEY},
            )
        )
        self.assertEqual(accepted.status, 200)
        self.assertEqual(json.loads(accepted.body)["key_mask"], "sk-****abcd")
        self.assertNotIn(VALID_KEY.encode("utf-8"), accepted.body)
        self.assertEqual(stat.S_IMODE((self.root / ".env").stat().st_mode), 0o600)

    def test_settings_persist_and_reject_secret_fields(self) -> None:
        store = ConfigStore(self.root)
        updated = store.update_settings({"target_lang": "fr"})
        self.assertEqual(updated["target_lang"], "fr")
        self.assertFalse(store.load_settings()["summary_enabled"])
        self.assertEqual(ConfigStore(self.root).load_settings()["target_lang"], "fr")

        application = create_app(self.root)
        update_response = asyncio.run(
            asgi_request(
                application,
                "/api/config",
                method="PUT",
                payload={"target_lang": "en"},
            )
        )
        self.assertEqual(update_response.status, 200)
        self.assertEqual(ConfigStore(self.root).load_settings()["target_lang"], "en")

        secret_response = asyncio.run(
            asgi_request(
                application,
                "/api/config",
                method="PUT",
                payload={"api_key": VALID_KEY},
            )
        )
        self.assertEqual(secret_response.status, 400)
        settings_text = (self.root / "config" / "user_settings.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(VALID_KEY, settings_text)

    def test_preexisting_sensitive_setting_is_never_returned(self) -> None:
        store = ConfigStore(self.root)
        store.settings_path.write_text(
            json.dumps({"target_lang": "fr", "api_key": "sk-preloaded-secret-1234"}),
            encoding="utf-8",
        )
        application = create_app(self.root)
        response = asyncio.run(asgi_request(application, "/api/config"))
        self.assertEqual(response.status, 200)
        self.assertNotIn(b"sk-preloaded-secret-1234", response.body)
        payload = json.loads(response.body)
        self.assertEqual(payload["settings"]["target_lang"], "fr")

    def test_new_env_key_wins_over_stale_process_environment(self) -> None:
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": PLACEHOLDER}):
            store = ConfigStore(self.root)
            self.assertEqual(store.set_api_key(VALID_KEY), "sk-****abcd")
            self.assertEqual(store.key_mask, "sk-****abcd")
            self.assertFalse(store.needs_setup)

            application = create_app(self.root)
            response = asyncio.run(asgi_request(application, "/api/config"))
        self.assertEqual(json.loads(response.body)["key_mask"], "sk-****abcd")

    def test_health_reports_setup_without_exposing_placeholder(self) -> None:
        application = create_app(self.root)
        application.state.port = 8123
        response = asyncio.run(asgi_request(application, "/api/health"))
        payload = json.loads(response.body)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["port"], 8123)
        self.assertTrue(payload["needs_setup"])
        self.assertEqual(payload["key_mask"], "")
        self.assertNotIn(PLACEHOLDER.encode("utf-8"), response.body)


class DynamicPortTests(unittest.TestCase):
    def test_occupied_port_scans_to_next_available_socket(self) -> None:
        guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        guard.bind(("127.0.0.1", 0))
        guard.listen(1)
        occupied_port = guard.getsockname()[1]
        candidate = None
        try:
            candidate = bind_server_socket(
                host="127.0.0.1",
                preferred_port=occupied_port,
                max_scan=10,
            )
            self.assertNotEqual(candidate.getsockname()[1], occupied_port)
        finally:
            guard.close()
            if candidate is not None:
                candidate.close()

    def test_single_port_scan_exhaustion_returns_structured_error(self) -> None:
        guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        guard.bind(("127.0.0.1", 0))
        guard.listen(1)
        occupied_port = guard.getsockname()[1]
        try:
            with self.assertRaises(PortBindingError):
                bind_server_socket(
                    host="127.0.0.1",
                    preferred_port=occupied_port,
                    max_scan=1,
                )
        finally:
            guard.close()


class SidecarProcessTests(unittest.TestCase):
    def test_port_zero_handshake_health_pages_and_cleanup(self) -> None:
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
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                assert process.stdout is not None
                ready: dict[str, Any] | None = None
                deadline = time.monotonic() + 12
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
                        ready = candidate
                        break
                self.assertIsNotNone(ready, process.stderr.read() if process.poll() else "")
                assert ready is not None
                self.assertGreater(ready["port"], 0)
                self.assertTrue(runtime_file.is_file())
                self.assertEqual(runtime_file.read_text(encoding="utf-8").strip(), str(ready["port"]))

                with urllib.request.urlopen(
                    f"http://127.0.0.1:{ready['port']}/api/health",
                    timeout=3,
                ) as response:
                    health = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertTrue(health["needs_setup"])
                self.assertNotIn(PLACEHOLDER, json.dumps(health))

                for route in ("/", "/en", "/zh", "/split"):
                    with self.subTest(route=route):
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{ready['port']}{route}",
                            timeout=3,
                        ) as page:
                            self.assertEqual(page.status, 200)
                            self.assertGreater(len(page.read(128)), 0)

                websocket = create_connection(
                    f"ws://127.0.0.1:{ready['port']}/ws/en",
                    timeout=3,
                )
                try:
                    initial_state = json.loads(websocket.recv())
                    self.assertEqual(initial_state["type"], "engine_state")
                    self.assertEqual(initial_state["state"], "idle")
                    self.assertEqual(initial_state["reason"], "needs_setup")
                    websocket.send(json.dumps({"type": "ping"}))
                    self.assertEqual(json.loads(websocket.recv())["type"], "pong")
                finally:
                    websocket.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            self.assertFalse(runtime_file.exists())


class BootstrapScriptTests(unittest.TestCase):
    def test_mac_only_bootstrap_sources_are_safe_and_parse(self) -> None:
        launcher = ROOT / "启动同传系统.command"
        bootstrap = ROOT / "scripts" / "bootstrap_macos.sh"
        self.assertTrue(launcher.is_file())
        self.assertTrue(bootstrap.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        combined = launcher.read_text(encoding="utf-8") + bootstrap.read_text(encoding="utf-8")
        for forbidden in ("/Users/", "brew install portaudio", "wmic", "PyAudio==0.2.14"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)
        self.assertIn(".venv-v2", combined)
        result = subprocess.run(
            ["bash", "-n", str(launcher), str(bootstrap)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_prepare_only_initializes_env_without_overwriting(self) -> None:
        bootstrap_source = ROOT / "scripts" / "bootstrap_macos.sh"
        self.assertTrue(bootstrap_source.is_file())
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            (sandbox / "scripts").mkdir()
            shutil.copyfile(bootstrap_source, sandbox / "scripts" / "bootstrap_macos.sh")
            shutil.copyfile(ROOT / "requirements.lock", sandbox / "requirements.lock")
            (sandbox / ".env.example").write_text(
                f"DASHSCOPE_API_KEY={PLACEHOLDER}\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["HARRY_BOOTSTRAP_PREPARE_ONLY"] = "1"
            environment["HARRY_BOOTSTRAP_SKIP_SYNC"] = "1"
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
            env_path = sandbox / ".env"
            self.assertTrue(env_path.is_file())
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

            sentinel = "DASHSCOPE_API_KEY=sk-existing987654321\n"
            env_path.write_text(sentinel, encoding="utf-8")
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
            self.assertEqual(env_path.read_text(encoding="utf-8"), sentinel)


if __name__ == "__main__":
    unittest.main()
