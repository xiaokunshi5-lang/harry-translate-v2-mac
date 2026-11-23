#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

exec /bin/bash "$ROOT_DIR/scripts/bootstrap_macos.sh" "$@"
