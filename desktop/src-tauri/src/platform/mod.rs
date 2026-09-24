//! Where cheesehost runs on this computer, and what it needs there first. Each
//! platform provides `prepare`, `install_connector`, `cheesehost` (the command),
//! `cheesehost_config` and `kill`.

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "macos")]
pub use macos::*;

#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub use windows::*;
