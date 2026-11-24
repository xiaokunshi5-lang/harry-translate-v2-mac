from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
PLACEHOLDER = "sk-your-dashscope-api-key-here"


@dataclass
class AsgiResponse:
    status: int
    headers: dict[str, str]
    body: bytes


async def asgi_request(
    application: Any,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    query_string: bytes = b"",
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
        "query_string": query_string,
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
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return AsgiResponse(
        status=int(start["status"]),
        headers=response_headers,
        body=response_body,
    )


class ReproR155ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "config").mkdir()
        (self.root / ".env.example").write_text(
            f"DASHSCOPE_API_KEY={PLACEHOLDER}\nWORKSPACE_ID=\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_fast_health_skips_coreaudio_and_exposes_instance_identity(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        application.state.port = 8123
        with patch(
            "app.main._health_audio",
            side_effect=AssertionError("fast health must not enumerate CoreAudio"),
        ):
            response = asyncio.run(
                asgi_request(
                    application,
                    "/api/health",
                    query_string=b"fast=1",
                )
            )

        self.assertEqual(response.status, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["fast"])
        self.assertEqual(payload["service"], "harry-translate-v2")
        self.assertEqual(payload["pid"], os.getpid())
        self.assertEqual(Path(payload["project_root"]), self.root.resolve())
        self.assertRegex(payload["frontend_fingerprint"], r"^[0-9a-f]{12,64}$")
        self.assertEqual(payload["audio_backend"], "skipped")
        self.assertEqual(payload["audio_devices"], [])
        self.assertIsNone(payload["audio_error"])

    def test_html_pages_are_never_browser_cached(self) -> None:
        dist = self.root / "frontend" / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<html></html>", encoding="utf-8")
        application = create_app(self.root, enable_audio_capture=False)

        response = asyncio.run(asgi_request(application, "/settings"))

        self.assertEqual(response.status, 200)
        cache_control = response.headers.get("cache-control", "")
        self.assertIn("no-store", cache_control)
        self.assertIn("max-age=0", cache_control)
        self.assertEqual(response.headers.get("pragma"), "no-cache")

    def test_overlay_control_api_exists_and_rejects_non_loopback_registration(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)

        status_response = asyncio.run(
            asgi_request(application, "/api/overlay/status")
        )
        rejected = asyncio.run(
            asgi_request(
                application,
                "/api/overlay/register",
                method="POST",
                payload={"control_url": "http://example.com:9000"},
            )
        )
        accepted = asyncio.run(
            asgi_request(
                application,
                "/api/overlay/register",
                method="POST",
                payload={"control_url": "http://127.0.0.1:43210"},
            )
        )

        self.assertEqual(status_response.status, 200)
        self.assertIn("running", json.loads(status_response.body))
        self.assertEqual(rejected.status, 400)
        self.assertEqual(accepted.status, 200)
        self.assertTrue(json.loads(accepted.body)["registered"])

    def test_config_socket_is_passive_and_does_not_join_subtitle_clients(self) -> None:
        application = create_app(self.root, enable_audio_capture=False)
        websocket_paths = {
            getattr(route, "path", "")
            for route in application.routes
            if route.__class__.__name__ == "APIWebSocketRoute"
        }
        self.assertIn("/ws/config", websocket_paths)

        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        config_route = source.split('@application.websocket("/ws/config")', 1)[1]
        config_route = config_route.split("@application.", 1)[0]
        self.assertNotIn("connect_client", config_route)
        self.assertNotIn("activate_channel", config_route)
        self.assertNotIn("route_audio_frame", config_route)

    def test_shutdown_uses_bounded_grace_and_tracks_active_sockets(self) -> None:
        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("active_sockets", source)
        self.assertIn("timeout_graceful_shutdown=1.5", source)
        self.assertIn("runtime_state.json", source)
        self.assertIn("shutdown_active_sockets", source)


class ReproR155FrontendContractTests(unittest.TestCase):
    def test_placeholder_copy_and_forced_wheel_snap_are_removed(self) -> None:
        timeline = (
            FRONTEND / "src" / "windows" / "overlay" / "timeline" / "Timeline.tsx"
        ).read_text(encoding="utf-8")
        monitor = (
            FRONTEND / "src" / "screens" / "MonitorScreens.tsx"
        ).read_text(encoding="utf-8")
        for placeholder in [
            "原文将在这里实时出现",
            "译文字幕等待音频输入",
            "译文字幕",
            "等待录屏翻译",
        ]:
            with self.subTest(placeholder=placeholder):
                self.assertNotIn(placeholder, timeline)
                self.assertNotIn(placeholder, monitor)
        self.assertNotIn("event.deltaY > 0", timeline)
        self.assertNotIn("returnToLiveNow();", timeline.split("onWheel", 1)[-1][:240])

    def test_history_is_not_hard_truncated_by_visible_count(self) -> None:
        model = (
            FRONTEND
            / "src"
            / "windows"
            / "overlay"
            / "timeline"
            / "overlayModel.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("MAX_TIMELINE_ENTRIES", model)
        self.assertNotIn("bounded.slice(-count)", model)

    def test_scrollbar_drag_and_empty_subtitle_guards_are_present(self) -> None:
        drag = (
            FRONTEND
            / "src"
            / "windows"
            / "overlay"
            / "interaction"
            / "overlayContentDrag.ts"
        ).read_text(encoding="utf-8")
        pipeline = (
            FRONTEND / "src" / "lib" / "subtitles" / "subtitlePipeline.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("return false", drag)
        self.assertNotIn(
            "Still allow dragging from subtitle text when user is not targeting a control.",
            drag,
        )
        self.assertIn("source.trim()", pipeline)
        self.assertIn("target.trim()", pipeline)

    def test_settings_use_one_authoritative_switch_and_passive_config_sync(
        self,
    ) -> None:
        settings = (
            FRONTEND / "src" / "screens" / "SettingsView.tsx"
        ).read_text(encoding="utf-8")
        controls = (
            FRONTEND / "src" / "components" / "settings" / "Controls.tsx"
        ).read_text(encoding="utf-8")
        api_client = (
            FRONTEND / "src" / "shared" / "apiClient.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("<Toggle", settings)
        self.assertIn('label="实时翻译"', settings)
        self.assertIn("checked={isRealtimeOn}", settings)
        self.assertIn("isOverlayRealtimeActive", settings)
        self.assertIn('event.type === "overlay_status"', settings)
        self.assertNotIn("启动实时翻译", settings)
        self.assertNotIn("隐藏暂停", settings)
        self.assertIn('role="switch"', controls)
        self.assertIn("aria-checked={checked}", controls)
        self.assertIn("disabled={blocked}", controls)
        self.assertIn("aria-busy={busy || undefined}", controls)
        self.assertIn("launchOverlay", settings)
        self.assertIn("hideOverlay", settings)
        self.assertIn("/ws/config", settings)
        self.assertNotIn('new ChannelSocket("main"', settings)
        for export in [
            "getOverlayStatus",
            "isOverlayRealtimeActive",
            "launchOverlay",
            "hideOverlay",
        ]:
            with self.subTest(export=export):
                self.assertIn(export, api_client)

    def test_all_settings_controls_use_low_latency_immediate_sync(self) -> None:
        store = (
            FRONTEND / "src" / "store" / "settingsStore.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("pendingPatch", store)
        self.assertIn("flush", store)
        self.assertIn("syncExternalSettings", store)
        for relative in [
            "src/components/settings/SubtitlePanel.tsx",
            "src/components/settings/AudioLanguagePanel.tsx",
            "src/components/settings/ProvidersPanel.tsx",
        ]:
            source = (FRONTEND / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                self.assertIn("onImmediate", source)
                self.assertNotIn("onUpdate", source)

    def test_overlay_config_updates_do_not_persist_back_to_python(self) -> None:
        overlay = (
            FRONTEND / "src" / "windows" / "overlay" / "OverlayWindow.tsx"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            overlay,
            r"invokeOverlaySyncPresentation\([\s\S]{0,500}?false,\s*\)",
        )
        self.assertIn("overlay-visibility-changed", overlay)
        self.assertIn("needsSetup", overlay)


class ReproR155BootstrapContractTests(unittest.TestCase):
    def test_bootstrap_has_launch_lock_reuse_and_fingerprint_skips(self) -> None:
        source = (ROOT / "scripts" / "bootstrap_macos.sh").read_text(
            encoding="utf-8"
        )
        for required in [
            "launch.lock",
            ".venv-v2.lockhash",
            "cargo-build.fingerprint",
            "HARRY_ATTACH_BACKEND_URL",
            "/api/health?fast=1",
            "/settings",
        ]:
            with self.subTest(required=required):
                self.assertIn(required, source)
        self.assertNotIn('rm -f "$RUNTIME_PORT_FILE"\n\n    if [[ ! -f', source)
        self.assertIn("legacy/v1/server.py", source)
        main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("timeout_graceful_shutdown", main_source)


if __name__ == "__main__":
    unittest.main()
