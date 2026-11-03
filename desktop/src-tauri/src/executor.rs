//! Fetching ahead the Claude Code build an AI teammate runs on this computer.
//!
//! The platform's sessions install it themselves the first time they run here
//! (`remote_execution/executor_download`), but that is a quarter of a gigabyte
//! inside someone's first request. So once this computer is connected, and at
//! each launch and every few hours after (the platform moves its pin with its
//! releases), the app asks the connector to install it in the background:
//! `cheesehost claude install`, which asks the server which build it runs and
//! places it where the sessions look. The two cooperate on the machine — one
//! lock, one partial file — so a session that starts first waits for this one
//! rather than fetching a second copy, and either continues what the other was
//! stopped in the middle of. Nothing waits on this: a computer is usable the
//! moment it is connected. The page hears where it stands as `executor-status`.

use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

use crate::connect::{self, Running};

const FETCH_EVERY: Duration = Duration::from_secs(6 * 60 * 60);

/// Where the build stands, as the page reads it.
#[derive(Clone, Debug, Default, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "lowercase")]
pub enum Status {
    /// Not looked yet, or this computer is not connected.
    #[default]
    Idle,
    /// Looking, or downloading; `percent` once the download says how far it has got.
    Fetching { percent: Option<u8> },
    /// This computer has the build the platform runs.
    Ready,
    /// The last attempt did not finish; a session fetches what is missing.
    Failed,
}

#[derive(Default)]
pub struct Executor {
    status: Mutex<Status>,
    running: Running,
}

pub fn status(app: &AppHandle) -> Status {
    app.state::<Executor>().status.lock().unwrap().clone()
}

fn set(app: &AppHandle, status: Status) {
    *app.state::<Executor>().status.lock().unwrap() = status.clone();
    let _ = app.emit_to("main", "executor-status", status);
}

/// Installs the build unless this computer has it; one at a time. Through any
/// enrollment of this computer: they all belong to the one server this app is
/// built for, and the build is one per computer.
pub async fn fetch(app: &AppHandle) {
    let Some(profile) = connect::enrollment_profiles().await.into_values().min() else {
        return;
    };
    let executor = app.state::<Executor>();
    if !executor.running.begin() {
        return;
    }
    set(app, Status::Fetching { percent: None });
    let mut cmd = connect::claude_command(Some(&profile));
    cmd.args(["claude", "install", "--progress"]);
    let progress = app.clone();
    let result = connect::run(
        cmd,
        &executor.running,
        &move |p: u8| set(&progress, Status::Fetching { percent: Some(p) }),
        &mut |_: &str| {},
    )
    .await;
    executor.running.end();
    set(app, if result.is_ok() { Status::Ready } else { Status::Failed });
    if let Err(e) = result {
        eprintln!("cheese: fetching Claude Code ahead failed: {e}");
    }
}

/// At launch and every few hours after, so a pin the platform moved is
/// fetched before a session needs it.
pub async fn keep_fetched(app: AppHandle) {
    loop {
        fetch(&app).await;
        tokio::time::sleep(FETCH_EVERY).await;
    }
}
