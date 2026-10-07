//! Keeping the app itself current. The page is the server's own and always
//! current; this is for the app around it. `.github/workflows/desktop.yml`
//! publishes each release with `latest.json` beside it.
//!
//! The app looks at launch and every few hours after (it runs for days with
//! its window closed), and whenever the person asks (the app menu's "Check for
//! Updates…", or 检查更新 in the page's 关于 dialog). A new version is
//! downloaded straight away and then installed in one of two ways: when the
//! person clicks 重启以完成更新, or on its own once the window is out of sight.
//! Either way never while this computer is being connected (connect.rs), and
//! never under someone's hands without their click. The page hears where
//! things stand as `update-status` (frontend/src/lib/desktopApp.ts).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

use crate::connect;

const UPDATE_EVERY: Duration = Duration::from_secs(6 * 60 * 60);
const START_HIDDEN_FILE: &str = "start-hidden";

/// Where the app's own update stands, as the page reads it.
#[derive(Clone, Debug, Default, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "lowercase")]
pub enum Status {
    /// Not looked yet since launch.
    #[default]
    Idle,
    Checking,
    /// Looked, and this is the newest version.
    Latest,
    Downloading { version: String },
    /// Downloaded; it takes a restart to finish.
    Ready { version: String },
    Failed,
}

#[derive(Default)]
pub struct Updates {
    status: Mutex<Status>,
    /// The downloaded update, until it is installed.
    pending: Mutex<Option<(Update, Vec<u8>)>>,
    checking: AtomicBool,
}

/// Looks for a newer version and downloads it. A look already under way, or an
/// update already waiting for its restart, is answered as it stands.
pub async fn check(app: &AppHandle) -> Status {
    let updates = app.state::<Updates>();
    if matches!(status(app), Status::Ready { .. }) || updates.checking.swap(true, Ordering::SeqCst) {
        return status(app);
    }
    let result = fetch(app).await;
    updates.checking.store(false, Ordering::SeqCst);
    let now = result.unwrap_or_else(|e| {
        eprintln!("cheese: update failed: {e}");
        Status::Failed
    });
    set(app, now.clone());
    if matches!(now, Status::Ready { .. }) {
        tauri::async_runtime::spawn(install_when_away(app.clone()));
    }
    now
}

async fn fetch(app: &AppHandle) -> tauri_plugin_updater::Result<Status> {
    set(app, Status::Checking);
    let Some(update) = app.updater()?.check().await? else {
        return Ok(Status::Latest);
    };
    let version = update.version.clone();
    set(app, Status::Downloading { version: version.clone() });
    let bytes = update.download(|_, _| {}, || {}).await?;
    *app.state::<Updates>().pending.lock().unwrap() = Some((update, bytes));
    Ok(Status::Ready { version })
}

pub fn status(app: &AppHandle) -> Status {
    app.state::<Updates>().status.lock().unwrap().clone()
}

fn set(app: &AppHandle, status: Status) {
    *app.state::<Updates>().status.lock().unwrap() = status.clone();
    let _ = app.emit_to("main", "update-status", status);
}

pub async fn keep_updated(app: AppHandle) {
    loop {
        check(&app).await;
        tokio::time::sleep(UPDATE_EVERY).await;
    }
}

/// Whether an update may install without anyone's click: only with the window
/// out of sight and no connection in progress.
fn may_install_unattended(connecting: bool, in_sight: bool) -> bool {
    !connecting && !in_sight
}

fn connecting(app: &AppHandle) -> bool {
    app.state::<connect::Running>().busy()
}

async fn install_when_away(app: AppHandle) {
    loop {
        let in_sight = app
            .get_webview_window("main")
            .is_some_and(|w| w.is_visible().unwrap_or(false));
        if may_install_unattended(connecting(&app), in_sight) {
            break;
        }
        tokio::time::sleep(Duration::from_secs(30)).await;
    }
    // The restarted app stays out of sight, as the window was.
    let _ = install_and_restart(&app, true);
}

/// Installs the downloaded update and starts the new version. Returns only when
/// there is nothing to install or installing failed.
fn install_and_restart(app: &AppHandle, hidden: bool) -> Result<(), String> {
    let Some((update, bytes)) = app.state::<Updates>().pending.lock().unwrap().take() else {
        return Err("no-update".into());
    };
    if hidden {
        if let Ok(dir) = app.path().app_config_dir() {
            let _ = std::fs::create_dir_all(&dir);
            let _ = std::fs::write(dir.join(START_HIDDEN_FILE), "");
        }
    }
    if let Err(e) = update.install(bytes) {
        eprintln!("cheese: installing the update failed: {e}");
        set(app, Status::Failed);
        return Err("failed".into());
    }
    app.restart();
}

/// Whether this launch is the restart after an unattended update, which keeps the window out of sight.
pub fn restarted_by_update(app: &tauri::App) -> bool {
    app.path()
        .app_config_dir()
        .is_ok_and(|dir| std::fs::remove_file(dir.join(START_HIDDEN_FILE)).is_ok())
}

#[tauri::command]
pub fn update_status(app: AppHandle) -> Status {
    status(&app)
}

#[tauri::command]
pub async fn check_for_updates(app: AppHandle) -> Status {
    check(&app).await
}

/// The person clicked 重启以完成更新. Refused with "connecting" while this
/// computer is being connected, which a restart would cut off.
#[tauri::command]
pub fn restart_to_update(app: AppHandle) -> Result<(), String> {
    if connecting(&app) {
        return Err("connecting".into());
    }
    install_and_restart(&app, false)
}

#[cfg(test)]
mod tests {
    use super::{may_install_unattended, Status};

    #[test]
    fn installs_unattended_only_out_of_sight_and_not_mid_connection() {
        assert!(may_install_unattended(false, false));
        assert!(!may_install_unattended(false, true));
        assert!(!may_install_unattended(true, false));
        assert!(!may_install_unattended(true, true));
    }

    // The page reads these shapes (frontend/src/lib/desktopApp.ts, DesktopUpdateStatus).
    #[test]
    fn the_page_reads_the_status_as_a_state_and_a_version() {
        let json = |s: Status| serde_json::to_value(s).unwrap();
        assert_eq!(json(Status::Idle), serde_json::json!({ "state": "idle" }));
        assert_eq!(json(Status::Latest), serde_json::json!({ "state": "latest" }));
        assert_eq!(
            json(Status::Downloading { version: "0.1.9".into() }),
            serde_json::json!({ "state": "downloading", "version": "0.1.9" })
        );
        assert_eq!(
            json(Status::Ready { version: "0.1.9".into() }),
            serde_json::json!({ "state": "ready", "version": "0.1.9" })
        );
        assert_eq!(json(Status::Failed), serde_json::json!({ "state": "failed" }));
    }
}
