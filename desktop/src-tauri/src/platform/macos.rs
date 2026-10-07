//! On a Mac cheesehost runs natively, installed by the server's install.sh like
//! on any other Mac. What a Mac without Homebrew lacks is tmux, which cheesehost
//! will not start without, and git and python3, which come with Apple's command
//! line tools.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use crate::connect::{self, Events, Failure, Running};

/// Where a connector installed for the whole machine lives (cheesehost's
/// `machineWideService`).
const MACHINE_WIDE_SERVICE: &str = "/Library/LaunchDaemons/cheese.plist";

// An app opened from Finder inherits launchd's bare PATH, not the user's shell.
const PATH: &str = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin";

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default())
}

/// cheesehost reads <os.UserConfigDir()>/cheese, which on macOS is here.
pub fn cheesehost_config() -> PathBuf {
    home().join("Library/Application Support/cheese/config.json")
}

/// install.sh puts cheesehost in ~/.local/bin.
pub fn cheesehost() -> Command {
    let mut cmd = Command::new(home().join(".local/bin/cheesehost"));
    cmd.env("PATH", PATH);
    cmd
}

/// Ends what `connect::run` started: it leads its own process group, and the
/// whole group goes.
pub fn kill(pid: u32) {
    let _ = std::process::Command::new("/bin/kill")
        .args(["-TERM", "--", &format!("-{pid}")])
        .status();
}

/// Runs a sh script, handed over on stdin. Braces make sh read all of it
/// before running any, so a command inside that reads stdin gets end-of-file.
async fn sh(script: &str) -> Result<String, String> {
    let mut child = Command::new("/bin/sh")
        .arg("-s")
        .env("PATH", PATH)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut stdin = child.stdin.take().unwrap();
    stdin.write_all(format!("{{\n{script}\n}}\n").as_bytes()).await.map_err(|e| e.to_string())?;
    drop(stdin);
    let out = child.wait_with_output().await.map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&out.stdout).into_owned() + &String::from_utf8_lossy(&out.stderr);
    if out.status.success() {
        Ok(text)
    } else {
        Err(text.trim().to_string())
    }
}

/// Runs the server's install.sh, which puts the connector the server publishes
/// for this Mac in ~/.local/bin; with CHEESE_PROGRESS set it shows curl's bar,
/// whose percentages the page sees.
pub async fn install_connector(
    origin: &str,
    running: &Running,
    on_percent: &(dyn Fn(u8) + Send + Sync),
) -> Result<(), String> {
    let mut cmd = Command::new("/bin/sh");
    cmd.arg("-c")
        .arg(format!("curl -fsSL '{origin}/connector/install.sh' | CHEESE_PROGRESS=1 sh"))
        .env("PATH", PATH);
    connect::run(cmd, running, on_percent, &mut |_: &str| {}).await
}

pub async fn prepare(resources: &Path, events: &Events<'_>, running: &Running) -> Result<(), Failure> {
    // Until 2026-08-28 `cheesehost link connect` installed itself for the whole
    // machine, with sudo. cheesehost will not install a second connector beside
    // that one (they would share one device credential), and taking it out needs
    // an administrator: ask the way macOS asks, in its own password dialog,
    // rather than leave a command for a terminal.
    if Path::new(MACHINE_WIDE_SERVICE).exists() {
        (events.step)("removeOld");
        sh(&format!(
            r#"osascript -e 'do shell script "launchctl bootout system/cheese 2>/dev/null; rm -f {MACHINE_WIDE_SERVICE}" with administrator privileges'"#
        ))
        .await
        .map_err(|e| Failure::at("removeOld", e))?;
    }

    // Without the command line tools /usr/bin/git is a stub that only offers to
    // install them, so ask for that install and wait for it.
    if sh("xcode-select -p").await.is_err() {
        (events.step)("tools");
        let _ = sh("xcode-select --install").await;
        let mut installed = false;
        for _ in 0..720 {
            tokio::time::sleep(Duration::from_secs(5)).await;
            if running.cancelled() {
                return Ok(());
            }
            if sh("xcode-select -p").await.is_ok() {
                installed = true;
                break;
            }
        }
        if !installed {
            return Err(Failure::at("tools", ""));
        }
    }

    // cheesehost prefers a private tmux over the one on PATH, so a Mac that has
    // none gets the one shipped inside this app. Copied out of a downloaded app
    // it keeps the quarantine flag, and macOS will not exec a quarantined binary
    // for a background service.
    let bundled = resources.join("tmux");
    sh(&format!(
        r#"command -v tmux >/dev/null && exit 0
dir="$HOME/Library/Application Support/cheese/bin"
[ -x "$dir/tmux" ] && exit 0
mkdir -p "$dir" && cp '{}' "$dir/tmux" && chmod 755 "$dir/tmux"
xattr -d com.apple.quarantine "$dir/tmux" 2>/dev/null || true"#,
        bundled.display()
    ))
    .await
    .map(drop)
    .map_err(|e| Failure::at("tools", e))
}
