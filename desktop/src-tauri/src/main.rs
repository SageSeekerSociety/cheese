// The window opens on the app's own page (../shell), which waits for the server
// and then hands the window to the web app, loaded from the server, so the desktop
// app and the site are one frontend. Besides that page, the app adds connecting
// this computer as a device (connect.rs), callable from that one origin and nowhere else.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod connect;
mod executor;
mod links;
mod notices;
// Unpacking the packed connector copy. Only Windows downloads it itself, so
// off Windows the helpers are unused; their tests run wherever the crate is.
#[cfg_attr(not(windows), allow(dead_code))]
mod packed;
mod platform;
mod resident;
mod updates;
mod update_launch;

use serde::Serialize;
use tauri::ipc::{CapabilityBuilder, Channel};
use tauri::webview::{NewWindowResponse, PageLoadEvent};
use tauri::{Emitter, Manager, State, Theme, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_window_state::StateFlags;
use std::sync::atomic::Ordering;
use url::Url;

// Built against okcheese.com; CHEESE_ORIGIN at build time points a build elsewhere.
const ORIGIN: &str = match option_env!("CHEESE_ORIGIN") {
    Some(o) => o,
    None => "https://okcheese.com",
};

// Where the server puts pages other people wrote — mini-apps, previews, Sites —
// one subdomain each, never on the server's own origin (backend `sites_domain`).
// Built for okcheese.com's; CHEESE_CONTENT_DOMAIN at build time names another.
const CONTENT_DOMAIN: &str = match option_env!("CHEESE_CONTENT_DOMAIN") {
    Some(d) => d,
    None => "cheeseusercontent.com",
};

// macOS draws the window buttons over the page; elsewhere the system title bar stays.
const TITLE_BAR: &str = if cfg!(target_os = "macos") { "overlay" } else { "native" };

// What a page the server sends may call. The opener plugin's init script turns a
// click on an `<a target="_blank">` into `plugin:opener|open_url`, so a link in
// the page needs `opener:allow-open-url` to reach that command at all; without it
// the click is refused and nothing happens. The command then refuses every URL
// whose scheme is not in `allow-default-urls` — that list is what actually lets
// the link out, and it stops at http, https, mailto and tel. `opener:default`
// would open the same links and also reveal a file in its folder, which nothing
// here does.
const SERVER_PERMISSIONS: &[&str] = &[
    "core:default",
    "core:window:allow-start-dragging",
    "opener:allow-open-url",
    "opener:allow-default-urls",
    "allow-connect-this-machine",
    "allow-cancel-connect",
    "allow-this-device",
    "allow-prevent-sleep",
    "allow-this-devices", "allow-enrollment-profiles",
    "allow-claude-login",
    "allow-cancel-claude-login",
    "allow-claude-logout",
    "allow-disconnect-this-machine",
    "allow-set-theme",
    "allow-set-badge",
    "allow-listen-for-notices",
    "allow-stop-notices",
    "allow-opens-at-login",
    "allow-set-opens-at-login",
    "allow-update-status",
    "allow-check-for-updates",
    "allow-restart-to-update",
    "allow-executor-status",
];

fn from_server(url: &Url) -> bool {
    url.origin().ascii_serialization() == ORIGIN
}

// The app's own pages: tauri://localhost, and http://tauri.localhost on Windows.
fn from_app(url: &Url) -> bool {
    url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost")
}

/// A page on the content domain: what the server's pages show in their frames.
fn from_content(url: &Url) -> bool {
    let scheme = Url::parse(ORIGIN).map(|o| o.scheme().to_owned()).unwrap_or_default();
    url.scheme() == scheme
        && url.host_str().is_some_and(|host| {
            host.strip_suffix(CONTENT_DOMAIN)
                .is_some_and(|label| label.len() > 1 && label.ends_with('.'))
        })
}

/// Whether a navigation stays in the window; anything else goes to the browser.
/// The server's documentation is a site of its own, with no way back to the
/// app from inside it, so it goes to the browser too.
///
/// On macOS this is asked of every frame's navigation, not only the window's,
/// and the handler is not told which frame it is: a mini-app or a preview the
/// page loads into a frame from the content domain has to stay, or it opens in
/// the browser and the frame stays empty. Nothing sends the window itself
/// there: the page frames that content sandboxed without
/// `allow-top-navigation`, links out of it open in the browser
/// (`on_new_window`), and the server's pages that hand over to the content
/// domain at the top level open themselves in the browser when they find
/// themselves in the app (frontend/src/lib/desktopApp.ts `leaveForBrowser`),
/// as does a `cheese://` link to one (links.rs). Nor can a content page call
/// the app: the IPC capability names the server's origin only.
fn stays_in_app(url: &Url) -> bool {
    (from_server(url) && !is_docs(url)) || from_app(url) || from_content(url)
}

fn is_docs(url: &Url) -> bool {
    url.path() == "/docs" || url.path().starts_with("/docs/")
}

/// What the page hears while this computer is connected: a step started, how
/// far a download has got, or the code to approve.
#[derive(Clone, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
enum Progress {
    Step { id: &'static str },
    Percent { value: u8 },
    Code { text: String },
}

#[tauri::command]
async fn connect_this_machine(
    app: tauri::AppHandle,
    running: State<'_, connect::Running>,
    known_device_ids: Vec<String>,
    team_id: u64,
    progress: Channel<Progress>,
) -> Result<(), connect::Failure> {
    let resources = app
        .path()
        .resource_dir()
        .map_err(|e| connect::Failure::at("prepare", e.to_string()))?;
    let step = |id: &'static str| drop(progress.send(Progress::Step { id }));
    let percent = |value: u8| drop(progress.send(Progress::Percent { value }));
    let code = |text: &str| drop(progress.send(Progress::Code { text: text.into() }));
    let events = connect::Events { step: &step, percent: &percent, code: &code };
    connect::connect(ORIGIN, &resources, &known_device_ids, team_id, &running, &events).await?;
    // The Claude Code build a teammate runs here, fetched in the background
    // while the computer is already in use (executor.rs).
    tauri::async_runtime::spawn(async move { executor::fetch(&app).await });
    Ok(())
}

/// Where fetching ahead the build a teammate runs here stands (executor.rs).
#[tauri::command]
fn executor_status(app: tauri::AppHandle) -> executor::Status {
    executor::status(&app)
}

/// Which device this computer is, so the page can tell whether it is already connected.
#[tauri::command]
async fn this_device() -> Option<String> {
    connect::stored_device_id().await
}

#[tauri::command]
async fn prevent_sleep(enabled: Option<bool>) -> Result<bool, String> {
    machine_sleep_setting(
        &platform::cheesehost_config().with_file_name("machine.json"),
        enabled,
    ).await
}

async fn machine_sleep_setting(path: &std::path::Path, enabled: Option<bool>) -> Result<bool, String> {
    #[derive(serde::Deserialize, serde::Serialize, Default)]
    struct Preference {
        prevent_sleep: Option<bool>,
    }

    // Read machine.json directly so the preference works before cheesehost is installed.
    let mut preference: Preference = match std::fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes).map_err(|e| e.to_string())?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Preference::default(),
        Err(e) => return Err(e.to_string()),
    };
    if let Some(enabled) = enabled {
        preference.prevent_sleep = Some(enabled);
        std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
        let bytes = serde_json::to_vec(&preference).map_err(|e| e.to_string())?;
        std::fs::write(path, bytes).map_err(|e| e.to_string())?;
    }
    Ok(preference.prevent_sleep.unwrap_or(true))
}

#[tauri::command]
async fn this_devices() -> Vec<String> { connect::stored_device_ids().await }

#[tauri::command]
async fn enrollment_profiles() -> std::collections::HashMap<String, String> { connect::enrollment_profiles().await }

#[tauri::command]
fn cancel_connect(running: State<'_, connect::Running>) {
    connect::cancel(&running);
}

/// The Claude Code login under way, apart from a connection under way.
#[derive(Default)]
struct ClaudeLogin(connect::Running);

/// Logs in the platform's Claude Code here: in the browser, no terminal. The
/// channel hears "preparing" and "browser". `login_id` names a login this
/// computer has, to log in again; without it a login is added.
#[tauri::command]
async fn claude_login(
    running: State<'_, ClaudeLogin>,
    login_id: Option<String>,
    profile: Option<String>,
    server: Option<String>,
    ticket: Option<String>,
    progress: Channel<Progress>,
) -> Result<(), connect::Failure> {
    let step = |id: &'static str| drop(progress.send(Progress::Step { id }));
    connect::claude_login(&running.0, login_id.as_deref(), profile.as_deref(), server.as_deref(), ticket.as_deref(), &step).await
}

#[tauri::command]
fn cancel_claude_login(running: State<'_, ClaudeLogin>) {
    connect::cancel(&running.0);
}

#[tauri::command]
async fn claude_logout(login_id: String, profile: Option<String>) -> Result<(), String> {
    connect::claude_logout(login_id, profile.as_deref()).await
}

/// Stops this computer's connector after the page has unbound the device.
#[tauri::command]
async fn disconnect_this_machine() -> Result<(), String> {
    connect::disconnect().await
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

// The macOS app menu is the system's usual one with the two items people look
// for first in it: "About Cheese", which shows the page's 关于 dialog (the app's
// and the page's versions, and where the update stands), and "Check for
// Updates…". The rest of the menu keeps its system wording, so these do too.
#[cfg(target_os = "macos")]
fn app_menu(app: &tauri::AppHandle) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    use tauri::menu::{Menu, MenuItem};
    let menu = Menu::default(app)?;
    if let Some(first) = menu.items()?.first().and_then(|item| item.as_submenu().cloned()) {
        // The system's own About panel shows the app's version only.
        first.remove_at(0)?;
        first.insert(&MenuItem::with_id(app, ABOUT, "About Cheese", true, None::<&str>)?, 0)?;
        first.insert(&MenuItem::with_id(app, CHECK_FOR_UPDATES, "Check for Updates…", true, None::<&str>)?, 1)?;
    }
    Ok(menu)
}

const ABOUT: &str = "about";
const CHECK_FOR_UPDATES: &str = "check-for-updates";

/// Both menu items open the page's 关于 dialog; the second also looks for an
/// update, whose progress the dialog shows.
fn on_menu(app: &tauri::AppHandle, id: &str) {
    if id != ABOUT && id != CHECK_FOR_UPDATES {
        return;
    }
    resident::bring_back(app);
    let _ = app.emit_to("main", "show-about", ());
    if id == CHECK_FOR_UPDATES {
        let app = app.clone();
        tauri::async_runtime::spawn(async move { updates::check(&app).await });
    }
}

fn main() {
    tauri::Builder::default()
        // Opening the app while it runs with its window closed brings that window
        // back rather than starting a second app. Registered first, as it must be.
        // A `cheese://` link opening it is passed on to the running app (links.rs).
        .plugin(tauri_plugin_single_instance::init(|app, _, _| resident::bring_back(app)))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_opener::init())
        // Off until the person turns it on; launched this way the app starts out of sight.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![resident::AT_LOGIN]),
        ))
        .plugin(tauri_plugin_updater::Builder::new().build())
        // Size, position and maximized, from one launch to the next. Whether the
        // window is shown is the app's to decide at each launch, not a thing to restore.
        .plugin(
            tauri_plugin_window_state::Builder::new()
                .with_state_flags(StateFlags::all() - StateFlags::VISIBLE)
                .build(),
        )
        .manage(connect::Running::default())
        .manage(ClaudeLogin::default())
        .manage(resident::StartHidden::default())
        .manage(notices::Notices::default())
        .manage(updates::Updates::default())
        .manage(executor::Executor::default())
        .invoke_handler(tauri::generate_handler![
            connect_this_machine,
            cancel_connect,
            this_device,
            prevent_sleep,
            this_devices,
            enrollment_profiles,
            claude_login,
            cancel_claude_login,
            claude_logout,
            disconnect_this_machine,
            set_theme,
            resident::set_badge,
            resident::opens_at_login,
            resident::set_opens_at_login,
            notices::listen_for_notices,
            notices::stop_notices,
            updates::update_status,
            updates::check_for_updates,
            updates::restart_to_update,
            executor_status
        ])
        // Closing the window keeps the app running (resident.rs); quitting is ⌘Q
        // on macOS and 退出 in the tray menu elsewhere.
        .on_menu_event(|app, event| on_menu(app, event.id().as_ref()))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .setup(|app| {
            // A page from the server may call the commands above; by default
            // a remote page reaches no IPC at all. The app's own page has the same.
            // Dragging is how the window moves where no title bar is drawn.
            let mut capability = CapabilityBuilder::new("server")
                .remote(format!("{ORIGIN}/*"))
                .window("main");
            for permission in SERVER_PERMISSIONS {
                capability = capability.permission(*permission);
            }
            app.add_capability(capability)?;
            if updates::start_hidden(app, std::env::args().any(|a| a == resident::AT_LOGIN)) {
                app.state::<resident::StartHidden>().0.store(true, Ordering::Relaxed);
            }
            #[cfg(not(target_os = "macos"))]
            resident::tray::install(app.handle())?;
            #[cfg(target_os = "macos")]
            app.set_menu(app_menu(app.handle())?)?;
            tauri::async_runtime::spawn(updates::keep_updated(app.handle().clone()));
            tauri::async_runtime::spawn(executor::keep_fetched(app.handle().clone()));
            notices::resume(app.handle());
            // The installer registers the scheme; registering again at each start
            // mends an install that lost it. macOS reads it from the bundle instead.
            #[cfg(windows)]
            let _ = app.deep_link().register_all();
            let linked = app.handle().clone();
            app.deep_link().on_open_url(move |event| {
                for link in event.urls() {
                    links::open(&linked, &link);
                }
            });
            // Started by a link (Windows, Linux): the first page goes on to that
            // page, or the link opens in the browser (links.rs).
            let started_by = app.deep_link().get_current().ok().flatten().unwrap_or_default();
            for link in &started_by {
                links::hand_off(app.handle(), link);
            }
            let start = started_by.iter().find_map(links::page);
            let opener = app.handle().clone();
            let opener2 = app.handle().clone();
            // Anything that is not the server — docs, GitHub, a shared link — opens
            // in the user's browser, so the commands are only ever reachable from our pages.
            let theme = stored_theme(app);
            // What the pages need to know about this window (frontend/src/lib/desktopApp.ts
            // and ../shell): where the server is, the theme last picked, whether
            // the title bar lies over the page, the app's own version, and what else
            // the app can do for it.
            let about = format!(
                "window.__CHEESE_APP__ = {{ origin: {ORIGIN:?}, theme: {theme:?}, titleBar: {TITLE_BAR:?}, version: {version:?}, start: {start}, can: [\"notices\", \"badge\", \"autostart\", \"links\", \"updates\", \"device\", \"teamEnrollment\", \"modelCredentialLogin\", \"executor\"] }};",
                version = app.package_info().version.to_string(),
                start = serde_json::to_string(&start).unwrap_or_else(|_| "null".into()),
            );
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .initialization_script(about)
                // On the page someone picks up a project tile in the rail and
                // drops it where it should go, and drops a file on the library to
                // upload it — all of that is the page's own drag and drop, and it
                // happens inside this webview. With wry's handler on, the webview
                // answers its own drag-destination callbacks and WebKit's handling
                // never runs, and this app's handler takes every drag it is offered
                // whatever it carries, so on macOS the tile lifts and the page
                // never sees the drop. Off, WebKit handles drags itself, which is
                // what the page's drag code expects; nothing here listens for the
                // events wry would send instead (`dragDrop` in the page is the DOM
                // one, not Tauri's). WebKit's own default for a file let go
                // where the page takes none is to open it in place of the page;
                // the page refuses those drops itself (frontend/src/lib/desktopNative.ts).
                .disable_drag_drop_handler()
                .theme(window_theme(&theme).flatten())
                .title("Cheese")
                // Shown once the app's own page has painted, so the window never
                // opens as a blank white rectangle; not at all after an update's restart.
                .visible(false)
                .on_page_load(|webview, payload| {
                    if payload.event() == PageLoadEvent::Finished {
                        // Here and not after `build()`: the window-state plugin
                        // restores the saved size from a task queued on the main
                        // thread, which has not run yet when `build()` returns.
                        fix_restored_size(&webview);
                        if !resident::start_hidden(webview.app_handle()) {
                            let _ = webview.show();
                        }
                    }
                })
                .inner_size(WINDOW_SIZE.0, WINDOW_SIZE.1)
                .min_inner_size(MIN_WINDOW_SIZE.0, MIN_WINDOW_SIZE.1)
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
        .build(tauri::generate_context!())
        .expect("error while building Cheese")
        .run(|app, event| {
            // Clicking the Dock icon with the window closed brings it back.
            #[cfg(target_os = "macos")]
            if let tauri::RunEvent::Reopen { .. } = event {
                resident::bring_back(app);
            }
            #[cfg(not(target_os = "macos"))]
            let _ = (app, event);
        });
}

/// The size the window opens at the first time.
const WINDOW_SIZE: (f64, f64) = (1280.0, 820.0);
/// The web app switches to its phone layout below 960 wide (Vuetify's md).
const MIN_WINDOW_SIZE: (f64, f64) = (960.0, 600.0);

/// Whether a restored size, in logical pixels, is one the window may keep.
fn usable_size(width: f64, height: f64) -> bool {
    width >= MIN_WINDOW_SIZE.0 && height >= MIN_WINDOW_SIZE.1
}

/// The window-state plugin saves the size in physical pixels and restores it
/// with the scale of whichever display the hidden window starts on. A size saved
/// on a 1x display and restored on a Retina one comes back at half, and a
/// programmatic resize is not held to `min_inner_size` on macOS, so the window
/// could shrink to little more than the logo. A size below the minimum goes back
/// to the first-launch size, centred.
fn fix_restored_size<R: tauri::Runtime>(window: &tauri::WebviewWindow<R>) {
    let (Ok(size), Ok(scale)) = (window.inner_size(), window.scale_factor()) else {
        return;
    };
    let size = size.to_logical::<f64>(scale);
    if !usable_size(size.width, size.height) {
        let _ = window.set_size(tauri::LogicalSize::new(WINDOW_SIZE.0, WINDOW_SIZE.1));
        let _ = window.center();
    }
}

#[cfg(test)]
mod tests {
    use super::{stays_in_app, usable_size, SERVER_PERMISSIONS};
    use url::Url;

    #[tokio::test]
    async fn sleep_preference_works_before_a_connector_is_installed() {
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let home = std::env::temp_dir().join(format!("cheese-sleep-{}-{unique}", std::process::id()));
        let path = home.join("cheese/machine.json");
        let result: Result<(), String> = async {
            assert!(super::machine_sleep_setting(&path, None).await?);
            assert!(!super::machine_sleep_setting(&path, Some(false)).await?);
            let stored: serde_json::Value = serde_json::from_slice(
                &std::fs::read(&path).map_err(|e| e.to_string())?
            ).map_err(|e| e.to_string())?;
            assert_eq!(stored["prevent_sleep"], false);
            assert!(!super::machine_sleep_setting(&path, None).await?);
            assert!(super::machine_sleep_setting(&path, Some(true)).await?);
            assert!(super::machine_sleep_setting(&path, None).await?);
            Ok(())
        }.await;
        let _ = std::fs::remove_dir_all(home);
        assert!(result.is_ok(), "{result:?}");
    }

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
    fn a_restored_size_below_the_minimum_is_not_kept() {
        assert!(!usable_size(319.0, 225.5));
        assert!(!usable_size(1280.0, 500.0));
        assert!(!usable_size(900.0, 820.0));
        assert!(usable_size(960.0, 600.0));
        assert!(usable_size(1135.0, 732.0));
    }

    #[test]
    fn every_other_site_goes_to_the_browser() {
        assert!(!stays("https://github.com/login/oauth/authorize"));
        assert!(!stays("https://docs.okcheese.com/"));
        assert!(!stays("https://okcheese.com.evil.example/"));
    }

    #[test]
    fn the_page_may_click_a_link_out_to_the_browser() {
        // The opener plugin's injected click handler calls open_url, so these
        // two are what makes the link open rather than do nothing.
        for wanted in ["opener:allow-open-url", "opener:allow-default-urls"] {
            assert!(SERVER_PERMISSIONS.contains(&wanted), "{wanted} is not granted");
        }
        // The default set also reveals a file in its folder; the page has no
        // such command, so the narrower pair is the whole grant.
        assert!(!SERVER_PERMISSIONS.contains(&"opener:default"));
    }

    #[test]
    fn the_content_the_page_frames_stays_in_the_window() {
        // A mini-app's shell, and a preview's session handshake and pages.
        assert!(stays(&format!("https://apps.{}/v1/", super::CONTENT_DOMAIN)));
        let project = "0123456789abcdef0123456789abcdef";
        assert!(stays(&format!("https://{project}.{}/_cheese/session", super::CONTENT_DOMAIN)));
        assert!(stays(&format!("https://preview-{project}.{}/index.html", super::CONTENT_DOMAIN)));
    }

    #[test]
    fn only_the_content_domain_itself_counts_as_content() {
        let domain = super::CONTENT_DOMAIN;
        assert!(!stays(&format!("https://apps.{domain}.evil.example/")));
        assert!(!stays(&format!("https://evil{domain}/")));
        assert!(!stays(&format!("https://{domain}/")));
        assert!(!stays(&format!("http://apps.{domain}/v1/")));
    }

    #[test]
    fn the_servers_documentation_goes_to_the_browser() {
        assert!(!stays(&format!("{}/docs/", super::ORIGIN)));
        assert!(!stays(&format!("{}/docs/guide/start.html", super::ORIGIN)));
        assert!(!stays(&format!("{}/docs", super::ORIGIN)));
        assert!(stays(&format!("{}/docsearch", super::ORIGIN)));
    }
}
