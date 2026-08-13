"""UI probe: the chat panel must STOP when the socket is refused at connect.

The backend has exactly three connect-time refusals (app/api/routes/chat.py):
`auth_required` (no token), `auth_expired` (a token it could not verify) and
`forbidden` (verified, but not a member of this topic). All three arrive as one
`error` frame and are followed by close 1008.

What makes this worth a probe rather than a unit test is WHERE it goes wrong.
The handshake SUCCEEDS — the refusal is a frame, not a failed upgrade — so by
the time the reason arrives, `onopen` has already cleared the banner and reset
the reconnect backoff to 1s. A code the panel does not recognise therefore falls
into the ordinary auto-reconnect path and hammers the server about once a second,
forever, with the reason blinking in and out. Measured with `forbidden` left out
of the recognised set: 9 connections in 8 seconds. Nothing about that is visible
to a test that only asserts the banner text — the banner reads correctly on every
retry — which is why the check below counts SOCKETS, not words.

Prereqs: the same ephemeral stack scripts/probe_reactions.py documents — a
throwaway POSTGRES database at `alembic upgrade head`, a backend on :8098
against it, and the probe vite on :5175. No AI provider needed; nothing here
summons 芝士.

Run:  cd backend && DATABASE_URL=... uv run --with playwright python \
        ../scripts/probe_chat_refusal.py

Run it from `backend/`, for the same reason probe_reactions.py does: the tokens
below are signed with `settings.jwt_secret`, and pydantic-settings resolves
`.env` against the CWD.

Screenshots land in tmp_review/refusal_*.png.
"""

import asyncio
import json
import os
import sys
import urllib.request

API = os.environ.get("PROBE_API", "http://127.0.0.1:8098")
BASE = os.environ.get("PROBE_BASE", "http://localhost:5175")
SHOT_DIR = "tmp_review"
# The banner must survive this long. The reconnect fires at 1s, so anything that
# retries has retried several times by the end of the window.
WATCH_MS = 8000


def _req(method: str, path: str, body: dict | None = None, token: str = ""):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(r) as resp:
        return json.load(resp)["data"]


def _token(handle: str, ttl_s: int | None = None) -> str:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    from app.core.tokens import mint_session_token

    return mint_session_token(handle=handle, user_id=None, ttl_s=ttl_s)


# Count the chat sockets the app opens, from before any app code runs.
COUNT_SOCKETS = """
window.__wsOpens = 0;
const Real = window.WebSocket;
window.WebSocket = function (url, ...rest) {
  if (String(url).includes('/chat')) window.__wsOpens += 1;
  return new Real(url, ...rest);
};
window.WebSocket.prototype = Real.prototype;
"""

SET_IDENTITY = """
([token, me]) => {
  if (token) localStorage.setItem('accessToken', token);
  else localStorage.removeItem('accessToken');
  localStorage.setItem('cheesex.me', JSON.stringify(me));
}
"""


async def main() -> None:
    from playwright.async_api import async_playwright

    owner = _token("user-1")
    proj = _req("POST", "/api/projects", {"name": "probe-refusal"}, token=owner)
    topic = _req(
        "POST", "/api/topics",
        {"project_id": proj["id"], "title": "拒绝横幅 probe"},
        token=owner,
    )
    print(f"[setup] project={proj['id']} topic={topic['id']}")

    # user-1 owns the room, so `mallory` holding a perfectly valid token is the
    # non-member case — the one refusal that is NOT about logging in again.
    cases = [
        ("forbidden", _token("mallory")),
        ("auth_expired", _token("user-1", ttl_s=-60)),
        ("auth_required", ""),
    ]

    failures: list[str] = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for label, token in cases:
            pg = await b.new_page(viewport={"width": 1440, "height": 900})
            await pg.goto(BASE)
            await pg.evaluate(
                SET_IDENTITY,
                [token, {"id": "probe", "handle": "mallory", "name": "路人"}],
            )
            await pg.add_init_script(COUNT_SOCKETS)
            await pg.goto(f"{BASE}/project/{proj['id']}?topic={topic['id']}")
            await pg.wait_for_load_state("networkidle")
            await pg.wait_for_timeout(WATCH_MS)

            opens = await pg.evaluate("() => window.__wsOpens")
            alert = pg.locator(".chat-error-toast")
            text = (
                (await alert.text_content() or "").strip()
                if await alert.count()
                else ""
            )
            await pg.screenshot(path=f"{SHOT_DIR}/refusal_{label}.png")

            checks = {
                "reason is on screen": bool(text),
                "not mislabelled as an outage": "正在自动重连" not in text,
                "retry loop is latched (exactly one socket)": opens == 1,
            }
            print(f"\n=== {label} ===")
            print(f"[banner] {text!r}  [chat sockets in {WATCH_MS}ms] {opens}")
            for name, ok in checks.items():
                print(f"[check {label}] {'PASS' if ok else 'FAIL'}  {name}")
                if not ok:
                    failures.append(f"{label}: {name}")
            await pg.close()
        await b.close()

    print()
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("RESULT: PASS — every refusal stops the panel and keeps its reason")


if __name__ == "__main__":
    asyncio.run(main())
