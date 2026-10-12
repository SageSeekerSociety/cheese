//! The next launch keeps an unattended update hidden, but shows a requested
//! update even when the old process was started at login. Windows' installer
//! exits the process itself, so this intent must be saved before installation.
use std::io;
use std::path::{Path, PathBuf};

const FILE: &str = "start-hidden";

pub struct RestartMarker {
    path: PathBuf,
    committed: bool,
}

impl RestartMarker {
    pub fn prepare(dir: &Path, hidden: bool) -> io::Result<Self> {
        std::fs::create_dir_all(dir)?;
        let path = dir.join(FILE);
        std::fs::write(&path, if hidden { "hidden" } else { "visible" })?;
        Ok(Self { path, committed: false })
    }

    pub fn commit(mut self) {
        self.committed = true;
    }
}

impl Drop for RestartMarker {
    fn drop(&mut self) {
        if !self.committed {
            // An installation that returned an error must not hide a later launch.
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

pub fn start_hidden(dir: &Path, at_login: bool) -> bool {
    let path = dir.join(FILE);
    match std::fs::read_to_string(&path) {
        Ok(intent) if std::fs::remove_file(path).is_ok() => {
            // Older installed apps wrote an empty file for unattended updates.
            intent != "visible"
        }
        _ => at_login,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Dir(PathBuf);
    impl Dir {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "cheese-update-launch-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn requested_update_shows_even_if_the_old_process_started_at_login() {
        let dir = Dir::new();
        RestartMarker::prepare(&dir.0, false).unwrap().commit();
        assert!(!start_hidden(&dir.0, true));
        // Only that restart overrides login; a later login is hidden as usual.
        assert!(start_hidden(&dir.0, true));
    }

    #[test]
    fn unattended_update_stays_hidden_only_for_its_restart() {
        let dir = Dir::new();
        RestartMarker::prepare(&dir.0, true).unwrap().commit();
        assert!(start_hidden(&dir.0, false));
        assert!(!start_hidden(&dir.0, false));
    }

    #[test]
    fn failed_install_does_not_hide_the_next_manual_launch() {
        let dir = Dir::new();
        let attempt = RestartMarker::prepare(&dir.0, true).unwrap();
        drop(attempt);
        assert!(!start_hidden(&dir.0, false));
        assert!(start_hidden(&dir.0, true));
    }

    #[test]
    fn requested_update_replaces_a_previous_hidden_intent() {
        let dir = Dir::new();
        std::fs::write(dir.0.join(FILE), "").unwrap();
        RestartMarker::prepare(&dir.0, false).unwrap().commit();
        assert!(!start_hidden(&dir.0, true));
    }

    #[test]
    fn restart_from_an_older_app_stays_hidden() {
        let dir = Dir::new();
        std::fs::write(dir.0.join(FILE), "").unwrap();
        assert!(start_hidden(&dir.0, false));
        assert!(!start_hidden(&dir.0, false));
    }

    #[test]
    fn cannot_prepare_restart_in_an_unwritable_location() {
        let dir = Dir::new();
        let not_a_dir = dir.0.join("file");
        std::fs::write(&not_a_dir, "").unwrap();
        assert!(RestartMarker::prepare(&not_a_dir, false).is_err());
    }
}
