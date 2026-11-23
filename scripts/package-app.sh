#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" || "${1:-}" == "--check" ]]; then
  DRY_RUN=1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ERROR: scripts/package-app.sh is Apple Silicon macOS only." >&2
  exit 1
fi

for required in \
  "$ROOT_DIR/rust-toolchain.toml" \
  "$ROOT_DIR/src-tauri/Cargo.toml" \
  "$ROOT_DIR/src-tauri/tauri.conf.json" \
  "$ROOT_DIR/src-tauri/tauri.macos.conf.json" \
  "$ROOT_DIR/src-tauri/Info.plist" \
  "$ROOT_DIR/src-tauri/Entitlements.plist"; do
  if [[ ! -f "$required" ]]; then
    echo "ERROR: missing packaging file: $required" >&2
    exit 1
  fi
done

if [[ "$DRY_RUN" != "1" ]]; then
  PYTHON_BIN=""
  for candidate in \
    "$ROOT_DIR/.venv/bin/python" \
    "$ROOT_DIR/.venv-v2/bin/python" \
    "python3"; do
    if [[ "$candidate" == "python3" ]] || [[ -x "$candidate" ]]; then
      if "$candidate" -c "import PyInstaller" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
      fi
    fi
  done
  if [[ -z "$PYTHON_BIN" ]]; then
    echo "ERROR: install the public PyInstaller build dependency." >&2
    exit 1
  fi
  "$PYTHON_BIN" "$ROOT_DIR/scripts/build_sidecar.py"
fi

echo ">> Verifying frontend production bundle..."
if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN: would run npm --prefix frontend run typecheck && npm --prefix frontend run build"
  echo "DRY_RUN: would run the selected Python build environment scripts/build_sidecar.py"
  echo "DRY_RUN: would run Tauri CLI build --target aarch64-apple-darwin"
  exit 0
fi

npm --prefix "$ROOT_DIR/frontend" run typecheck
npm --prefix "$ROOT_DIR/frontend" run build

if ! command -v cargo >/dev/null 2>&1; then
  echo "ERROR: cargo toolchain not found." >&2
  exit 1
fi

TAURI_CLI=()
if cargo tauri --version >/dev/null 2>&1; then
  TAURI_CLI=(cargo tauri)
else
  TAURI_CLI=(npx --yes @tauri-apps/cli@2.9.4)
fi

(
  cd "$ROOT_DIR/src-tauri"
  "${TAURI_CLI[@]}" build \
    --target aarch64-apple-darwin \
    --config tauri.macos.conf.json
)

if [[ -x "$ROOT_DIR/src-tauri/binaries/harry-sidecar-aarch64-apple-darwin" ]]; then
  "$PYTHON_BIN" "$ROOT_DIR/scripts/verify_package_smoke.py" \
    --lifecycle \
    --binary "$ROOT_DIR/src-tauri/binaries/harry-sidecar-aarch64-apple-darwin"
fi
