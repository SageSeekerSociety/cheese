//! cheesehost does not run on Windows — it needs tmux and a POSIX pty, and the
//! work it hosts needs bash, git and python3 — so on Windows it runs in a Linux
//! of its own under WSL: a distro named Cheese, apart from any the user keeps,
//! where it is the Linux build every other Linux machine gets.

use std::os::windows::process::CommandExt;
use std::path::Path;

use tokio::process::Command;

use crate::connect::{run_script, sh as run_sh};

// Inside the distro cheesehost is the Linux build, reading ~/.config/cheese.
pub const CHEESEHOST_CONFIG: &str = "$HOME/.config/cheese/config.json";

const DISTRO: &str = "Cheese";
const USER: &str = "cheese";
// No console window flashes up for any of the commands below.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const DETACHED_PROCESS: u32 = 0x0000_0008;

// Ubuntu's own WSL image. The Chinese university mirrors come first: this is a
// 350 MB download, and cloud-images.ubuntu.com is slow from where most users are.
const ROOTFS: &str = "ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz";
const ROOTFS_HOSTS: [&str; 3] = [
    "https://mirrors.ustc.edu.cn/ubuntu-cloud-images/wsl/releases/24.04/current",
    "https://mirror.nju.edu.cn/ubuntu-cloud-images/wsl/releases/24.04/current",
    "https://cloud-images.ubuntu.com/wsl/releases/24.04/current",
];

fn hidden(program: &str) -> Command {
    let mut cmd = Command::new(program);
    // wsl.exe writes UTF-16 to a pipe unless told otherwise.
    cmd.env("WSL_UTF8", "1").creation_flags(CREATE_NO_WINDOW);
    cmd
}

// wsl.exe starts no login session, so systemctl --user cannot find the user
// manager unless pointed at it; cheesehost installs its service through it.
pub const PRELUDE: &str = "export XDG_RUNTIME_DIR=/run/user/$(id -u); cd\n";

/// A sh inside the distro reading its script from stdin (connect::spawn_script).
/// --exec runs it directly rather than through the user's default shell.
pub fn shell() -> Command {
    shell_as(USER)
}

fn shell_as(user: &str) -> Command {
    let mut cmd = hidden("wsl.exe");
    cmd.args(["-d", DISTRO, "-u", user, "--exec", "sh", "-s"]);
    cmd
}

pub fn kill(pid: u32) {
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

async fn run(program: &str, args: &[&str]) -> Result<String, String> {
    let out = hidden(program).args(args).output().await.map_err(|e| format!("{program}: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout).into_owned() + &String::from_utf8_lossy(&out.stderr);
    if out.status.success() {
        Ok(text)
    } else {
        Err(text.trim().to_string())
    }
}

async fn as_root(script: &str) -> Result<String, String> {
    run_script(shell_as("root"), script).await
}

async fn powershell(script: &str) -> Result<String, String> {
    run("powershell.exe", &["-NoProfile", "-NonInteractive", "-Command", script]).await
}

pub async fn prepare(_resources: &Path, step: &impl Fn(&str)) -> Result<(), String> {
    // WSL itself is a Windows feature: turning it on takes an administrator's
    // yes and a restart, after which the user comes back and clicks again.
    if run("wsl.exe", &["--status"]).await.is_err() {
        step("需要先打开 Windows 自带的 Linux 子系统（WSL），请在弹出的窗口里点「是」");
        powershell("Start-Process wsl.exe -ArgumentList '--install','--no-distribution' -Verb RunAs -Wait")
            .await
            .map_err(|e| format!("安装 WSL 失败：{e}"))?;
        return Err("WSL 已安装，请重启电脑，再点一次「接入这台电脑」".into());
    }

    let distros = run("wsl.exe", &["--list", "--quiet"]).await.unwrap_or_default();
    if !distros.lines().any(|l| l.trim() == DISTRO) {
        import_distro(step).await?;
    }

    step("正在准备运行环境");
    // Idempotent: a user to run as, systemd for cheesehost's service, and what
    // cheesehost and the work it hosts call.
    as_root(&format!(
        r#"set -e
id {USER} >/dev/null 2>&1 || useradd -m -s /bin/bash {USER}
printf '[boot]\nsystemd=true\n[user]\ndefault={USER}\n' > /etc/wsl.conf
missing=""
for c in tmux git python3 curl; do command -v $c >/dev/null || missing="$missing $c"; done
if [ -n "$missing" ]; then apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $missing; fi"#
    ))
    .await
    .map_err(|e| format!("准备运行环境失败：{e}"))?;
    // wsl.conf is read at boot: restart the distro once if systemd is not up yet.
    if as_root("[ -d /run/systemd/system ]").await.is_err() {
        let _ = run("wsl.exe", &["--terminate", DISTRO]).await;
    }
    keep_running().await?;
    // Linger starts the user's service manager at boot rather than at a login
    // that, under WSL, never happens.
    as_root(&format!("loginctl enable-linger {USER}"))
        .await
        .map(drop)
        .map_err(|e| format!("准备运行环境失败：{e}"))
}

async fn import_distro(step: &impl Fn(&str)) -> Result<(), String> {
    step("正在下载 Linux 运行环境（约 350 MB，第一次需要几分钟）");
    let dir = std::env::var("LOCALAPPDATA").map_err(|e| e.to_string())? + "\\Cheese";
    let tar = format!("{dir}\\{ROOTFS}");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let mut last_err = String::new();
    let mut downloaded = false;
    for host in ROOTFS_HOSTS {
        // Checked against the list beside it, so a cut-off download is not imported.
        let script = format!(
            "$ErrorActionPreference='Stop'; \
             curl.exe -fsSL -o '{tar}' '{host}/{ROOTFS}'; \
             $want = ((curl.exe -fsSL '{host}/SHA256SUMS') -split \"`n\" | Where-Object {{ $_ -like '*{ROOTFS}' }}).Split(' ')[0]; \
             $got = (Get-FileHash '{tar}' -Algorithm SHA256).Hash; \
             if ($got -ne $want) {{ throw \"checksum $got, expected $want\" }}"
        );
        match powershell(&script).await {
            Ok(_) => {
                downloaded = true;
                break;
            }
            Err(e) => last_err = format!("{host}: {e}"),
        }
    }
    if !downloaded {
        return Err(format!("下载 Linux 运行环境失败：{last_err}"));
    }
    step("正在安装 Linux 运行环境");
    let result = run("wsl.exe", &["--import", DISTRO, &format!("{dir}\\wsl"), &tar, "--version", "2"]).await;
    let _ = std::fs::remove_file(&tar);
    result.map(drop).map_err(|e| format!("安装 Linux 运行环境失败：{e}"))
}

// WSL stops a distro soon after the last Windows process attached to it exits,
// services and all. One process that never exits, started now and at every
// login, keeps cheesehost running while the user is logged in to Windows.
async fn keep_running() -> Result<(), String> {
    let keeper = format!("conhost.exe --headless wsl.exe -d {DISTRO} -u root --exec sleep infinity");
    run(
        "reg.exe",
        &["add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", "/v", DISTRO, "/t", "REG_SZ", "/d", &keeper, "/f"],
    )
    .await
    .map_err(|e| format!("设置开机自动运行失败：{e}"))?;
    if run_sh("pgrep -f '^sleep infinity$' >/dev/null").await.is_err() {
        std::process::Command::new("wsl.exe")
            .args(["-d", DISTRO, "-u", "root", "--exec", "sleep", "infinity"])
            .creation_flags(CREATE_NO_WINDOW | DETACHED_PROCESS)
            .spawn()
            .map_err(|e| format!("启动 Linux 运行环境失败：{e}"))?;
    }
    Ok(())
}
