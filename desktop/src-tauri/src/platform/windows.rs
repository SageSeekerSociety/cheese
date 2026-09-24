//! On Windows cheesehost runs natively, and brings what the server's commands
//! need (python3, and Git for Windows' shell) on its own at `link connect`. So
//! all this adds is the one step install.sh does elsewhere: put the binary in a
//! directory the user can write, where it can update itself.

use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};

use tokio::process::Command;

// No console window flashes up for any of the commands below.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn env_dir(name: &str) -> PathBuf {
    PathBuf::from(std::env::var(name).unwrap_or_default())
}

fn cheesehost_path() -> PathBuf {
    env_dir("LOCALAPPDATA").join("cheese").join("bin").join("cheesehost.exe")
}

/// cheesehost reads <os.UserConfigDir()>\cheese, which on Windows is %APPDATA%.
pub fn cheesehost_config() -> PathBuf {
    env_dir("APPDATA").join("cheese").join("config.json")
}

pub fn cheesehost() -> Command {
    let mut cmd = Command::new(cheesehost_path());
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

pub fn kill(pid: u32) {
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

/// Downloads the connector the server publishes for this machine. Once it is
/// there it keeps itself current (the server tells it when it is stale), and a
/// running copy could not be overwritten anyway.
pub async fn install_connector(origin: &str) -> Result<(), String> {
    let path = cheesehost_path();
    if path.exists() {
        return Ok(());
    }
    let arch = if std::env::consts::ARCH == "aarch64" { "arm64" } else { "amd64" };
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let partial = path.with_extension("exe.part");
    let out = Command::new("curl.exe")
        .args(["-fsSL", "-o"])
        .arg(&partial)
        .arg(format!("{origin}/connector/latest/windows-{arch}/cheesehost.exe"))
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .await
        .map_err(|e| format!("curl.exe: {e}"))?;
    if !out.status.success() {
        let _ = std::fs::remove_file(&partial);
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    std::fs::rename(&partial, &path).map_err(|e| e.to_string())
}

pub async fn prepare(_resources: &Path, _step: &impl Fn(&str)) -> Result<(), String> {
    Ok(())
}
