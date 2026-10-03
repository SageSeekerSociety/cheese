"""What a preview answers while its app cannot be reached right now.

A browser navigated to a preview whose app is momentarily unreachable — a
platform deploy leaving a ~1s tunnel gap, a dev server restarting, the bound
listener replaced, a stream that timed out — used to get a bare
``404 preview unavailable``. For a top-level navigation that 404 is a white
frame the user reads as broken, and nothing ever tried again.

A navigation now gets a small self-contained page saying what is happening,
counting the seconds and reloading itself with backoff; everything else gets a
transient ``503`` with ``Retry-After`` so asset and HMR clients back off instead
of treating the preview as gone. The page carries no external asset and no
internal detail, and runs under the content origin's sandbox CSP
(``allow-scripts allow-same-origin``), so its CSS and JS are inline.
"""

import json
from collections.abc import Mapping

from starlette.responses import HTMLResponse, Response

# The state header's value when no upstream response could be obtained at all
# and the transport cannot say why. The typed path names its own reason instead
# (``transport_unavailable``, ``app_unavailable``, ``instance_gone``).
UNAVAILABLE = "app_unavailable"

# How long the client should wait before asking again, and how long the app may
# stay unreachable before the page stops claiming it is coming up. Two seconds
# catches the ~1s window a platform deploy leaves; the page backs its own
# reloads off to ten.
RETRY_AFTER_S = 2
SLOW_AFTER_S = 60

# The interstitial is served by an origin whose job is the app; a page weighing
# more than the app would be one more thing failing in the failure. Tests hold
# the built page to this.
MAX_PAGE_BYTES = 4 * 1024

_COPY = {
    "zh": {
        "lang": "zh-CN",
        "starting_title": "应用启动中",
        "starting": "应用正在启动或重新连接…",
        "slow": "应用还没有响应，芝士可能需要重启它",
        "elapsed": "已等待 %s 秒",
        "restarted_title": "应用已重启",
        "restarted": "应用已重启，请刷新预览",
    },
    "en": {
        "lang": "en",
        "starting_title": "App starting",
        "starting": "The app is starting or reconnecting…",
        "slow": "The app still is not responding. Cheese may need to restart it.",
        "elapsed": "Waiting %s s",
        "restarted_title": "App restarted",
        "restarted": "The app restarted. Refresh the preview.",
    },
}

_STYLE = (
    "body{margin:0;min-height:100vh;display:flex;align-items:center;"
    "justify-content:center;background:#111;color:#eee;"
    "font:15px/1.6 system-ui,-apple-system,'Segoe UI',sans-serif;text-align:center}"
    "main{max-width:24rem;padding:1.5rem}"
    "p{margin:.5rem 0}"
    "#e{opacity:.6;font-size:13px}"
)

# The reload loop. State across reloads lives in sessionStorage so the elapsed
# counter and the slow message survive a reload; a sandbox without it (or a
# throw) degrades to a per-load counter, which is still honest. Backoff is
# 2s -> 10s and stays at 10: past the slow threshold the app is not coming up
# quickly, but stopping would leave the frame dark.
_RETRY_SCRIPT = (
    "(function(){"
    "var s=null;try{s=sessionStorage}catch(e){}"
    "function g(k){try{return s?s.getItem(k):null}catch(e){return null}}"
    "function w(k,v){try{if(s)s.setItem(k,v)}catch(e){}}"
    'var now=Date.now(),t=parseInt(g("cw.t")||"",10),n=parseInt(g("cw.n")||"",10);'
    'if(!t||t>now){t=now;w("cw.t",String(now))}'
    "if(!(n>=0)||n>100)n=0;"
    'var m=document.getElementById("m"),e=document.getElementById("e"),slow=false;'
    "function tick(){"
    "var sec=Math.round((Date.now()-t)/1000);"
    "if(sec>=__SLOW_AFTER__&&!slow){slow=true;m.textContent=__SLOW__}"
    'e.textContent=__ELAPSED__.replace("%s",String(sec))'
    "}"
    "tick();setInterval(tick,1000);"
    'w("cw.n",String(n+1));'
    "setTimeout(function(){location.reload()},Math.min(2*(n+1),10)*1000)"
    "})();"
)

_DOCUMENT = (
    '<!doctype html><html lang="__LANG__"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    "<title>__TITLE__</title><style>__STYLE__</style></head>"
    "<body>__MAIN__</body></html>"
)


def is_document_navigation(method: str, headers: Mapping[str, str]) -> bool:
    """Whether this request is a document navigation, not an asset or fetch.

    Only a navigation gets the HTML page. An asset or an XHR wants an error it
    can back off from, and an HTML body handed to a ``<script>`` slot would break
    whatever asked for it, so both keep the plain error.

    A modern browser states the destination outright; the ``Accept``/mode pair
    covers one that does not send ``Sec-Fetch-Dest``.
    """
    if method.upper() != "GET":
        return False
    dest = headers.get("sec-fetch-dest", "").strip().lower()
    if dest in {"document", "iframe"}:
        return True
    return (
        "text/html" in headers.get("accept", "").lower()
        and headers.get("sec-fetch-mode", "").strip().lower() == "navigate"
    )


def _prefers_chinese(accept_language: str | None) -> bool:
    """Chinese unless the client's first stated language is something else.

    The header is ordered by preference, so the first tag decides; English is the
    fallback for a client that states nothing recognizable.
    """
    for part in (accept_language or "").split(","):
        tag = part.split(";", 1)[0].strip().lower()
        if tag:
            return tag.startswith("zh")
    return False


def _document(copy: dict[str, str], title: str, main: str) -> str:
    return (
        _DOCUMENT.replace("__LANG__", copy["lang"])
        .replace("__TITLE__", title)
        .replace("__STYLE__", _STYLE)
        .replace("__MAIN__", main)
    )


def _waiting_page(copy: dict[str, str]) -> str:
    script = (
        _RETRY_SCRIPT.replace("__SLOW_AFTER__", str(SLOW_AFTER_S))
        .replace("__SLOW__", json.dumps(copy["slow"], ensure_ascii=False))
        .replace("__ELAPSED__", json.dumps(copy["elapsed"], ensure_ascii=False))
    )
    main = f'<main><p id="m">{copy["starting"]}</p><p id="e"></p></main>'
    return _document(copy, copy["starting_title"], f"{main}<script>{script}</script>")


def _restarted_page(copy: dict[str, str]) -> str:
    """The instance is gone and the bound URL will not come back on its own.

    Reloading would ask for the same replaced instance forever, so this page is
    static: it says to refresh the preview, which resolves the current instance.

    It does not signal the host. The runtime channel needs a ``hello`` handshake
    carrying a per-frame session id the parent issued, and the parent drops any
    message from a source/origin it did not hand-shake — a wildcard post would be
    ignored, and nothing about an instance restart is something the parent acts
    on. Staying silent leaks nothing and lies to no one.
    """
    return _document(
        copy, copy["restarted_title"], f"<main><p>{copy['restarted']}</p></main>"
    )


def preview_unavailable_response(
    *, state: str, navigation: bool, accept_language: str | None
) -> Response:
    """The response for a request whose app could not be reached.

    ``instance_gone`` keeps the typed path's ``409`` (the bound instance is not
    coming back on this URL) and gets the restarted page. Everything else is
    ``503`` with ``Retry-After``: a navigation gets the wait-and-retry page, and
    anything else the plain error the legacy path already returned, so a client
    treats it as transient instead of as a missing preview.
    """
    headers = {"Cache-Control": "no-store", "X-Cheese-Preview-State": state}
    if state != "instance_gone":
        headers["Retry-After"] = str(RETRY_AFTER_S)
        status = 503
    else:
        status = 409
    if not navigation:
        return Response(
            b"preview unavailable",
            status_code=status,
            media_type="text/plain",
            headers=headers,
        )
    copy = _COPY["zh"] if _prefers_chinese(accept_language) else _COPY["en"]
    page = _restarted_page(copy) if state == "instance_gone" else _waiting_page(copy)
    return HTMLResponse(page, status_code=status, headers=headers)
