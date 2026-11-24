from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_package_smoke import (  # noqa: E402
    classify_signature_details,
    inspect_signature,
)


class PackageContractTests(unittest.TestCase):
    def test_only_selected_glossary_packs_are_package_inputs(self) -> None:
        packs = sorted(path.name for path in (ROOT / "glossary_bank").glob("*.json"))
        self.assertEqual(
            packs,
            ["00_base_emergency.json", "01_curated_everyday.json"],
        )
        total = 0
        for path in (ROOT / "glossary_bank").glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for key in ("asr_fixes", "terms_zh_to_en", "terms_en_to_zh"):
                value = payload.get(key, {})
                if isinstance(value, (dict, list)):
                    total += len(value)
        self.assertLessEqual(total, 250, total)

    def test_macos_bundle_contract_is_present_and_portable(self) -> None:
        config = json.loads(
            (ROOT / "src-tauri" / "tauri.macos.conf.json").read_text(encoding="utf-8")
        )
        bundle = config["bundle"]
        self.assertIn("dmg", bundle["targets"])
        self.assertEqual(bundle["macOS"]["minimumSystemVersion"], "13.0")
        self.assertIn("binaries/harry-sidecar", bundle["externalBin"])
        self.assertTrue((ROOT / "src-tauri" / "Info.plist").is_file())
        self.assertTrue((ROOT / "src-tauri" / "Entitlements.plist").is_file())

    def test_build_and_verification_scripts_are_executable_and_checkable(self) -> None:
        for relative in (
            "scripts/package-app.sh",
            "scripts/build_sidecar.py",
            "scripts/verify_package_smoke.py",
            "scripts/macos/prepare-macos-release.sh",
        ):
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
        result = subprocess.run(
            [str(ROOT / "scripts" / "build_sidecar.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forbidden_runtime_artifacts_are_not_packaged(self) -> None:
        build_script = (ROOT / "scripts" / "build_sidecar.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("01/02/09", build_script)
        self.assertNotIn(".env", build_script)
        self.assertNotIn("projects", build_script)
        self.assertIn("provider_catalog.json", build_script)
        self.assertNotIn(f"{ROOT / 'config'}:config", build_script)
        package_script = (ROOT / "scripts" / "package-app.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("aarch64-apple-darwin", package_script)
        self.assertNotIn("--target x86_64", package_script)

    def test_signature_classifier_distinguishes_developer_id_adhoc_and_unsigned(self) -> None:
        self.assertEqual(
            classify_signature_details(
                "Identifier=com.harry.translate.mac\n"
                "Authority=Developer ID Application: Example (TEAMID)\n"
                "TeamIdentifier=TEAMID"
            ),
            "developer_id",
        )
        self.assertEqual(
            classify_signature_details(
                "Identifier=harry-sidecar\nSignature=adhoc\nTeamIdentifier=not set"
            ),
            "adhoc_local",
        )
        self.assertEqual(
            classify_signature_details(
                "code object is not signed at all\nIn subcomponent: /tmp/app"
            ),
            "unsigned_local",
        )

    def test_package_scripts_implement_dual_track_signing_without_secret_output(self) -> None:
        package_script = (ROOT / "scripts" / "package-app.sh").read_text(
            encoding="utf-8"
        )
        prepare_script = (
            ROOT / "scripts" / "macos" / "prepare-macos-release.sh"
        ).read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify_package_smoke.py").read_text(
            encoding="utf-8"
        )

        for needle in (
            "APPLE_SIGNING_IDENTITY",
            "APPLE_NOTARY_KEYCHAIN_PROFILE",
            "APPLE_API_KEY_PATH",
            "APPLE_API_KEY_ID",
            "APPLE_API_ISSUER",
            "--options runtime",
            "--timestamp",
            "--entitlements",
            "xcrun notarytool submit",
            "xcrun stapler staple",
            "spctl -a -vvv -t install",
            "developer_id_unnotarized",
            "adhoc_local",
        ):
            with self.subTest(script="package-app", needle=needle):
                self.assertIn(needle, package_script)

        self.assertIn("cleanup_package_artifacts", package_script)
        self.assertIn("SIDECAR_ANCHOR_BACKUP", package_script)
        self.assertIn("trap cleanup_package_artifacts EXIT", package_script)
        self.assertIn("RELEASE-SIGNING mode=adhoc_local", prepare_script)
        self.assertIn("./scripts/package-app.sh --check", prepare_script)
        self.assertIn('"signature_mode"', verifier)
        self.assertIn('"has_stapled_ticket"', verifier)
        self.assertIn('"notarized"', verifier)
        self.assertNotIn("print(f\"CERT", package_script)
        self.assertNotIn("echo \"$SIGNING_IDENTITY\"", package_script)

    def test_package_check_without_credentials_reports_adhoc_local(self) -> None:
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
            [str(ROOT / "scripts" / "package-app.sh"), "--check"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("projected signature mode: adhoc_local", result.stdout)
        self.assertIn("notarization mode: none", result.stdout)
        self.assertNotIn("Developer ID Application:", result.stdout)

    def test_package_check_accepts_both_api_key_variable_conventions(self) -> None:
        identity = "Developer ID Application: Package Test (TEAMID)"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            key_path = root / "AuthKey_TEST.p8"
            key_path.write_text("placeholder\n", encoding="utf-8")
            scripts = {
                "security": (
                    "#!/bin/sh\n"
                    f"printf '%s\\n' '1) ABCDEF123456 \"{identity}\"'\n"
                ),
                "codesign": "#!/bin/sh\nexit 0\n",
                "xcrun": "#!/bin/sh\nexit 0\n",
                "spctl": "#!/bin/sh\nexit 0\n",
            }
            for name, source in scripts.items():
                path = fake_bin / name
                path.write_text(source, encoding="utf-8")
                path.chmod(0o755)

            base_environment = os.environ.copy()
            base_environment["PATH"] = f"{fake_bin}:{base_environment['PATH']}"
            base_environment["APPLE_SIGNING_IDENTITY"] = identity
            base_environment["APPLE_API_ISSUER"] = "issuer-test"
            for key in (
                "APPLE_NOTARY_KEYCHAIN_PROFILE",
                "APPLE_API_KEY",
                "APPLE_API_KEY_PATH",
                "APPLE_API_KEY_ID",
            ):
                base_environment.pop(key, None)

            task_form = {
                **base_environment,
                "APPLE_API_KEY": str(key_path),
                "APPLE_API_KEY_ID": "KEYIDTEST",
            }
            task_result = subprocess.run(
                [str(ROOT / "scripts" / "package-app.sh"), "--check"],
                cwd=ROOT,
                env=task_form,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                task_result.returncode,
                0,
                task_result.stderr or task_result.stdout,
            )
            self.assertIn("developer_id_notarized", task_result.stdout)
            self.assertIn("notarization mode: api_key", task_result.stdout)
            self.assertNotIn(str(key_path), task_result.stdout + task_result.stderr)
            self.assertNotIn("KEYIDTEST", task_result.stdout + task_result.stderr)

            tauri_form = {
                **base_environment,
                "APPLE_API_KEY": "KEYIDTEST",
                "APPLE_API_KEY_PATH": str(key_path),
            }
            tauri_result = subprocess.run(
                [str(ROOT / "scripts" / "package-app.sh"), "--check"],
                cwd=ROOT,
                env=tauri_form,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                tauri_result.returncode,
                0,
                tauri_result.stderr or tauri_result.stdout,
            )
            self.assertIn("developer_id_notarized", tauri_result.stdout)
            self.assertIn("notarization mode: api_key", tauri_result.stdout)

            profile_form = {
                **base_environment,
                "APPLE_NOTARY_KEYCHAIN_PROFILE": "test-profile",
            }
            profile_result = subprocess.run(
                [str(ROOT / "scripts" / "package-app.sh"), "--check"],
                cwd=ROOT,
                env=profile_form,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                profile_result.returncode,
                0,
                profile_result.stderr or profile_result.stdout,
            )
            self.assertIn("notarization mode: keychain_profile", profile_result.stdout)
            self.assertNotIn(
                "test-profile",
                profile_result.stdout + profile_result.stderr,
            )

            incomplete_environment = {
                **base_environment,
                "APPLE_API_KEY_PATH": str(key_path),
            }
            incomplete_environment.pop("APPLE_API_ISSUER", None)
            incomplete_result = subprocess.run(
                [str(ROOT / "scripts" / "package-app.sh"), "--check"],
                cwd=ROOT,
                env=incomplete_environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(incomplete_result.returncode, 0)
            self.assertIn(
                "incomplete notarization credentials",
                incomplete_result.stderr,
            )

    def test_notarization_requires_both_app_and_dmg_evidence(self) -> None:
        details = "Authority=Developer ID Application: Example (TEAMID)"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "HARRY Translate.app"
            dmg = root / "HARRY Translate.dmg"
            app.mkdir()
            dmg.write_bytes(b"dmg")
            with (
                mock.patch(
                    "verify_package_smoke.command_output",
                    return_value=(0, details),
                ),
                mock.patch(
                    "verify_package_smoke.has_stapled_ticket",
                    side_effect=lambda target: target == app,
                ),
                mock.patch(
                    "verify_package_smoke.gatekeeper_assessment",
                    side_effect=[(True, True), (False, False)],
                ),
            ):
                partial = inspect_signature(app=app, dmg=dmg, binary=None)
            self.assertEqual(partial["signature_mode"], "developer_id_unnotarized")
            self.assertFalse(partial["notarized"])
            self.assertFalse(partial["has_stapled_ticket"])

            with (
                mock.patch(
                    "verify_package_smoke.command_output",
                    return_value=(0, details),
                ),
                mock.patch(
                    "verify_package_smoke.has_stapled_ticket",
                    return_value=True,
                ),
                mock.patch(
                    "verify_package_smoke.gatekeeper_assessment",
                    side_effect=[(True, True), (True, True)],
                ),
            ):
                complete = inspect_signature(app=app, dmg=dmg, binary=None)
            self.assertEqual(complete["signature_mode"], "developer_id_notarized")
            self.assertTrue(complete["notarized"])
            self.assertTrue(complete["has_stapled_ticket"])


if __name__ == "__main__":
    unittest.main()
