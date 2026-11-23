#!/bin/bash

set -Eeuo pipefail
IFS=$'\n\t'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

VENV_DIR="$ROOT_DIR/.venv-v2"
VENV_PYTHON="$VENV_DIR/bin/python"
AGENTS_DIR="$ROOT_DIR/.agents"
RUNTIME_PORT_FILE="$AGENTS_DIR/runtime_port"
RUNTIME_STATE_FILE="$AGENTS_DIR/runtime_state.json"
LAUNCH_LOCK_DIR="$AGENTS_DIR/launch.lock"
UV_LOCKHASH_FILE="$AGENTS_DIR/.venv-v2.lockhash"
CARGO_FINGERPRINT_FILE="$AGENTS_DIR/cargo-build.fingerprint"
REQUESTED_PORT="${PORT:-8000}"
SERVER_PID=""
SERVER_OWNED=0
TAURI_PID=""
ACTIVE_PORT=""
REUSED_BACKEND=0
REUSED_PID=""
LOCK_OWNED=0
LEGACY_MODE=0
DESKTOP_MODE=0
PREPARE_ONLY="${HARRY_BOOTSTRAP_PREPARE_ONLY:-0}"

while (( $# > 0 )); do
    case "$1" in
        --legacy)
            LEGACY_MODE=1
            ;;
        --desktop)
            DESKTOP_MODE=1
            ;;
        --no-open-settings)
            export HARRY_NO_OPEN_SETTINGS=1
            ;;
        *)
            break
            ;;
    esac
    shift
done

# Finder-launched apps do not inherit the user's interactive-shell PATH.
# Preserve explicit caller overrides while appending the usual macOS locations.
export PATH="${PATH}:${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin"

log() {
    printf '>> %s\n' "$*"
}

fail() {
    printf '>> [错误] %s\n' "$*" >&2
    exit 1
}

project_sha256() {
    local path="$1"
    shasum -a 256 "$path" 2>/dev/null | awk '{print $1}'
}

hash_text() {
    shasum -a 256 2>/dev/null | awk '{print $1}'
}

atomic_write() {
    local path="$1"
    local value="$2"
    local temporary
    mkdir -p "$(dirname "$path")"
    temporary="$(mktemp "${path}.tmp.XXXXXX")"
    printf '%s\n' "$value" >"$temporary"
    chmod 600 "$temporary"
    mv -f "$temporary" "$path"
}

compute_lock_hash() {
    [[ -f "$ROOT_DIR/uv.lock" ]] || fail "缺少 uv.lock。"
    [[ -f "$ROOT_DIR/pyproject.toml" ]] || fail "缺少 pyproject.toml。"
    {
        printf 'uv.lock\n'
        project_sha256 "$ROOT_DIR/uv.lock"
        printf 'pyproject.toml\n'
        project_sha256 "$ROOT_DIR/pyproject.toml"
    } | hash_text
}

compute_paths_fingerprint() {
    local path relative file
    {
        for path in "$@"; do
            if [[ -d "$path" ]]; then
                while IFS= read -r file; do
                    relative="${file#"$ROOT_DIR"/}"
                    printf 'FILE:%s\n' "$relative"
                    project_sha256 "$file"
                done < <(
                    find "$path" -type f ! -name '.DS_Store' -print \
                        | LC_ALL=C sort
                )
            elif [[ -f "$path" ]]; then
                relative="${path#"$ROOT_DIR"/}"
                printf 'FILE:%s\n' "$relative"
                project_sha256 "$path"
            fi
        done
    } | hash_text
}

compute_frontend_fingerprint() {
    local dist="$ROOT_DIR/frontend/dist"
    if [[ ! -x "$VENV_PYTHON" ]]; then
        printf 'missing\n'
        return 0
    fi
    "$VENV_PYTHON" -c '
import hashlib
import sys
from pathlib import Path

frontend_dist = Path(sys.argv[1])
digest = hashlib.sha256()
files = sorted(
    (path for path in frontend_dist.rglob("*") if path.is_file()),
    key=lambda path: path.relative_to(frontend_dist).as_posix(),
) if frontend_dist.is_dir() else []
if not files:
    digest.update(b"frontend-dist-missing")
for path in files:
    relative = path.relative_to(frontend_dist).as_posix().encode("utf-8")
    digest.update(len(relative).to_bytes(4, "big"))
    digest.update(relative)
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(128 * 1024):
                digest.update(chunk)
    except OSError:
        digest.update(b"<unreadable>")
print(digest.hexdigest()[:16])
' "$dist"
}

compute_cargo_fingerprint() {
    compute_paths_fingerprint \
        "$ROOT_DIR/src-tauri/src" \
        "$ROOT_DIR/src-tauri/build.rs" \
        "$ROOT_DIR/src-tauri/Cargo.toml" \
        "$ROOT_DIR/src-tauri/Cargo.lock" \
        "$ROOT_DIR/src-tauri/tauri.conf.json" \
        "$ROOT_DIR/src-tauri/capabilities" \
        "$ROOT_DIR/frontend/dist"
}

read_runtime_port() {
    local port
    [[ -f "$RUNTIME_PORT_FILE" ]] || return 1
    port="$(tr -d '[:space:]' <"$RUNTIME_PORT_FILE")"
    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    (( port > 0 && port <= 65535 )) || return 1
    printf '%s\n' "$port"
}

read_json_identity() {
    local path="$1"
    local output
    [[ -f "$path" ]] || return 1
    [[ -x "$VENV_PYTHON" ]] || return 1
    output="$(
        "$VENV_PYTHON" -c '
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    raise SystemExit(1)

def text(key):
    value = payload.get(key, "")
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)

print("\x1f".join(text(key) for key in (
    "service",
    "project_root",
    "pid",
    "port",
    "frontend_fingerprint",
    "fast",
    "running",
    "registered",
    "visible",
)))
' "$path"
    )" || return 1
    IFS=$'\x1f' read -r ID_SERVICE ID_PROJECT_ROOT ID_PID ID_PORT \
        ID_FRONTEND_FINGERPRINT ID_FAST ID_RUNNING ID_REGISTERED ID_VISIBLE <<<"$output"
}

fetch_health() {
    local port="$1"
    local output="$2"
    curl --fail --silent --show-error --max-time 1 \
        --output "$output" \
        "http://127.0.0.1:${port}/api/health?fast=1" >/dev/null 2>&1
}

health_is_current() {
    local port="$1"
    local expected_pid="${2:-}"
    local expected_fingerprint="${3:-$(compute_frontend_fingerprint)}"
    local temporary
    temporary="$(mktemp "$AGENTS_DIR/.health.XXXXXX")"
    if ! fetch_health "$port" "$temporary"; then
        rm -f "$temporary"
        return 1
    fi
    if ! read_json_identity "$temporary"; then
        rm -f "$temporary"
        return 1
    fi
    rm -f "$temporary"
    [[ "$ID_SERVICE" == "harry-translate-v2" ]] || return 1
    [[ "$ID_PROJECT_ROOT" == "$ROOT_DIR" ]] || return 1
    [[ "$ID_PID" =~ ^[0-9]+$ ]] || return 1
    [[ "$ID_PORT" == "$port" ]] || return 1
    [[ "$ID_FAST" == "1" ]] || return 1
    [[ "$ID_FRONTEND_FINGERPRINT" == "$expected_fingerprint" ]] || return 1
    if [[ -n "$expected_pid" ]]; then
        [[ "$ID_PID" == "$expected_pid" ]] || return 1
    fi
    return 0
}

health_is_current_with_retries() {
    local port="$1"
    local expected_pid="$2"
    local expected_fingerprint="$3"
    local attempt
    for attempt in 1 2 3; do
        if health_is_current "$port" "$expected_pid" "$expected_fingerprint"; then
            return 0
        fi
        if (( attempt < 3 )); then
            sleep 0.1
        fi
    done
    return 1
}

process_command() {
    ps -p "$1" -o command= 2>/dev/null
}

process_working_directory() {
    lsof -a -p "$1" -d cwd -Fn 2>/dev/null \
        | sed -n 's/^n//p' \
        | head -n 1
}

process_is_v2_for_project() {
    local pid="$1"
    local command cwd
    kill -0 "$pid" 2>/dev/null || return 1
    command="$(process_command "$pid")"
    cwd="$(process_working_directory "$pid")"
    [[ "$cwd" == "$ROOT_DIR" ]] || return 1
    [[ "$command" == *"$VENV_PYTHON"* || "$command" == *".venv-v2/bin/python"* ]] \
        || return 1
    [[ "$command" == *"app.main"* ]]
}

process_is_legacy_for_project() {
    local pid="$1"
    local command
    kill -0 "$pid" 2>/dev/null || return 1
    command="$(process_command "$pid")"
    [[ "$command" == *"$ROOT_DIR/legacy/v1/server.py"* ]]
}

process_is_tauri_for_project() {
    local pid="$1"
    local command cwd
    kill -0 "$pid" 2>/dev/null || return 1
    command="$(process_command "$pid")"
    cwd="$(process_working_directory "$pid")"
    [[ "$cwd" == "$ROOT_DIR" ]] || return 1
    [[ "$command" == "$ROOT_DIR/src-tauri/target/debug/harry-translate-mac" ]] \
        || [[ "$command" == "$ROOT_DIR/src-tauri/target/release/harry-translate-mac" ]] \
        || [[ "$command" == "$ROOT_DIR/src-tauri/target/release/bundle/macos/HARRY Translate.app/Contents/MacOS/harry-translate-mac" ]] \
        || [[ "$command" == "$ROOT_DIR/dist/HARRY Translate.app/Contents/MacOS/harry-translate-mac" ]]
}

stop_verified_process() {
    local pid="$1"
    local label="$2"
    local port="${3:-}"
    local deadline
    [[ "$pid" =~ ^[0-9]+$ ]] || return 0
    (( pid > 1 && pid != $$ )) || return 0
    if [[ -n "$port" ]]; then
        curl --silent --show-error --max-time 1 \
            -X POST "http://127.0.0.1:${port}/api/system/shutdown" \
            >/dev/null 2>&1 || true
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    log "停止已确认属于本项目的${label}进程 PID ${pid}..."
    kill "$pid" 2>/dev/null || true
    deadline=$((SECONDS + 2))
    while (( SECONDS < deadline )); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.05
    done
    kill -9 "$pid" 2>/dev/null || true
}

cleanup_legacy_instances() {
    local pid
    command -v pgrep >/dev/null 2>&1 || return 0
    while IFS= read -r pid; do
        [[ -n "$pid" ]] || continue
        if process_is_legacy_for_project "$pid"; then
            stop_verified_process "$pid" "legacy/v1 " ""
        fi
    done < <(pgrep -f "$ROOT_DIR/legacy/v1/server.py" 2>/dev/null || true)
}

cleanup_tauri_instances() {
    local pid
    command -v pgrep >/dev/null 2>&1 || return 0
    while IFS= read -r pid; do
        [[ -n "$pid" ]] || continue
        if process_is_tauri_for_project "$pid"; then
            stop_verified_process "$pid" "Tauri " ""
        fi
    done < <(pgrep -f "harry-translate-mac" 2>/dev/null || true)
}

remove_confirmed_runtime_files() {
    local expected_pid="$1"
    local expected_port="$2"
    if [[ -f "$RUNTIME_STATE_FILE" ]] && read_json_identity "$RUNTIME_STATE_FILE"; then
        if [[ "$ID_SERVICE" == "harry-translate-v2" ]] \
            && [[ "$ID_PROJECT_ROOT" == "$ROOT_DIR" ]] \
            && [[ "$ID_PID" == "$expected_pid" ]]; then
            rm -f "$RUNTIME_STATE_FILE"
            if [[ -n "$expected_port" ]] && [[ "$ID_PORT" == "$expected_port" ]]; then
                rm -f "$RUNTIME_PORT_FILE"
            fi
        fi
    fi
}

cleanup_stale_instances() {
    local state_pid state_port current_port listener_pid
    local current_fingerprint
    current_fingerprint="$(compute_frontend_fingerprint)"
    cleanup_legacy_instances
    cleanup_tauri_instances

    if [[ -f "$RUNTIME_STATE_FILE" ]] && read_json_identity "$RUNTIME_STATE_FILE"; then
        state_pid="$ID_PID"
        state_port="$ID_PORT"
        if [[ "$ID_SERVICE" == "harry-translate-v2" ]] \
            && [[ "$ID_PROJECT_ROOT" == "$ROOT_DIR" ]] \
            && [[ "$state_pid" =~ ^[0-9]+$ ]] \
            && [[ "$state_port" =~ ^[0-9]+$ ]]; then
            if kill -0 "$state_pid" 2>/dev/null; then
                if ! health_is_current_with_retries \
                    "$state_port" "$state_pid" "$current_fingerprint"; then
                    if process_is_v2_for_project "$state_pid"; then
                        stop_verified_process "$state_pid" "旧版/半死 V2 " "$state_port"
                    fi
                fi
            else
                remove_confirmed_runtime_files "$state_pid" "$state_port"
            fi
        fi
    fi

    if current_port="$(read_runtime_port)"; then
        listener_pid="$(
            lsof -nP -iTCP:"$current_port" -sTCP:LISTEN -t 2>/dev/null \
                | head -n 1
        )"
        if [[ -n "$listener_pid" ]] && process_is_v2_for_project "$listener_pid"; then
            if ! health_is_current_with_retries \
                "$current_port" "$listener_pid" "$current_fingerprint"; then
                stop_verified_process "$listener_pid" "未登记 V2 " "$current_port"
            fi
        fi
    fi
}

find_reusable_backend() {
    local state_pid state_port current_fingerprint
    [[ -f "$RUNTIME_STATE_FILE" ]] || return 1
    read_json_identity "$RUNTIME_STATE_FILE" || return 1
    state_pid="$ID_PID"
    state_port="$ID_PORT"
    current_fingerprint="$(compute_frontend_fingerprint)"
    [[ "$ID_SERVICE" == "harry-translate-v2" ]] || return 1
    [[ "$ID_PROJECT_ROOT" == "$ROOT_DIR" ]] || return 1
    [[ "$state_pid" =~ ^[0-9]+$ ]] || return 1
    [[ "$state_port" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$state_pid" 2>/dev/null || return 1
    health_is_current_with_retries \
        "$state_port" "$state_pid" "$current_fingerprint" || return 1
    ACTIVE_PORT="$state_port"
    REUSED_PID="$state_pid"
    REUSED_BACKEND=1
}

validate_port() {
    [[ "$REQUESTED_PORT" =~ ^[0-9]+$ ]] || fail "PORT 必须是 0-65535 的整数。"
    (( REQUESTED_PORT >= 0 && REQUESTED_PORT <= 65535 )) || fail "PORT 必须是 0-65535 的整数。"
}

validate_python_312() {
    local python_bin="$1"
    "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' \
        || fail "V2 运行时必须是 Python 3.12；不会改动 V1 的 .venv。"
}

initialize_local_files() {
    mkdir -p "$AGENTS_DIR"

    if [[ ! -f "$ROOT_DIR/.env" ]]; then
        [[ -f "$ROOT_DIR/.env.example" ]] \
            || fail "缺少 .env 和 .env.example，无法初始化本地配置。"
        (umask 077 && cp -n "$ROOT_DIR/.env.example" "$ROOT_DIR/.env") \
            || fail "无法从 .env.example 创建 .env。"
        [[ -f "$ROOT_DIR/.env" ]] || fail "无法创建 .env。"
        log "已从 .env.example 创建 .env；请按需填写本地密钥。"
    fi

    chmod 600 "$ROOT_DIR/.env" || fail "无法将 .env 权限设置为 0600。"
}

release_launch_lock() {
    local owner=""
    if (( LOCK_OWNED == 1 )); then
        if [[ -f "$LAUNCH_LOCK_DIR/pid" ]]; then
            owner="$(tr -d '[:space:]' <"$LAUNCH_LOCK_DIR/pid" 2>/dev/null || true)"
        fi
        if [[ -z "$owner" || "$owner" == "$$" ]]; then
            rm -rf "$LAUNCH_LOCK_DIR"
        fi
        LOCK_OWNED=0
    fi
}

acquire_launch_lock() {
    local deadline=$((SECONDS + 10))
    local owner stale lock_age lock_mtime
    mkdir -p "$AGENTS_DIR"
    while ! mkdir "$LAUNCH_LOCK_DIR" 2>/dev/null; do
        owner=""
        if [[ -f "$LAUNCH_LOCK_DIR/pid" ]]; then
            owner="$(tr -d '[:space:]' <"$LAUNCH_LOCK_DIR/pid" 2>/dev/null || true)"
        fi
        lock_mtime="$(stat -f '%m' "$LAUNCH_LOCK_DIR" 2>/dev/null || stat -c '%Y' "$LAUNCH_LOCK_DIR" 2>/dev/null || date +%s)"
        lock_age=$(( $(date +%s) - lock_mtime ))
        if [[ -n "$owner" ]] && ! kill -0 "$owner" 2>/dev/null; then
            stale="$LAUNCH_LOCK_DIR.stale.$$"
            if mv "$LAUNCH_LOCK_DIR" "$stale" 2>/dev/null; then
                rm -rf "$stale"
                continue
            fi
        elif [[ -z "$owner" ]] && (( lock_age >= 10 )); then
            stale="$LAUNCH_LOCK_DIR.stale.$$"
            if mv "$LAUNCH_LOCK_DIR" "$stale" 2>/dev/null; then
                rm -rf "$stale"
                continue
            fi
        fi
        if (( SECONDS >= deadline )); then
            fail "等待其他启动实例超过 10 秒；未修改共享运行态。"
        fi
        sleep 0.1
    done
    LOCK_OWNED=1
    printf '%s\n' "$$" >"$LAUNCH_LOCK_DIR/pid"
    date +%s >"$LAUNCH_LOCK_DIR/started_at"
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM HUP

    if [[ -n "$TAURI_PID" ]] && kill -0 "$TAURI_PID" 2>/dev/null; then
        kill "$TAURI_PID" 2>/dev/null || true
        wait "$TAURI_PID" 2>/dev/null || true
    fi

    if (( SERVER_OWNED == 1 )) && [[ -n "$SERVER_PID" ]] \
        && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi

    if (( SERVER_OWNED == 1 )) && [[ -n "$SERVER_PID" ]]; then
        remove_confirmed_runtime_files "$SERVER_PID" "$ACTIVE_PORT"
        if [[ -n "$ACTIVE_PORT" ]] \
            && [[ "$(read_runtime_port 2>/dev/null || true)" == "$ACTIVE_PORT" ]]; then
            rm -f "$RUNTIME_PORT_FILE"
        fi
    fi

    release_launch_lock
    exit "$status"
}

trap cleanup EXIT INT TERM HUP

sync_with_uv() {
    command -v uv >/dev/null 2>&1 || return 1

    log "检测到 uv，正在同步 Python 3.12 的 .venv-v2 环境..."
    UV_PROJECT_ENVIRONMENT=.venv-v2 uv sync --frozen --python 3.12
}

find_python_312() {
    local candidate
    for candidate in python3.12 "$HOME/.local/bin/python3.12" /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

sync_with_pip() {
    local python_312 index_url
    python_312="$(find_python_312)" \
        || fail "未找到 uv 或 Python 3.12；请先安装其中一项后重试。"

    if [[ -x "$VENV_PYTHON" ]]; then
        validate_python_312 "$VENV_PYTHON"
    else
        log "未检测到 uv，正在使用 Python 3.12 创建 .venv-v2..."
        "$python_312" -m venv "$VENV_DIR" || fail "创建 .venv-v2 失败。"
        validate_python_312 "$VENV_PYTHON"
    fi

    log "正在按 requirements.lock 安装固定依赖..."
    for index_url in \
        "https://pypi.org/simple" \
        "https://mirrors.aliyun.com/pypi/simple" \
        "https://pypi.tuna.tsinghua.edu.cn/simple"
    do
        if "$VENV_PYTHON" -m pip install \
            --disable-pip-version-check \
            --no-input \
            --require-hashes \
            --index-url "$index_url" \
            -r "$ROOT_DIR/requirements.lock"
        then
            return 0
        fi
        log "当前镜像安装失败，将尝试下一个来源：$index_url"
    done

    fail "官方源、阿里云镜像和清华镜像均无法安装锁定依赖。"
}

setup_runtime() {
    local desired_lock_hash existing_lock_hash=""
    [[ -f "$ROOT_DIR/requirements.lock" ]] || fail "缺少 requirements.lock。"

    if [[ "${HARRY_BOOTSTRAP_SKIP_SYNC:-0}" == "1" ]]; then
        log "诊断模式：已跳过 uv/venv 依赖同步。"
        if [[ -x "$VENV_PYTHON" ]]; then
            validate_python_312 "$VENV_PYTHON"
        fi
        return 0
    fi

    desired_lock_hash="$(compute_lock_hash)"
    if [[ -f "$UV_LOCKHASH_FILE" ]]; then
        existing_lock_hash="$(tr -d '[:space:]' <"$UV_LOCKHASH_FILE")"
    fi
    if [[ -x "$VENV_PYTHON" ]] && [[ "$existing_lock_hash" == "$desired_lock_hash" ]]; then
        validate_python_312 "$VENV_PYTHON"
        log "依赖指纹未变化，跳过 uv sync。"
        return 0
    fi

    if sync_with_uv; then
        :
    else
        sync_with_pip
    fi

    [[ -x "$VENV_PYTHON" ]] || fail ".venv-v2 中缺少 python 可执行文件。"
    validate_python_312 "$VENV_PYTHON"
    atomic_write "$UV_LOCKHASH_FILE" "$desired_lock_hash"
}

ensure_frontend_dist() {
    if [[ -f "$ROOT_DIR/frontend/dist/index.html" ]]; then
        return 0
    fi
    command -v npm >/dev/null 2>&1 \
        || fail "缺少 frontend/dist 且未检测到 npm，无法构建前端。"
    log "检测到前端构建缺失，正在构建 frontend/dist..."
    npm --prefix "$ROOT_DIR/frontend" run build
}

ensure_cargo_build() {
    local tauri_bin="$ROOT_DIR/src-tauri/target/debug/harry-translate-mac"
    local desired_fingerprint existing_fingerprint=""
    ensure_frontend_dist
    desired_fingerprint="$(compute_cargo_fingerprint)"
    if [[ -f "$CARGO_FINGERPRINT_FILE" ]]; then
        existing_fingerprint="$(tr -d '[:space:]' <"$CARGO_FINGERPRINT_FILE")"
    fi
    if [[ -x "$tauri_bin" ]] && [[ "$existing_fingerprint" == "$desired_fingerprint" ]]; then
        log "Rust/前端构建指纹未变化，跳过 cargo build。"
        return 0
    fi
    command -v cargo >/dev/null 2>&1 \
        || fail "Rust/前端构建已变化且未检测到 cargo，无法更新 Tauri 二进制。"
    log "正在按变化指纹编译 Tauri 原生桌面悬浮窗 (custom-protocol)..."
    cargo build --offline --features custom-protocol --manifest-path "$ROOT_DIR/src-tauri/Cargo.toml"
    [[ -x "$tauri_bin" ]] || fail "cargo build 未生成预期 Tauri 二进制。"
    atomic_write "$CARGO_FINGERPRINT_FILE" "$desired_fingerprint"
}

wait_for_owned_server() {
    local deadline=$((SECONDS + 60))
    local candidate_port=""
    while (( SECONDS < deadline )); do
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            wait "$SERVER_PID" 2>/dev/null || true
            fail "app.main 在健康检查就绪前退出。"
        fi

        if [[ -f "$RUNTIME_STATE_FILE" ]] && read_json_identity "$RUNTIME_STATE_FILE"; then
            candidate_port="$ID_PORT"
            if [[ "$candidate_port" =~ ^[0-9]+$ ]] \
                && health_is_current "$candidate_port" "$SERVER_PID"; then
                ACTIVE_PORT="$candidate_port"
                return 0
            fi
        fi

        if candidate_port="$(read_runtime_port)"; then
            if health_is_current "$candidate_port" "$SERVER_PID"; then
                ACTIVE_PORT="$candidate_port"
                return 0
            fi
        fi
        sleep 0.05
    done

    fail "等待 app.main 就绪超时；请检查终端日志。"
}

launch_backend() {
    [[ -f "$ROOT_DIR/app/main.py" ]] || fail "缺少 app/main.py，无法启动 V2 服务。"
    [[ -x "$VENV_PYTHON" ]] || fail ".venv-v2 尚未就绪；请先完成依赖同步。"
    validate_python_312 "$VENV_PYTHON"

    log "正在启动 V2 同传服务，请求端口：$REQUESTED_PORT"
    PYTHONUNBUFFERED=1 "$VENV_PYTHON" -m app.main \
        --host 127.0.0.1 \
        --port "$REQUESTED_PORT" \
        --project-root "$ROOT_DIR" \
        --runtime-port-file "$RUNTIME_PORT_FILE" &
    SERVER_PID=$!
    SERVER_OWNED=1
    wait_for_owned_server
}

prepare_backend() {
    if find_reusable_backend; then
        log "复用已确认的最新版 V2 后端：http://127.0.0.1:${ACTIVE_PORT}"
        return 0
    fi
    cleanup_stale_instances
    launch_backend
}

open_settings_page() {
    local url="http://127.0.0.1:${ACTIVE_PORT}/settings"
    log "服务已就绪：${url}"
    if [[ "${HARRY_NO_OPEN_SETTINGS:-0}" == "1" ]]; then
        return 0
    fi
    open "$url" || log "无法自动打开浏览器，请手动访问 ${url}。"
}

wait_for_shared_backend() {
    while kill -0 "$1" 2>/dev/null; do
        sleep 1
    done
}

start_server() {
    ensure_frontend_dist
    prepare_backend
    release_launch_lock
    open_settings_page
    if (( REUSED_BACKEND == 1 )); then
        wait_for_shared_backend "$REUSED_PID"
    else
        wait "$SERVER_PID"
    fi
}

overlay_is_running() {
    local temporary
    temporary="$(mktemp "$AGENTS_DIR/.overlay-status.XXXXXX")"
    if ! curl --fail --silent --show-error --max-time 1 \
        --output "$temporary" \
        "http://127.0.0.1:${ACTIVE_PORT}/api/overlay/status" >/dev/null 2>&1; then
        rm -f "$temporary"
        return 1
    fi
    if ! read_json_identity "$temporary"; then
        rm -f "$temporary"
        return 1
    fi
    rm -f "$temporary"
    [[ "$ID_RUNNING" == "1" ]]
}

overlay_is_registered() {
    local temporary
    temporary="$(mktemp "$AGENTS_DIR/.overlay-status.XXXXXX")"
    if ! curl --fail --silent --show-error --max-time 1 \
        --output "$temporary" \
        "http://127.0.0.1:${ACTIVE_PORT}/api/overlay/status" >/dev/null 2>&1; then
        rm -f "$temporary"
        return 1
    fi
    if ! read_json_identity "$temporary"; then
        rm -f "$temporary"
        return 1
    fi
    rm -f "$temporary"
    [[ "$ID_REGISTERED" == "1" ]]
}

wait_for_overlay_registration() {
    local deadline=$((SECONDS + 5))
    while (( SECONDS < deadline )); do
        if overlay_is_registered; then
            return 0
        fi
        sleep 0.1
    done
    fail "原生悬浮窗未在 5 秒内注册 loopback 控制服务。"
}

launch_or_show_overlay() {
    local tauri_bin="$ROOT_DIR/src-tauri/target/debug/harry-translate-mac"
    if overlay_is_running; then
        log "检测到已注册的唯一悬浮窗，正在唤出..."
        curl --fail --silent --show-error --max-time 2 \
            -X POST "http://127.0.0.1:${ACTIVE_PORT}/api/overlay/launch" \
            >/dev/null
        release_launch_lock
        return 0
    fi

    log "正在以附着模式启动唯一原生悬浮窗 (共享同一后端)..."
    HARRY_PROJECT_ROOT="$ROOT_DIR" \
        HARRY_ATTACH_BACKEND_URL="http://127.0.0.1:${ACTIVE_PORT}" \
        HARRY_NO_OPEN_SETTINGS=1 \
        "$tauri_bin" &
    TAURI_PID=$!
    wait_for_overlay_registration
    release_launch_lock
    wait "$TAURI_PID"
    TAURI_PID=""
}

start_desktop_overlay() {
    ensure_cargo_build
    prepare_backend
    launch_or_show_overlay
}

start_legacy_server() {
    local legacy_python="$ROOT_DIR/.venv/bin/python"
    local legacy_server="$ROOT_DIR/legacy/v1/server.py"

    [[ -x "$legacy_python" ]] || fail "V1 应急回退缺失 .venv；未修改或重建原回退环境。"
    [[ -f "$legacy_server" ]] || fail "缺少 legacy/v1/server.py。"

    log "以应急模式启动 legacy/v1；该入口不属于 V2 自动验收。"
    open "http://127.0.0.1:8000/" \
        || log "无法自动打开浏览器，请手动访问 http://127.0.0.1:8000/。"
    exec "$legacy_python" "$legacy_server"
}

main() {
    validate_port
    initialize_local_files

    if [[ "$PREPARE_ONLY" == "1" ]]; then
        setup_runtime
        log "诊断模式：初始化与校验完成，未获取启动锁、未启动 app.main，也未打开浏览器。"
        exit 0
    fi

    if [[ "$LEGACY_MODE" == "1" ]]; then
        start_legacy_server
    fi

    acquire_launch_lock
    setup_runtime
    if [[ "$DESKTOP_MODE" == "1" ]]; then
        start_desktop_overlay
    else
        start_server
    fi
}

main "$@"
