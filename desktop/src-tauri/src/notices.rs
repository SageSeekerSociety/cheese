//! The app's own connection to the server for the person's notices
//! (backend/app/api/routes/notifications_live.py). It shows what browser push
//! would have said as system notifications, the moment it is committed, in the
//! words the server sends — already in the person's language — and
//! keeps the count of things waiting on the icon. It needs no page: a closed
//! window, a frozen web view or a page on another screen changes nothing.
//!
//! The connection will drop: sleep, a new network, a proxy, a deploy. It is
//! built for that. Silence longer than two server beats counts as a drop, a
//! drop is followed by reconnecting with a growing, jittered wait, and each
//! connection starts by saying where the last one stopped, so what came in
//! between is handed over once. A backlog of more than a few becomes one
//! notification, not a column of them.
//!
//! The signed-in page hands over the credential (frontend/src/lib/desktopApp.ts)
//! and takes it back on sign-out. A refused credential means the sign-in is
//! over; the app then waits for the page to sign in again.

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use tauri::async_runtime::JoinHandle;
use tauri::{AppHandle, Manager};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::header::AUTHORIZATION;
use tokio_tungstenite::tungstenite::{Error as WsError, Message};

use crate::resident;

/// Two beats (the server sends one every 25 s) and some slack.
const SILENCE: Duration = Duration::from_secs(60);
const FIRST_RETRY: Duration = Duration::from_secs(1);
const LAST_RETRY: Duration = Duration::from_secs(60);
/// More than this many at once, coming back from being away, is one summary.
const ONE_BY_ONE: usize = 3;

const CREDENTIAL_FILE: &str = "notices";

/// The running connection, so a new credential replaces it and sign-out ends it.
#[derive(Default)]
pub struct Notices(Mutex<Option<JoinHandle<()>>>);

#[derive(Deserialize)]
struct Credential {
    token: String,
    /// Whose notices these are; where they stopped is kept per account.
    account: u64,
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
enum Frame {
    Notices {
        latest: Option<u64>,
        items: Vec<Notice>,
        /// The one line for a backlog, in the person's language. A server from
        /// before it was sent has none; the Chinese line below stands in.
        #[serde(default)]
        away: Option<String>,
    },
    Waiting { count: u32 },
    Beat,
}

#[derive(Deserialize)]
struct Notice {
    title: String,
    body: String,
    url: String,
}

enum Ended {
    /// The sign-in is over; only the page signing in again helps.
    Refused,
    /// The connection went away; `after_open` says whether it had got going.
    Dropped { after_open: bool },
}

/// The page, signed in, hands over the credential for this account's notices.
#[tauri::command]
pub fn listen_for_notices(app: AppHandle, token: String, account: u64) {
    let credential = serde_json::json!({ "token": token, "account": account });
    if let Some(path) = file(&app, CREDENTIAL_FILE) {
        let _ = std::fs::write(path, credential.to_string());
    }
    start(&app, Credential { token, account });
}

/// Signed out: no more notices, and nothing waits on anyone here.
#[tauri::command]
pub fn stop_notices(app: AppHandle) {
    if let Some(path) = file(&app, CREDENTIAL_FILE) {
        let _ = std::fs::remove_file(path);
    }
    if let Some(running) = app.state::<Notices>().0.lock().unwrap().take() {
        running.abort();
    }
    resident::show_waiting(&app, 0);
}

/// At launch, carry on with the credential the page handed over last time.
pub fn resume(app: &AppHandle) {
    let stored = file(app, CREDENTIAL_FILE)
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str::<Credential>(&text).ok());
    if let Some(credential) = stored {
        start(app, credential);
    }
}

fn start(app: &AppHandle, credential: Credential) {
    let task = tauri::async_runtime::spawn(keep_listening(app.clone(), credential));
    if let Some(previous) = app.state::<Notices>().0.lock().unwrap().replace(task) {
        previous.abort();
    }
}

async fn keep_listening(app: AppHandle, credential: Credential) {
    let mut wait = FIRST_RETRY;
    loop {
        match listen_once(&app, &credential).await {
            Ended::Refused => return,
            Ended::Dropped { after_open } => {
                if after_open {
                    wait = FIRST_RETRY;
                }
                tokio::time::sleep(jittered(wait)).await;
                wait = (wait * 2).min(LAST_RETRY);
            }
        }
    }
}

async fn listen_once(app: &AppHandle, credential: &Credential) -> Ended {
    let Ok(mut request) = live_url().into_client_request() else {
        return Ended::Refused;
    };
    let Ok(bearer) = format!("Bearer {}", credential.token).parse() else {
        return Ended::Refused;
    };
    request.headers_mut().insert(AUTHORIZATION, bearer);
    let socket = match tokio_tungstenite::connect_async(request).await {
        Ok((socket, _)) => socket,
        // Turned away at the door: the credential names a sign-in that is over.
        Err(WsError::Http(response)) if response.status().is_client_error() => return Ended::Refused,
        Err(_) => return Ended::Dropped { after_open: false },
    };
    let (mut outgoing, mut incoming) = socket.split();
    let cursor_file = file(app, &format!("notices-after-{}", credential.account));
    let after: Option<u64> = cursor_file
        .as_ref()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| text.trim().parse().ok());
    let hello = serde_json::json!({ "after": after }).to_string();
    if outgoing.send(Message::Text(hello.into())).await.is_err() {
        return Ended::Dropped { after_open: false };
    }

    let mut backlog = true;
    loop {
        let message = match tokio::time::timeout(SILENCE, incoming.next()).await {
            Ok(Some(Ok(message))) => message,
            // Silence, an error or the end: the connection is gone either way.
            _ => return Ended::Dropped { after_open: true },
        };
        let text = match message {
            Message::Text(text) => text,
            // 4401: the sign-in ended while connected.
            Message::Close(Some(close)) if u16::from(close.code) == 4401 => return Ended::Refused,
            Message::Close(_) => return Ended::Dropped { after_open: true },
            _ => continue,
        };
        match serde_json::from_str::<Frame>(&text) {
            Ok(Frame::Notices { latest, items, away }) => {
                if backlog && items.len() > ONE_BY_ONE {
                    let title = away.unwrap_or_else(|| format!("离开期间有 {} 条新通知", items.len()));
                    resident::show_notice(app, title, String::new(), "/inbox".into());
                } else {
                    for notice in items {
                        resident::show_notice(app, notice.title, notice.body, notice.url);
                    }
                }
                backlog = false;
                // Nothing has ever come for this account: anything from now on is new.
                if let Some(path) = cursor_file.as_ref() {
                    let _ = std::fs::write(path, latest.unwrap_or(0).to_string());
                }
            }
            Ok(Frame::Waiting { count }) => resident::show_waiting(app, count),
            Ok(Frame::Beat) | Err(_) => {}
        }
    }
}

/// The live notices address on the server this app is built for.
fn live_url() -> String {
    let origin = crate::ORIGIN;
    let ws = origin
        .strip_prefix("https://")
        .map(|rest| format!("wss://{rest}"))
        .or_else(|| origin.strip_prefix("http://").map(|rest| format!("ws://{rest}")))
        .unwrap_or_else(|| origin.to_string());
    format!("{ws}/api/notifications/live")
}

/// Up to a quarter more, so apps dropped by the same deploy do not all come back in the same second.
fn jittered(wait: Duration) -> Duration {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    wait + wait.mul_f64(f64::from(nanos % 1000) / 4000.0)
}

fn file(app: &AppHandle, name: &str) -> Option<PathBuf> {
    let dir = app.path().app_config_dir().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join(name))
}

#[cfg(test)]
mod tests {
    use super::{jittered, Frame};
    use std::time::Duration;

    #[test]
    fn reads_every_frame_the_server_sends() {
        let notices = r#"{"kind":"notices","latest":7,"items":[{"id":7,"title":"用哪个数据库？","body":"在「迁移」","url":"/projects/p/topics/t"}]}"#;
        assert!(matches!(serde_json::from_str(notices), Ok(Frame::Notices { latest: Some(7), items, .. }) if items.len() == 1));
        let away = r#"{"kind":"notices","latest":9,"items":[],"away":"4 new notifications while you were away"}"#;
        assert!(matches!(serde_json::from_str(away), Ok(Frame::Notices { away: Some(line), .. }) if line.starts_with("4 new")));
        let first = r#"{"kind":"notices","latest":null,"items":[]}"#;
        assert!(matches!(serde_json::from_str(first), Ok(Frame::Notices { latest: None, away: None, .. })));
        assert!(matches!(serde_json::from_str(r#"{"kind":"waiting","count":3}"#), Ok(Frame::Waiting { count: 3 })));
        assert!(matches!(serde_json::from_str(r#"{"kind":"beat"}"#), Ok(Frame::Beat)));
    }

    #[test]
    fn a_retry_waits_at_least_as_long_and_at_most_a_quarter_more() {
        let wait = Duration::from_secs(8);
        let jittered = jittered(wait);
        assert!(jittered >= wait && jittered <= wait + wait / 4);
    }
}
