#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" || "${1:-}" == "--check" ]]; then
  DRY_RUN=1
fi

SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:-}"
NOTARY_PROFILE="${APPLE_NOTARY_KEYCHAIN_PROFILE:-}"
API_KEY_PATH="${APPLE_API_KEY_PATH:-}"
API_KEY_ID="${APPLE_API_KEY_ID:-}"
API_ISSUER="${APPLE_API_ISSUER:-}"

# Tauri canonical API-key variables use APPLE_API_KEY_PATH + APPLE_API_KEY
# (Key ID). The task-level contract also accepts APPLE_API_KEY=path with
# APPLE_API_KEY_ID. Resolve both forms without printing the values.
if [[ -n "$API_KEY_PATH" ]]; then
  API_KEY_ID="${API_KEY_ID:-${APPLE_API_KEY:-}}"
elif [[ -n "$API_KEY_ID" ]]; then
  API_KEY_PATH="${APPLE_API_KEY:-}"
elif [[ -n "${APPLE_API_KEY:-}" && -f "${APPLE_API_KEY:-}" ]]; then
  API_KEY_PATH="${APPLE_API_KEY}"
fi

NOTARY_MODE="none"
NOTARY_CONFIG_PRESENT=0
if [[ -n "$NOTARY_PROFILE" || -n "$API_KEY_PATH" || -n "$API_KEY_ID" || -n "$API_ISSUER" ]]; then
  NOTARY_CONFIG_PRESENT=1
fi
if [[ -n "$NOTARY_PROFILE" ]]; then
  NOTARY_MODE="keychain_profile"
elif [[ -n "$API_KEY_PATH" || -n "$API_KEY_ID" || -n "$API_ISSUER" ]]; then
  if [[ -n "$API_KEY_PATH" && -n "$API_KEY_ID" && -n "$API_ISSUER" && -f "$API_KEY_PATH" ]]; then
    NOTARY_MODE="api_key"
  else
    NOTARY_MODE="invalid"
  fi
fi

if [[ -n "$SIGNING_IDENTITY" ]]; then
  if [[ "$NOTARY_MODE" == "invalid" ]]; then
    echo "ERROR: incomplete notarization credentials; provide APPLE_NOTARY_KEYCHAIN_PROFILE or all API-key fields." >&2
    exit 1
  fi
  if [[ "$NOTARY_MODE" == "none" ]]; then
    PROJECTED_SIGNATURE_MODE="developer_id_unnotarized"
  else
    PROJECTED_SIGNATURE_MODE="developer_id_notarized"
  fi
else
  PROJECTED_SIGNATURE_MODE="adhoc_local"
  if [[ "$NOTARY_CONFIG_PRESENT" == "1" ]]; then
    echo "WARN: notarization environment is present but APPLE_SIGNING_IDENTITY is empty; building adhoc_local and ignoring notarization." >&2
  fi
  NOTARY_MODE="none"
fi

log() {
  printf '>> %s\n' "$*"
}

fail() {
  printf '>> [ERROR] %s\n' "$*" >&2
  exit 1
}

validate_signing_environment() {
  if [[ -z "$SIGNING_IDENTITY" ]]; then
    return 0
  fi
  command -v codesign >/dev/null 2>&1 || fail "codesign was not found."
  command -v security >/dev/null 2>&1 || fail "security was not found."
  if ! security find-identity -v -p codesigning 2>/dev/null | grep -F -- "$SIGNING_IDENTITY" >/dev/null; then
    fail "APPLE_SIGNING_IDENTITY is not available in the current codesigning keychain."
  fi
  if [[ "$NOTARY_MODE" != "none" ]]; then
    xcrun --find notarytool >/dev/null 2>&1 || fail "xcrun notarytool was not found."
    xcrun --find stapler >/dev/null 2>&1 || fail "xcrun stapler was not found."
    command -v spctl >/dev/null 2>&1 || fail "spctl was not found."
  fi
}

print_signing_plan() {
  log "projected signature mode: $PROJECTED_SIGNATURE_MODE"
  log "notarization mode: $NOTARY_MODE"
  if [[ "$PROJECTED_SIGNATURE_MODE" == "adhoc_local" ]]; then
    log "no Developer ID identity supplied; build remains reproducible but Gatekeeper may require documented quarantine recovery."
  fi
}

find_bundle_artifact() {
  local directory="$1"
  local suffix="$2"
  if [[ ! -d "$directory" ]]; then
    return 1
  fi
  find "$directory" -maxdepth 1 -name "*${suffix}" -print -quit 2>/dev/null
}

NOTARY_RESULT=""
NOTARY_ERROR=""
SIDECAR_ANCHOR_BACKUP=""
SIDECAR_ANCHOR_EXISTED=0

cleanup_package_artifacts() {
  if [[ -n "$NOTARY_RESULT" && -f "$NOTARY_RESULT" ]]; then
    rm -f "$NOTARY_RESULT"
  fi
  if [[ -n "$NOTARY_ERROR" && -f "$NOTARY_ERROR" ]]; then
    rm -f "$NOTARY_ERROR"
  fi
  if [[ -n "$SIDECAR_ANCHOR_BACKUP" && -f "$SIDECAR_ANCHOR_BACKUP" ]]; then
    if [[ "$SIDECAR_ANCHOR_EXISTED" == "1" ]]; then
      cp -p "$SIDECAR_ANCHOR_BACKUP" "$SIDECAR_BIN"
    else
      rm -f "$SIDECAR_BIN"
    fi
    rm -f "$SIDECAR_ANCHOR_BACKUP"
  fi
}

trap cleanup_package_artifacts EXIT

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

validate_signing_environment
print_signing_plan

SIDECAR_BIN="$ROOT_DIR/src-tauri/binaries/harry-sidecar-aarch64-apple-darwin"

if [[ "$DRY_RUN" != "1" ]]; then
  if [[ -e "$SIDECAR_BIN" ]]; then
    SIDECAR_ANCHOR_EXISTED=1
    SIDECAR_ANCHOR_BACKUP="$(mktemp "${TMPDIR:-/tmp}/harry-sidecar-anchor.XXXXXX")"
    cp -p "$SIDECAR_BIN" "$SIDECAR_ANCHOR_BACKUP"
  fi
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
  if [[ -n "$SIGNING_IDENTITY" ]]; then
    log "signing inner sidecar with hardened runtime..."
    codesign \
      --force \
      --options runtime \
      --timestamp \
      --entitlements "$ROOT_DIR/src-tauri/Entitlements.plist" \
      --sign "$SIGNING_IDENTITY" \
      "$SIDECAR_BIN"
    codesign --verify --strict --verbose=2 "$SIDECAR_BIN"
  fi
fi

echo ">> Verifying frontend production bundle..."
if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN: would run npm --prefix frontend run typecheck && npm --prefix frontend run build"
  echo "DRY_RUN: would run the selected Python build environment scripts/build_sidecar.py"
  if [[ -n "$SIGNING_IDENTITY" ]]; then
    echo "DRY_RUN: would sign the inner sidecar with --options runtime --timestamp --entitlements"
  fi
  echo "DRY_RUN: would run Tauri CLI build --target aarch64-apple-darwin"
  if [[ "$NOTARY_MODE" != "none" ]]; then
    echo "DRY_RUN: would submit the DMG, staple app and DMG, validate the ticket and run spctl"
  fi
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

# Tauri reads the official API-key variable names. Export normalized values so
# both the documented task form and Tauri's canonical form work without
# persisting credentials anywhere.
if [[ "$NOTARY_MODE" == "api_key" ]]; then
  export APPLE_API_KEY_PATH="$API_KEY_PATH"
  export APPLE_API_KEY="$API_KEY_ID"
  export APPLE_API_ISSUER="$API_ISSUER"
fi

(
  cd "$ROOT_DIR/src-tauri"
  "${TAURI_CLI[@]}" build \
    --target aarch64-apple-darwin \
    --config tauri.macos.conf.json
)

BUNDLE_ROOT="$ROOT_DIR/src-tauri/target/aarch64-apple-darwin/release/bundle"
APP_PATH="$(find_bundle_artifact "$BUNDLE_ROOT/macos" ".app" || true)"
DMG_PATH="$(find_bundle_artifact "$BUNDLE_ROOT/dmg" ".dmg" || true)"
if [[ -z "$APP_PATH" || ! -d "$APP_PATH" ]]; then
  fail "Tauri did not produce an app bundle under $BUNDLE_ROOT/macos."
fi
if [[ -z "$DMG_PATH" || ! -f "$DMG_PATH" ]]; then
  fail "Tauri did not produce a DMG under $BUNDLE_ROOT/dmg."
fi

if [[ -n "$SIGNING_IDENTITY" ]]; then
  log "verifying Developer ID signature on the app bundle..."
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
  APP_SIDECAR="$APP_PATH/Contents/MacOS/harry-sidecar"
  [[ -x "$APP_SIDECAR" ]] || fail "packaged sidecar is missing from the app bundle."
  codesign --verify --strict --verbose=2 "$APP_SIDECAR"
fi

if [[ "$NOTARY_MODE" != "none" ]]; then
  log "submitting DMG for Apple notarization..."
  NOTARY_RESULT="$(mktemp "${TMPDIR:-/tmp}/harry-notary-result.XXXXXX")"
  NOTARY_ERROR="$(mktemp "${TMPDIR:-/tmp}/harry-notary-error.XXXXXX")"
  if [[ "$NOTARY_MODE" == "keychain_profile" ]]; then
    if ! xcrun notarytool submit "$DMG_PATH" \
        --keychain-profile "$NOTARY_PROFILE" \
        --output-format json \
        --wait >"$NOTARY_RESULT" 2>"$NOTARY_ERROR"; then
      fail "notarytool submission failed; diagnostic output was not printed to avoid credential leakage."
    fi
  else
    if ! xcrun notarytool submit "$DMG_PATH" \
        --key "$API_KEY_PATH" \
        --key-id "$API_KEY_ID" \
        --issuer "$API_ISSUER" \
        --output-format json \
        --wait >"$NOTARY_RESULT" 2>"$NOTARY_ERROR"; then
      fail "notarytool submission failed; diagnostic output was not printed to avoid credential leakage."
    fi
  fi
  NOTARY_STATUS="$(
    "$PYTHON_BIN" -c '
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    print("invalid")
else:
    print(str(payload.get("status") or "missing"))
' "$NOTARY_RESULT"
  )"
  if [[ "$NOTARY_STATUS" != "Accepted" ]]; then
    fail "Apple notarization did not return Accepted (status=$NOTARY_STATUS)."
  fi
  log "Apple notarization status: Accepted"
  log "stapling notarization tickets..."
  xcrun stapler staple "$APP_PATH"
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
  spctl -a -vvv -t install "$APP_PATH"
fi

log "DMG: $DMG_PATH"
shasum -a 256 "$DMG_PATH"

VERIFY_ARGS=(
  --lifecycle
  --binary "$SIDECAR_BIN"
  --app "$APP_PATH"
  --dmg "$DMG_PATH"
)
"$PYTHON_BIN" "$ROOT_DIR/scripts/verify_package_smoke.py" "${VERIFY_ARGS[@]}"
log "package build complete (projected mode: $PROJECTED_SIGNATURE_MODE)"
