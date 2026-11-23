from __future__ import annotations

import json
import os
import plistlib
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_TAURI = ROOT / "src-tauri"


class TauriManifestContractTests(unittest.TestCase):
    def test_rust_toolchain_and_cargo_manifest_are_locked(self) -> None:
        toolchain = (ROOT / "rust-toolchain.toml").read_text(encoding="utf-8")
        self.assertIn("1.90", toolchain)
        self.assertIn("aarch64-apple-darwin", toolchain)

        cargo_toml = (SRC_TAURI / "Cargo.toml").read_text(encoding="utf-8")
        self.assertIn('rust-version = "1.90"', cargo_toml)
        self.assertIn('tauri = { version = "2"', cargo_toml)
        self.assertIn("macos-private-api", cargo_toml)
        self.assertNotIn("windows-sys", cargo_toml)
        self.assertNotIn("AGPL", cargo_toml)
        self.assertTrue((SRC_TAURI / "Cargo.lock").is_file())
        self.assertTrue((SRC_TAURI / "build.rs").is_file())

    def test_tauri_configs_enforce_single_overlay_window_and_mac_only_bundle(self) -> None:
        conf = json.loads((SRC_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
        self.assertTrue(conf["app"]["macOSPrivateApi"])
        windows = conf["app"]["windows"]
        self.assertEqual(len(windows), 1)
        overlay = windows[0]
        self.assertEqual(overlay["label"], "overlay")
        self.assertEqual(overlay["url"], "/hud")
        self.assertEqual(overlay["width"], 580)
        self.assertEqual(overlay["height"], 240)
        self.assertTrue(overlay["transparent"])
        self.assertTrue(overlay["alwaysOnTop"])
        self.assertTrue(overlay["acceptFirstMouse"])
        self.assertEqual(conf["bundle"]["macOS"]["minimumSystemVersion"], "13.0")
        self.assertNotIn("updater", json.dumps(conf).lower())

        mac_conf = json.loads(
            (SRC_TAURI / "tauri.macos.conf.json").read_text(encoding="utf-8")
        )
        self.assertIn("binaries/harry-sidecar", mac_conf["bundle"]["externalBin"])
        self.assertFalse((SRC_TAURI / "tauri.windows.conf.json").exists())

    def test_info_plist_entitlements_and_least_privilege_capabilities(self) -> None:
        with (SRC_TAURI / "Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        for key in (
            "NSScreenCaptureUsageDescription",
            "NSAudioCaptureUsageDescription",
            "NSMicrophoneUsageDescription",
        ):
            with self.subTest(plist_key=key):
                self.assertIn(key, info)
                self.assertTrue(str(info[key]).strip())

        with (SRC_TAURI / "Entitlements.plist").open("rb") as handle:
            entitlements = plistlib.load(handle)
        self.assertIs(entitlements["com.apple.security.device.audio-input"], True)

        cap_dir = SRC_TAURI / "capabilities"
        for name in ("default.json", "overlay.json", "settings.json"):
            cap = json.loads((cap_dir / name).read_text(encoding="utf-8"))
            self.assertNotIn("*", cap["windows"])
        overlay_cap = json.loads((cap_dir / "overlay.json").read_text(encoding="utf-8"))
        self.assertEqual(overlay_cap["windows"], ["overlay"])
        self.assertIn("core:window:allow-set-ignore-cursor-events", overlay_cap["permissions"])

    def test_notices_and_packaging_script_compliance(self) -> None:
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("yuxino/Mimi", notices)
        self.assertIn("Copyright (c) 2026 yuxino", notices)
        self.assertIn("HiMeditator/auto-caption", notices)
        self.assertIn("Copyright (c) 2025 Chen Janai", notices)
        self.assertIn("MIT License", notices)
        self.assertNotIn("AGPL-3.0 source is copied", "")

        package_script = ROOT / "scripts" / "package-app.sh"
        self.assertTrue(package_script.is_file())
        self.assertTrue(os.access(package_script, os.X_OK))
        parsed = subprocess.run(
            ["bash", "-n", str(package_script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stderr)


if __name__ == "__main__":
    unittest.main()
