fn main() {
    // Declaring the commands gives each an `allow-…` permission, which is what
    // lets main.rs grant them to the server's pages — a remote page reaches no
    // command that has none.
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["connect_this_machine", "cancel_connect", "this_device", "claude_login", "cancel_claude_login", "claude_logout", "disconnect_this_machine", "set_theme", "set_badge", "listen_for_notices", "stop_notices", "opens_at_login", "set_opens_at_login", "update_status", "check_for_updates", "restart_to_update"])),
    )
    .expect("failed to run tauri-build");
}
