//! Connects this computer as a device, doing what the devices page otherwise
//! asks the user to do in a terminal: install the server's own connector, then
//! `cheesehost link connect`. Nothing here re-implements the connector — the
//! binary, its login flow and its background service are the ones every other
//! machine gets. What this adds is what a terminal user supplies by hand
//! (platform::prepare), a place to approve the login, and progress the page can
//! show step by step. The page owns every word shown; this reports step ids.

use std::path::Path;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde::Serialize;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};
use tokio::process::Command;

use crate::platform;

/// What the page hears while this computer is connected.
pub struct Events<'a> {
    /// A step has started: "removeOld", "tools", "download", "approve", "runtime".
    pub step: &'a (dyn Fn(&'static str) + Send + Sync),
    /// How far the step that downloads has got, 0–100.
    pub percent: &'a (dyn Fn(u8) + Send + Sync),
    /// The approval code, for the page to approve as the signed-in person.
    pub code: &'a (dyn Fn(&str) + Send + Sync),
}

/// Why connecting stopped: the step it stopped at, or "cancelled", and what
/// the tool said, for the page to show folded away.
#[derive(Debug, Serialize)]
pub struct Failure {
    pub step: &'static str,
    pub detail: String,
}

impl Failure {
    pub fn at(step: &'static str, detail: impl Into<String>) -> Self {
        Failure { step, detail: detail.into() }
    }

    fn cancelled() -> Self {
        Failure::at("cancelled", "")
    }
}

/// The approval code is the one field of the link cheesehost prints.
pub fn approval_code(printed: &str) -> Option<String> {
    let rest = &printed[printed.find("/connect?code=")? + "/connect?code=".len()..];
    let code: String = rest
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric() || *c == '-' || *c == '_')
        .collect();
    // The link is printed on its own line; until its newline arrives the code may be cut short.
    let complete = rest[code.len()..].starts_with(char::is_whitespace);
    (complete && !code.is_empty()).then_some(code)
}

/// The last percentage in what curl's progress bar printed ("####   42.7%").
pub fn percent(printed: &str) -> Option<u8> {
    let end = printed.rfind('%')?;
    let digits: String = printed[..end]
        .chars()
        .rev()
        .take_while(|c| c.is_ascii_digit() || *c == '.')
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    let value: f32 = digits.parse().ok()?;
    (0.0..=100.0).contains(&value).then_some(value as u8)
}

/// The device this computer's cheesehost is logged in as, if any.
pub async fn stored_device_id() -> Option<String> {
    let config = tokio::fs::read_to_string(platform::cheesehost_config()).await.ok()?;
    let config: serde_json::Value = serde_json::from_str(&config).ok()?;
    config.get("device_id")?.as_str().map(str::to_string)
}

/// What is running for the page right now, so it can be cancelled: the child
/// process, and a flag a waiting step checks between polls.
#[derive(Default)]
pub struct Running {
    pid: Mutex<Option<u32>>,
    busy: AtomicBool,
    cancelled: AtomicBool,
}

impl Running {
    /// Whether something is under way (an update must not restart into it).
    pub fn busy(&self) -> bool {
        self.busy.load(Ordering::Relaxed)
    }

    pub fn cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Relaxed)
    }

    fn begin(&self) -> bool {
        if self.busy.swap(true, Ordering::Relaxed) {
            return false;
        }
        self.cancelled.store(false, Ordering::Relaxed);
        true
    }

    fn end(&self) {
        *self.pid.lock().unwrap() = None;
        self.busy.store(false, Ordering::Relaxed);
    }
}

/// Stops what is running, if anything. The step under way sees the flag and
/// reports a cancel.
pub fn cancel(running: &Running) {
    running.cancelled.store(true, Ordering::Relaxed);
    if let Some(pid) = running.pid.lock().unwrap().take() {
        platform::kill(pid);
    }
}

/// Runs `cmd` to the end, cancellable, reporting each percentage its stderr
/// prints and each line its stdout prints. Fails with the last lines it said.
pub async fn run(
    mut cmd: Command,
    running: &Running,
    on_percent: &(dyn Fn(u8) + Send + Sync),
    on_line: &mut (dyn FnMut(&str) + Send),
) -> Result<(), String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    *running.pid.lock().unwrap() = child.id();
    let mut stdout = BufReader::new(child.stdout.take().unwrap()).lines();
    let mut stderr = child.stderr.take().unwrap();
    let mut said = String::new();
    let mut err = Vec::new();
    let mut buf = [0u8; 4096];
    let (mut out_open, mut err_open) = (true, true);
    // curl redraws its bar with \r and never ends the line, so stderr is read
    // as it comes rather than by lines.
    while out_open || err_open {
        tokio::select! {
            line = stdout.next_line(), if out_open => match line {
                Ok(Some(line)) => {
                    on_line(&line);
                    said.push_str(&line);
                    said.push('\n');
                }
                _ => out_open = false,
            },
            n = stderr.read(&mut buf), if err_open => match n {
                Ok(n) if n > 0 => {
                    let chunk = String::from_utf8_lossy(&buf[..n]);
                    if let Some(p) = percent(&chunk) {
                        on_percent(p);
                    }
                    err.extend_from_slice(&buf[..n]);
                }
                _ => err_open = false,
            },
        }
    }
    let status = child.wait().await.map_err(|e| e.to_string())?;
    *running.pid.lock().unwrap() = None;
    if running.cancelled() {
        return Err(String::new());
    }
    if status.success() {
        return Ok(());
    }
    let err = String::from_utf8_lossy(&err).replace('\r', "\n");
    let tail: Vec<_> = (said + &err).trim().lines().rev().take(3).map(str::to_string).collect();
    Err(tail.into_iter().rev().collect::<Vec<_>>().join("\n"))
}

pub async fn connect(
    origin: &str,
    resources: &Path,
    known_device_ids: &[String],
    running: &Running,
    events: &Events<'_>,
) -> Result<(), Failure> {
    if !running.begin() {
        return Err(Failure::at("busy", ""));
    }
    let result = connect_steps(origin, resources, known_device_ids, running, events).await;
    running.end();
    result
}

async fn connect_steps(
    origin: &str,
    resources: &Path,
    known_device_ids: &[String],
    running: &Running,
    events: &Events<'_>,
) -> Result<(), Failure> {
    platform::prepare(resources, events, running).await?;
    if running.cancelled() {
        return Err(Failure::cancelled());
    }

    (events.step)("download");
    platform::install_connector(origin, running, events.percent)
        .await
        .map_err(|e| if running.cancelled() { Failure::cancelled() } else { Failure::at("download", e) })?;

    // A credential on disk for a device the signed-in user does not own (unbound
    // since, or someone else's) would connect as that device or be refused, so
    // it is dropped and the login runs again.
    if stored_device_id().await.is_some_and(|id| !known_device_ids.contains(&id)) {
        let _ = platform::cheesehost().args(["auth", "logout"]).output().await;
    }

    // On Windows `link connect` also fetches the runtime the server's commands
    // need, which is most of the wait the first time.
    (events.step)("approve");
    let mut cmd = platform::cheesehost();
    cmd.args(["link", "connect", &format!("{origin}/connector")]);
    let mut printed = String::new();
    let mut sent = false;
    let mut on_line = |line: &str| {
        printed.push_str(line);
        printed.push('\n');
        if !sent {
            if let Some(c) = approval_code(&printed) {
                sent = true;
                (events.code)(&c);
            }
        }
        if line.contains("downloading") {
            (events.step)("runtime");
        }
    };
    run(cmd, running, events.percent, &mut on_line).await.map_err(|e| {
        if running.cancelled() {
            Failure::cancelled()
        } else {
            Failure::at("approve", e)
        }
    })
}

/// Logs in the Claude Code the platform runs here, with a Claude subscription
/// or, `console`, an Anthropic Console account. It opens in the browser and
/// finishes there; `on_step` hears "preparing" while the build downloads and
/// "browser" once the sign-in page is open.
pub async fn claude_login(
    running: &Running,
    console: bool,
    on_step: &(dyn Fn(&'static str) + Send + Sync),
) -> Result<(), Failure> {
    if !running.begin() {
        return Err(Failure::at("busy", ""));
    }
    let mut cmd = platform::cheesehost();
    cmd.args(["claude", "login"]);
    if console {
        cmd.arg("--console");
    }
    let mut on_line = |line: &str| {
        if line.contains("Downloading Claude Code") {
            on_step("preparing");
        } else if line.contains("Opening browser") || line.contains("visit:") {
            on_step("browser");
        }
    };
    let result = run(cmd, running, &|_: u8| {}, &mut on_line).await;
    let cancelled = running.cancelled();
    running.end();
    result.map_err(|e| if cancelled { Failure::cancelled() } else { Failure::at("login", e) })
}

/// Logs the platform's Claude Code out on this computer.
pub async fn claude_logout() -> Result<(), String> {
    let out = platform::cheesehost()
        .args(["claude", "logout"])
        .stdin(Stdio::null())
        .output()
        .await
        .map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
    }
}

/// Stops this computer's connector, keeps it from coming back at the next
/// login, and forgets its credential: the server has already unbound the
/// device, and a connector left running would dial in to be refused.
pub async fn disconnect() -> Result<(), String> {
    for args in [
        &["link", "disconnect", "--force"][..],
        &["link", "no-auto-connect"][..],
        &["auth", "logout"][..],
    ] {
        let out = platform::cheesehost()
            .args(args)
            .stdin(Stdio::null())
            .output()
            .await
            .map_err(|e| e.to_string())?;
        if !out.status.success() && args[0] == "link" && args[1] == "disconnect" {
            return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{approval_code, percent};

    #[test]
    fn reads_the_code_out_of_the_link_cheesehost_prints() {
        let printed = "Not logged in yet — starting login first.\n\
                       Open this link and approve this machine:\n\n    \
                       https://okcheese.com/connect?code=3f2a9c0d1e4b4b7e9a51c2d3e4f5a6b7\n\n\
                       Waiting for approval…\n";
        assert_eq!(approval_code(printed).as_deref(), Some("3f2a9c0d1e4b4b7e9a51c2d3e4f5a6b7"));
    }

    #[test]
    fn has_no_code_before_the_link_line_is_complete() {
        assert_eq!(approval_code("    https://okcheese.com/connect?code=3f2a9c"), None);
    }

    #[test]
    fn reads_how_far_a_download_has_got_from_curls_bar() {
        assert_eq!(percent("\r#####                       12.5%"), Some(12));
        assert_eq!(percent("\r####   7.0%\r##########   42.7%"), Some(42));
        assert_eq!(percent("\r######################## 100.0%"), Some(100));
        assert_eq!(percent("curl: (56) Recv failure"), None);
    }
}
