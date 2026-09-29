"""Handing a third-party authorization back to the desktop app.

The app sends authorization to the person's browser, where their accounts
are signed in and where Google accepts to show its page at all. The page in
the app says so on the request that starts a flow (``started_in_app``); the
flow carries that in its state; and its callback, instead of landing the
browser on the page with the result, lands it on ``/account/to-app``, which opens the
app on that page through a ``cheese://`` link (desktop/src-tauri/src/links.rs).
The result is saved by then either way: this only decides where it is shown.
"""

from urllib.parse import urlencode, urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse

from app.core.config import settings

# Set by the web app while it runs in the desktop app (frontend/src/lib/desktopApp.ts).
APP_HEADER = "X-Cheese-App"


def started_in_app(request: Request) -> bool:
    return request.headers.get(APP_HEADER) == "1"


def back_in_app(response: RedirectResponse) -> RedirectResponse:
    """The same landing, shown in the app rather than in this browser."""
    parts = urlsplit(response.headers["location"])
    page = parts.path or "/"
    if parts.query:
        page += f"?{parts.query}"
    if parts.fragment:
        page += f"#{parts.fragment}"
    to_app = f"{settings.frontend_url}/account/to-app?{urlencode({'path': page})}"
    return RedirectResponse(to_app, status_code=302)
