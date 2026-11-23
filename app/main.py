"""Mac V2 Sidecar entry point with dynamic port binding and safe setup."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import fcntl
import hashlib
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import shutil
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.request

import certifi
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.audio import AudioDeviceError, AudioPipeline, create_audio_backend
from app.audio.device_probe import probe_system_audio
from app.bypass.patch_worker import PatchWorker
from app.bypass.summary_worker import SummaryWorker
from app.config_store import ConfigStore
from app.engines import (
    DEFAULT_QWEN_WS_URL,
    EngineRegistry,
    OpenAICompatibleAdapter,
    QwenRealtimeAdapter,
    UnsupportedLanguagePairError,
    resolve_ws_url,
)
from app.glossary import GlossaryPersistError, GlossaryStore, QUERY_CATEGORIES
from app.session_manager import (
    SUPPORTED_CHANNELS,
    SessionController,
    SessionManager,
)
from app.transcript_exporter import SessionPersistenceManager

SOURCE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SOURCE_ROOT
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
DEFAULT_RUNTIME_PORT_FILE = PROJECT_ROOT / ".agents" / "runtime_port"
SERVICE_ID = "harry-translate-v2"
OVERLAY_CONTROL_HOST = "127.0.0.1"
SUPPORTED_PAGES = ("/", "/en", "/zh", "/split", "/settings", "/hud")
OVERLAY_BINARY_CANDIDATES = (
    "src-tauri/target/debug/harry-translate-mac",
    "src-tauri/target/release/harry-translate-mac",
    "src-tauri/target/release/bundle/macos/HARRY Translate.app/Contents/MacOS/harry-translate-mac",
    "dist/HARRY Translate.app/Contents/MacOS/harry-translate-mac",
)


class PortBindingError(RuntimeError):
    """Raised when the configured port scan cannot reserve a socket."""


class AudioRuntime:
    """Open the microphone only while at least one subtitle client is active."""

    def __init__(self, manager: SessionManager) -> None:
        self.manager = manager
        self.pipeline = AudioPipeline()
        self.backend: Any | None = None
        self._lock = threading.RLock()
        self._running = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _on_audio(self, raw_pcm: bytes) -> None:
        for frame in self.pipeline.slice_pcm_frames(raw_pcm):
            frame = self.pipeline.process_frame(
                frame,
                enable_agc=self.manager.audio_source != "system",
            )
            for channel in SUPPORTED_CHANNELS:
                self.manager.route_audio_frame(channel, frame)

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return True
            backend = create_audio_backend("auto")
            try:
                backend.open_stream(callback=self._on_audio)
            except Exception:
                backend.close()
                raise
            self.backend = backend
            self._running = True
            return True

    def stop(self) -> None:
        with self._lock:
            backend, self.backend = self.backend, None
            self._running = False
        if backend is not None:
            backend.close()


def bind_server_socket(
    host: str = "127.0.0.1",
    preferred_port: int = 8000,
    max_scan: int = 21,
) -> socket.socket:
    """Reserve a socket immediately to avoid probe-then-bind races."""

    if not 0 <= preferred_port <= 65_535:
        raise ValueError("preferred_port must be between 0 and 65535")
    if max_scan <= 0:
        raise ValueError("max_scan must be positive")

    candidate_ports = [0] if preferred_port == 0 else range(
        preferred_port,
        min(65_536, preferred_port + max_scan),
    )
    for port in candidate_ports:
        candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            candidate.bind((host, port))
            return candidate
        except OSError:
            candidate.close()
    raise PortBindingError(
        f"无法绑定 {host}:{preferred_port}"
        + ("" if preferred_port == 0 else f"..{preferred_port + max_scan - 1}")
    )


def write_runtime_port(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(f"{port}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def read_runtime_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@contextmanager
def runtime_state_file_lock(path: Path) -> Any:
    """Serialize runtime-state ownership checks across Sidecar processes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _runtime_state_pid(state: dict[str, Any]) -> int | None:
    try:
        return int(state.get("pid"))
    except (TypeError, ValueError):
        return None


def _runtime_state_port(state: dict[str, Any]) -> int | None:
    try:
        return int(state.get("port"))
    except (TypeError, ValueError):
        return None


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _runtime_state_matches(
    state: dict[str, Any],
    *,
    pid: int,
    port: int,
    instance_token: str,
) -> bool:
    return (
        _runtime_state_pid(state) == pid
        and _runtime_state_port(state) == port
        and state.get("instance_token") == instance_token
    )


def _write_runtime_state_unlocked(path: Path, payload: dict[str, Any]) -> None:
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def write_runtime_state(
    path: Path,
    *,
    pid: int,
    port: int,
    project_root: Path,
    frontend_fingerprint: str,
    overlay_control_url: str | None,
    instance_token: str,
    require_matching: bool = False,
) -> bool:
    """Atomically publish state after a lock-protected ownership check."""

    token = str(instance_token).strip()
    if not token:
        raise ValueError("instance_token_required")
    payload = {
        "service": SERVICE_ID,
        "pid": pid,
        "port": port,
        "project_root": str(project_root.resolve()),
        "frontend_fingerprint": frontend_fingerprint,
        "overlay_control_url": overlay_control_url,
        "instance_token": token,
    }
    with runtime_state_file_lock(path):
        existing = read_runtime_state(path)
        if existing:
            if require_matching:
                if not _runtime_state_matches(
                    existing,
                    pid=pid,
                    port=port,
                    instance_token=token,
                ):
                    return False
            else:
                existing_pid = _runtime_state_pid(existing)
                if (
                    existing_pid is not None
                    and existing_pid != pid
                    and _pid_is_alive(existing_pid)
                ):
                    return False
        _write_runtime_state_unlocked(path, payload)
    return True


def clear_runtime_state(
    path: Path,
    *,
    expected_pid: int,
    expected_port: int,
    expected_instance_token: str,
) -> bool:
    """Delete runtime state only when all ownership fields match under lock."""

    with runtime_state_file_lock(path):
        if not path.is_file():
            return True
        state = read_runtime_state(path)
        if not _runtime_state_matches(
            state,
            pid=expected_pid,
            port=expected_port,
            instance_token=expected_instance_token,
        ):
            return False
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return False
    return True


def clear_runtime_port(path: Path, expected_port: int | None = None) -> None:
    try:
        if not path.is_file():
            return
        if expected_port is not None:
            current = path.read_text(encoding="utf-8").strip()
            if current != str(expected_port):
                return
        path.unlink(missing_ok=True)
    except OSError:
        pass


def frontend_fingerprint(frontend_dist: Path) -> str:
    """Return a short content fingerprint for the active frontend build."""

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
    return digest.hexdigest()[:16]


def normalize_overlay_control_url(value: Any) -> str:
    """Accept only the exact loopback HTTP origin used by the native control server."""

    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("control_url_required")
    parsed = urlparse(candidate)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("control_url_invalid_port") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != OVERLAY_CONTROL_HOST
        or port is None
        or not 1 <= port <= 65_535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("control_url_must_be_loopback_origin")
    return f"http://{OVERLAY_CONTROL_HOST}:{port}"


def overlay_binary_path(project_root: Path) -> Path | None:
    """Resolve only project-owned precompiled Tauri binaries."""

    root = project_root.resolve()
    for relative in OVERLAY_BINARY_CANDIDATES:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            continue
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _overlay_control_request(
    control_url: str,
    action: str,
    *,
    method: str = "POST",
    timeout: float = 0.75,
) -> bool:
    normalized = normalize_overlay_control_url(control_url)
    if action not in {"health", "show", "hide", "shutdown"}:
        raise ValueError("unsupported_overlay_action")
    request = urllib.request.Request(
        f"{normalized}/{action}",
        data=b"" if method == "POST" else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read(1024)
            return 200 <= int(response.status) < 300
    except Exception:
        return False


def _overlay_process_running(application: FastAPI) -> bool:
    with application.state.overlay_process_lock:
        process = application.state.overlay_process
        if process is None:
            return False
        if process.poll() is None:
            return True
        application.state.overlay_process = None
        return False


def _stop_overlay_process(application: FastAPI) -> None:
    control_url = getattr(application.state, "overlay_control_url", None)
    shutdown_requested = bool(
        control_url
        and _overlay_control_request(control_url, "shutdown", timeout=0.75)
    )
    with application.state.overlay_process_lock:
        process = application.state.overlay_process
        application.state.overlay_process = None

    deadline = time.monotonic() + 1.5
    while shutdown_requested and time.monotonic() < deadline:
        if process is not None:
            if process.poll() is not None:
                return
        elif not _overlay_control_request(
            control_url,
            "health",
            method="GET",
            timeout=0.2,
        ):
            return
        time.sleep(0.05)

    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1.5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=0.5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _safe_endpoint_label(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if hostname.endswith(".maas.aliyuncs.com"):
        suffix = ".".join(hostname.split(".")[1:])
        safe_host = f"<workspace>.{suffix}"
    else:
        safe_host = hostname
    query = "?model=qwen3.8-livetranslate-flash-realtime"
    return f"{parsed.scheme}://{safe_host}{parsed.path}{query}"


def probe_provider_endpoint(
    *,
    provider: str,
    source_lang: str,
    target_lang: str,
    api_key: str,
    workspace_id: str,
    configured_url: str,
    model: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Perform a bounded TLS reachability check without starting a translation."""

    if provider != "qwen":
        return {
            "ok": False,
            "provider": provider,
            "routed_endpoint": "",
            "latency_ms": None,
            "error_code": "provider_probe_not_implemented",
        }
    try:
        url = resolve_ws_url(
            configured_url,
            api_key,
            model=model,
            workspace_id=workspace_id,
        )
        host = urlparse(url).hostname
        if not host:
            raise ValueError("endpoint_host_missing")
        started = time.perf_counter()
        context = ssl.create_default_context(cafile=certifi.where())
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                tls.getpeercert()
        latency_ms = round((time.perf_counter() - started) * 1_000, 2)
        return {
            "ok": True,
            "provider": provider,
            "routed_endpoint": _safe_endpoint_label(url),
            "latency_ms": latency_ms,
            "error_code": None,
            "source_lang": source_lang,
            "target_lang": target_lang,
        }
    except Exception as error:
        return {
            "ok": False,
            "provider": provider,
            "routed_endpoint": "",
            "latency_ms": None,
            "error_code": getattr(error, "code", None) or "tls_probe_failed",
        }


def _health_audio() -> tuple[str, list[dict[str, Any]], dict[str, str] | None]:
    backend = None
    try:
        backend = create_audio_backend("auto")
        devices = [asdict(device) for device in backend.list_input_devices()]
        return backend.name, devices, None
    except AudioDeviceError as error:
        return "unavailable", [], error.as_dict()
    except Exception as error:
        return "unavailable", [], {
            "code": "audio_probe_failed",
            "message": str(error),
            "hint": "检查 macOS 麦克风权限与音频设备后重试。",
        }
    finally:
        if backend is not None:
            backend.close()


def _bootstrap_runtime_assets(root: Path) -> None:
    """Copy only the selected public packs into the writable app data root."""

    source_bank = BUNDLE_ROOT / "glossary_bank"
    target_bank = root / "glossary_bank"
    if source_bank.is_dir() and not target_bank.is_dir():
        target_bank.mkdir(parents=True, exist_ok=True)
        for name in ("00_base_emergency.json", "01_curated_everyday.json"):
            source = source_bank / name
            if source.is_file():
                shutil.copy2(source, target_bank / name)
    config_path = root / "config" / "glossary.json"
    if not config_path.is_file():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_version": "1.0",
            "description": "HARRY Translate runtime glossary",
            "asr_fixes": {},
            "terms_zh_to_en": {},
            "terms_en_to_zh": {},
            "_enabled_packs": ["00_base_emergency", "01_curated_everyday"],
        }
        temp_path = config_path.with_name(f".{config_path.name}.bootstrap.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, config_path)


def create_app(
    project_root: Path | str = PROJECT_ROOT,
    *,
    enable_audio_capture: bool = True,
) -> FastAPI:
    root = Path(project_root).resolve()
    if getattr(sys, "frozen", False):
        _bootstrap_runtime_assets(root)
    frontend_dist = root / "frontend" / "dist"
    if not frontend_dist.is_dir():
        frontend_dist = BUNDLE_ROOT / "frontend" / "dist"
    frontend_digest = frontend_fingerprint(frontend_dist)
    overlay_path = overlay_binary_path(root)

    def store() -> ConfigStore:
        if application.state.config_store is None:
            application.state.config_store = ConfigStore(root)
        return application.state.config_store

    config_bootstrap = ConfigStore(root)
    initial_settings = config_bootstrap.load_settings(create_if_missing=True)
    glossary_store = GlossaryStore(
        root,
        wire_budget_bytes=int(
            initial_settings.get("glossary_wire_budget_bytes", 4096)
        ),
        glossary_enabled=bool(initial_settings.get("glossary_enabled", True)),
    )

    def glossary() -> GlossaryStore:
        return glossary_store

    patch_worker = PatchWorker(
        initial_settings,
        glossary_engine=glossary_store.engine,
        api_key_provider=lambda: store().get_api_key(),
    )
    summary_worker = SummaryWorker(
        initial_settings,
        api_key_provider=lambda: store().get_api_key(),
    )
    manager = SessionManager(
        needs_setup=lambda: store().needs_setup,
        glossary_engine=glossary_store.engine,
        patch_worker=patch_worker,
        summary_worker=summary_worker,
        audio_source=str(initial_settings.get("audio_input") or "microphone"),
    )
    session_persistence = SessionPersistenceManager(root)
    runtime = AudioRuntime(manager) if enable_audio_capture else None
    catalog_path = root / "config" / "provider_catalog.json"
    if not catalog_path.is_file():
        catalog_path = BUNDLE_ROOT / "config" / "provider_catalog.json"
    engine_registry = EngineRegistry(catalog_path)
    active_sockets: set[WebSocket] = set()
    passive_config_sockets: set[WebSocket] = set()
    config_socket_lock = asyncio.Lock()

    async def _close_socket(socket_to_close: WebSocket) -> None:
        try:
            await asyncio.wait_for(
                socket_to_close.close(code=1001),
                timeout=0.25,
            )
        except Exception:
            pass

    async def shutdown_active_sockets() -> None:
        sockets = tuple(active_sockets)
        if not sockets:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *(_close_socket(socket_to_close) for socket_to_close in sockets),
                    return_exceptions=True,
                ),
                timeout=0.75,
            )
        except asyncio.TimeoutError:
            pass

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> Any:
        manager.bind_loop(asyncio.get_running_loop())
        await patch_worker.apply_settings(store().load_settings())
        await manager.apply_summary_settings(
            summary_worker,
            store().load_settings(),
        )
        try:
            yield
        finally:
            await shutdown_active_sockets()
            await summary_worker.stop()
            await patch_worker.stop()
            if runtime is not None:
                await asyncio.to_thread(runtime.stop)
            await asyncio.to_thread(_stop_overlay_process, application)
            glossary().stop_background_load()
            await manager.stop_all()

    application = FastAPI(
        title="HARRY Translate V2 Mac",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=(
            r"^(tauri://localhost|https?://(tauri\.localhost|127\.0\.0\.1|localhost)(:\d+)?)$"
        ),
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    application.state.project_root = root
    application.state.port = 0
    application.state.request_shutdown = None
    application.state.config_store = config_bootstrap
    application.state.glossary_store = glossary_store
    application.state.glossary_engine = glossary_store.engine
    application.state.engine_registry = engine_registry
    application.state.session_manager = manager
    application.state.patch_worker = patch_worker
    application.state.summary_worker = summary_worker
    application.state.session_persistence = session_persistence
    application.state.audio_runtime = runtime
    application.state.frontend_dist = frontend_dist
    application.state.frontend_fingerprint = frontend_digest
    application.state.overlay_binary = overlay_path
    application.state.overlay_control_url = None
    application.state.overlay_visible = False
    application.state.overlay_process = None
    application.state.overlay_process_lock = threading.RLock()
    application.state.overlay_launch_lock = asyncio.Lock()
    application.state.runtime_state_path = None
    application.state.runtime_state_lock = threading.RLock()
    application.state.instance_token = None
    application.state.active_sockets = active_sockets
    application.state.passive_config_sockets = passive_config_sockets
    application.state.shutdown_active_sockets = shutdown_active_sockets
    lifecycle_lock = asyncio.Lock()
    patch_worker.bind_publisher(manager.publish_patch_event)
    summary_worker.bind_publisher(manager.publish_summary_event)

    def qwen_factory(
        *,
        source_lang: str,
        target_lang: str,
        model: str,
        event_callback: Any,
        window_id: str = "",
        channel_id: str = "",
        **_kwargs: Any,
    ) -> QwenRealtimeAdapter:
        settings = store().load_settings()
        configured_url = store().get_env_value("QWEN_REALTIME_WS_URL")
        configured_url = configured_url or DEFAULT_QWEN_WS_URL
        workspace_id = (
            str(settings.get("workspace_id", "")).strip()
            or store().get_env_value("WORKSPACE_ID")
            or store().get_env_value("DASHSCOPE_WORKSPACE_ID")
        )
        return QwenRealtimeAdapter(
            api_key=store().get_api_key(),
            source_lang=source_lang,
            target_lang=target_lang,
            workspace_id=workspace_id,
            ws_url=configured_url,
            model=model,
            window_id=window_id,
            channel_id=channel_id,
            event_callback=event_callback,
            corpus_phrases=glossary().get_budgeted_corpus_phrases(
                source_lang=source_lang,
                target_lang=target_lang,
            ),
            hotwords=[],
        )

    def openai_factory(*, event_callback: Any, **_kwargs: Any) -> OpenAICompatibleAdapter:
        return OpenAICompatibleAdapter(event_callback=event_callback)

    engine_registry.register("qwen", qwen_factory)
    engine_registry.register("openai_compatible", openai_factory)

    def engine_factory(channel: str) -> Any:
        settings = store().load_settings()
        provider = str(settings.get("active_provider") or settings.get("provider") or "qwen")
        target_lang = (
            "en"
            if channel == "en"
            else "zh"
            if channel == "zh"
            else str(settings.get("target_lang") or "zh")
        )
        source_lang = str(settings.get("source_lang") or "auto")
        model = str(settings.get("active_model") or settings.get("model"))
        generation = manager.channel_generation(channel)

        def on_engine_event(event: dict[str, Any]) -> None:
            manager.publish_from_thread(channel, event, generation=generation)

        return engine_registry.create_engine(
            provider,
            source_lang=source_lang,
            target_lang=target_lang,
            model=model,
            window_id=channel,
            channel_id=channel,
            event_callback=on_engine_event,
        )

    for channel in SUPPORTED_CHANNELS:
        controller = SessionController(
            channel=channel,
            manager=manager,
            engine_factory=lambda channel_name=channel: engine_factory(channel_name),
        )
        manager.register_controller(channel, controller)
    manager.set_channel_languages(
        "main",
        str(initial_settings.get("source_lang") or "auto"),
        str(initial_settings.get("target_lang") or "zh"),
    )

    def persist_runtime_state() -> bool:
        state_path = getattr(application.state, "runtime_state_path", None)
        instance_token = getattr(application.state, "instance_token", None)
        if state_path is None or not instance_token:
            return False
        path = Path(state_path)
        with application.state.runtime_state_lock:
            return write_runtime_state(
                path,
                pid=os.getpid(),
                port=int(application.state.port),
                project_root=root,
                frontend_fingerprint=frontend_digest,
                overlay_control_url=application.state.overlay_control_url,
                instance_token=str(instance_token),
                require_matching=True,
            )

    @application.get("/api/health")
    def health(fast: bool = False) -> dict[str, Any]:
        config = store()
        if fast:
            audio_backend, devices, audio_error = "skipped", [], None
        else:
            audio_backend, devices, audio_error = _health_audio()
        engine_states = {
            channel: manager.current_state_event(channel) for channel in SUPPORTED_CHANNELS
        }
        return {
            "status": "ok",
            "service": SERVICE_ID,
            "pid": os.getpid(),
            "instance_token": getattr(application.state, "instance_token", None) or "",
            "project_root": str(root),
            "frontend_fingerprint": frontend_digest,
            "fast": fast,
            "port": int(application.state.port),
            "python_version": sys.version.split()[0],
            "audio_backend": audio_backend,
            "audio_devices": devices,
            "audio_error": audio_error,
            "env_exists": config.env_path.is_file(),
            "needs_setup": config.needs_setup,
            "key_mask": config.key_mask,
            "engine_states": engine_states,
            "permission_hint": (
                "请在设置页填写有效的 DASHSCOPE_API_KEY。"
                if config.needs_setup
                else ""
            ),
        }

    @application.post("/api/overlay/register")
    async def overlay_register(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        try:
            control_url = normalize_overlay_control_url(payload.get("control_url"))
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        application.state.overlay_control_url = control_url
        application.state.overlay_visible = True
        await asyncio.to_thread(persist_runtime_state)
        return JSONResponse(
            content={
                "ok": True,
                "registered": True,
                "control_url": control_url,
                "visible": True,
            }
        )

    @application.get("/api/overlay/status")
    async def overlay_status() -> dict[str, Any]:
        process_running = _overlay_process_running(application)
        control_url = application.state.overlay_control_url
        control_healthy = False
        if control_url:
            try:
                control_healthy = await asyncio.wait_for(
                    asyncio.to_thread(
                        _overlay_control_request,
                        control_url,
                        "health",
                        method="GET",
                        timeout=0.2,
                    ),
                    timeout=0.3,
                )
            except asyncio.TimeoutError:
                control_healthy = False
        if (
            control_url
            and not control_healthy
            and not process_running
            and application.state.overlay_control_url == control_url
        ):
            application.state.overlay_control_url = None
            application.state.overlay_visible = False
            await asyncio.to_thread(persist_runtime_state)
        current_control_url = application.state.overlay_control_url
        control_running = bool(
            control_healthy and current_control_url == control_url
        )
        return {
            "ok": True,
            "service": SERVICE_ID,
            "running": bool(process_running or control_running),
            "visible": bool(application.state.overlay_visible),
            "registered": bool(current_control_url),
            "control_url": current_control_url or "",
            "binary_available": overlay_path is not None,
            "fallback_url": "/hud?fallback=browser",
        }

    @application.post("/api/overlay/launch")
    async def overlay_launch() -> JSONResponse:
        async with application.state.overlay_launch_lock:
            control_url = application.state.overlay_control_url
            if control_url and await asyncio.to_thread(
                _overlay_control_request,
                control_url,
                "health",
                method="GET",
                timeout=0.35,
            ):
                shown = await asyncio.to_thread(
                    _overlay_control_request,
                    control_url,
                    "show",
                )
                application.state.overlay_visible = shown
                return JSONResponse(
                    status_code=200 if shown else 502,
                    content={
                        "ok": shown,
                        "action": "shown" if shown else "control_failed",
                        "running": True,
                        "visible": shown,
                        "control_url": control_url,
                    },
                )

            if control_url:
                application.state.overlay_control_url = None
                application.state.overlay_visible = False
                await asyncio.to_thread(persist_runtime_state)

            if _overlay_process_running(application):
                for _attempt in range(10):
                    await asyncio.sleep(0.05)
                    registered_url = application.state.overlay_control_url
                    if registered_url and await asyncio.to_thread(
                        _overlay_control_request,
                        registered_url,
                        "health",
                        method="GET",
                        timeout=0.2,
                    ):
                        shown = await asyncio.to_thread(
                            _overlay_control_request,
                            registered_url,
                            "show",
                        )
                        application.state.overlay_visible = shown
                        return JSONResponse(
                            content={
                                "ok": shown,
                                "action": "shown" if shown else "starting",
                                "running": True,
                                "visible": shown,
                                "control_url": registered_url,
                            }
                        )
                return JSONResponse(
                    content={
                        "ok": True,
                        "action": "starting",
                        "running": True,
                        "visible": False,
                        "control_url": "",
                    }
                )

            if overlay_path is None:
                return JSONResponse(
                    content={
                        "ok": True,
                        "action": "browser_fallback",
                        "running": False,
                        "visible": False,
                        "binary_available": False,
                        "fallback_url": "/hud?fallback=browser",
                    }
                )

            actual_port = int(application.state.port)
            if not 1 <= actual_port <= 65_535:
                return JSONResponse(
                    status_code=409,
                    content={"error": "backend_port_unavailable"},
                )
            environment = os.environ.copy()
            environment["HARRY_ATTACH_BACKEND_URL"] = (
                f"http://{OVERLAY_CONTROL_HOST}:{actual_port}"
            )
            environment["HARRY_OWNER_PID"] = str(os.getpid())
            instance_token = getattr(application.state, "instance_token", None)
            if instance_token:
                environment["HARRY_INSTANCE_TOKEN"] = str(instance_token)
            environment["HARRY_NO_OPEN_SETTINGS"] = "1"
            try:
                process = subprocess.Popen(
                    [str(overlay_path)],
                    cwd=str(root),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                return JSONResponse(
                    status_code=500,
                    content={"error": "overlay_launch_failed"},
                )
            with application.state.overlay_process_lock:
                application.state.overlay_process = process
            return JSONResponse(
                content={
                    "ok": True,
                    "action": "launched",
                    "running": True,
                    "visible": False,
                    "binary_available": True,
                }
            )

    @application.post("/api/overlay/hide")
    async def overlay_hide() -> JSONResponse:
        control_url = application.state.overlay_control_url
        if not control_url:
            return JSONResponse(
                content={
                    "ok": True,
                    "action": "not_running",
                    "running": False,
                    "visible": False,
                }
            )
        hidden = await asyncio.to_thread(
            _overlay_control_request,
            control_url,
            "hide",
        )
        if hidden:
            application.state.overlay_visible = False
        return JSONResponse(
            status_code=200 if hidden else 502,
            content={
                "ok": hidden,
                "action": "hidden" if hidden else "control_failed",
                "running": True,
                "visible": False if hidden else application.state.overlay_visible,
                "control_url": control_url,
            },
        )

    @application.get("/api/audio/devices")
    def audio_devices() -> dict[str, Any]:
        probe = probe_system_audio()
        return {
            "backend": probe.backend,
            "status": probe.status,
            "loopback_available": probe.loopback_available,
            "loopback_devices": probe.loopback_devices,
            "input_devices": probe.input_devices,
            "hint": probe.hint,
        }

    @application.post("/api/audio/input")
    async def select_audio_input(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:
            return {"ok": False, "status": "invalid_json"}
        mode = payload.get("mode") if isinstance(payload, dict) else None
        if mode not in {"microphone", "system", "both"}:
            return {"ok": False, "status": "invalid_mode"}
        probe = await asyncio.to_thread(probe_system_audio)
        if mode in {"system", "both"} and probe.status != "available":
            settings = store().update_settings({"audio_input": "microphone"})
            manager.set_audio_source("microphone")
            await _broadcast_config_update(settings)
            return {
                "ok": False,
                "status": "loopback_missing",
                "channel_b_status": "loopback_missing",
                "system_audio_idle": True,
                "hint": probe.hint,
                "settings": settings,
            }
        settings = store().update_settings({"audio_input": mode})
        manager.set_audio_source(mode)
        await _broadcast_config_update(settings)
        return {
            "ok": True,
            "status": probe.status,
            "channel_b_status": "available",
            "system_audio_idle": False,
            "hint": probe.hint,
            "settings": settings,
        }

    @application.get("/api/config")
    def get_config() -> dict[str, Any]:
        payload = store().public_config()
        payload["provider_catalog"] = engine_registry.get_catalog()
        payload["engine_states"] = {
            channel: manager.current_state_event(channel) for channel in SUPPORTED_CHANNELS
        }
        return payload

    @application.get("/api/summary")
    def get_summary() -> dict[str, Any]:
        if summary_worker.settings.enabled is not True:
            return {
                "enabled": False,
                "pinned": [],
                "archived": [],
            }
        return summary_worker.snapshot()

    @application.post("/api/summary/refresh")
    async def refresh_summary() -> JSONResponse:
        if summary_worker.settings.enabled is not True:
            return JSONResponse(
                status_code=409,
                content={"error": "summary_disabled"},
            )
        return JSONResponse(content=await summary_worker.refresh())

    @application.post("/api/session/save")
    async def save_session(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"error": "session_payload_required"})
        try:
            result = await asyncio.to_thread(session_persistence.save, payload)
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        return JSONResponse(content=result)

    @application.post("/api/sessions/{session_id}")
    async def save_session_compat(session_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"error": "session_payload_required"})
        payload = {**payload, "id": session_id}
        try:
            result = await asyncio.to_thread(session_persistence.save, payload)
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        return JSONResponse(content=result)

    @application.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> JSONResponse:
        try:
            payload = await asyncio.to_thread(session_persistence.load, session_id)
        except FileNotFoundError:
            return JSONResponse(status_code=404, content={"error": "session_not_found"})
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        return JSONResponse(content=payload)

    @application.get("/api/session/latest")
    async def get_latest_session() -> dict[str, Any]:
        payload = await asyncio.to_thread(session_persistence.latest)
        return {"ok": True, "session": payload}

    @application.get("/api/export")
    async def export_session(session_id: str, format: str = "md") -> Response:
        try:
            result = await asyncio.to_thread(
                session_persistence.export,
                session_id,
                format,
            )
        except FileNotFoundError:
            return JSONResponse(status_code=404, content={"error": "session_not_found"})
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        return Response(
            content=result.content,
            media_type=result.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{result.filename}"',
                "Cache-Control": "no-store",
            },
        )

    def _config_event(settings: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "config_updated",
            "settings": settings,
            "needs_setup": store().needs_setup,
            "key_mask": store().key_mask,
        }

    async def _broadcast_config_update(settings: dict[str, Any]) -> None:
        event = _config_event(settings)
        async with config_socket_lock:
            for config_socket in tuple(passive_config_sockets):
                try:
                    await asyncio.wait_for(
                        config_socket.send_json(event),
                        timeout=0.25,
                    )
                except Exception:
                    passive_config_sockets.discard(config_socket)
                    try:
                        await asyncio.wait_for(config_socket.close(), timeout=0.1)
                    except Exception:
                        pass
        for channel in SUPPORTED_CHANNELS:
            await manager.broadcast_to_channel(channel, event)

    @application.get("/api/glossary/packs")
    def glossary_packs() -> dict[str, Any]:
        return glossary().get_packs()

    @application.post("/api/glossary/packs/{pack_id}/toggle")
    async def toggle_glossary_pack(pack_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        enabled = payload.get("enabled") if isinstance(payload, dict) else None
        if not isinstance(enabled, bool):
            return JSONResponse(status_code=400, content={"error": "enabled_required"})
        try:
            result = await asyncio.to_thread(glossary().toggle_pack, pack_id, enabled)
        except PermissionError:
            return JSONResponse(status_code=403, content={"error": "locked_pack"})
        except KeyError:
            return JSONResponse(status_code=404, content={"error": "pack_not_found"})
        except GlossaryPersistError as error:
            return JSONResponse(
                status_code=500,
                content={"error": "glossary_persist_failed", "detail": str(error)},
            )
        return JSONResponse(content=result)

    @application.get("/api/glossary/terms/active")
    def glossary_terms(
        category: str = "all",
        scope: str = "all",
        pack_id: str = "",
        q: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> Any:
        if category not in {"all", *QUERY_CATEGORIES}:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_category"},
            )
        return glossary().get_active_terms(
            category=category,
            scope=scope,
            pack_id=pack_id,
            query=q,
            limit=limit,
            offset=offset,
        )

    @application.get("/api/glossary/session")
    def glossary_session_list() -> dict[str, Any]:
        items = glossary().list_session_terms()
        return {
            "items": items,
            "count": len(items),
            "quota": glossary().get_quota_status(),
        }

    @application.post("/api/glossary/session/add")
    async def glossary_session_add(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"error": "invalid_json"})

        source = str(payload.get("source") or "").strip()
        target = str(payload.get("target") or "").strip()
        expr = str(payload.get("expr") or "").strip()
        if expr and (not source or not target):
            for separator in ("=>", "->", "=", "：", ":"):
                if separator in expr:
                    left, right = expr.split(separator, 1)
                    source = left.strip()
                    target = right.strip()
                    break

        bucket = str(payload.get("bucket") or "terms_trans")
        lang_key = str(payload.get("lang_key") or payload.get("direction") or "")
        direction = payload.get("direction")

        started = time.perf_counter()
        try:
            entry = glossary().add_session_term(
                source,
                target,
                bucket=bucket,
                lang_key=lang_key,
                direction=str(direction) if direction is not None else None,
            )
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        elapsed_ms = (time.perf_counter() - started) * 1_000

        pushed = await asyncio.to_thread(
            manager.push_glossary_update,
            glossary(),
            bucket=entry.bucket,
            lang_key=entry.lang_key,
        )
        session_pool = getattr(application.state, "session_pool", None)
        if session_pool is not None:
            pushed.extend(
                await asyncio.to_thread(
                    session_pool.push_glossary_update,
                    glossary(),
                    bucket=entry.bucket,
                    lang_key=entry.lang_key,
                )
            )
        return JSONResponse(
            content={
                "ok": True,
                "item": entry.as_item(),
                "session_items": glossary().list_session_terms(),
                "pushed_channels": pushed,
                "elapsed_ms": round(elapsed_ms, 3),
                "quota": glossary().get_quota_status(),
            }
        )

    @application.post("/api/glossary/promote-term")
    async def glossary_promote_term(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"error": "invalid_json"})

        source = str(payload.get("source") or "").strip()
        raw_target = payload.get("target")
        target = str(raw_target).strip() if raw_target is not None else None
        bucket = str(payload.get("bucket") or "terms_trans")
        lang_key = str(payload.get("lang_key") or payload.get("direction") or "")
        direction = payload.get("direction")

        try:
            promoted = await asyncio.to_thread(
                glossary().promote_term,
                source,
                target,
                bucket=bucket,
                lang_key=lang_key,
                direction=str(direction) if direction is not None else None,
            )
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        except KeyError:
            return JSONResponse(status_code=404, content={"error": "session_term_not_found"})
        except GlossaryPersistError as error:
            return JSONResponse(
                status_code=500,
                content={"error": "glossary_persist_failed", "detail": str(error)},
            )
        return JSONResponse(
            content={
                "ok": True,
                "item": promoted.as_item(),
                "session_items": glossary().list_session_terms(),
                "quota": glossary().get_quota_status(),
            }
        )

    @application.post("/api/glossary/session/clear")
    async def glossary_session_clear() -> JSONResponse:
        cleared = glossary().clear_session()
        pushed = await asyncio.to_thread(manager.push_glossary_update, glossary())
        session_pool = getattr(application.state, "session_pool", None)
        if session_pool is not None:
            pushed.extend(
                await asyncio.to_thread(session_pool.push_glossary_update, glossary())
            )
        return JSONResponse(
            content={
                "ok": True,
                "cleared": cleared,
                "session_items": [],
                "pushed_channels": pushed,
                "quota": glossary().get_quota_status(),
            }
        )

    @application.put("/api/config")
    async def put_config(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        patch = payload.get("settings", payload) if isinstance(payload, dict) else None
        if not isinstance(patch, dict):
            return JSONResponse(status_code=400, content={"error": "settings_required"})
        before = store().load_settings()
        provider = str(
            patch.get("active_provider")
            or patch.get("provider")
            or before.get("active_provider")
            or "qwen"
        )
        source_lang = str(patch.get("source_lang") or before.get("source_lang") or "auto")
        target_lang = str(patch.get("target_lang") or before.get("target_lang") or "zh")
        try:
            engine_registry.validate_language_pair(provider, source_lang, target_lang)
        except UnsupportedLanguagePairError as error:
            return JSONResponse(status_code=400, content=error.as_dict())
        try:
            settings = store().update_settings(patch)
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        glossary_store.set_corpus_policy(
            enabled=bool(settings.get("glossary_enabled", True)),
            wire_budget_bytes=int(settings.get("glossary_wire_budget_bytes", 4096)),
        )
        manager.set_audio_source(str(settings.get("audio_input") or "microphone"))
        manager.set_channel_languages(
            "main",
            str(settings.get("source_lang") or "auto"),
            str(settings.get("target_lang") or "zh"),
        )
        await patch_worker.apply_settings(settings)
        await manager.apply_summary_settings(summary_worker, settings)
        runtime_keys = {
            "active_provider",
            "active_model",
            "provider",
            "model",
            "source_lang",
            "target_lang",
            "workspace_id",
        }
        runtime_changed = any(
            before.get(key) != settings.get(key) for key in runtime_keys
        )
        glossary_policy_changed = (
            before.get("glossary_enabled") != settings.get("glossary_enabled")
            or before.get("glossary_wire_budget_bytes")
            != settings.get("glossary_wire_budget_bytes")
        )
        await _broadcast_config_update(settings)
        if runtime_changed and manager.total_active_clients() > 0:
            async with lifecycle_lock:
                channel = manager.active_channel
                controller = manager.controller(channel) if channel else None
                if controller is not None and manager.active_client_count(channel) > 0:
                    await controller.reload_session()
        elif glossary_policy_changed and manager.total_active_clients() > 0:
            await asyncio.to_thread(manager.push_glossary_update, glossary_store)
        public = store().public_config()
        return JSONResponse(
            content={
                **public,
                "provider_catalog": engine_registry.get_catalog(),
            }
        )

    @application.post("/api/session/switch")
    async def switch_session(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        before = store().load_settings()
        provider = str(
            before.get("active_provider") or before.get("provider") or "qwen"
        )
        source_lang = str(payload.get("source_lang") or before.get("source_lang") or "auto")
        target_lang = str(payload.get("target_lang") or before.get("target_lang") or "zh")
        try:
            engine_registry.validate_language_pair(provider, source_lang, target_lang)
        except UnsupportedLanguagePairError as error:
            return JSONResponse(status_code=400, content=error.as_dict())

        requested_audio = payload.get("audio_input", payload.get("audio_source"))
        settings_patch: dict[str, Any] = {
            "source_lang": source_lang,
            "target_lang": target_lang,
        }
        channel_b_status = "idle"
        system_audio_idle = True
        audio_hint = ""
        if requested_audio is not None:
            audio_mode = str(requested_audio).strip()
            if audio_mode not in {"microphone", "system", "both"}:
                return JSONResponse(
                    status_code=400,
                    content={"error": "invalid_audio_input"},
                )
            if audio_mode in {"system", "both"}:
                probe = probe_system_audio()
                audio_hint = probe.hint
                if probe.status != "available":
                    settings_patch["audio_input"] = "microphone"
                    channel_b_status = "loopback_missing"
                    system_audio_idle = True
                else:
                    settings_patch["audio_input"] = audio_mode
                    channel_b_status = "available"
                    system_audio_idle = False
            else:
                settings_patch["audio_input"] = "microphone"
                channel_b_status = "idle"
                system_audio_idle = True

        settings = store().update_settings(settings_patch)
        manager.set_audio_source(
            str(settings.get("audio_input") or "microphone")
        )
        manager.set_channel_languages("main", source_lang, target_lang)
        controller = manager.controller("main")
        if controller is not None and manager.active_client_count("main") > 0:
            async with lifecycle_lock:
                await manager.activate_channel("main")
                await controller.reload_session()
        else:
            await manager.reset_channel_session_async("main")
            await manager.broadcast_to_channel(
                "main",
                {"type": "session_reset", "channel": "main"},
            )
        await _broadcast_config_update(settings)
        return JSONResponse(
            content={
                "ok": True,
                "settings": settings,
                "user_settings": settings,
                "channel_b_status": channel_b_status,
                "system_audio_idle": system_audio_idle,
                "hint": audio_hint,
                "engine_state": manager.current_state_event("main"),
            }
        )

    @application.post("/api/config/secrets")
    async def post_secret(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        api_key = payload.get("api_key", "") if isinstance(payload, dict) else ""
        try:
            key_mask = store().set_api_key(str(api_key))
        except ValueError as error:
            return JSONResponse(status_code=400, content={"error": str(error)})
        workspace_id = (
            str(store().load_settings().get("workspace_id", "")).strip()
            or store().get_env_value("WORKSPACE_ID")
            or store().get_env_value("DASHSCOPE_WORKSPACE_ID")
        )
        for channel in SUPPORTED_CHANNELS:
            controller = manager.controller(channel)
            engine = controller.engine if controller is not None else None
            if engine is not None:
                await asyncio.to_thread(
                    engine.update_api_key,
                    store().get_api_key(),
                    workspace_id,
                )
        await _broadcast_config_update(store().load_settings())
        return JSONResponse(
            content={
                "key_mask": key_mask,
                "api_key_masked": key_mask,
                "needs_setup": store().needs_setup,
            }
        )

    @application.get("/api/providers/catalog")
    def provider_catalog() -> dict[str, Any]:
        return engine_registry.get_catalog()

    @application.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @application.post("/api/system/shutdown")
    def system_shutdown() -> dict[str, Any]:
        request_shutdown = getattr(application.state, "request_shutdown", None)
        if callable(request_shutdown):
            request_shutdown()
        return {"ok": True, "status": "shutting_down"}

    @application.post("/api/providers/test")
    async def provider_test(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        if not isinstance(payload, dict):
            return JSONResponse(status_code=400, content={"error": "invalid_json"})
        settings = store().load_settings()
        provider = str(payload.get("provider") or settings["active_provider"])
        source_lang = str(payload.get("source_lang") or settings["source_lang"])
        target_lang = str(payload.get("target_lang") or settings["target_lang"])
        try:
            engine_registry.validate_language_pair(provider, source_lang, target_lang)
        except UnsupportedLanguagePairError as error:
            return JSONResponse(status_code=400, content=error.as_dict())
        if store().needs_setup:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "provider": provider,
                    "routed_endpoint": "",
                    "latency_ms": None,
                    "error_code": "missing_api_key",
                },
            )
        result = await asyncio.to_thread(
            probe_provider_endpoint,
            provider=provider,
            source_lang=source_lang,
            target_lang=target_lang,
            api_key=store().get_api_key(),
            workspace_id=str(payload.get("workspace_id") or settings.get("workspace_id") or ""),
            configured_url=store().get_env_value("QWEN_REALTIME_WS_URL"),
            model=str(settings.get("active_model") or settings.get("model")),
        )
        return JSONResponse(content=result)

    @application.websocket("/ws/config")
    async def websocket_config(websocket: WebSocket) -> None:
        await websocket.accept()
        active_sockets.add(websocket)
        passive_config_sockets.add(websocket)
        try:
            await websocket.send_json(_config_event(store().load_settings()))
            while True:
                message = await websocket.receive_text()
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            passive_config_sockets.discard(websocket)
            active_sockets.discard(websocket)

    @application.websocket("/ws/{channel}")
    async def websocket_channel(websocket: WebSocket, channel: str) -> None:
        resolved_channel = "main" if channel == "subtitles" else channel
        if resolved_channel not in SUPPORTED_CHANNELS:
            await websocket.close(code=1003)
            return

        await websocket.accept()
        active_sockets.add(websocket)
        controller = manager.controller(resolved_channel)
        try:
            async with lifecycle_lock:
                await manager.activate_channel(resolved_channel)
                manager.add_client(resolved_channel, websocket)
                await websocket.send_json(manager.current_state_event(resolved_channel))
                if not manager.needs_setup() and controller is not None:
                    if runtime is not None and not runtime.is_running:
                        try:
                            await asyncio.to_thread(runtime.start)
                        except AudioDeviceError as error:
                            await websocket.send_json(
                                {
                                    "type": "audio_state",
                                    "state": "error",
                                    "reason": error.code,
                                    "hint": error.hint,
                                }
                            )
                    started = await controller.start()
                    if not started:
                        await websocket.send_json(manager.current_state_event(resolved_channel))

            while True:
                message = await websocket.receive_text()
                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            async with lifecycle_lock:
                manager.disconnect_client(resolved_channel, websocket)
                if controller is not None:
                    await controller.stop_if_idle()
                await manager.deactivate_channel_if_idle(resolved_channel)
                if manager.total_active_clients() == 0 and runtime is not None:
                    await asyncio.to_thread(runtime.stop)
            active_sockets.discard(websocket)

    def page_handler(page_path: Path) -> Any:
        async def page() -> Any:
            if not page_path.is_file():
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "frontend_build_missing",
                        "message": "请先在 frontend 目录运行 npm ci && npm run build。",
                    },
                )
            return FileResponse(
                page_path,
                media_type="text/html; charset=utf-8",
                headers={
                    "Cache-Control": "no-store, max-age=0",
                    "Pragma": "no-cache",
                },
            )

        return page

    assets_path = frontend_dist / "assets"
    if assets_path.is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=assets_path),
            name="frontend-assets",
        )
    index_path = frontend_dist / "index.html"
    for route in SUPPORTED_PAGES:
        application.add_api_route(
            route,
            page_handler(index_path),
            methods=["GET"],
            include_in_schema=False,
        )

    return application


_LAZY_APP: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Create the ASGI app only when an importer explicitly requests it."""

    global _LAZY_APP
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if _LAZY_APP is None:
        _LAZY_APP = create_app()
    return _LAZY_APP


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HARRY Translate V2 Mac Sidecar")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Preferred port; use 0 for an OS-assigned sidecar port",
    )
    parser.add_argument("--max-scan", type=int, default=21)
    parser.add_argument("--runtime-port-file", type=Path, default=DEFAULT_RUNTIME_PORT_FILE)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--watch-stdin", action="store_true")
    parser.add_argument("--no-audio-capture", action="store_true")
    return parser.parse_args(argv)


def _watch_stdin(
    server: uvicorn.Server,
    request_shutdown: Any | None = None,
) -> None:
    try:
        while sys.stdin.buffer.read(4096):
            pass
    except (OSError, ValueError, AttributeError):
        pass
    if callable(request_shutdown):
        request_shutdown()
    else:
        server.should_exit = True


async def run_server(arguments: argparse.Namespace) -> int:
    runtime_port_file = Path(arguments.runtime_port_file).resolve()
    runtime_state_file = runtime_port_file.with_name("runtime_state.json")
    root = Path(arguments.project_root).resolve()

    try:
        bound_socket = bind_server_socket(
            host=arguments.host,
            preferred_port=arguments.port,
            max_scan=arguments.max_scan,
        )
    except (OSError, PortBindingError, ValueError) as error:
        print(f"无法启动服务: {error}", file=sys.stderr, flush=True)
        return 2

    actual_port = int(bound_socket.getsockname()[1])
    runtime_app = create_app(root, enable_audio_capture=not arguments.no_audio_capture)
    instance_token = uuid.uuid4().hex
    runtime_app.state.port = actual_port
    runtime_app.state.runtime_state_path = runtime_state_file
    runtime_app.state.instance_token = instance_token
    store = ConfigStore(root)
    runtime_app.state.config_store = store

    config = uvicorn.Config(
        runtime_app,
        host=arguments.host,
        port=actual_port,
        log_level="warning",
        access_log=False,
        lifespan="on",
        timeout_graceful_shutdown=1.5,
    )
    server = uvicorn.Server(config)
    event_loop = asyncio.get_running_loop()
    runtime_app.state.event_loop = event_loop
    shutdown_lock = threading.Lock()
    shutdown_started = False

    async def shutdown_runtime() -> None:
        await runtime_app.state.shutdown_active_sockets()
        server.should_exit = True

    def schedule_shutdown() -> None:
        nonlocal shutdown_started
        with shutdown_lock:
            if shutdown_started:
                return
            shutdown_started = True
        asyncio.create_task(shutdown_runtime())

    def request_shutdown() -> None:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if event_loop.is_closed():
            server.should_exit = True
        elif current_loop is event_loop:
            schedule_shutdown()
        else:
            try:
                event_loop.call_soon_threadsafe(schedule_shutdown)
            except RuntimeError:
                server.should_exit = True

    runtime_app.state.request_shutdown = request_shutdown
    if arguments.watch_stdin:
        threading.Thread(
            target=_watch_stdin,
            args=(server, request_shutdown),
            daemon=True,
        ).start()

    def cleanup_runtime() -> None:
        state_cleared = clear_runtime_state(
            runtime_state_file,
            expected_pid=os.getpid(),
            expected_port=actual_port,
            expected_instance_token=instance_token,
        )
        if state_cleared:
            clear_runtime_port(runtime_port_file, actual_port)
        _stop_overlay_process(runtime_app)

    atexit.register(cleanup_runtime)
    previous_signal_handlers: dict[signal.Signals, Any] = {}

    def handle_signal(_signum: int, _frame: Any) -> None:
        request_shutdown()

    try:
        serve_task = asyncio.create_task(server.serve(sockets=[bound_socket]))
        startup_deadline = asyncio.get_running_loop().time() + 10
        while not server.started:
            if serve_task.done():
                await serve_task
                return 1
            if asyncio.get_running_loop().time() >= startup_deadline:
                request_shutdown()
                await serve_task
                print("Sidecar 启动超时", file=sys.stderr, flush=True)
                return 1
            await asyncio.sleep(0.01)
        if threading.current_thread() is threading.main_thread():
            for signal_name in (signal.SIGINT, signal.SIGTERM):
                previous_signal_handlers[signal_name] = signal.getsignal(signal_name)
                signal.signal(signal_name, handle_signal)
        state_published = write_runtime_state(
            runtime_state_file,
            pid=os.getpid(),
            port=actual_port,
            project_root=root,
            frontend_fingerprint=runtime_app.state.frontend_fingerprint,
            overlay_control_url=runtime_app.state.overlay_control_url,
            instance_token=instance_token,
        )
        if not state_published:
            print(
                "runtime_state.json 仍属于另一个存活实例，拒绝覆盖。",
                file=sys.stderr,
                flush=True,
            )
            request_shutdown()
            await serve_task
            return 1
        write_runtime_port(runtime_port_file, actual_port)
        ready = {
            "event": "sidecar_ready",
            "type": "sidecar_ready",
            "port": actual_port,
            "pid": os.getpid(),
            "needs_setup": store.needs_setup,
        }
        print(json.dumps(ready, ensure_ascii=False), flush=True)
        await serve_task
    finally:
        await runtime_app.state.shutdown_active_sockets()
        for signal_name, previous_handler in previous_signal_handlers.items():
            signal.signal(signal_name, previous_handler)
        cleanup_runtime()
        atexit.unregister(cleanup_runtime)
        try:
            bound_socket.close()
        except OSError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    return asyncio.run(run_server(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
