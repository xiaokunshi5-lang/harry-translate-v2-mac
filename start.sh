#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
exec /bin/bash "$ROOT_DIR/启动同传系统.command" --legacy
