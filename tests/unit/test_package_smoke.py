from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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


if __name__ == "__main__":
    unittest.main()
