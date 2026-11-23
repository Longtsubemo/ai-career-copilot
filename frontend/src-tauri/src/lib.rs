use serde::Serialize;
use std::env;
use std::io::{BufRead, BufReader, Write};
#[cfg(not(windows))]
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager, State, WebviewWindow, WindowEvent};
use url::Url;

const ERROR_MESSAGE: &str =
    "The local services could not be started. Your saved data was preserved.";
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(120);
const STARTUP_TIMEOUT: Duration = Duration::from_secs(180);

#[derive(Clone, Serialize)]
struct BackendStatus {
    phase: &'static str,
    api_url: Option<String>,
    message: Option<String>,
}

impl BackendStatus {
    fn starting() -> Self {
        Self {
            phase: "starting",
            api_url: None,
            message: None,
        }
    }
    fn ready(api_url: String) -> Self {
        Self {
            phase: "ready",
            api_url: Some(api_url),
            message: None,
        }
    }
    fn failed(message: &str) -> Self {
        Self {
            phase: "failed",
            api_url: None,
            message: Some(message.to_owned()),
        }
    }
    fn stopping() -> Self {
        Self {
            phase: "stopping",
            api_url: None,
            message: None,
        }
    }
}

enum Control {
    Retry,
    Shutdown,
}

struct RuntimeManager {
    status: Arc<Mutex<BackendStatus>>,
    api_capability: Arc<Mutex<Option<String>>>,
    control: mpsc::Sender<Control>,
}

struct RunningChild {
    child: Child,
    stdin: ChildStdin,
    lines: mpsc::Receiver<String>,
    api_capability: String,
    ready: bool,
    started_at: Instant,
}

#[tauri::command]
fn backend_status(manager: State<'_, RuntimeManager>) -> BackendStatus {
    manager
        .status
        .lock()
        .map(|status| status.clone())
        .unwrap_or_else(|_| BackendStatus::failed(ERROR_MESSAGE))
}

#[tauri::command]
fn retry_backend(manager: State<'_, RuntimeManager>) -> Result<(), String> {
    manager
        .control
        .send(Control::Retry)
        .map_err(|_| ERROR_MESSAGE.to_owned())
}

#[tauri::command]
fn api_capability(
    window: WebviewWindow,
    manager: State<'_, RuntimeManager>,
) -> Result<String, String> {
    let url = window.url().map_err(|_| ERROR_MESSAGE.to_owned())?;
    if !is_trusted_frontend_origin(window.label(), &url) {
        return Err("the local API capability is unavailable to this window".to_owned());
    }
    let is_ready = manager
        .status
        .lock()
        .map(|status| status.phase == "ready")
        .unwrap_or(false);
    if !is_ready {
        return Err("the local API is not ready".to_owned());
    }
    manager
        .api_capability
        .lock()
        .map_err(|_| ERROR_MESSAGE.to_owned())?
        .clone()
        .ok_or_else(|| "the local API capability is unavailable".to_owned())
}

pub fn run() {
    let status = Arc::new(Mutex::new(BackendStatus::starting()));
    let capability_state = Arc::new(Mutex::new(None));
    let (control, receiver) = mpsc::channel();
    let manager = RuntimeManager {
        status: Arc::clone(&status),
        api_capability: Arc::clone(&capability_state),
        control,
    };

    tauri::Builder::default()
        .manage(manager)
        .invoke_handler(tauri::generate_handler![
            backend_status,
            retry_backend,
            api_capability
        ])
        .setup(move |app| {
            let app_handle = app.handle().clone();
            let worker_status = Arc::clone(&status);
            let worker_capability = Arc::clone(&capability_state);
            thread::Builder::new()
                .name("career-copilot-runtime-supervisor".to_owned())
                .spawn(move || {
                    runtime_worker(app_handle, receiver, worker_status, worker_capability)
                })?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let manager = window.state::<RuntimeManager>();
                let phase = manager
                    .status
                    .lock()
                    .map(|state| state.phase)
                    .unwrap_or("failed");
                if phase == "failed" {
                    window.app_handle().exit(0);
                } else {
                    if let Ok(mut capability) = manager.api_capability.lock() {
                        *capability = None;
                    }
                    let _ = manager.control.send(Control::Shutdown);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("desktop application failed to start");
}

fn runtime_worker(
    app: tauri::AppHandle,
    controls: mpsc::Receiver<Control>,
    status: Arc<Mutex<BackendStatus>>,
    api_capability: Arc<Mutex<Option<String>>>,
) {
    let mut running: Option<RunningChild> = None;
    let mut retry = true;

    loop {
        if running.is_none() && retry {
            retry = false;
            set_status(&status, BackendStatus::starting());
            match spawn_supervisor() {
                Ok(child) => {
                    if let Ok(mut current) = api_capability.lock() {
                        *current = Some(child.api_capability.clone());
                    }
                    running = Some(child);
                }
                Err(()) => {
                    clear_api_capability(&api_capability);
                    fail(&app, &status);
                    continue;
                }
            }
        }

        let Some(active) = running.as_mut() else {
            match controls.recv() {
                Ok(Control::Retry) => retry = true,
                Ok(Control::Shutdown) | Err(_) => {
                    app.exit(0);
                    return;
                }
            }
            continue;
        };

        match controls.recv_timeout(Duration::from_millis(60)) {
            Ok(Control::Retry) => {}
            Ok(Control::Shutdown) => {
                set_status(&status, BackendStatus::stopping());
                if shutdown_child(active) {
                    clear_api_capability(&api_capability);
                    app.exit(0);
                    return;
                }
                // Retain ownership of a child that has not confirmed shutdown.
                // A later close request can send the graceful command again.
                set_status(&status, BackendStatus::stopping());
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                let _ = active.stdin.write_all(b"{\"command\":\"shutdown\"}\n");
                let _ = active.stdin.flush();
                loop {
                    match active.child.try_wait() {
                        Ok(Some(_)) | Err(_) => return,
                        Ok(None) => thread::sleep(Duration::from_millis(100)),
                    }
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }

        if let Some(active) = running.as_mut() {
            while let Ok(line) = active.lines.try_recv() {
                if let Some(api_url) = parse_ready_url(&line) {
                    if valid_api_url(&api_url) {
                        active.ready = true;
                        set_status(&status, BackendStatus::ready(api_url));
                    }
                }
            }
            match active.child.try_wait() {
                Ok(Some(_)) => {
                    running = None;
                    clear_api_capability(&api_capability);
                    fail(&app, &status);
                }
                Ok(None) => {}
                Err(_) => {
                    set_status(&status, BackendStatus::stopping());
                    let _ = active.stdin.write_all(b"{\"command\":\"shutdown\"}\n");
                    let _ = active.stdin.flush();
                }
            }
        }

        if let Some(active) = running.as_mut() {
            if !active.ready && active.started_at.elapsed() >= STARTUP_TIMEOUT {
                set_status(&status, BackendStatus::stopping());
                if shutdown_child(active) {
                    running = None;
                    clear_api_capability(&api_capability);
                    fail(&app, &status);
                } else {
                    // Retain ownership of a supervisor that has not confirmed exit.
                    set_status(&status, BackendStatus::stopping());
                }
            }
        }
    }
}

fn spawn_supervisor() -> Result<RunningChild, ()> {
    let root = if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(Path::parent)
            .ok_or(())?
            .to_path_buf()
    } else {
        env::current_exe()
            .map_err(|_| ())?
            .parent()
            .ok_or(())?
            .to_path_buf()
    };
    let backend = root.join("backend");
    let development_python = backend.join(".venv").join("Scripts").join("python.exe");
    let packaged_backend = root
        .join("resources")
        .join("backend")
        .join("AI-Career-Copilot-Backend.exe");
    let postgres_bin = root
        .join("resources")
        .join("postgresql")
        .join("16.16")
        .join("windows-x64-minimal")
        .join("bin");
    let backend_program = if cfg!(debug_assertions) {
        &development_python
    } else {
        &packaged_backend
    };
    if !backend_program.is_file() || !postgres_bin.join("postgres.exe").is_file() {
        return Err(());
    }

    let webview_mode = if cfg!(debug_assertions) {
        "tauri-dev"
    } else {
        "tauri-local"
    };
    let mut command = Command::new(backend_program);
    if cfg!(debug_assertions) {
        command.arg("-u").arg("-m").arg("app.desktop_runtime");
    }
    command
        .current_dir(&root)
        .arg("--webview-mode")
        .arg(webview_mode)
        .arg("--install-dir")
        .arg(&root)
        .env_clear()
        .env("CAREER_COPILOT_RUNTIME_MODE", "desktop")
        .env(
            "PATH",
            sanitized_path(
                &backend,
                &development_python,
                &postgres_bin,
                &root,
                cfg!(debug_assertions),
            ),
        )
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    copy_safe_environment(&mut command);

    if let Ok(data_dir) = env::var("AI_CAREER_COPILOT_TEST_DATA_DIR") {
        let path = PathBuf::from(data_dir);
        if !path.is_absolute() {
            return Err(());
        }
        command.arg("--data-dir").arg(path);
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    let api_capability = generate_api_capability()?;
    let mut child = command.spawn().map_err(|_| ())?;
    let mut stdin = child.stdin.take().ok_or(())?;
    let handshake = serde_json::json!({
        "command": "initialize",
        "api_capability": api_capability.as_str(),
    });
    serde_json::to_writer(&mut stdin, &handshake).map_err(|_| ())?;
    stdin.write_all(b"\n").map_err(|_| ())?;
    stdin.flush().map_err(|_| ())?;
    let stdout = child.stdout.take().ok_or(())?;
    let (line_tx, lines) = mpsc::channel();
    thread::Builder::new()
        .name("career-copilot-runtime-output".to_owned())
        .spawn(move || {
            for line in BufReader::new(stdout).lines().flatten() {
                if line_tx.send(line).is_err() {
                    break;
                }
            }
        })
        .map_err(|_| ())?;
    Ok(RunningChild {
        child,
        stdin,
        lines,
        api_capability,
        ready: false,
        started_at: Instant::now(),
    })
}

fn sanitized_path(
    backend: &Path,
    _python: &Path,
    postgres_bin: &Path,
    install_root: &Path,
    include_development_python: bool,
) -> String {
    let mut entries = vec![postgres_bin.to_path_buf()];
    if include_development_python {
        entries.push(backend.join(".venv").join("Scripts"));
        if let Ok(contents) = std::fs::read_to_string(backend.join(".venv").join("pyvenv.cfg")) {
            if let Some(home) = contents.lines().find_map(|line| {
                let (key, value) = line.split_once('=')?;
                (key.trim().eq_ignore_ascii_case("home")).then(|| PathBuf::from(value.trim()))
            }) {
                entries.push(home.clone());
                entries.push(home.join("DLLs"));
            }
        }
    } else {
        // The per-machine installer verifies the Microsoft x64 v14 runtime
        // before copying files. PostgreSQL resolves it from Windows' runtime
        // locations; no app-local DLL copy or PATH change is needed.
        entries.push(install_root.to_path_buf());
    }
    if let Some(system_root) = env::var_os("SystemRoot").map(PathBuf::from) {
        entries.push(system_root.join("System32"));
        entries.push(system_root);
    }
    env::join_paths(entries)
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned()
}

fn copy_safe_environment(command: &mut Command) {
    for key in [
        "SystemRoot",
        "WINDIR",
        "SYSTEMDRIVE",
        "LOCALAPPDATA",
        "APPDATA",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "TEMP",
        "TMP",
        "PROGRAMDATA",
    ] {
        if let Some(value) = env::var_os(key) {
            command.env(key, value);
        }
    }
}

fn parse_ready_url(line: &str) -> Option<String> {
    let message: serde_json::Value = serde_json::from_str(line).ok()?;
    (message.get("event")?.as_str()? == "ready")
        .then(|| message.get("api_url")?.as_str().map(str::to_owned))?
}

fn valid_api_url(value: &str) -> bool {
    let Ok(url) = Url::parse(value) else {
        return false;
    };
    url.scheme() == "http"
        && url.host_str() == Some("localhost")
        && url.username().is_empty()
        && url.password().is_none()
        && url.path() == "/"
        && url.query().is_none()
        && url.fragment().is_none()
        && url.port().is_some_and(|port| port > 0 && port != 5173)
}

fn is_trusted_frontend_origin(label: &str, url: &Url) -> bool {
    if label != "main" || !url.username().is_empty() || url.password().is_some() {
        return false;
    }
    (url.scheme() == "http"
        && url.host_str() == Some("localhost")
        && url.port() == Some(5173))
        || (url.scheme() == "http"
            && url.host_str() == Some("tauri.localhost")
            && url.port().is_none())
}

#[cfg(windows)]
fn generate_api_capability() -> Result<String, ()> {
    use std::ffi::c_void;

    #[link(name = "bcrypt")]
    extern "system" {
        fn BCryptGenRandom(
            algorithm: *mut c_void,
            buffer: *mut u8,
            length: u32,
            flags: u32,
        ) -> i32;
    }

    const BCRYPT_USE_SYSTEM_PREFERRED_RNG: u32 = 0x00000002;
    let mut bytes = [0_u8; 32];
    let status = unsafe {
        BCryptGenRandom(
            std::ptr::null_mut(),
            bytes.as_mut_ptr(),
            bytes.len() as u32,
            BCRYPT_USE_SYSTEM_PREFERRED_RNG,
        )
    };
    if status < 0 {
        return Err(());
    }
    Ok(hex_encode(&bytes))
}

#[cfg(not(windows))]
fn generate_api_capability() -> Result<String, ()> {
    let mut bytes = [0_u8; 32];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut random| random.read_exact(&mut bytes))
        .map_err(|_| ())?;
    Ok(hex_encode(&bytes))
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(HEX[(byte >> 4) as usize] as char);
        encoded.push(HEX[(byte & 0x0f) as usize] as char);
    }
    encoded
}

fn clear_api_capability(api_capability: &Arc<Mutex<Option<String>>>) {
    if let Ok(mut current) = api_capability.lock() {
        *current = None;
    }
}

fn shutdown_child(active: &mut RunningChild) -> bool {
    let _ = active.stdin.write_all(b"{\"command\":\"shutdown\"}\n");
    let _ = active.stdin.flush();
    let deadline = Instant::now() + SHUTDOWN_TIMEOUT;
    while Instant::now() < deadline {
        match active.child.try_wait() {
            Ok(Some(_)) => return true,
            Ok(None) => thread::sleep(Duration::from_millis(100)),
            Err(_) => return false,
        }
    }
    false
}

fn set_status(status: &Arc<Mutex<BackendStatus>>, next: BackendStatus) {
    if let Ok(mut current) = status.lock() {
        *current = next;
    }
}

fn fail(app: &tauri::AppHandle, status: &Arc<Mutex<BackendStatus>>) {
    set_status(status, BackendStatus::failed(ERROR_MESSAGE));
    let _ = app.emit("desktop-runtime-error", ERROR_MESSAGE);
    return_to_bootstrap_page(app);
}

fn return_to_bootstrap_page(app: &tauri::AppHandle) {
    if cfg!(debug_assertions) {
        return;
    }
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    if window
        .url()
        .is_ok_and(|current| current.host_str() == Some("tauri.localhost"))
    {
        return;
    }
    if let Ok(url) = Url::parse("http://tauri.localhost/") {
        let _ = window.navigate(url);
    }
}

#[cfg(test)]
mod tests {
    use super::{
        generate_api_capability, is_trusted_frontend_origin, parse_ready_url, sanitized_path,
        valid_api_url,
    };
    use std::env;
    use std::path::Path;
    use url::Url;

    #[test]
    fn accepts_only_the_expected_dynamic_local_api_url() {
        assert!(valid_api_url("http://localhost:49152"));
        assert!(!valid_api_url("https://localhost:49152"));
        assert!(!valid_api_url("http://127.0.0.1:49152"));
        assert!(!valid_api_url("http://user@localhost:49152"));
        assert!(!valid_api_url("http://localhost:5173"));
        assert!(!valid_api_url("http://localhost:49152/unexpected"));
    }

    #[test]
    fn parses_only_a_ready_protocol_message() {
        assert_eq!(
            parse_ready_url(r#"{"event":"ready","api_url":"http://localhost:49152"}"#),
            Some("http://localhost:49152".to_owned())
        );
        assert_eq!(
            parse_ready_url(r#"{"event":"error","api_url":"http://localhost:49152"}"#),
            None
        );
        assert_eq!(parse_ready_url("not json"), None);
    }

    #[test]
    fn generates_a_fresh_256_bit_capability() {
        let first = generate_api_capability().unwrap();
        let second = generate_api_capability().unwrap();
        assert_eq!(first.len(), 64);
        assert!(first.bytes().all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase()));
        assert_ne!(first, second);
    }

    #[test]
    fn capability_is_available_only_to_the_main_trusted_webview() {
        assert!(is_trusted_frontend_origin(
            "main",
            &Url::parse("http://localhost:5173/profile").unwrap()
        ));
        assert!(is_trusted_frontend_origin(
            "main",
            &Url::parse("http://tauri.localhost/").unwrap()
        ));
        assert!(!is_trusted_frontend_origin(
            "other",
            &Url::parse("http://tauri.localhost/").unwrap()
        ));
        assert!(!is_trusted_frontend_origin(
            "main",
            &Url::parse("http://localhost:5174/").unwrap()
        ));
        assert!(!is_trusted_frontend_origin(
            "main",
            &Url::parse("https://tauri.localhost/").unwrap()
        ));
    }

    #[test]
    fn packaged_path_includes_the_app_local_runtime_without_python() {
        let backend = Path::new(r"C:\Program Files\AI Career Copilot\resources\backend");
        let python = backend.join(".venv").join("Scripts").join("python.exe");
        let postgres = Path::new(
            r"C:\Program Files\AI Career Copilot\resources\postgresql\16.16\windows-x64-minimal\bin",
        );
        let install_root = Path::new(r"C:\Program Files\AI Career Copilot");

        let entries = env::split_paths(&sanitized_path(
            backend,
            &python,
            postgres,
            install_root,
            false,
        ))
        .collect::<Vec<_>>();

        assert!(entries.contains(&postgres.to_path_buf()));
        assert!(entries.contains(&install_root.to_path_buf()));
        assert!(!entries.contains(&python.parent().unwrap().to_path_buf()));
    }
}
