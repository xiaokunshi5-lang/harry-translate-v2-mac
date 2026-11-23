#!/usr/bin/env python3
"""Build and validate the public PyInstaller sidecar for R16."""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY_DIR = ROOT / "glossary_bank"
EXPECTED_PACKS = {"00_base_emergency.json", "01_curated_everyday.json"}
TARGET_TRIPLE = "aarch64-apple-darwin"
DEFAULT_OUTPUT = ROOT / "src-tauri" / "binaries" / f"harry-sidecar-{TARGET_TRIPLE}"


def glossary_count() -> int:
    total = 0
    for path in GLOSSARY_DIR.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"glossary pack must be an object: {path.name}")
        for key in ("asr_fixes", "terms_zh_to_en", "terms_en_to_zh"):
            value = payload.get(key, {})
            if isinstance(value, (dict, list)):
                total += len(value)
    return total


def check_static() -> list[str]:
    errors: list[str] = []
    if platform.system() != "Darwin":
        errors.append("build_sidecar requires macOS")
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        errors.append("build_sidecar requires Apple Silicon arm64")
    found = {path.name for path in GLOSSARY_DIR.glob("*.json")}
    if found != EXPECTED_PACKS:
        errors.append(f"unexpected glossary packs: {sorted(found)}")
    try:
        count = glossary_count()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(str(error))
        count = 999
    if count > 250:
        errors.append(f"glossary count exceeds 250: {count}")
    for required in (
        ROOT / "config" / "provider_catalog.json",
        ROOT / "src-tauri" / "tauri.macos.conf.json",
        ROOT / "src-tauri" / "Info.plist",
        ROOT / "src-tauri" / "Entitlements.plist",
        ROOT / "app" / "main.py",
    ):
        if not required.is_file():
            errors.append(f"missing packaging input: {required.relative_to(ROOT)}")
    config = json.loads(
        (ROOT / "src-tauri" / "tauri.macos.conf.json").read_text(encoding="utf-8")
    )
    if "binaries/harry-sidecar" not in config.get("bundle", {}).get("externalBin", []):
        errors.append("tauri externalBin does not declare binaries/harry-sidecar")
    if config.get("bundle", {}).get("macOS", {}).get("minimumSystemVersion") != "13.0":
        errors.append("tauri minimumSystemVersion must be 13.0")
    return errors


def pyinstaller_command(output: Path) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "harry-sidecar",
        "--paths",
        str(ROOT),
        "--collect-submodules",
        "sounddevice",
        "--add-data",
        f"{GLOSSARY_DIR}:glossary_bank",
        "--add-data",
        f"{ROOT / 'config' / 'provider_catalog.json'}:config",
        "--distpath",
        str(ROOT / ".tmp" / "r16-sidecar-dist"),
        "--workpath",
        str(ROOT / ".tmp" / "r16-sidecar-work"),
        "--specpath",
        str(ROOT / ".tmp" / "r16-sidecar-spec"),
    ]
    frontend_dist = ROOT / "frontend" / "dist"
    if frontend_dist.is_dir():
        command.extend(["--add-data", f"{frontend_dist}:frontend/dist"])
    command.append(str(ROOT / "app" / "main.py"))
    _ = output
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate inputs only")
    parser.add_argument("--dry-run", action="store_true", help="print build command")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    errors = check_static()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    count = glossary_count()
    print(f"sidecar target={TARGET_TRIPLE} glossary_terms={count}")
    if args.check:
        print("SIDECAR-CHECK PASS")
        return 0

    command = pyinstaller_command(args.output)
    if args.dry_run:
        print(" ".join(str(part) for part in command))
        return 0
    if importlib.util.find_spec("PyInstaller") is None:
        print(
            "ERROR: PyInstaller is not installed. Install the public build tool "
            "with `.venv-v2/bin/python -m pip install pyinstaller`.",
            file=sys.stderr,
        )
        return 2

    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        return completed.returncode
    built = ROOT / ".tmp" / "r16-sidecar-dist" / "harry-sidecar"
    if not built.is_file():
        print(f"ERROR: PyInstaller did not create {built}", file=sys.stderr)
        return 3
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, args.output)
    args.output.chmod(0o755)
    print(f"Sidecar written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
