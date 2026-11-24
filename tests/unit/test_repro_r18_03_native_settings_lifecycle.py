from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = ROOT / "src-tauri"
FRONTEND = ROOT / "frontend"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def production_prefix(path: Path) -> str:
    return read_text(path).split("#[cfg(test)]", 1)[0]


class NativeSettingsLifecycleReproductionTests(unittest.TestCase):
    def assert_contains(self, source: str, needle: str, message: str) -> None:
        self.assertIn(needle, source, message)

    def assert_matches(self, source: str, pattern: str, message: str) -> None:
        self.assertIsNotNone(re.search(pattern, source, re.DOTALL), message)

    def test_dynamic_settings_window_is_regular_and_closes_by_hiding(self) -> None:
        settings_path = SRC_TAURI / "src" / "windows" / "settings.rs"
        self.assertTrue(settings_path.is_file(), "windows/settings.rs must exist")
        settings = production_prefix(settings_path)

        for needle in [
            'SETTINGS_WINDOW_LABEL: &str = "settings"',
            "WebviewWindowBuilder::new",
            'WebviewUrl::App("/settings".into())',
            "inner_size",
            "min_inner_size",
            "resizable(true)",
            "decorations(true)",
            "transparent(false)",
            "always_on_top(false)",
            "WindowEvent::CloseRequested",
            "api.prevent_close()",
            ".hide()",
            ".unminimize()",
            ".show()",
            ".set_focus()",
        ]:
            with self.subTest(needle=needle):
                self.assert_contains(settings, needle, f"missing settings contract: {needle}")

        self.assertNotIn("to_panel", settings)
        self.assertNotIn("NSPanel", settings)
        self.assertNotIn("/ws/main", settings)
        self.assertIn(
            "pub mod settings;",
            read_text(SRC_TAURI / "src" / "windows" / "mod.rs"),
        )

        manifest = json.loads(
            read_text(SRC_TAURI / "tauri.conf.json")
        )
        self.assertEqual(len(manifest["app"]["windows"]), 1)
        self.assertEqual(manifest["app"]["windows"][0]["label"], "overlay")

    def test_startup_secondary_launch_and_dock_reopen_route_to_settings(self) -> None:
        source = production_prefix(SRC_TAURI / "src" / "lib.rs")

        single_instance = source[
            source.index("tauri_plugin_single_instance::init")
            : source.index(".plugin(tauri_nspanel::init())")
        ]
        self.assert_contains(
            single_instance,
            "show_and_focus_settings_window",
            "secondary launch must restore the settings window",
        )
        self.assertNotIn(
            "show_primary_overlay_window",
            single_instance,
            "secondary launch must not force the hidden overlay visible",
        )
        self.assert_matches(
            source,
            r"RunEvent::Reopen\s*\{[^}]*\}\s*=>\s*\{[\s\S]{0,400}show_and_focus_settings_window",
            "Dock/Finder reopen must restore the settings window",
        )
        self.assert_matches(
            source,
            r"endpoint\.is_some\(\)\s*&&\s*should_open_settings\(\)[\s\S]{0,300}show_and_focus_settings_window",
            "cold startup must show the native settings window",
        )
        self.assertNotIn(
            "controls::open_settings_in_browser(app.handle())",
            source,
            "cold startup must not open the system browser",
        )
        self.assertIn("MENU_OPEN_SETTINGS_ID", source)
        self.assertIn("CmdOrCtrl+,", source)
        self.assertIn("CmdOrCtrl+Q", source)

    def test_controlled_exit_stops_translation_and_backend_before_exit(self) -> None:
        controls = production_prefix(SRC_TAURI / "src" / "windows" / "controls.rs")
        lib = production_prefix(SRC_TAURI / "src" / "lib.rs")
        sidecar_full = read_text(SRC_TAURI / "src" / "sidecar.rs")
        tray = production_prefix(SRC_TAURI / "src" / "windows" / "tray.rs")

        for needle in [
            "pub fn app_quit",
            "pub fn open_auxiliary_monitor_in_browser",
            "pub(crate) fn request_application_exit",
            "POST /api/overlay/hide",
            "shutdown_for_application_exit()",
            "app.exit(0)",
        ]:
            with self.subTest(needle=needle):
                self.assertIn(needle, controls)

        self.assertIn("windows::controls::app_quit", lib)
        self.assertIn(
            "windows::controls::open_auxiliary_monitor_in_browser",
            lib,
        )
        self.assert_matches(
            lib,
            r"RunEvent::ExitRequested\s*\{\s*code:\s*None[\s\S]{0,350}prevent_exit\(\)[\s\S]{0,250}request_application_exit",
            "normal user quit must be intercepted for graceful cleanup",
        )

        self.assertIn("pub fn shutdown_for_application_exit", sidecar_full)
        self.assertIn("fn request_overlay_hide", controls)
        self.assertIn("打开设置窗口", tray)
        self.assertIn("退出 HARRY Translate", tray)
        self.assertIn("request_application_exit(app.clone())", tray)
        self.assertNotIn("退出悬浮窗", tray)

    def test_native_settings_uses_external_auxiliary_and_visible_exit_action(self) -> None:
        settings = read_text(FRONTEND / "src" / "screens" / "SettingsView.tsx")
        bridge = read_text(FRONTEND / "src" / "lib" / "backendBridge.ts")

        for needle in [
            "isTauriRuntime",
            "open_auxiliary_monitor_in_browser",
            "app_quit",
            "settings-native-exit",
            "退出应用",
            "停止实时翻译并关闭本地服务",
        ]:
            with self.subTest(needle=needle):
                self.assertIn(needle, settings)

        self.assertNotIn(
            'nativeRuntime ? <a href="/"',
            settings,
            "native auxiliary action must not navigate the settings WebView",
        )
        self.assertIn("SIDECAR_READY_RETRY_ATTEMPTS = 40", bridge)
        self.assertIn("SIDECAR_READY_RETRY_DELAY_MS = 50", bridge)
        self.assertIn("sidecar_not_ready", bridge)
        self.assertIn("isSidecarNotReadyError", bridge)

        settings_tests = read_text(
            FRONTEND / "src" / "screens" / "SettingsView.test.tsx"
        )
        self.assertIn("open_auxiliary_monitor_in_browser", settings_tests)
        self.assertIn("app_quit", settings_tests)
        bridge_tests = FRONTEND / "src" / "lib" / "backendBridge.test.ts"
        self.assertTrue(bridge_tests.is_file())
        self.assertIn("retries sidecar_not_ready", read_text(bridge_tests))

    def test_settings_capability_is_least_privilege_and_desktop_launcher_opens_it(self) -> None:
        capability = json.loads(
            read_text(SRC_TAURI / "capabilities" / "settings.json")
        )
        self.assertEqual(capability["windows"], ["settings"])
        for permission in [
            "core:window:allow-show",
            "core:window:allow-set-focus",
            "core:window:allow-close",
            "core:window:allow-hide",
            "core:window:allow-unminimize",
            "opener:default",
        ]:
            with self.subTest(permission=permission):
                self.assertIn(permission, capability["permissions"])
        self.assertNotIn("*", capability["permissions"])

        bootstrap = read_text(ROOT / "scripts" / "bootstrap_macos.sh")
        launch_section = bootstrap[
            bootstrap.index("launch_or_show_overlay()")
            : bootstrap.index("start_desktop_overlay()")
        ]
        self.assertNotIn(
            "HARRY_NO_OPEN_SETTINGS=1",
            launch_section,
            "desktop launcher must let the host open its native settings window",
        )
        self.assertNotIn("/api/overlay/launch", launch_section)


if __name__ == "__main__":
    unittest.main()
