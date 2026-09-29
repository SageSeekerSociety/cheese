//! The app stays running with its window closed, so it can call the person back
//! when something needs them: a system notification that opens the room it is
//! about, and the count of things waiting on the Dock or taskbar. Closing the
//! window hides it; the Dock icon (macOS) or the tray icon (Windows) brings it
//! back, and so does opening the app again.
//!
//! What to notify and the count come from the page, which is signed in and
//! polls the server (frontend/src/lib/desktopNotices.ts); the app only shows them.

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{AppHandle, Emitter, Manager, WebviewWindow};

/// Set while the window should stay out of sight at launch: after an update
/// restarted the app behind someone's back. Cleared once anything brings it back.
#[derive(Default)]
pub struct StartHidden(pub AtomicBool);

pub fn start_hidden(app: &AppHandle) -> bool {
    app.state::<StartHidden>().0.load(Ordering::Relaxed)
}

/// Shows the window and puts it in front.
pub fn bring_back(app: &AppHandle) {
    app.state::<StartHidden>().0.store(false, Ordering::Relaxed);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

/// Brings the window back on a page of the web app, e.g. "/inbox". The page
/// routes there itself (desktopNotices.ts), without reloading.
pub fn open_page(app: &AppHandle, path: &str) {
    bring_back(app);
    let _ = app.emit_to("main", "open-page", path.to_string());
}

/// A system notification. Clicking it opens `url`, a path in the web app.
/// Nothing shows while the window is in front: the person is already looking.
#[tauri::command]
pub fn notify(app: AppHandle, window: WebviewWindow, title: String, body: String, url: String) {
    if window.is_visible().unwrap_or(false) && window.is_focused().unwrap_or(false) {
        return;
    }
    let identifier = app.config().identifier.clone();
    // Each notification waits for its answer on a thread of its own; the answer
    // may come minutes later, or never.
    std::thread::spawn(move || {
        let mut notification = notify_rust::Notification::new();
        notification.summary(&title).body(&body);
        // Shown as this app, so the system names it, uses its icon and hands the
        // click back to it. Naming the app again on macOS only fails, harmlessly.
        #[cfg(windows)]
        notification.app_id(&identifier);
        #[cfg(target_os = "macos")]
        let _ = notify_rust::set_application(&identifier);
        #[cfg(not(any(target_os = "macos", windows)))]
        let _ = identifier;
        let Ok(shown) = notification.show() else {
            return;
        };
        #[cfg(any(target_os = "macos", windows))]
        let _ = shown.wait_for_response(|response: &notify_rust::NotificationResponse| {
            if matches!(
                response,
                notify_rust::NotificationResponse::Default | notify_rust::NotificationResponse::Action(_)
            ) {
                open_page(&app, &url);
            }
        });
        #[cfg(not(any(target_os = "macos", windows)))]
        drop((shown, app, url));
    });
}

/// How many things wait on the person: the number on the Dock icon (macOS), a
/// dot on the taskbar button (Windows, which shows no numbers), and the tray menu.
#[tauri::command]
pub fn set_badge(app: AppHandle, window: WebviewWindow, count: u32) {
    #[cfg(windows)]
    let _ = window.set_overlay_icon((count > 0).then(dot));
    #[cfg(not(windows))]
    let _ = window.set_badge_count((count > 0).then_some(i64::from(count)));
    #[cfg(not(target_os = "macos"))]
    tray::show_count(&app, count);
    #[cfg(target_os = "macos")]
    let _ = app;
}

/// An amber dot for the taskbar button, drawn here so it needs no image file.
#[cfg(windows)]
fn dot() -> tauri::image::Image<'static> {
    const SIDE: u32 = 16;
    let mut rgba = Vec::with_capacity((SIDE * SIDE * 4) as usize);
    let centre = (SIDE as f32 - 1.0) / 2.0;
    for y in 0..SIDE {
        for x in 0..SIDE {
            let d = ((x as f32 - centre).powi(2) + (y as f32 - centre).powi(2)).sqrt();
            // The brand amber (--accent), with a soft one-pixel edge.
            let alpha = (centre + 0.5 - d).clamp(0.0, 1.0);
            rgba.extend_from_slice(&[0xf5, 0x7f, 0x17, (alpha * 255.0) as u8]);
        }
    }
    tauri::image::Image::new_owned(rgba, SIDE, SIDE)
}

/// The tray icon, where there is no Dock to bring the window back from.
#[cfg(not(target_os = "macos"))]
pub mod tray {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
    use tauri::{AppHandle, Manager};

    use super::{bring_back, open_page};

    /// The menu line that shows how many things wait.
    pub struct Waiting(MenuItem<tauri::Wry>);

    pub fn install(app: &AppHandle) -> tauri::Result<()> {
        let open = MenuItem::with_id(app, "open", "打开 Cheese", true, None::<&str>)?;
        let waiting = MenuItem::with_id(app, "inbox", "待办", true, None::<&str>)?;
        let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
        let menu = Menu::with_items(app, &[&open, &waiting, &PredefinedMenuItem::separator(app)?, &quit])?;
        let mut tray = TrayIconBuilder::with_id("main")
            .tooltip("Cheese")
            .menu(&menu)
            .show_menu_on_left_click(false)
            .on_menu_event(|app, event| match event.id().as_ref() {
                "open" => bring_back(app),
                "inbox" => open_page(app, "/inbox"),
                "quit" => app.exit(0),
                _ => {}
            })
            .on_tray_icon_event(|tray, event| {
                if let TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } = event
                {
                    bring_back(tray.app_handle());
                }
            });
        if let Some(icon) = app.default_window_icon() {
            tray = tray.icon(icon.clone());
        }
        tray.build(app)?;
        app.manage(Waiting(waiting));
        Ok(())
    }

    pub fn show_count(app: &AppHandle, count: u32) {
        if let Some(waiting) = app.try_state::<Waiting>() {
            let text = if count > 0 { format!("待办 {count} 件") } else { "待办".to_string() };
            let _ = waiting.0.set_text(text);
        }
    }
}
