"""Running a mini-app once before it is kept.

The platform's browser (browser-render's ``/inspect``) runs the shell with the
code in it, at the width of a phone and of the document's column, light and
dark. What it reports decides whether the code is kept: an app that throws,
draws nothing or keeps growing goes back to whoever wrote it, with why. What it
draws is kept as the pictures a phone shows before the app runs, and its
height is the room the document holds for it before it starts.

The browser is an optional service, and the shell is fetched from the
frontend: either can be away. Then nothing was learned, and the code is kept
unchecked rather than refused — refusing would leave 芝士 unable to write any
app for as long as the browser is down.
"""

import base64
import json
from dataclasses import dataclass, field

import httpx

from app.core.config import settings
from app.core.obs import get_logger

_log = get_logger("cheesex.web_snippet")

#: A phone, and the document's column at its widest.
WIDTHS = (400, 720)
THEMES = ("light", "dark")
#: Where the shell draws the app: the box the pictures are of.
APP_ELEMENT = "#cheese-app"
#: Two runs of two widths each, and a browser that may be starting cold.
CHECK_TIMEOUT_S = 90.0


@dataclass
class Ran:
    #: Height in pixels by width.
    heights: dict[int, int] = field(default_factory=dict)
    #: PNG bytes by "<width>-<theme>".
    shots: dict[str, bytes] = field(default_factory=dict)


class Refused(Exception):
    """The code ran and did not work; the message says how, for its writer."""


async def shell_page(shell: int) -> str | None:
    """The shell's page, as the frontend built it; None when it cannot be had."""
    base = settings.miniapp_shell_url
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base.rstrip('/')}/v{shell}/index.html")
    except httpx.HTTPError as exc:
        _log.warning("miniapp_shell_unreachable", error=type(exc).__name__)
        return None
    if r.status_code != 200:
        _log.warning("miniapp_shell_missing", status=r.status_code)
        return None
    return r.text


def with_code(page: str, code: str, theme: str) -> str:
    """The shell's page with the code already in it, for a browser that is
    handed markup and has no parent page to receive it from."""
    boot = json.dumps(
        {"code": code, "theme": theme, "mode": "shot"}, ensure_ascii=False
    )
    # Inside a <script>, only "</" can end it early.
    boot = boot.replace("</", "<\\/")
    script = f"<script>window.__CHEESE_APP__={boot}</script>"
    head = page.find("<head>")
    if head < 0:
        return script + page
    at = head + len("<head>")
    return page[:at] + script + page[at:]


async def run(code: str, shell: int) -> Ran | None:
    """What the browser saw of ``code``; None when no browser or shell could be
    had. Raises :class:`Refused` when the code did not work."""
    endpoint = settings.fetch_browser_endpoint
    if not endpoint:
        return None
    page = await shell_page(shell)
    if page is None:
        return None
    ran = Ran()
    for theme in THEMES:
        try:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S) as client:
                r = await client.post(
                    endpoint.rstrip("/") + "/inspect",
                    json={
                        "html": with_code(page, code, theme),
                        "widths": list(WIDTHS),
                        "color_scheme": theme,
                        "settle_seconds": 1.0,
                        "element": APP_ELEMENT,
                    },
                )
        except httpx.HTTPError as exc:
            _log.warning("miniapp_check_unreachable", error=type(exc).__name__)
            return None
        answer = r.json() if r.status_code == 200 else {}
        if not answer.get("ok"):
            # Busy, or the browser itself broke: nothing was learned about the code.
            _log.warning("miniapp_check_unavailable", error=answer.get("error"))
            return None
        for view in answer.get("views") or []:
            errors = view.get("console_errors") or []
            if errors:
                raise Refused(errors[0])
            if view.get("element_height") is None:
                # A renderer older than the `element` option measured the page,
                # not the app: it says nothing about whether the app drew.
                _log.warning("miniapp_check_renderer_outdated")
                return None
            height = int(view["element_height"])
            if height <= 0:
                raise Refused("运行后什么都没画出来：app 里没有放进任何内容。")
            ran.heights[view["width"]] = height
            ran.shots[f"{view['width']}-{theme}"] = base64.b64decode(view["png_b64"])
    return ran
