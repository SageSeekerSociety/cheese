// The window opens on the app's own page (../shell), which waits for the server
// and then hands the window to the web app, loaded from the server, so the desktop
// app and the site are one frontend. Besides that page, the app adds connecting
// this computer as a device (connect.rs), callable from that one origin and nowhere else.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod connect;
mod platform;

use serde::Serialize;
use tauri::ipc::{CapabilityBuilder, Channel};
use tauri::webview::{NewWindowResponse, PageLoadEvent};
use tauri::{Manager, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_updater::UpdaterExt;
use tauri_plugin_window_state::StateFlags;
use url::Url;

// Built against okcheese.com; CHEESE_ORIGIN at build time points a build elsewhere.
const ORIGIN: &str = match option_env!("CHEESE_ORIGIN") {
    Some(o) => o,
    None => "https://okcheese.com",
};

fn from_server(url: &Url) -> bool {
    url.origin().ascii_serialization() == ORIGIN
}

// The app's own pages: tauri://localhost, and http://tauri.localhost on Windows.
fn from_app(url: &Url) -> bool {
    url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost")
}

/// Whether a navigation stays in the window; anything else goes to the browser.
fn stays_in_app(url: &Url) -> bool {
    from_server(url) || from_app(url)
}

#[derive(Clone, Serialize)]
#[serde(tag = "kind", content = "text", rename_all = "lowercase")]
enum Progress {
    Step(String),
    Code(String),
}

#[tauri::command]
async fn connect_this_machine(
    app: tauri::AppHandle,
    running: State<'_, connect::Running>,
    known_device_ids: Vec<String>,
    progress: Channel<Progress>,
) -> Result<(), String> {
    let resources = app.path().resource_dir().map_err(|e| e.to_string())?;
    connect::connect(
        ORIGIN,
        &resources,
        &known_device_ids,
        &running,
        |s| drop(progress.send(Progress::Step(s.into()))),
        |c| drop(progress.send(Progress::Code(c.into()))),
    )
    .await
}

/// Which device this computer is, so the page can tell whether it is already connected.
#[tauri::command]
async fn this_device() -> Option<String> {
    connect::stored_device_id().await
}

#[tauri::command]
fn cancel_connect(running: State<'_, connect::Running>) {
    connect::cancel(&running);
}

// Replaces this app with the newest release (.github/workflows/desktop.yml
// publishes it with latest.json beside it) and starts that. The page is the
// server's own and always current; this is for the app around it. A
// connection in progress finishes first: a restart would cut it off halfway.
async fn update(app: tauri::AppHandle) -> tauri_plugin_updater::Result<()> {
    let Some(update) = app.updater()?.check().await? else {
        return Ok(());
    };
    let bytes = update.download(|_, _| {}, || {}).await?;
    while app.state::<connect::Running>().0.lock().unwrap().is_some() {
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    }
    update.install(bytes)?;
    app.restart();
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        // Size, position and maximized, from one launch to the next. Whether the
        // window is shown is the app's to decide at each launch, not a thing to restore.
        .plugin(
            tauri_plugin_window_state::Builder::new()
                .with_state_flags(StateFlags::all() - StateFlags::VISIBLE)
                .build(),
        )
        .manage(connect::Running::default())
        .invoke_handler(tauri::generate_handler![connect_this_machine, cancel_connect, this_device])
        .setup(|app| {
            // A page from the server may call the commands above; by default
            // a remote page reaches no IPC at all.
            app.add_capability(
                CapabilityBuilder::new("server")
                    .remote(format!("{ORIGIN}/*"))
                    .window("main")
                    .permission("core:default")
                    .permission("allow-connect-this-machine")
                    .permission("allow-cancel-connect")
                    .permission("allow-this-device"),
            )?;
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = update(handle).await {
                    eprintln!("cheese: update check failed: {e}");
                }
            });
            let opener = app.handle().clone();
            let opener2 = app.handle().clone();
            // Anything that is not the server — docs, GitHub, a shared link — opens
            // in the user's browser, so the commands are only ever reachable from our pages.
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .initialization_script(format!("window.__CHEESE_ORIGIN__ = {ORIGIN:?};"))
                .title("Cheese")
                // Shown once the app's own page has painted, so the window never
                // opens as a blank white rectangle.
                .visible(false)
                .on_page_load(|webview, payload| {
                    if payload.event() == PageLoadEvent::Finished {
                        let _ = webview.show();
                    }
                })
                .inner_size(1280.0, 820.0)
                .min_inner_size(900.0, 600.0)
                .on_navigation(move |url| {
                    stays_in_app(url) || {
                        let _ = opener.opener().open_url(url.as_str(), None::<&str>);
                        false
                    }
                })
                .on_new_window(move |url, _| {
                    let _ = opener2.opener().open_url(url.as_str(), None::<&str>);
                    NewWindowResponse::Deny
                })
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Cheese");
}

#[cfg(test)]
mod tests {
    use super::stays_in_app;
    use url::Url;

    fn stays(url: &str) -> bool {
        stays_in_app(&Url::parse(url).unwrap())
    }

    #[test]
    fn the_server_and_the_apps_own_page_stay_in_the_window() {
        assert!(stays(&format!("{}/projects/42", super::ORIGIN)));
        assert!(stays("tauri://localhost/index.html"));
        assert!(stays("http://tauri.localhost/index.html"));
    }

    #[test]
    fn every_other_site_goes_to_the_browser() {
        assert!(!stays("https://github.com/login/oauth/authorize"));
        assert!(!stays("https://docs.okcheese.com/"));
        assert!(!stays("https://okcheese.com.evil.example/"));
    }
}
