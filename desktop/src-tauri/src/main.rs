// The window is the web app itself, loaded from the server, so the desktop app
// and the site are one frontend. The only thing the app adds is connecting this
// computer as a device (connect.rs), callable from that one origin and nowhere else.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod connect;
mod platform;

use serde::Serialize;
use tauri::ipc::{CapabilityBuilder, Channel};
use tauri::webview::NewWindowResponse;
use tauri::{Manager, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;
use url::Url;

// Built against okcheese.com; CHEESE_ORIGIN at build time points a build elsewhere.
const ORIGIN: &str = match option_env!("CHEESE_ORIGIN") {
    Some(o) => o,
    None => "https://okcheese.com",
};

fn from_server(url: &Url) -> bool {
    url.origin().ascii_serialization() == ORIGIN
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

#[tauri::command]
fn cancel_connect(running: State<'_, connect::Running>) {
    connect::cancel(&running);
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(connect::Running::default())
        .invoke_handler(tauri::generate_handler![connect_this_machine, cancel_connect])
        .setup(|app| {
            // A page from the server may call the two commands above; by default
            // a remote page reaches no IPC at all.
            app.add_capability(
                CapabilityBuilder::new("server")
                    .remote(format!("{ORIGIN}/*"))
                    .window("main")
                    .permission("core:default")
                    .permission("allow-connect-this-machine")
                    .permission("allow-cancel-connect"),
            )?;
            let opener = app.handle().clone();
            let opener2 = app.handle().clone();
            // Anything that is not the server — docs, GitHub, a shared link — opens
            // in the user's browser, so the commands are only ever reachable from our pages.
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(ORIGIN.parse()?))
                .title("Cheese")
                .inner_size(1280.0, 820.0)
                .min_inner_size(900.0, 600.0)
                .on_navigation(move |url| {
                    from_server(url) || {
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
