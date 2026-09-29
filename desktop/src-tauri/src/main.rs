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
use tauri::{Manager, State, Theme, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_updater::UpdaterExt;
use tauri_plugin_window_state::StateFlags;
use url::Url;

// Built against okcheese.com; CHEESE_ORIGIN at build time points a build elsewhere.
const ORIGIN: &str = match option_env!("CHEESE_ORIGIN") {
    Some(o) => o,
    None => "https://okcheese.com",
};

// macOS draws the window buttons over the page; elsewhere the system title bar stays.
const TITLE_BAR: &str = if cfg!(target_os = "macos") { "overlay" } else { "native" };

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

/// The theme the person picked in the web app: "system", "light" or "dark".
/// It colours the title bar now, and the app's own first page from the next launch.
#[tauri::command]
fn set_theme(app: tauri::AppHandle, window: tauri::WebviewWindow, preference: String) {
    let Some(theme) = window_theme(&preference) else {
        return;
    };
    let _ = window.set_theme(theme);
    if let Ok(dir) = app.path().app_config_dir() {
        let _ = std::fs::create_dir_all(&dir);
        let _ = std::fs::write(dir.join(THEME_FILE), &preference);
    }
}

const THEME_FILE: &str = "theme";

/// None for anything that is not a preference; Some(None) is "follow the system".
fn window_theme(preference: &str) -> Option<Option<Theme>> {
    match preference {
        "system" => Some(None),
        "light" => Some(Some(Theme::Light)),
        "dark" => Some(Some(Theme::Dark)),
        _ => None,
    }
}

fn stored_theme(app: &tauri::App) -> String {
    app.path()
        .app_config_dir()
        .ok()
        .and_then(|dir| std::fs::read_to_string(dir.join(THEME_FILE)).ok())
        .filter(|p| window_theme(p).is_some())
        .unwrap_or_else(|| "system".into())
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
        .invoke_handler(tauri::generate_handler![connect_this_machine, cancel_connect, this_device, set_theme])
        .setup(|app| {
            // A page from the server may call the commands above; by default
            // a remote page reaches no IPC at all. The app's own page has the same.
            // Dragging is how the window moves where no title bar is drawn.
            app.add_capability(
                CapabilityBuilder::new("server")
                    .remote(format!("{ORIGIN}/*"))
                    .window("main")
                    .permission("core:default")
                    .permission("core:window:allow-start-dragging")
                    .permission("allow-connect-this-machine")
                    .permission("allow-cancel-connect")
                    .permission("allow-this-device")
                    .permission("allow-set-theme"),
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
            let theme = stored_theme(app);
            // What the pages need to know about this window (frontend/src/lib/desktopApp.ts
            // and ../shell): where the server is, the theme last picked, and whether
            // the title bar lies over the page.
            let about = format!(
                "window.__CHEESE_APP__ = {{ origin: {ORIGIN:?}, theme: {theme:?}, titleBar: {TITLE_BAR:?} }};"
            );
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .initialization_script(about)
                .theme(window_theme(&theme).flatten())
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
                // The web app switches to its phone layout below 960 wide (Vuetify's md).
                .min_inner_size(960.0, 600.0)
                .on_navigation(move |url| {
                    stays_in_app(url) || {
                        let _ = opener.opener().open_url(url.as_str(), None::<&str>);
                        false
                    }
                })
                .on_new_window(move |url, _| {
                    let _ = opener2.opener().open_url(url.as_str(), None::<&str>);
                    NewWindowResponse::Deny
                });
            // On macOS the window buttons sit in the web app's top bar, centred in
            // its 32px height above the 64px rail; the bar leaves them that room
            // (AppBar.vue) and the sign-in pages move their mark below them (Account.vue).
            #[cfg(target_os = "macos")]
            let window = window
                .title_bar_style(tauri::TitleBarStyle::Overlay)
                .hidden_title(true)
                .traffic_light_position(tauri::LogicalPosition::new(12.0, 16.0));
            window.build()?;
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
