//! The next launch keeps an unattended update hidden, but shows a requested
//! update even when the old process was started at login. Windows' installer
//! exits the process itself, so this intent must be saved before installation.
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const FILE: &str = "start-hidden";
const MAX_AGE_SECS: u64 = 10 * 60;

pub struct RestartMarker {
    path: PathBuf,
    hidden: bool,
    committed: bool,
}

impl RestartMarker {
    pub fn prepare(dir: &Path, hidden: bool) -> io::Result<Self> {
        std::fs::create_dir_all(dir)?;
        let path = dir.join(FILE);
        write_intent(&path, hidden)?;
        Ok(Self { path, hidden, committed: false })
    }

    pub fn commit(mut self) {
        // macOS installation can wait for administrator approval. The restart
        // intent must stay fresh from installation completion, not its start.
        if let Err(e) = write_intent(&self.path, self.hidden) {
            eprintln!("cheese: could not refresh the update restart intent: {e}");
        }
        self.committed = true;
    }
}

fn write_intent(path: &Path, hidden: bool) -> io::Result<()> {
    let intent = if hidden { "hidden" } else { "visible" };
    std::fs::write(path, format!("{intent}\n{}", now_secs()))
}

impl Drop for RestartMarker {
    fn drop(&mut self) {
        if !self.committed {
            // An installation that returned an error must not hide a later launch.
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn intent(content: &str, now: u64) -> Option<bool> {
    let mut lines = content.lines();
    let hidden = match lines.next()? {
        "hidden" => true,
        "visible" => false,
        _ => return None,
    };
    let written = lines.next()?.parse::<u64>().ok()?;
    if now < written || now - written > MAX_AGE_SECS {
        return None;
    }
    Some(hidden)
}

pub fn start_hidden(dir: &Path, at_login: bool) -> bool {
    let path = dir.join(FILE);
    let hidden = std::fs::read_to_string(&path).ok().and_then(|content| {
        if content.is_empty() {
            // The first fixed release may be installed by an older app. Its
            // empty marker is valid only while that restart is still recent.
            let written = std::fs::metadata(&path).ok()?.modified().ok()?
                .duration_since(UNIX_EPOCH).ok()?.as_secs();
            intent(&format!("hidden\n{written}"), now_secs())
        } else {
            intent(&content, now_secs())
        }
    });
    let _ = std::fs::remove_file(path);
    hidden.unwrap_or(at_login)
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
                "cheese-update-launch-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
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
    fn old_or_expired_markers_fall_back_to_the_launch_mode() {
        assert_eq!(intent("", 100), None);
        assert_eq!(intent("hidden\n1", 1 + MAX_AGE_SECS + 1), None);
        assert_eq!(intent("visible\n101", 100), None);
        assert_eq!(intent("hidden\n100", 100 + MAX_AGE_SECS), Some(true));
    }

    #[test]
    fn abandoned_restart_does_not_change_a_later_launch() {
        let dir = Dir::new();
        let expired = now_secs() - MAX_AGE_SECS - 1;
        std::fs::write(dir.0.join(FILE), format!("hidden\n{expired}")).unwrap();
        assert!(!start_hidden(&dir.0, false));
        std::fs::write(dir.0.join(FILE), format!("visible\n{expired}")).unwrap();
        assert!(start_hidden(&dir.0, true));
    }

    #[test]
    fn slow_install_preserves_the_requested_restart_mode() {
        for hidden in [false, true] {
            let dir = Dir::new();
            let marker = RestartMarker::prepare(&dir.0, hidden).unwrap();
            // Model an installation that outlasted the original intent.
            let expired = now_secs() - MAX_AGE_SECS - 1;
            let mode = if hidden { "hidden" } else { "visible" };
            std::fs::write(dir.0.join(FILE), format!("{mode}\n{expired}")).unwrap();
            marker.commit();
            assert_eq!(start_hidden(&dir.0, !hidden), hidden);
        }
    }

    #[test]
    fn cannot_prepare_restart_in_an_unwritable_location() {
        let dir = Dir::new();
        let not_a_dir = dir.0.join("file");
        std::fs::write(&not_a_dir, "").unwrap();
        assert!(RestartMarker::prepare(&not_a_dir, false).is_err());
    }
}
