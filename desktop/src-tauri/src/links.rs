//! `cheese://` links: the browser handing the app a page to show. Signing in
//! with GitHub or Google and connecting GitHub, Feishu or an MCP server happen
//! in the browser (frontend/src/lib/desktopApp.ts); when they are done, the
//! browser opens `cheese://open?path=<a page of the web app>` and the window
//! comes back on that page (backend/app/api/app_return.py).
//!
//! A link can come from anywhere, so it names a page on the server and nothing
//! else: whatever it says, the window only ever goes to a page of `ORIGIN`.
//! Two of those pages exist only to hand the reader to the content domain at
//! the top level (a preview, a published Site, frontend/src/views/PreviewOpenView.vue
//! and SiteOpenView.vue): the web app opens them in a new tab, so a link to one
//! goes to the browser, and the window never ends up on a content page.

use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;
use url::Url;

use crate::resident;

/// The server's pages that hand over to the content domain in place.
const HANDED_TO_CONTENT: &[&str] = &["previews", "sites"];

/// The path a link names on the server ("/inbox?x=1"), if it names one.
fn named(link: &Url) -> Option<String> {
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

/// Whether `page` is one that hands over to the content domain. The web app's
/// router matches paths case-insensitively, so the first segment is compared
/// that way, and with its percent-escapes read.
fn hands_to_content(page: &str) -> bool {
    let first = page.trim_start_matches('/').split(['/', '?', '#']).next().unwrap_or("");
    let mut bytes = Vec::with_capacity(first.len());
    let mut rest = first.as_bytes();
    while let Some((&b, tail)) = rest.split_first() {
        let escaped = (b == b'%' && tail.len() >= 2)
            .then(|| std::str::from_utf8(&tail[..2]).ok())
            .flatten()
            .and_then(|hex| u8::from_str_radix(hex, 16).ok());
        match escaped {
            Some(decoded) => {
                bytes.push(decoded);
                rest = &tail[2..];
            }
            None => {
                bytes.push(b);
                rest = tail;
            }
        }
    }
    let segment = String::from_utf8_lossy(&bytes).to_lowercase();
    HANDED_TO_CONTENT.contains(&segment.as_str())
}

/// The page a link names, as a path on the server ("/inbox?x=1"), if it names
/// one the window shows.
pub fn page(link: &Url) -> Option<String> {
    named(link).filter(|page| !hands_to_content(page))
}

/// Where in the browser a link goes instead: a page that hands over to the
/// content domain, on the server.
fn in_browser(link: &Url) -> Option<Url> {
    let page = named(link).filter(|page| hands_to_content(page))?;
    Url::parse(crate::ORIGIN).ok()?.join(&page).ok()
}

/// Opens in the browser a link the window does not show; whether it was one.
pub fn hand_off(app: &AppHandle, link: &Url) -> bool {
    let Some(target) = in_browser(link) else {
        return false;
    };
    let _ = app.opener().open_url(target.as_str(), None::<&str>);
    true
}

/// Where the window goes to show `page`, from where it is now. A page of the
/// web app, or a content page the window should never have reached, is
/// replaced by it, so what it shows is read afresh; the app's own first page
/// (still waiting for the server, or offline) goes there once the server answers.
fn next(now: &Url, page: &str) -> Option<Url> {
    if crate::from_server(now) || crate::from_content(now) {
        return Url::parse(crate::ORIGIN).and_then(|origin| origin.join(page)).ok();
    }
    let mut now = now.clone();
    now.query_pairs_mut().clear().append_pair("start", page);
    Some(now)
}

/// Brings the window back on the page a link names, or sends the link to the browser.
pub fn open(app: &AppHandle, link: &Url) {
    if hand_off(app, link) {
        return;
    }
    let Some(page) = page(link) else {
        return;
    };
    resident::bring_back(app);
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let Ok(now) = window.url() else {
        return;
    };
    if let Some(target) = next(&now, &page) {
        let _ = window.navigate(target);
    }
}

#[cfg(test)]
mod tests {
    use super::{in_browser, next, page};
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

    #[test]
    fn a_link_to_a_preview_or_a_site_opens_in_the_browser() {
        let preview = "cheese://open?path=%2Fpreviews%2Fabc%3Fpath%3D%2Findex.html";
        let site = "cheese://open?path=%2Fsites%2Fp";
        for link in [preview, site, "cheese://open?path=%2FPreviews%2Fabc", "cheese://open?path=%2F%70reviews%2Fabc"] {
            assert_eq!(page_of(link), None, "{link} would open in the window");
        }
        let url = |link: &str| in_browser(&Url::parse(link).unwrap()).map(String::from);
        assert_eq!(url(preview), Some(format!("{}/previews/abc?path=/index.html", crate::ORIGIN)));
        assert_eq!(url(site), Some(format!("{}/sites/p", crate::ORIGIN)));
        assert_eq!(url("cheese://open?path=%2Finbox"), None);
        assert_eq!(page_of("cheese://open?path=%2Fpreviewsx").as_deref(), Some("/previewsx"));
    }

    #[test]
    fn a_link_brings_a_window_on_a_content_page_back_to_the_server() {
        let content = Url::parse(&format!("https://abc.{}/", crate::CONTENT_DOMAIN)).unwrap();
        assert_eq!(
            next(&content, "/inbox").map(String::from),
            Some(format!("{}/inbox", crate::ORIGIN))
        );
    }
}
