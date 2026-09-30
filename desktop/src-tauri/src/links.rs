//! `cheese://` links: the browser handing the app a page to show. Signing in
//! with GitHub or Google and connecting GitHub, Feishu or an MCP server happen
//! in the browser (frontend/src/lib/desktopApp.ts); when they are done, the
//! browser opens `cheese://open?path=<a page of the web app>` and the window
//! comes back on that page (backend/app/api/app_return.py).
//!
//! A link can come from anywhere, so it names a page on the server and nothing
//! else: whatever it says, the window only ever goes to a page of `ORIGIN`.

use tauri::{AppHandle, Manager};
use url::Url;

use crate::resident;

/// The page a link names, as a path on the server ("/inbox?x=1"), if it names one.
pub fn page(link: &Url) -> Option<String> {
    if link.scheme() != "cheese" || link.host_str() != Some("open") {
        return None;
    }
    let (_, path) = link.query_pairs().find(|(key, _)| key == "path")?;
    let origin = Url::parse(crate::ORIGIN).ok()?;
    if !path.starts_with('/') || path.starts_with("//") || path.starts_with("/\\") {
        return None;
    }
    let target = origin.join(&path).ok()?;
    (target.origin() == origin.origin()).then(|| {
        let mut page = target.path().to_string();
        if let Some(query) = target.query() {
            page = format!("{page}?{query}");
        }
        if let Some(fragment) = target.fragment() {
            page = format!("{page}#{fragment}");
        }
        page
    })
}

/// Brings the window back on the page a link names. A page of the web app is
/// replaced by it, so what it shows is read afresh; the app's own first page
/// (still waiting for the server, or offline) goes there once the server answers.
pub fn open(app: &AppHandle, link: &Url) {
    let Some(page) = page(link) else {
        return;
    };
    resident::bring_back(app);
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let Ok(mut now) = window.url() else {
        return;
    };
    if crate::from_server(&now) {
        if let Ok(target) = Url::parse(crate::ORIGIN).and_then(|origin| origin.join(&page)) {
            let _ = window.navigate(target);
        }
    } else {
        now.query_pairs_mut().clear().append_pair("start", &page);
        let _ = window.navigate(now);
    }
}

#[cfg(test)]
mod tests {
    use super::page;
    use url::Url;

    fn page_of(link: &str) -> Option<String> {
        page(&Url::parse(link).unwrap())
    }

    #[test]
    fn a_link_opens_the_page_it_names() {
        assert_eq!(
            page_of("cheese://open?path=%2Finbox").as_deref(),
            Some("/inbox")
        );
        assert_eq!(
            page_of("cheese://open?path=%2Fprojects%2Fp%2Fsettings%3Fmcp%3Dx%23mcp").as_deref(),
            Some("/projects/p/settings?mcp=x#mcp")
        );
    }

    #[test]
    fn a_link_never_leaves_the_server() {
        assert_eq!(
            page_of("cheese://open?path=https%3A%2F%2Fevil.example%2F"),
            None
        );
        assert_eq!(page_of("cheese://open?path=%2F%2Fevil.example%2F"), None);
        assert_eq!(page_of("cheese://open?path=%2F%5Cevil.example%2F"), None);
        assert_eq!(page_of("cheese://open?path=javascript%3Aalert(1)"), None);
        assert_eq!(page_of("cheese://elsewhere?path=%2Finbox"), None);
        assert_eq!(page_of("cheese://open"), None);
    }

    #[test]
    fn dots_stay_on_the_server() {
        assert_eq!(
            page_of("cheese://open?path=%2F..%2F..%2Finbox").as_deref(),
            Some("/inbox")
        );
    }
}
