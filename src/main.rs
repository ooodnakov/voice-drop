#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use tauri::Emitter;

const SEND_SCRIPT: &str = include_str!("../tg_send.py");

const THEMES: [&str; 5] = ["system", "gruvbox-dark", "gruvbox-light", "paper", "slate"];

fn app_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

fn is_writable(dir: &Path) -> bool {
    let probe = dir.join(".voicedrop-write-test");
    match std::fs::write(&probe, b"1") {
        Ok(()) => {
            let _ = std::fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

fn user_data_dir() -> PathBuf {
    #[cfg(windows)]
    {
        return std::env::var("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|_| std::env::temp_dir())
            .join("VoiceDrop");
    }
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("."));
        return home.join("Library").join("Application Support").join("VoiceDrop");
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        if let Ok(xdg) = std::env::var("XDG_DATA_HOME") {
            if !xdg.is_empty() {
                return PathBuf::from(xdg).join("voicedrop");
            }
        }
        let home = std::env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("."));
        return home.join(".local").join("share").join("voicedrop");
    }
}

/// Portable when the install folder is writable (folder copy), otherwise the
/// per-user data dir (macOS .app in /Applications, Linux AppImage mount).
fn base_dir() -> PathBuf {
    let exe_dir = app_dir();
    if is_writable(&exe_dir) {
        exe_dir
    } else {
        let dir = user_data_dir();
        let _ = std::fs::create_dir_all(&dir);
        dir
    }
}

#[cfg(unix)]
fn restrict(path: &Path, mode: u32) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode));
}

fn data_dir() -> PathBuf {
    let dir = base_dir().join("data");
    let _ = std::fs::create_dir_all(&dir);
    #[cfg(unix)]
    restrict(&dir, 0o700);
    dir
}

fn accounts_dir() -> PathBuf {
    let dir = data_dir().join("accounts");
    let _ = std::fs::create_dir_all(&dir);
    #[cfg(unix)]
    restrict(&dir, 0o700);
    dir
}

fn config_path() -> PathBuf {
    data_dir().join("config.json")
}

fn runtime_dir() -> PathBuf {
    base_dir().join("runtime")
}

fn managed_python() -> PathBuf {
    let venv = runtime_dir().join("python");
    if cfg!(windows) {
        venv.join("Scripts").join("python.exe")
    } else {
        venv.join("bin").join("python3")
    }
}

fn local_tool(name: &str) -> PathBuf {
    let file = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    };
    app_dir().join("bin").join(file)
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct Account {
    key: String,
    #[serde(default)]
    session: String,
    #[serde(default)]
    name: String,
    #[serde(default)]
    username: String,
    #[serde(default)]
    phone: String,
}

impl Account {
    fn label(&self) -> String {
        if !self.name.is_empty() && !self.username.is_empty() {
            format!("{} · @{}", self.name, self.username)
        } else if !self.name.is_empty() {
            self.name.clone()
        } else if !self.username.is_empty() {
            format!("@{}", self.username)
        } else if !self.phone.is_empty() {
            self.phone.clone()
        } else {
            self.key.clone()
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Config {
    #[serde(default = "default_api_id")]
    api_id: u64,
    #[serde(default = "default_api_hash")]
    api_hash: String,
    #[serde(default)]
    python: Option<String>,
    #[serde(default)]
    account: String,
    #[serde(default)]
    accounts: Vec<Account>,
    #[serde(default)]
    chat: String,
    /// Recipient per account key; `chat` above is the legacy mirror of the
    /// active entry.
    #[serde(default)]
    chats: BTreeMap<String, String>,
    #[serde(default = "default_theme")]
    theme: String,
}

fn default_theme() -> String {
    "gruvbox-dark".into()
}

fn default_api_id() -> u64 {
    option_env!("VOICEDROP_API_ID")
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(0)
}

fn default_api_hash() -> String {
    option_env!("VOICEDROP_API_HASH")
        .map(str::to_string)
        .unwrap_or_default()
}

impl Default for Config {
    fn default() -> Self {
        Self {
            api_id: default_api_id(),
            api_hash: default_api_hash(),
            python: None,
            account: String::new(),
            accounts: Vec::new(),
            chat: String::new(),
            chats: BTreeMap::new(),
            theme: default_theme(),
        }
    }
}

static CONFIG_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn lock_config() -> std::sync::MutexGuard<'static, ()> {
    CONFIG_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn unix_ts() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or(0)
}

/// Write through a sibling temp file so an interrupted save never leaves half
/// a config behind; `rename` is atomic within one directory.
fn save_config_locked(config: &Config) -> bool {
    let path = config_path();
    let Some(parent) = path.parent() else {
        return false;
    };
    if std::fs::create_dir_all(parent).is_err() {
        return false;
    }
    #[cfg(unix)]
    restrict(parent, 0o700);
    let text = match serde_json::to_string_pretty(config) {
        Ok(text) => text,
        Err(_) => return false,
    };
    let tmp = parent.join("config.json.tmp");
    if std::fs::write(&tmp, text).is_err() {
        return false;
    }
    #[cfg(unix)]
    restrict(&tmp, 0o600);
    if std::fs::rename(&tmp, &path).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return false;
    }
    true
}

/// Keep a config we cannot parse instead of overwriting it with defaults.
fn set_config_aside(path: &Path) {
    if !path.exists() {
        return;
    }
    let aside = path.with_file_name(format!("config.json.corrupt-{}", unix_ts()));
    let _ = std::fs::rename(path, aside);
}

fn load_config_locked() -> Config {
    let path = config_path();
    match std::fs::read_to_string(&path) {
        Ok(text) => match serde_json::from_str::<Config>(&text) {
            Ok(mut config) => {
                if migrate_chats(&mut config) {
                    save_config_locked(&config);
                }
                return config;
            }
            Err(_) => set_config_aside(&path),
        },
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => set_config_aside(&path),
    }
    let config = Config::default();
    save_config_locked(&config);
    config
}

fn load_config() -> Config {
    let _guard = lock_config();
    load_config_locked()
}

/// Read, mutate and persist the config under a single lock so two commands
/// cannot lose each other's update.
fn update_config<R>(body: impl FnOnce(&mut Config) -> R) -> (Config, R) {
    let _guard = lock_config();
    let mut config = load_config_locked();
    let result = body(&mut config);
    save_config_locked(&config);
    (config, result)
}

/// The legacy `chat` belongs to the active account; seed `chats` from it once.
fn migrate_chats(config: &mut Config) -> bool {
    if !config.chats.is_empty() || config.chat.trim().is_empty() || config.account.is_empty() {
        return false;
    }
    config.chats.insert(config.account.clone(), config.chat.clone());
    true
}

/// Recipient for one account. When per-account chats exist and this account has
/// none, the legacy value is not reused: it belongs to somebody else.
fn chat_for(config: &Config, key: &str) -> String {
    if let Some(chat) = config.chats.get(key) {
        return chat.clone();
    }
    if config.chats.is_empty() {
        return config.chat.clone();
    }
    String::new()
}

fn account_by_key<'a>(config: &'a Config, key: &str) -> Option<&'a Account> {
    config.accounts.iter().find(|account| account.key == key)
}

fn active_account(config: &Config) -> Option<Account> {
    account_by_key(config, &config.account)
        .or_else(|| config.accounts.first())
        .cloned()
}

fn session_path(account: &Account) -> PathBuf {
    let name = if account.session.is_empty() {
        format!("{}.session", account.key)
    } else {
        account.session.clone()
    };
    accounts_dir().join(name)
}

fn hidden(command: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
}

fn probe_python(program: &str, args: &[&str]) -> Option<PathBuf> {
    let mut command = Command::new(program);
    command
        .args(args)
        .args(["-c", "import sys;print(sys.executable)"]);
    hidden(&mut command);
    let output = command.output().ok()?;
    if !output.status.success() {
        return None;
    }
    let path = PathBuf::from(String::from_utf8_lossy(&output.stdout).trim());
    path.exists().then_some(path)
}

fn has_telethon(python: &Path) -> bool {
    let mut command = Command::new(python);
    command.args(["-c", "import telethon"]);
    hidden(&mut command);
    command.output().map(|out| out.status.success()).unwrap_or(false)
}

fn run_checked(mut command: Command) -> Result<(), String> {
    hidden(&mut command);
    match command.output() {
        Ok(output) if output.status.success() => Ok(()),
        Ok(output) => Err(format!(
            "{} failed: {}",
            command_label(&command),
            String::from_utf8_lossy(&output.stderr).trim().chars().take(400).collect::<String>()
        )),
        Err(error) => Err(format!("cannot start {}: {error}", command_label(&command))),
    }
}

fn command_label(command: &Command) -> String {
    command.get_program().to_string_lossy().into_owned()
}

fn system_python() -> Option<PathBuf> {
    probe_python("python", &[])
        .or_else(|| probe_python("python3", &[]))
        .or_else(|| probe_python("py", &["-3"]))
}

fn find_ffmpeg() -> Option<PathBuf> {
    let beside = app_dir().join(if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" });
    if beside.exists() {
        return Some(beside);
    }
    let local = local_tool("ffmpeg");
    if local.exists() {
        return Some(local);
    }
    let mut command = Command::new("ffmpeg");
    command.arg("-version");
    hidden(&mut command);
    if command.output().map(|out| out.status.success()).unwrap_or(false) {
        return Some(PathBuf::from("ffmpeg"));
    }
    None
}

const VIDEO_EXTS: [&str; 12] = [
    "mp4", "m4v", "mov", "mkv", "webm", "avi", "flv", "wmv", "3gp", "mpeg", "mpg", "ts",
];
const AUDIO_EXTS: [&str; 15] = [
    "mp3", "m4a", "aac", "flac", "wav", "wma", "aiff", "aif", "alac", "amr", "ape", "wv", "ogg",
    "oga", "opus",
];
const AUDIO_PASSTHROUGH_EXTS: [&str; 3] = ["ogg", "oga", "opus"];

fn file_needs_ffmpeg(path: &str) -> bool {
    let extension = Path::new(path)
        .extension()
        .map(|extension| extension.to_string_lossy().to_ascii_lowercase())
        .unwrap_or_default();
    if VIDEO_EXTS.contains(&extension.as_str()) {
        return true;
    }
    AUDIO_EXTS.contains(&extension.as_str())
        && !AUDIO_PASSTHROUGH_EXTS.contains(&extension.as_str())
}

/// Whether this batch has to be transcoded at all: video (always a circle) and
/// non-Opus audio are re-encoded, a plain document is uploaded untouched.
fn needs_ffmpeg(paths: &[String]) -> bool {
    paths.iter().any(|path| file_needs_ffmpeg(path))
}

fn missing_ffmpeg_error(paths: &[String]) -> String {
    let names: Vec<String> = paths
        .iter()
        .filter(|path| file_needs_ffmpeg(path))
        .map(|path| {
            Path::new(path)
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_else(|| path.clone())
        })
        .collect();
    format!(
        "ffmpeg is needed for: {} (put ffmpeg on PATH or next to the app)",
        names.join(", ")
    )
}

#[derive(Debug, Serialize)]
struct Runtime {
    ok: bool,
    python: String,
    ffmpeg: String,
    error: Option<String>,
}

fn find_uv() -> Option<PathBuf> {
    let mut candidates = vec![local_tool("uv"), app_dir().join(if cfg!(windows) { "uv.exe" } else { "uv" })];
    if let Some(parent) = app_dir().parent() {
        candidates.push(
            parent
                .join("Resources")
                .join("bin")
                .join(if cfg!(windows) { "uv.exe" } else { "uv" }),
        );
    }
    candidates.push(PathBuf::from("/usr/lib/voicedrop/bin/uv"));
    if let Some(found) = candidates.into_iter().find(|path| path.exists()) {
        return Some(found);
    }
    let mut command = Command::new("uv");
    command.arg("--version");
    hidden(&mut command);
    command
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
        .then(|| PathBuf::from("uv"))
}

fn provision_with_uv(uv: &Path) -> Result<(), String> {
    let venv = runtime_dir().join("python");
    let mut command = Command::new(uv);
    command
        .args(["venv", "--python", "3.12", "--allow-existing"])
        .arg(&venv);
    run_checked(command)?;

    let managed = managed_python();
    if !managed.exists() {
        return Err(format!(
            "uv venv produced no interpreter at {}",
            managed.display()
        ));
    }
    let mut pip = Command::new(uv);
    pip.args(["pip", "install", "--python"])
        .arg(&managed)
        .arg("telethon>=1.43.2");
    run_checked(pip)?;
    Ok(())
}

fn provision_with_python(base: &Path) -> Result<(), String> {
    let venv = runtime_dir().join("python");
    if !managed_python().exists() {
        let mut command = Command::new(base);
        command.arg("-m").arg("venv").arg(&venv);
        run_checked(command)?;
    }
    let mut pip = Command::new(&managed_python());
    pip.args([
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--no-warn-script-location",
        "telethon>=1.43.2",
    ]);
    run_checked(pip)?;
    Ok(())
}

fn ensure_python(config: &Config) -> Result<PathBuf, String> {
    let managed = managed_python();
    if has_telethon(&managed) {
        return Ok(managed);
    }
    if let Some(configured) = config.python.as_ref().map(PathBuf::from) {
        if has_telethon(&configured) {
            return Ok(configured);
        }
    }

    let mut errors: Vec<String> = Vec::new();

    if let Some(uv) = find_uv() {
        if let Err(error) = provision_with_uv(&uv) {
            errors.push(format!("uv: {error}"));
        }
    }

    if !has_telethon(&managed) {
        match system_python() {
            Some(base) => {
                if let Err(error) = provision_with_python(&base) {
                    errors.push(format!("python: {error}"));
                }
            }
            None => errors.push("no system python found".into()),
        }
    }

    if has_telethon(&managed) {
        Ok(managed)
    } else if errors.is_empty() {
        Err("could not provision the python runtime".into())
    } else {
        Err(errors.join("; "))
    }
}

fn short(text: &str, max: usize) -> String {
    let line = text
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .unwrap_or("")
        .trim();
    let mut out: String = line.chars().take(max).collect();
    if line.chars().count() > max {
        out.push('\u{2026}');
    }
    out
}

fn absorb(
    value: serde_json::Value,
    on_event: Option<&dyn Fn(serde_json::Value)>,
    result: &mut Option<serde_json::Value>,
) {
    if value.get("event").is_some() {
        if let Some(callback) = on_event {
            callback(value);
        }
    } else {
        *result = Some(value);
    }
}

/// Overall budget for one sender run; `0` disables the deadline.
fn send_timeout_secs() -> u64 {
    std::env::var("VOICEDROP_SEND_TIMEOUT")
        .ok()
        .and_then(|value| value.trim().parse::<u64>().ok())
        .unwrap_or(1800)
}

fn run_sender(
    config: &Config,
    python: &Path,
    session: &Path,
    extra: &[String],
    on_event: Option<&dyn Fn(serde_json::Value)>,
) -> serde_json::Value {
    run_sender_with_env(config, python, session, extra, on_event, &[])
}

fn run_sender_with_env(
    config: &Config,
    python: &Path,
    session: &Path,
    extra: &[String],
    on_event: Option<&dyn Fn(serde_json::Value)>,
    env: &[(&str, &str)],
) -> serde_json::Value {
    let script = match sender_script() {
        Some(script) => script,
        None => {
            return serde_json::json!({
                "ok": false,
                "error": "cannot write the sender script to the temp directory"
            })
        }
    };

    let mut command = Command::new(python);
    command
        .arg(script)
        .arg("--session")
        .arg(session)
        // Credentials travel through the environment: argv is readable by any
        // process on the machine.
        .env("VOICEDROP_API_ID", config.api_id.to_string())
        .env("VOICEDROP_API_HASH", &config.api_hash);
    for (key, value) in env {
        command.env(key, value);
    }
    if let Some(ffmpeg) = find_ffmpeg() {
        command.arg("--ffmpeg").arg(ffmpeg);
    }
    for arg in extra {
        command.arg(arg);
    }
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hidden(&mut command);

    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return serde_json::json!({
                "ok": false,
                "error": format!("cannot run {}: {error}", python.display())
            })
        }
    };
    let stdout = match child.stdout.take() {
        Some(stdout) => stdout,
        None => {
            let _ = child.kill();
            return serde_json::json!({ "ok": false, "error": "sender stdout was not captured" });
        }
    };
    let stderr = child.stderr.take();

    let (tx, rx) = std::sync::mpsc::channel::<serde_json::Value>();
    let reader = std::thread::spawn(move || {
        let mut raw = String::new();
        for line in BufReader::new(stdout).lines() {
            let Ok(line) = line else { break };
            raw.push_str(&line);
            raw.push('\n');
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            if let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) {
                if value.is_object() && tx.send(value).is_err() {
                    break;
                }
            }
        }
        raw
    });
    let error_reader = stderr.map(|stderr| {
        std::thread::spawn(move || {
            let mut stderr = stderr;
            let mut raw = String::new();
            let _ = stderr.read_to_string(&mut raw);
            raw
        })
    });

    let timeout_secs = send_timeout_secs();
    let deadline = if timeout_secs == 0 {
        None
    } else {
        Some(Instant::now() + Duration::from_secs(timeout_secs))
    };
    let mut result: Option<serde_json::Value> = None;
    let mut exit_code: Option<i32> = None;
    let mut timed_out = false;
    loop {
        while let Ok(value) = rx.try_recv() {
            absorb(value, on_event, &mut result);
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                exit_code = Some(status.code().unwrap_or(-1));
                break;
            }
            Ok(None) => {}
            Err(_) => break,
        }
        if deadline.is_some_and(|limit| Instant::now() >= limit) {
            let _ = child.kill();
            let _ = child.wait();
            timed_out = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }

    // A killed child may leave a helper (ffmpeg) holding the pipe, so the
    // deadline response must not wait for the reader threads.
    if timed_out {
        while let Ok(value) = rx.try_recv() {
            absorb(value, on_event, &mut result);
        }
        return serde_json::json!({
            "ok": false,
            "error": format!("sender timed out after {timeout_secs}s")
        });
    }

    // The reader finishes once the pipe closes, so joining it guarantees every
    // line the child wrote has been delivered.
    let stdout_text = reader.join().unwrap_or_default();
    while let Ok(value) = rx.try_recv() {
        absorb(value, on_event, &mut result);
    }
    let stderr_text = error_reader
        .and_then(|handle| handle.join().ok())
        .unwrap_or_default();

    if let Some(value) = result {
        return value;
    }
    let detail = if stdout_text.trim().is_empty() {
        short(&stderr_text, 240)
    } else {
        short(&stdout_text, 240)
    };
    serde_json::json!({
        "ok": false,
        "error": format!(
            "sender failed (exit {}): {}",
            exit_code.unwrap_or(-1),
            if detail.is_empty() { "no output".to_string() } else { detail }
        )
    })
}

fn sender_script() -> Option<PathBuf> {
    let path = std::env::temp_dir().join("VoiceDrop").join("tg_send.py");
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok()?;
    }
    std::fs::write(&path, SEND_SCRIPT).ok()?;
    Some(path)
}

fn with_python<F>(config: &Config, body: F) -> Result<serde_json::Value, String>
where
    F: FnOnce(&Path) -> serde_json::Value,
{
    let python = ensure_python(config)?;
    Ok(body(&python))
}

#[tauri::command]
async fn prepare() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let config = load_config();
        let ffmpeg = find_ffmpeg();
        let python = ensure_python(&config);
        let resolved_python = python
            .as_ref()
            .ok()
            .map(|path| path.to_string_lossy().to_string());

        // Fill in names for accounts whose session exists but metadata is empty.
        // Network work happens outside the config lock; results are applied by
        // account key afterwards so a concurrent edit is never clobbered.
        let mut fetched: Vec<(String, String, String)> = Vec::new();
        if let Ok(python_path) = &python {
            for account in config.accounts.iter().filter(|account| account.name.is_empty()) {
                let path = session_path(account);
                if !path.exists() {
                    continue;
                }
                let result =
                    run_sender(&config, python_path, &path, &["--whoami".to_string()], None);
                if result.get("ok").and_then(|value| value.as_bool()) == Some(true) {
                    if let Some(user) = result.get("user") {
                        fetched.push((
                            account.key.clone(),
                            user.get("name")
                                .and_then(|value| value.as_str())
                                .unwrap_or_default()
                                .to_string(),
                            user.get("username")
                                .and_then(|value| value.as_str())
                                .unwrap_or_default()
                                .to_string(),
                        ));
                    }
                }
            }
        }

        let config = if resolved_python.is_some() || !fetched.is_empty() {
            update_config(|config| {
                if let Some(path) = &resolved_python {
                    if config.python.as_deref() != Some(path.as_str()) {
                        config.python = Some(path.clone());
                    }
                }
                for (key, name, username) in &fetched {
                    if let Some(entry) = config.accounts.iter_mut().find(|entry| &entry.key == key) {
                        entry.name = name.clone();
                        entry.username = username.clone();
                    }
                }
            })
            .0
        } else {
            config
        };

        let runtime = Runtime {
            // Documents and Opus files need no ffmpeg, so it stays advisory.
            ok: python.is_ok(),
            python: python
                .as_ref()
                .map(|path| path.display().to_string())
                .unwrap_or_default(),
            ffmpeg: ffmpeg
                .as_ref()
                .map(|path| path.display().to_string())
                .unwrap_or_default(),
            error: match &python {
                Err(error) => Some(error.clone()),
                Ok(_) if ffmpeg.is_none() => Some(
                    "ffmpeg not found. Put ffmpeg on PATH or next to the app; it is only needed for video and non-Opus audio."
                        .to_string(),
                ),
                Ok(_) => None,
            },
        };
        serde_json::json!({ "runtime": runtime, "state": state_payload(&config) })
    })
    .await
    .map_err(|error| error.to_string())
}

fn state_payload(config: &Config) -> serde_json::Value {
    serde_json::json!({
        "api_id": config.api_id,
        "api_hash": config.api_hash,
        "account": config.account,
        "chat": chat_for(config, &config.account),
        "chats": config.chats,
        "theme": config.theme,
        "data_dir": data_dir().display().to_string(),
        "accounts": config.accounts.iter().map(|account| serde_json::json!({
            "key": account.key,
            "label": account.label(),
            "phone": account.phone,
            "name": account.name,
            "username": account.username,
        })).collect::<Vec<_>>(),
    })
}

#[tauri::command]
fn current_state() -> serde_json::Value {
    state_payload(&load_config())
}

#[tauri::command]
fn set_chat(chat: String) -> serde_json::Value {
    let chat = chat.trim().to_string();
    if chat.is_empty() {
        return state_payload(&load_config());
    }
    let (config, _) = update_config(|config| {
        let key = if config.account.is_empty() {
            config.accounts.first().map(|account| account.key.clone()).unwrap_or_default()
        } else {
            config.account.clone()
        };
        // `chats` is the source of truth; `chat` stays as the legacy mirror.
        config.chat = chat.clone();
        if !key.is_empty() {
            config.chats.insert(key, chat);
        }
    });
    state_payload(&config)
}

#[tauri::command]
fn set_account(key: String) -> serde_json::Value {
    let (config, _) = update_config(|config| {
        if account_by_key(config, &key).is_some() {
            config.account = key;
        }
    });
    state_payload(&config)
}

fn probe_output(mut command: Command) -> Option<(bool, String)> {
    hidden(&mut command);
    let output = command.output().ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    Some((output.status.success(), if stdout.is_empty() { stderr } else { stdout }))
}

#[tauri::command]
async fn diagnostics() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let config = load_config();

        let python = match ensure_python(&config) {
            Ok(path) => {
                let mut command = Command::new(&path);
                command.args([
                    "-c",
                    "import sys, telethon; print(sys.version.split()[0] + ' · telethon ' + telethon.__version__)",
                ]);
                match probe_output(command) {
                    Some((true, detail)) => serde_json::json!({
                        "ok": true,
                        "detail": detail,
                        "path": path.display().to_string(),
                    }),
                    Some((false, detail)) => serde_json::json!({
                        "ok": false,
                        "detail": detail.lines().last().unwrap_or("python check failed"),
                    }),
                    None => serde_json::json!({ "ok": false, "detail": "cannot start python" }),
                }
            }
            Err(error) => serde_json::json!({ "ok": false, "detail": error }),
        };

        let ffmpeg = match find_ffmpeg() {
            Some(path) => {
                let mut command = Command::new(&path);
                command.arg("-version");
                match probe_output(command) {
                    Some((true, detail)) => {
                        let version = detail
                            .lines()
                            .next()
                            .unwrap_or("ffmpeg")
                            .split(" version ")
                            .nth(1)
                            .map(|rest| rest.split_whitespace().next().unwrap_or(rest))
                            .unwrap_or("ffmpeg");
                        serde_json::json!({
                            "ok": true,
                            "detail": format!("version {version}"),
                            "path": path.display().to_string(),
                        })
                    }
                    Some((false, detail)) => serde_json::json!({
                        "ok": false,
                        "detail": detail.lines().next().unwrap_or("ffmpeg check failed"),
                    }),
                    None => serde_json::json!({ "ok": false, "detail": "cannot start ffmpeg" }),
                }
            }
            None => serde_json::json!({
                "ok": false,
                "detail": "not on PATH and no <app>\\bin\\ffmpeg.exe",
            }),
        };

        let telegram = match active_account(&config) {
            Some(account) => {
                let session = session_path(&account);
                match ensure_python(&config) {
                    Ok(python_path) => {
                        let result = run_sender(
                            &config,
                            &python_path,
                            &session,
                            &["--whoami".to_string()],
                            None,
                        );
                        if result.get("ok").and_then(|value| value.as_bool()) == Some(true) {
                            let user = result.get("user").cloned().unwrap_or_default();
                            let name = user
                                .get("name")
                                .and_then(|value| value.as_str())
                                .unwrap_or("account");
                            let username = user
                                .get("username")
                                .and_then(|value| value.as_str())
                                .unwrap_or("");
                            let id = user
                                .get("id")
                                .and_then(|value| value.as_i64())
                                .unwrap_or_default();
                            let handle = if username.is_empty() {
                                id.to_string()
                            } else {
                                format!("@{username}")
                            };
                            serde_json::json!({
                                "ok": true,
                                "detail": format!("{name} · {handle}"),
                            })
                        } else {
                            let error = result
                                .get("error")
                                .and_then(|value| value.as_str())
                                .unwrap_or("session check failed");
                            serde_json::json!({ "ok": false, "detail": error })
                        }
                    }
                    Err(error) => serde_json::json!({ "ok": false, "detail": error }),
                }
            }
            None => serde_json::json!({ "ok": false, "detail": "no account logged in" }),
        };

        serde_json::json!({ "python": python, "telegram": telegram, "ffmpeg": ffmpeg })
    })
    .await
    .map_err(|error| error.to_string())
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("only http(s) links can be opened".into());
    }
    #[cfg(windows)]
    {
        let mut command = Command::new("cmd");
        command.args(["/C", "start", "", &url]);
        hidden(&mut command);
        return command.spawn().map(|_| ()).map_err(|error| error.to_string());
    }
    #[cfg(target_os = "macos")]
    {
        return Command::new("open")
            .arg(&url)
            .spawn()
            .map(|_| ())
            .map_err(|error| error.to_string());
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        return Command::new("xdg-open")
            .arg(&url)
            .spawn()
            .map(|_| ())
            .map_err(|error| error.to_string());
    }
}

#[tauri::command]
fn set_theme(theme: String) -> serde_json::Value {
    let (config, _) = update_config(|config| {
        if THEMES.contains(&theme.as_str()) {
            config.theme = theme;
        }
    });
    state_payload(&config)
}

#[tauri::command]
fn set_api_credentials(api_id: u64, api_hash: String) -> serde_json::Value {
    let api_hash = api_hash.trim().to_string();
    let (config, _) = update_config(|config| {
        if api_id > 0 && !api_hash.is_empty() {
            config.api_id = api_id;
            config.api_hash = api_hash;
        }
    });
    state_payload(&config)
}

#[tauri::command]
fn remove_account(key: String) -> serde_json::Value {
    let (config, _) = update_config(|config| {
        if let Some(index) = config.accounts.iter().position(|account| account.key == key) {
            let account = config.accounts.remove(index);
            let path = session_path(&account);
            let _ = std::fs::remove_file(&path);
            let _ = std::fs::remove_file(PathBuf::from(format!("{}-journal", path.display())));
            config.chats.remove(&key);
            if config.account == key {
                config.account = config
                    .accounts
                    .first()
                    .map(|entry| entry.key.clone())
                    .unwrap_or_default();
            }
        }
    });
    state_payload(&config)
}

#[tauri::command]
async fn list_targets() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let config = load_config();
        let account = match active_account(&config) {
            Some(account) => account,
            None => return Ok(serde_json::json!({ "ok": false, "error": "no account logged in" })),
        };
        let session = session_path(&account);
        with_python(&config, |python| {
            run_sender(
                &config,
                python,
                &session,
                &["--list-targets".into(), "--limit".into(), "50".into()],
                None,
            )
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn send_files(
    app: tauri::AppHandle,
    account: String,
    chat: String,
    paths: Vec<String>,
) -> Result<serde_json::Value, String> {
    if paths.is_empty() {
        return Ok(serde_json::json!({ "ok": false, "error": "no files dropped" }));
    }
    let chat = chat.trim().to_string();
    if chat.is_empty() {
        return Ok(serde_json::json!({ "ok": false, "error": "no recipient selected" }));
    }
    // The caller froze account/chat when the batch was queued: never consult
    // the active account or config.chat here.
    let requested = account;
    tauri::async_runtime::spawn_blocking(move || {
        let config = load_config();
        let account = match account_by_key(&config, &requested) {
            Some(account) => account.clone(),
            None => {
                return Ok(serde_json::json!({
                    "ok": false,
                    "error": "account is not logged in",
                    "account": requested,
                    "chat": chat,
                }))
            }
        };
        let session = session_path(&account);
        if !session.exists() {
            return Ok(serde_json::json!({
                "ok": false,
                "error": "this account has no session file, log in again",
                "account": account.key,
                "chat": chat,
            }));
        }
        if needs_ffmpeg(&paths) && find_ffmpeg().is_none() {
            return Ok(serde_json::json!({
                "ok": false,
                "error": missing_ffmpeg_error(&paths),
                "account": account.key,
                "chat": chat,
            }));
        }
        // audio becomes a voice note, video a circle, everything else a document
        let mut extra = vec!["--chat".to_string(), chat.clone(), "--circle".to_string()];
        extra.extend(paths.iter().cloned());

        let handle = app.clone();
        let emit = move |value: serde_json::Value| {
            let _ = handle.emit("send-progress", value);
        };
        let mut payload = with_python(&config, |python| {
            run_sender(
                &config,
                python,
                &session,
                &extra,
                Some(&emit as &dyn Fn(serde_json::Value)),
            )
        })?;
        if let Some(object) = payload.as_object_mut() {
            object.insert("account".into(), serde_json::Value::String(account.key));
            object.insert("chat".into(), serde_json::Value::String(chat));
        }
        Ok(payload)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn login_start(phone: String) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let config = load_config();
        if config.api_id == 0 || config.api_hash.trim().is_empty() {
            return Ok(serde_json::json!({
                "ok": false,
                "error": "set api_id and api_hash in settings first (my.telegram.org → API development tools)"
            }));
        }
        let session = accounts_dir().join(format!("{}.session", sanitize_key(&phone)));
        with_python(&config, |python| {
            run_sender(
                &config,
                python,
                &session,
                &["--login-send-code".into(), phone.clone()],
                None,
            )
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn login_code(
    phone: String,
    code: String,
    phone_hash: String,
) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let config = load_config();
        let key = sanitize_key(&phone);
        let session = accounts_dir().join(format!("{key}.session"));
        let python = ensure_python(&config)?;
        let result = run_sender(
            &config,
            &python,
            &session,
            &[
                "--login-send-code".into(),
                phone.clone(),
                "--login-code".into(),
                code,
                "--phone-hash".into(),
                phone_hash,
            ],
            None,
        );
        update_config(|config| finalize_login(config, &key, &phone, &result)).1
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn login_password(phone: String, password: String) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let config = load_config();
        let key = sanitize_key(&phone);
        let session = accounts_dir().join(format!("{key}.session"));
        let python = ensure_python(&config)?;
        let result = run_sender_with_env(
            &config,
            &python,
            &session,
            &["--login-2fa".into()],
            None,
            &[("VOICEDROP_2FA", password.as_str())],
        );
        update_config(|config| finalize_login(config, &key, &phone, &result)).1
    })
    .await
    .map_err(|error| error.to_string())?
}

fn finalize_login(
    config: &mut Config,
    key: &str,
    phone: &str,
    result: &serde_json::Value,
) -> Result<serde_json::Value, String> {
    if result.get("ok").and_then(|value| value.as_bool()) == Some(true) {
        let user = result.get("user").cloned().unwrap_or_default();
        let name = user
            .get("name")
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_string();
        let username = user
            .get("username")
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_string();
        let session = format!("{key}.session");
        if let Some(account) = config.accounts.iter_mut().find(|entry| entry.key == key) {
            account.name = name;
            account.username = username;
            account.phone = phone.to_string();
            account.session = session;
        } else {
            config.accounts.push(Account {
                key: key.to_string(),
                session,
                name,
                username,
                phone: phone.to_string(),
            });
        }
        config.account = key.to_string();
        return Ok(serde_json::json!({
            "ok": true,
            "account": key,
            "state": state_payload(config),
        }));
    }
    if result.get("needs_password").and_then(|value| value.as_bool()) == Some(true) {
        return Ok(serde_json::json!({ "ok": false, "needs_password": true }));
    }
    Ok(result.clone())
}

fn sanitize_key(phone: &str) -> String {
    let digits: String = phone.chars().filter(|ch| ch.is_ascii_digit()).collect();
    if digits.is_empty() {
        "account".into()
    } else {
        digits
    }
}

fn oneshot(args: &[String]) -> i32 {
    // voice-drop --oneshot <chat> <result.json> <file> [file...]
    if args.len() < 4 {
        eprintln!("usage: voice-drop --oneshot <chat> <result.json> <file> [file...]");
        return 2;
    }
    let requested_chat = args[1].trim().to_string();
    let out = PathBuf::from(&args[2]);
    let files: Vec<String> = args[3..].to_vec();
    let config = load_config();
    let account = match active_account(&config) {
        Some(account) => account,
        None => {
            eprintln!("no account logged in");
            return 2;
        }
    };
    // An empty chat argument means "whatever this account already sends to".
    let chat = if requested_chat.is_empty() {
        chat_for(&config, &account.key)
    } else {
        requested_chat
    };
    if chat.is_empty() {
        eprintln!("no recipient selected");
        return 2;
    }
    if needs_ffmpeg(&files) && find_ffmpeg().is_none() {
        eprintln!("{}", missing_ffmpeg_error(&files));
        return 2;
    }
    let python = match ensure_python(&config) {
        Ok(python) => python,
        Err(error) => {
            eprintln!("{error}");
            return 2;
        }
    };
    let mut extra = vec!["--chat".to_string(), chat, "--circle".to_string()];
    extra.extend(files);
    let result = run_sender(&config, &python, &session_path(&account), &extra, None);
    let text = serde_json::to_string_pretty(&result).unwrap_or_default();
    let _ = std::fs::write(&out, &text);
    println!("{text}");
    if result.get("ok").and_then(|value| value.as_bool()) == Some(true) {
        0
    } else {
        1
    }
}

fn acquire_instance_lock() -> Option<std::fs::File> {
    let path = data_dir().join("instance.lock");
    let parent = path.parent()?.to_path_buf();
    std::fs::create_dir_all(&parent).ok()?;
    let file = std::fs::OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(&path)
        .ok()?;
    match file.try_lock() {
        Ok(()) => Some(file),
        Err(_) => None,
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("--oneshot") {
        std::process::exit(oneshot(&args));
    }

    let _instance_guard = match acquire_instance_lock() {
        Some(guard) => guard,
        // Another window is already open; two instances would fight over the
        // Telethon session files.
        None => std::process::exit(0),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            prepare,
            diagnostics,
            open_url,
            current_state,
            set_chat,
            set_theme,
            set_account,
            set_api_credentials,
            remove_account,
            list_targets,
            send_files,
            login_start,
            login_code,
            login_password
        ])
        .run(tauri::generate_context!())
        .expect("error while running VoiceDrop");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_key_keeps_digits_only() {
        assert_eq!(sanitize_key("+7 (987) 654-32-10"), "79876543210");
        assert_eq!(sanitize_key("79876543210"), "79876543210");
        assert_eq!(sanitize_key(""), "account");
        assert_eq!(sanitize_key("@handle"), "account");
    }

    #[test]
    fn needs_ffmpeg_only_when_a_file_is_transcoded() {
        let check = |name: &str, expected: bool| {
            let paths = vec![name.to_string()];
            assert_eq!(needs_ffmpeg(&paths), expected, "{name}");
        };
        check("notes.pdf", false);
        check("track.mp3", true);
        check("voice.ogg", false);
        check("clip.mp4", true);
        check("Clip.MOV", true);
        check("voice.opus", false);
        check("archive.zip", false);
    }

    #[test]
    fn needs_ffmpeg_handles_empty_and_mixed_batches() {
        assert!(!needs_ffmpeg(&[]));
        let mixed = vec!["notes.pdf".to_string(), "track.mp3".to_string()];
        assert!(needs_ffmpeg(&mixed));
        let documents = vec!["notes.pdf".to_string(), "image.png".to_string()];
        assert!(!needs_ffmpeg(&documents));
    }

    #[test]
    fn migrate_chats_moves_the_legacy_recipient() {
        let mut legacy = Config {
            account: "79876543210".into(),
            chat: "@durov".into(),
            ..Config::default()
        };
        assert!(migrate_chats(&mut legacy));
        assert_eq!(
            legacy.chats.get("79876543210").map(String::as_str),
            Some("@durov")
        );
        assert!(!migrate_chats(&mut legacy), "a second load is a no-op");

        let mut per_account = Config {
            account: "1".into(),
            chat: "@legacy".into(),
            ..Config::default()
        };
        per_account.chats.insert("1".into(), "@own".into());
        assert!(!migrate_chats(&mut per_account));
        assert_eq!(
            per_account.chats.get("1").map(String::as_str),
            Some("@own")
        );

        let mut anonymous = Config {
            chat: "@durov".into(),
            ..Config::default()
        };
        assert!(!migrate_chats(&mut anonymous));
        assert!(anonymous.chats.is_empty());
    }

    #[test]
    fn chat_for_never_borrows_another_accounts_recipient() {
        let mut config = Config::default();
        config.chat = "@legacy".into();
        assert_eq!(chat_for(&config, "1"), "@legacy");

        config.chats.insert("2".into(), "@second".into());
        assert_eq!(chat_for(&config, "2"), "@second");
        assert_eq!(chat_for(&config, "1"), "");
    }

    #[test]
    fn short_keeps_the_last_line_and_truncates() {
        assert_eq!(short("warning\nboom\n\n", 40), "boom");
        assert_eq!(short("", 40), "");
        assert_eq!(short(&"x".repeat(20), 5), "xxxxx\u{2026}");
    }
}
