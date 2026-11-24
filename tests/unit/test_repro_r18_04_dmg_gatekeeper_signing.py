from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DmgGatekeeperSigningReproductionTests(unittest.TestCase):
    def test_package_script_has_real_developer_id_and_notarization_track(self) -> None:
        source = read_text("scripts/package-app.sh")
        for needle in (
            "APPLE_SIGNING_IDENTITY",
            "APPLE_NOTARY_KEYCHAIN_PROFILE",
            "APPLE_API_KEY_PATH",
            "APPLE_API_KEY_ID",
            "APPLE_API_ISSUER",
            "--options runtime",
            "--timestamp",
            "--entitlements",
            'export APPLE_API_KEY_PATH="$API_KEY_PATH"',
            'export APPLE_API_KEY="$API_KEY_ID"',
            'export APPLE_API_ISSUER="$API_ISSUER"',
            "xcrun notarytool submit",
            "--output-format json",
            'NOTARY_STATUS" != "Accepted',
            '2>"$NOTARY_ERROR"',
            'xcrun stapler staple "$APP_PATH"',
            'xcrun stapler staple "$DMG_PATH"',
            'xcrun stapler validate "$DMG_PATH"',
            'spctl -a -vvv -t install "$APP_PATH"',
            'PROJECTED_SIGNATURE_MODE="adhoc_local"',
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, source)

        self.assertNotIn("echo \"$SIGNING_IDENTITY\"", source)
        self.assertNotIn("--master-disable", source)
        self.assertIn("codesign --verify --strict --verbose=2", source)
        self.assertIn("codesign --verify --deep --strict --verbose=2", source)
        self.assertIn(
            'codesign --verify --strict --verbose=2 "$APP_SIDECAR"',
            source,
        )

    def test_release_preparation_reports_adhoc_mode_without_blocking(self) -> None:
        environment = os.environ.copy()
        for key in (
            "APPLE_SIGNING_IDENTITY",
            "APPLE_NOTARY_KEYCHAIN_PROFILE",
            "APPLE_API_KEY",
            "APPLE_API_KEY_PATH",
            "APPLE_API_KEY_ID",
            "APPLE_API_ISSUER",
        ):
            environment.pop(key, None)
        result = subprocess.run(
            [str(ROOT / "scripts" / "macos" / "prepare-macos-release.sh"), "--check"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("RELEASE-SIGNING mode=adhoc_local", result.stdout)
        self.assertIn("projected signature mode: adhoc_local", result.stdout)
        self.assertIn("notarization mode: none", result.stdout)
        self.assertIn("MACOS-RELEASE-CHECK PASS", result.stdout)

    def test_verifier_reports_secret_free_signature_truth(self) -> None:
        result = subprocess.run(
            [
                str(ROOT / ".venv-v2" / "bin" / "python"),
                str(ROOT / "scripts" / "verify_package_smoke.py"),
                "--check",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        signing_line = next(
            line
            for line in result.stdout.splitlines()
            if line.startswith("PACKAGE-SIGNING ")
        )
        payload = json.loads(signing_line.removeprefix("PACKAGE-SIGNING "))
        self.assertIn(
            payload["signature_mode"],
            {
                "adhoc_local",
                "developer_id_notarized",
                "developer_id_unnotarized",
                "unsigned_local",
                "unknown",
                "unavailable",
            },
        )
        for key in (
            "notarized",
            "has_stapled_ticket",
            "app_stapled",
            "dmg_stapled",
            "app_gatekeeper_accepted",
            "dmg_gatekeeper_accepted",
        ):
            with self.subTest(key=key):
                self.assertIn(key, payload)
                self.assertIsInstance(payload[key], bool)

    def test_download_guidance_verifies_bytes_before_single_app_quarantine_removal(self) -> None:
        documents = "\n".join(
            read_text(relative)
            for relative in ("README.md", "INSTALL.md", "TROUBLESHOOTING.md")
        )
        for needle in (
            "已损坏",
            "shasum -a 256",
            "hdiutil verify",
            'xattr -dr com.apple.quarantine "/Applications/HARRY Translate.app"',
            "不要关闭系统全局 Gatekeeper",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, documents)
        self.assertNotIn("spctl --master-disable", documents)

    def test_no_certificate_or_private_key_artifacts_are_tracked(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout.split(b"\0")
        forbidden_suffixes = (b".p12", b".p8", b".cer", b".mobileprovision")
        findings = [
            path.decode("utf-8", errors="replace")
            for path in tracked
            if path and path.lower().endswith(forbidden_suffixes)
        ]
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
