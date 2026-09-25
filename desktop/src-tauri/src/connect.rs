//! Connects this computer as a device, doing what the 「我的设备」 page otherwise
//! asks the user to do in a terminal: install the server's own connector, then
//! `cheesehost link connect`. Nothing here re-implements the connector — the
//! binary, its login flow and its background service are the ones every other
//! machine gets. What this adds is what a terminal user supplies by hand
//! (platform::prepare), and a place to approve the login.

use std::path::Path;
use std::process::Stdio;
use std::sync::Mutex;

use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};

use crate::platform;

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

/// The device this computer's cheesehost is logged in as, if any.
pub async fn stored_device_id() -> Option<String> {
    let config = tokio::fs::read_to_string(platform::cheesehost_config()).await.ok()?;
    let config: serde_json::Value = serde_json::from_str(&config).ok()?;
    config.get("device_id")?.as_str().map(str::to_string)
}

/// The running `cheesehost link connect`, so the page can cancel it.
#[derive(Default)]
pub struct Running(pub Mutex<Option<u32>>);

pub async fn connect(
    origin: &str,
    resources: &Path,
    known_device_ids: &[String],
    running: &Running,
    step: impl Fn(&str),
    code: impl Fn(&str),
) -> Result<(), String> {
    platform::prepare(resources, &step).await?;

    step("正在下载连接程序");
    platform::install_connector(origin)
        .await
        .map_err(|e| format!("下载连接程序失败：{e}"))?;

    // A credential on disk for a device the signed-in user does not own (unbound
    // since, or someone else's) would connect as that device or be refused, so
    // it is dropped and the login runs again.
    if stored_device_id().await.is_some_and(|id| !known_device_ids.contains(&id)) {
        let _ = platform::cheesehost().args(["auth", "logout"]).output().await;
    }

    // On Windows `link connect` also fetches the runtime the server's commands
    // need, which is most of the wait the first time.
    step("正在接入");
    let mut child = platform::cheesehost()
        .args(["link", "connect", &format!("{origin}/connector")])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("cheesehost: {e}"))?;
    *running.0.lock().unwrap() = child.id();

    let mut lines = BufReader::new(child.stdout.take().unwrap()).lines();
    let mut printed = String::new();
    let mut sent = false;
    while let Ok(Some(line)) = lines.next_line().await {
        printed.push_str(&line);
        printed.push('\n');
        if !sent {
            if let Some(c) = approval_code(&printed) {
                sent = true;
                code(&c);
            }
        }
    }
    let status = child.wait().await.map_err(|e| e.to_string())?;
    if running.0.lock().unwrap().take().is_none() {
        return Err("已取消".into());
    }
    if status.success() {
        return Ok(());
    }
    let mut err = String::new();
    if let Some(mut stderr) = child.stderr.take() {
        let _ = stderr.read_to_string(&mut err).await;
    }
    let tail: Vec<_> = (printed + &err).trim().lines().rev().take(3).map(str::to_string).collect();
    Err(format!("接入失败：{}", tail.into_iter().rev().collect::<Vec<_>>().join(" ")))
}

/// Stops the running `link connect`, if any. `connect` sees the pid taken and reports a cancel.
pub fn cancel(running: &Running) {
    if let Some(pid) = running.0.lock().unwrap().take() {
        platform::kill(pid);
    }
}

#[cfg(test)]
mod tests {
    use super::approval_code;

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
}
