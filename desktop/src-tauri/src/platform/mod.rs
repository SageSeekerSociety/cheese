//! Where cheesehost runs on this computer, and what it needs there first. Each
//! platform provides `prepare`, `shell` and `PRELUDE` (a sh where cheesehost
//! lives), `kill` and `CHEESEHOST_CONFIG`.

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "macos")]
pub use macos::*;

#[cfg(windows)]
mod windows;
#[cfg(windows)]
pub use windows::*;
