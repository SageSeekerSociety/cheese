//! On a Mac cheesehost runs natively. What a Mac without Homebrew lacks is tmux,
//! which cheesehost will not start without, and git and python3, which come with
//! Apple's command line tools.

use std::path::Path;
use std::time::Duration;

use tokio::process::Command;

use crate::connect::sh as run_sh;

// cheesehost reads <os.UserConfigDir()>/cheese, which on macOS is here.
pub const CHEESEHOST_CONFIG: &str = "$HOME/Library/Application Support/cheese/config.json";

// An app opened from Finder inherits launchd's bare PATH, not the user's shell.
const PATH: &str = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin";

pub const PRELUDE: &str = "";

/// A sh reading its script from stdin (connect::spawn_script).
pub fn shell() -> Command {
    let mut cmd = Command::new("/bin/sh");
    cmd.arg("-s").env("PATH", PATH);
    cmd
}

pub fn kill(pid: u32) {
    let _ = std::process::Command::new("/bin/kill").arg(pid.to_string()).status();
}

pub async fn prepare(resources: &Path, step: &impl Fn(&str)) -> Result<(), String> {
    // Without the command line tools /usr/bin/git is a stub that only offers to
    // install them, so ask for that install and wait for it.
    if run_sh("xcode-select -p").await.is_err() {
        step("需要先安装苹果的「命令行开发者工具」，请在弹出的窗口里点「安装」");
        let _ = run_sh("xcode-select --install").await;
        let mut installed = false;
        for _ in 0..720 {
            tokio::time::sleep(Duration::from_secs(5)).await;
            if run_sh("xcode-select -p").await.is_ok() {
                installed = true;
                break;
            }
        }
        if !installed {
            return Err("命令行开发者工具还没装好，装完后再点一次「接入这台电脑」".into());
        }
    }

    step("正在准备运行环境");
    // cheesehost prefers a private tmux over the one on PATH, so a Mac that has
    // none gets the one shipped inside this app. Copied out of a downloaded app
    // it keeps the quarantine flag, and macOS will not exec a quarantined binary
    // for a background service.
    let bundled = resources.join("tmux");
    run_sh(&format!(
        r#"command -v tmux >/dev/null && exit 0
dir="$HOME/Library/Application Support/cheese/bin"
[ -x "$dir/tmux" ] && exit 0
mkdir -p "$dir" && cp '{}' "$dir/tmux" && chmod 755 "$dir/tmux"
xattr -d com.apple.quarantine "$dir/tmux" 2>/dev/null || true"#,
        bundled.display()
    ))
    .await
    .map(drop)
    .map_err(|e| format!("准备 tmux 失败：{e}"))
}
