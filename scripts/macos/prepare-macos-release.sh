#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

CHECK_ONLY=0
if [[ "${1:-}" == "--check" || "${1:-}" == "--dry-run" ]]; then
  CHECK_ONLY=1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: macOS release preparation is Darwin-only." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "ERROR: Apple Silicon arm64 is required." >&2
  exit 1
fi

for required in \
  src-tauri/Info.plist \
  src-tauri/Entitlements.plist \
  src-tauri/tauri.macos.conf.json \
  frontend/package.json \
  scripts/build_sidecar.py; do
  [[ -f "$required" ]] || {
    echo "ERROR: missing release input: $required" >&2
    exit 1
  }
done

python_bin="${HARRY_PYTHON:-}"
if [[ -z "$python_bin" ]]; then
  for candidate in \
    "$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/.venv-v2/bin/python" \
    "python3"; do
    if [[ "$candidate" == "python3" ]] || [[ -x "$candidate" ]]; then
      if "$candidate" -c "import PyInstaller" >/dev/null 2>&1; then
        python_bin="$candidate"
        break
      fi
    fi
  done
fi
if [[ -z "$python_bin" ]]; then
  echo "ERROR: install the public PyInstaller build dependency." >&2
  exit 1
fi
"$python_bin" scripts/build_sidecar.py --check
plutil -lint src-tauri/Info.plist src-tauri/Entitlements.plist

if [[ "$CHECK_ONLY" == "1" ]]; then
  echo "MACOS-RELEASE-CHECK PASS"
  exit 0
fi

echo ">> Preparing release artifacts. This does not publish or sign with a private identity."
./scripts/package-app.sh
echo "MACOS-RELEASE PREPARED"
