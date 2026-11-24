//! Python FastAPI Sidecar lifecycle manager for Apple Silicon macOS.
//!
//! Launch resolution, readiness handshake, and graceful process-tree cleanup
//! follow patterns from MIT `HiMeditator/auto-caption` (`CaptionEngine.ts`)
//! and MIT `phuc-nt/my-translator` (`ExitRequested` cleanup).

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};
use tauri::Emitter;

const HEALTH_TIMEOUT: Duration = Duration::from_secs(10);
const FAST_HEALTH_INTERVAL: Duration = Duration::from_millis(25);
const GRACEFUL_EXIT_TIMEOUT: Duration = Duration::from_millis(1500);
const SIGTERM_EXIT_TIMEOUT: Duration = Duration::from_millis(300);
const ATTACHED_HEALTH_INTERVAL: Duration = Duration::from_millis(500);
const ATTACHED_HEALTH_FAILURE_LIMIT: u32 = 3;
const ATTACH_BACKEND_ENV: &str = "HARRY_ATTACH_BACKEND_URL";
const OWNER_PID_ENV: &str = "HARRY_OWNER_PID";
const INSTANCE_TOKEN_ENV: &str = "HARRY_INSTANCE_TOKEN";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct SidecarEndpoint {
    pub host: String,
    pub port: u16,
    pub base_url: String,
    pub ws_base_url: String,
    pub pid: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct AttachedBackendIdentity {
    pid: u32,
    instance_token: Option<String>,
}

impl SidecarEndpoint {
    pub fn from_port(port: u16, pid: Option<u32>) -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port,
            base_url: format!("http://127.0.0.1:{port}"),
            ws_base_url: format!("ws://127.0.0.1:{port}"),
            pid,
        }
    }
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
pub struct SidecarReadyEvent {
    pub event: String,
    pub port: u16,
    pub pid: u32,
    #[serde(default)]
    pub needs_setup: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SidecarLaunchSpec {
    pub program: PathBuf,
    pub args: Vec<String>,
    pub cwd: PathBuf,
}

#[derive(Debug, thiserror::Error)]
pub enum SidecarError {
    #[error("failed to bind loopback port: {0}")]
    PortBind(#[from] std::io::Error),
    #[error("failed to locate python runtime or packaged sidecar binary under {0}")]
    BinaryNotFound(PathBuf),
    #[error("failed to spawn sidecar process: {0}")]
    SpawnFailed(String),
    #[error("sidecar exited early with status {0:?}")]
    ExitedEarly(Option<i32>),
    #[error("sidecar did not become healthy within {0:?}")]
    HealthTimeout(Duration),
    #[error("invalid HARRY_ATTACH_BACKEND_URL: {0}")]
    InvalidAttachedBackend(String),
    #[error("attached backend is not healthy: {0}")]
    AttachedBackendUnhealthy(String),
}

pub fn parse_loopback_backend_url(value: &str) -> Result<u16, SidecarError> {
    let raw = value.trim();
    let port_text = raw
        .strip_prefix("http://127.0.0.1:")
        .ok_or_else(|| SidecarError::InvalidAttachedBackend(value.to_string()))?;
    if port_text.is_empty() || !port_text.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(SidecarError::InvalidAttachedBackend(value.to_string()));
    }
    let port = port_text
        .parse::<u16>()
        .map_err(|_| SidecarError::InvalidAttachedBackend(value.to_string()))?;
    if port == 0 {
        return Err(SidecarError::InvalidAttachedBackend(value.to_string()));
    }
    Ok(port)
}

pub fn reserve_free_port() -> Result<u16, SidecarError> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

pub fn parse_sidecar_ready_line(line: &str) -> Option<SidecarReadyEvent> {
    let trimmed = line.trim();
    if !trimmed.starts_with('{') {
        return None;
    }
    let parsed: SidecarReadyEvent = serde_json::from_str(trimmed).ok()?;
    if parsed.event == "sidecar_ready" && parsed.port > 0 {
        Some(parsed)
    } else {
        None
    }
}

pub fn resolve_launch_spec(
    project_root: &Path,
    resource_dir: Option<&Path>,
    port: u16,
    runtime_port_file: &Path,
    is_dev: bool,
) -> Result<SidecarLaunchSpec, SidecarError> {
    let common_args = vec![
        "--host".to_string(),
        "127.0.0.1".to_string(),
        "--port".to_string(),
        port.to_string(),
        "--max-scan".to_string(),
        "1".to_string(),
        "--runtime-port-file".to_string(),
        runtime_port_file.to_string_lossy().into_owned(),
        "--project-root".to_string(),
        project_root.to_string_lossy().into_owned(),
        "--watch-stdin".to_string(),
    ];

    if !is_dev {
        let mut packaged_candidates: Vec<PathBuf> = Vec::new();
        if let Some(dir) = resource_dir {
            packaged_candidates.push(dir.join("binaries").join("harry-sidecar"));
            packaged_candidates.push(dir.join("harry-sidecar"));
        }
        if let Ok(exe) = std::env::current_exe() {
            if let Some(exe_dir) = exe.parent() {
                packaged_candidates.push(exe_dir.join("harry-sidecar"));
                packaged_candidates.push(exe_dir.join("harry-sidecar-aarch64-apple-darwin"));
            }
        }
        for candidate in packaged_candidates {
            if candidate.is_file() {
                return Ok(SidecarLaunchSpec {
                    program: candidate,
                    args: common_args,
                    cwd: project_root.to_path_buf(),
                });
            }
        }
    }

    let python_candidates = [
        project_root.join(".venv-v2").join("bin").join("python"),
        project_root.join(".venv").join("bin").join("python"),
        PathBuf::from("python3"),
    ];
    let program = python_candidates
        .into_iter()
        .find(|path| path.as_os_str() == "python3" || path.is_file())
        .ok_or_else(|| SidecarError::BinaryNotFound(project_root.to_path_buf()))?;

    let mut args = vec!["-m".to_string(), "app.main".to_string()];
    args.extend(common_args);
    Ok(SidecarLaunchSpec {
        program,
        args,
        cwd: project_root.to_path_buf(),
    })
}

pub fn check_health_once(port: u16, timeout: Duration) -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, timeout) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let request = format!(
        "GET /api/health?fast=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = [0u8; 64];
    match stream.read(&mut response) {
        Ok(n) if n > 0 => {
            let head = String::from_utf8_lossy(&response[..n]);
            head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
        }
        _ => false,
    }
}

fn probe_attached_backend(port: u16, timeout: Duration) -> Option<AttachedBackendIdentity> {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut stream = TcpStream::connect_timeout(&addr, timeout).ok()?;
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let request = format!(
        "GET /api/health?fast=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).ok()?;
    let head_end = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")?
        + 4;
    let head = String::from_utf8_lossy(&response[..head_end]);
    if !head.starts_with("HTTP/1.1 200") && !head.starts_with("HTTP/1.0 200") {
        return None;
    }
    let payload: serde_json::Value = serde_json::from_slice(&response[head_end..]).ok()?;
    if payload.get("service").and_then(serde_json::Value::as_str) != Some("harry-translate-v2")
        || payload.get("fast").and_then(serde_json::Value::as_bool) != Some(true)
    {
        return None;
    }
    let pid = payload
        .get("pid")
        .and_then(serde_json::Value::as_u64)
        .and_then(|pid| u32::try_from(pid).ok())
        .filter(|pid| *pid > 0)?;
    let instance_token = payload
        .get("instance_token")
        .and_then(serde_json::Value::as_str)
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(ToOwned::to_owned);
    Some(AttachedBackendIdentity {
        pid,
        instance_token,
    })
}

fn pid_is_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(unix)]
    {
        let result = unsafe { libc::kill(pid as libc::pid_t, 0) };
        if result == 0 {
            return true;
        }
        return std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM);
    }
    #[cfg(not(unix))]
    {
        true
    }
}

fn owner_pid_from_env() -> Option<u32> {
    std::env::var(OWNER_PID_ENV)
        .ok()
        .and_then(|value| value.trim().parse::<u32>().ok())
        .filter(|pid| *pid > 0)
}

fn instance_token_from_env() -> Option<String> {
    std::env::var(INSTANCE_TOKEN_ENV)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|token| !token.is_empty())
}

fn monitor_attached_backend(
    app_handle: tauri::AppHandle,
    endpoint: SidecarEndpoint,
    expected_identity: AttachedBackendIdentity,
    owner_pid: Option<u32>,
    shutting_down: Arc<AtomicBool>,
) {
    thread::spawn(move || {
        let mut consecutive_failures = 0_u32;
        loop {
            thread::sleep(ATTACHED_HEALTH_INTERVAL);
            if shutting_down.load(Ordering::SeqCst) {
                return;
            }
            let owner_alive = owner_pid.is_none_or(pid_is_alive);
            let observed_identity =
                probe_attached_backend(endpoint.port, Duration::from_millis(350));
            let identity_matches = observed_identity.as_ref().is_some_and(|identity| {
                identity.pid == expected_identity.pid
                    && expected_identity
                        .instance_token
                        .as_ref()
                        .is_none_or(|expected_token| {
                            identity.instance_token.as_deref() == Some(expected_token.as_str())
                        })
            });
            if owner_alive && identity_matches {
                consecutive_failures = 0;
                continue;
            }
            consecutive_failures = consecutive_failures.saturating_add(1);
            if consecutive_failures < ATTACHED_HEALTH_FAILURE_LIMIT {
                continue;
            }
            if shutting_down.swap(true, Ordering::SeqCst) {
                return;
            }
            let _ = app_handle.emit(
                "attached-backend-lost",
                serde_json::json!({
                    "owner_alive": owner_alive,
                    "backend_pid": expected_identity.pid,
                    "failures": consecutive_failures,
                }),
            );
            app_handle.exit(0);
            return;
        }
    });
}

pub fn wait_for_health(port: u16, timeout: Duration) -> Result<(), SidecarError> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if check_health_once(port, Duration::from_millis(400)) {
            return Ok(());
        }
        thread::sleep(FAST_HEALTH_INTERVAL);
    }
    Err(SidecarError::HealthTimeout(timeout))
}

fn send_http_shutdown(port: u16) {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    if let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(250)) {
        let _ = stream.set_write_timeout(Some(Duration::from_millis(250)));
        let request = format!(
            "POST /api/system/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        );
        let _ = stream.write_all(request.as_bytes());
    }
}

pub struct PythonSidecarManager {
    project_root: PathBuf,
    resource_dir: Option<PathBuf>,
    child: Arc<Mutex<Option<Child>>>,
    endpoint: Arc<Mutex<Option<SidecarEndpoint>>>,
    shutting_down: Arc<AtomicBool>,
    owns_child: AtomicBool,
}

impl PythonSidecarManager {
    pub fn new(project_root: PathBuf, resource_dir: Option<PathBuf>) -> Self {
        Self {
            project_root,
            resource_dir,
            child: Arc::new(Mutex::new(None)),
            endpoint: Arc::new(Mutex::new(None)),
            shutting_down: Arc::new(AtomicBool::new(false)),
            owns_child: AtomicBool::new(false),
        }
    }

    pub fn current_endpoint(&self) -> Option<SidecarEndpoint> {
        self.endpoint.lock().clone()
    }

    pub fn start(
        &self,
        app_handle: Option<tauri::AppHandle>,
    ) -> Result<SidecarEndpoint, SidecarError> {
        if let Some(existing) = self.current_endpoint() {
            if check_health_once(existing.port, Duration::from_millis(300)) {
                return Ok(existing);
            }
            *self.endpoint.lock() = None;
            if !self.owns_child.load(Ordering::SeqCst) {
                self.shutting_down.store(false, Ordering::SeqCst);
            }
        }

        if let Some(attach_value) = std::env::var_os(ATTACH_BACKEND_ENV) {
            let value = attach_value
                .into_string()
                .map_err(|_| SidecarError::InvalidAttachedBackend("non-UTF-8 value".to_string()))?;
            return self.attach_with_app(value, app_handle.clone());
        }

        if self.child.lock().is_some() {
            self.shutdown_owned_child();
        }
        self.shutting_down.store(false, Ordering::SeqCst);

        let port = reserve_free_port()?;
        let runtime_port_file = self.project_root.join(".agents").join("runtime_port");
        let spec = resolve_launch_spec(
            &self.project_root,
            self.resource_dir.as_deref(),
            port,
            &runtime_port_file,
            cfg!(debug_assertions),
        )?;

        let mut command = Command::new(&spec.program);
        command
            .args(&spec.args)
            .current_dir(&spec.cwd)
            .env("PYTHONUTF8", "1")
            .env("PYTHONUNBUFFERED", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());

        let mut child = command
            .spawn()
            .map_err(|error| SidecarError::SpawnFailed(error.to_string()))?;
        let pid = child.id();
        let stdout = child.stdout.take();

        let (tx, rx) = std::sync::mpsc::channel::<u16>();
        if let Some(out) = stdout {
            thread::spawn(move || {
                let reader = BufReader::new(out);
                for line in reader.lines().map_while(Result::ok) {
                    if let Some(ready) = parse_sidecar_ready_line(&line) {
                        let _ = tx.send(ready.port);
                    }
                }
            });
        }

        let deadline = Instant::now() + HEALTH_TIMEOUT;
        let mut active_port = port;
        loop {
            if let Ok(reported_port) = rx.try_recv() {
                active_port = reported_port;
            }
            if check_health_once(active_port, Duration::from_millis(300)) {
                break;
            }
            if let Ok(Some(status)) = child.try_wait() {
                return Err(SidecarError::ExitedEarly(status.code()));
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                return Err(SidecarError::HealthTimeout(HEALTH_TIMEOUT));
            }
            thread::sleep(FAST_HEALTH_INTERVAL);
        }

        let endpoint = SidecarEndpoint::from_port(active_port, Some(pid));
        *self.child.lock() = Some(child);
        *self.endpoint.lock() = Some(endpoint.clone());
        self.owns_child.store(true, Ordering::SeqCst);

        let child_ref = Arc::clone(&self.child);
        let endpoint_ref = Arc::clone(&self.endpoint);
        let shutdown_flag = Arc::clone(&self.shutting_down);
        thread::spawn(move || loop {
            thread::sleep(Duration::from_millis(250));
            if shutdown_flag.load(Ordering::SeqCst) {
                break;
            }
            let exited_code = {
                let mut guard = child_ref.lock();
                match guard.as_mut() {
                    Some(proc_handle) => match proc_handle.try_wait() {
                        Ok(Some(status)) => {
                            *guard = None;
                            Some(status.code())
                        }
                        Ok(None) => None,
                        Err(_) => {
                            *guard = None;
                            Some(None)
                        }
                    },
                    None => break,
                }
            };
            if let Some(code) = exited_code {
                *endpoint_ref.lock() = None;
                if !shutdown_flag.load(Ordering::SeqCst) {
                    if let Some(handle) = app_handle.as_ref() {
                        let _ = handle.emit(
                            "sidecar-crashed",
                            serde_json::json!({
                                "code": code,
                                "recoverable": true,
                            }),
                        );
                    }
                }
                break;
            }
        });

        Ok(endpoint)
    }

    fn attach_with_app(
        &self,
        value: String,
        app_handle: Option<tauri::AppHandle>,
    ) -> Result<SidecarEndpoint, SidecarError> {
        let port = parse_loopback_backend_url(&value)?;
        let observed_identity = probe_attached_backend(port, Duration::from_millis(500))
            .ok_or_else(|| SidecarError::AttachedBackendUnhealthy(value.clone()))?;
        let expected_token = instance_token_from_env();
        if expected_token.as_ref().is_some_and(|expected| {
            observed_identity.instance_token.as_deref() != Some(expected.as_str())
        }) {
            return Err(SidecarError::AttachedBackendUnhealthy(value));
        }
        let expected_identity = AttachedBackendIdentity {
            pid: observed_identity.pid,
            instance_token: expected_token.or(observed_identity.instance_token),
        };
        let owner_pid = owner_pid_from_env().or(Some(expected_identity.pid));
        if self.child.lock().is_some() {
            self.shutdown_owned_child();
        }
        self.shutting_down.store(false, Ordering::SeqCst);
        self.owns_child.store(false, Ordering::SeqCst);
        let endpoint = SidecarEndpoint::from_port(port, Some(expected_identity.pid));
        *self.endpoint.lock() = Some(endpoint.clone());
        if let Some(handle) = app_handle {
            monitor_attached_backend(
                handle,
                endpoint.clone(),
                expected_identity,
                owner_pid,
                Arc::clone(&self.shutting_down),
            );
        }
        Ok(endpoint)
    }

    #[cfg(test)]
    fn attach(&self, value: String) -> Result<SidecarEndpoint, SidecarError> {
        self.attach_with_app(value, None)
    }

    fn shutdown_owned_child(&self) {
        self.shutting_down.store(true, Ordering::SeqCst);
        let port = self.endpoint.lock().take().map(|endpoint| endpoint.port);
        let Some(mut child) = self.child.lock().take() else {
            self.owns_child.store(false, Ordering::SeqCst);
            return;
        };

        drop(child.stdin.take());
        if let Some(active_port) = port {
            send_http_shutdown(active_port);
        }

        let deadline = Instant::now() + GRACEFUL_EXIT_TIMEOUT;
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                self.owns_child.store(false, Ordering::SeqCst);
                return;
            }
            thread::sleep(Duration::from_millis(25));
        }

        #[cfg(unix)]
        unsafe {
            libc::kill(child.id() as libc::pid_t, libc::SIGTERM);
        }

        let term_deadline = Instant::now() + SIGTERM_EXIT_TIMEOUT;
        while Instant::now() < term_deadline {
            if let Ok(Some(_)) = child.try_wait() {
                self.owns_child.store(false, Ordering::SeqCst);
                return;
            }
            thread::sleep(Duration::from_millis(25));
        }

        let _ = child.kill();
        let _ = child.wait();
        self.owns_child.store(false, Ordering::SeqCst);
    }

    pub fn shutdown(&self) {
        self.shutting_down.store(true, Ordering::SeqCst);
        if !self.owns_child.load(Ordering::SeqCst) {
            *self.endpoint.lock() = None;
            return;
        }
        self.shutdown_owned_child();
    }

    pub fn shutdown_for_application_exit(&self) {
        self.shutting_down.store(true, Ordering::SeqCst);
        if self.owns_child.load(Ordering::SeqCst) {
            self.shutdown_owned_child();
            return;
        }

        let endpoint = self.endpoint.lock().take();
        let Some(endpoint) = endpoint else {
            return;
        };
        send_http_shutdown(endpoint.port);
        let deadline = Instant::now() + GRACEFUL_EXIT_TIMEOUT;
        while Instant::now() < deadline {
            if !check_health_once(endpoint.port, Duration::from_millis(100)) {
                return;
            }
            thread::sleep(Duration::from_millis(25));
        }
        tracing::warn!(
            port = endpoint.port,
            "attached backend did not stop before the explicit application-exit deadline"
        );
    }
}

impl Drop for PythonSidecarManager {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserve_free_port_returns_ephemeral_port() {
        let port = reserve_free_port().expect("ephemeral port");
        assert!(port > 0);
    }

    #[test]
    fn parse_sidecar_ready_line_parses_valid_json_and_ignores_noise() {
        assert!(parse_sidecar_ready_line("INFO: Uvicorn running").is_none());
        let parsed = parse_sidecar_ready_line(
            r#"{"event":"sidecar_ready","type":"sidecar_ready","port":18123,"pid":42,"needs_setup":false}"#,
        )
        .expect("valid sidecar_ready json");
        assert_eq!(parsed.port, 18123);
        assert_eq!(parsed.pid, 42);
        assert!(!parsed.needs_setup);
    }

    #[test]
    fn attached_backend_url_accepts_only_exact_loopback_http_url() {
        assert_eq!(
            parse_loopback_backend_url("http://127.0.0.1:8123").expect("valid loopback URL"),
            8123
        );
        for invalid in [
            "https://127.0.0.1:8123",
            "http://localhost:8123",
            "http://127.0.0.1:0",
            "http://127.0.0.1:8123/settings",
            "http://127.0.0.1",
            "http://192.168.1.2:8123",
        ] {
            assert!(
                parse_loopback_backend_url(invalid).is_err(),
                "{invalid} must be rejected"
            );
        }
    }

    #[test]
    fn health_probe_requests_fast_endpoint() {
        let source = include_str!("sidecar.rs");
        assert!(source.contains("/api/health?fast=1"));
        assert!(source.contains("Duration::from_millis(25)"));
    }

    #[test]
    fn attached_backend_does_not_own_or_shutdown_the_shared_process() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("fake backend listener");
        let port = listener.local_addr().expect("fake backend address").port();
        let server = thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut request = [0_u8; 512];
                let _ = stream.read(&mut request);
                let body = br#"{"service":"harry-translate-v2","fast":true,"pid":4242}"#;
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
                let _ = stream.write_all(body);
            }
        });

        let manager = PythonSidecarManager::new(std::env::temp_dir(), None);
        let endpoint = manager
            .attach(format!("http://127.0.0.1:{port}"))
            .expect("healthy attached backend");
        assert_eq!(endpoint.port, port);
        assert_eq!(endpoint.pid, Some(4242));
        assert!(!manager.owns_child.load(Ordering::SeqCst));
        assert!(manager.child.lock().is_none());

        manager.shutdown();
        assert!(manager.current_endpoint().is_none());
        server.join().expect("fake backend thread");
    }

    #[test]
    fn explicit_application_exit_requests_shutdown_from_attached_backend() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("fake backend listener");
        let port = listener.local_addr().expect("fake backend address").port();
        let server = thread::spawn(move || {
            let (mut health_stream, _) = listener.accept().expect("attach request");
            let mut health_request = [0_u8; 512];
            let _ = health_stream.read(&mut health_request);
            let body = br#"{"service":"harry-translate-v2","fast":true,"pid":4242}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            );
            let _ = health_stream.write_all(response.as_bytes());
            let _ = health_stream.write_all(body);
            drop(health_stream);

            let (mut shutdown_stream, _) = listener.accept().expect("shutdown request");
            let mut shutdown_request = [0_u8; 512];
            let read = shutdown_stream.read(&mut shutdown_request).unwrap_or(0);
            let request = String::from_utf8_lossy(&shutdown_request[..read]);
            let _ = shutdown_stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}");
            request.contains("POST /api/system/shutdown")
        });

        let manager = PythonSidecarManager::new(std::env::temp_dir(), None);
        manager
            .attach(format!("http://127.0.0.1:{port}"))
            .expect("healthy attached backend");
        manager.shutdown_for_application_exit();
        assert!(manager.current_endpoint().is_none());
        assert!(
            server.join().expect("fake shutdown backend thread"),
            "explicit quit must POST /api/system/shutdown"
        );
    }

    #[test]
    fn resolve_launch_spec_prefers_venv_in_dev_and_external_bin_in_release() {
        let temp =
            std::env::temp_dir().join(format!("harry-sidecar-spec-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&temp);
        let venv_python = temp.join(".venv-v2").join("bin").join("python");
        let resource_bin = temp
            .join("resources")
            .join("binaries")
            .join("harry-sidecar");
        std::fs::create_dir_all(venv_python.parent().unwrap()).unwrap();
        std::fs::create_dir_all(resource_bin.parent().unwrap()).unwrap();
        std::fs::write(&venv_python, "#!/bin/sh\n").unwrap();
        std::fs::write(&resource_bin, "#!/bin/sh\n").unwrap();

        let port_file = temp.join(".agents").join("runtime_port");
        let dev_spec =
            resolve_launch_spec(&temp, Some(&temp.join("resources")), 9123, &port_file, true)
                .unwrap();
        assert_eq!(dev_spec.program, venv_python);
        assert!(dev_spec.args.contains(&"--watch-stdin".to_string()));
        assert!(dev_spec.args.contains(&"9123".to_string()));

        let prod_spec = resolve_launch_spec(
            &temp,
            Some(&temp.join("resources")),
            9123,
            &port_file,
            false,
        )
        .unwrap();
        assert_eq!(prod_spec.program, resource_bin);

        let _ = std::fs::remove_dir_all(&temp);
    }
}
