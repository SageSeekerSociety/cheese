fn main() {
    // Declaring the commands gives each an `allow-…` permission, which is what
    // lets main.rs grant them to the server's pages — a remote page reaches no
    // command that has none.
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(&["connect_this_machine", "cancel_connect"])),
    )
    .expect("failed to run tauri-build");
}
