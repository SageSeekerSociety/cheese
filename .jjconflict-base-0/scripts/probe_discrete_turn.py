"""UI probe: a REAL summoned turn on the ephemeral stack (:8098/:5175) to see
the Slack-style flow live: instant 正在看… indicator → 芝士's ✅ receipt on the
summoning message → the reply landing as complete message block(s), no
token-streaming bubble.

Prereqs: the same ephemeral stack scripts/probe_reactions.py documents — a
throwaway POSTGRES database at `alembic upgrade head`, a backend on :8098
against it, and the probe vite on :5175. This one additionally needs the AI
provider configured (it drives a REAL turn); without credentials 芝士 answers
with a provider-error echo, which still satisfies the assertions below.

Run:  cd backend && DATABASE_URL=... uv run --with playwright python \
        ../scripts/probe_discrete_turn.py

Run it from `backend/` (as above), for the same reason probe_reactions.py does:
the session token this script mints must be signed with the secret the server
verifies with, and pydantic-settings resolves `.env` against the CWD.

Screenshots land in tmp_review/discrete_*.png.
"""

import asyncio
import json
import os
import sys
import urllib.request

API = "http://127.0.0.1:8098"
BASE = "http://localhost:5175"
SHOT_DIR = "tmp_review"


def _req(method: str, path: str, body: dict | None = None, token: str = ""):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(r) as resp:
        return json.load(resp)["data"]


def _token(handle: str) -> str:
    """A session token for `handle` — see probe_reactions.py for the full why.

    Short version: the chat WS refuses a socket it can't identify, and this probe
    never logs in, so localStorage holds no `accessToken` to read.
    """
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    from app.core.tokens import mint_session_token

    return mint_session_token(handle=handle, user_id=None)


async def main() -> None:
    from playwright.async_api import async_playwright

    tok = _token("user-1")
    proj = _req("POST", "/api/projects", {"name": "probe-discrete"}, token=tok)
    topic = _req(
        "POST", "/api/topics",
        {"project_id": proj["id"], "title": "离散消息实况 probe"},
        token=tok,
    )
    print(f"[setup] project={proj['id']} topic={topic['id']}")

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:300]))

        await pg.goto(BASE)
        # `accessToken` is what both the app's REST calls and its chat socket
        # read; without it ChatPanel's own connection is refused and the live
        # indicator/receipt this probe watches for never arrives.
        await pg.evaluate(
            """([token, me]) => {
              localStorage.setItem('accessToken', token);
              localStorage.setItem('cheesex.me', JSON.stringify(me));
            }""",
            [tok, {"id": "probe", "handle": "user-1", "name": "张衡"}],
        )
        await pg.goto(f"{BASE}/project/{proj['id']}?topic={topic['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(800)

        # Summon 芝士 through the real composer path (WS message, summon=True).
        # The token is passed in, not read from localStorage: a refusal is one
        # error frame and a close, which from here would look like 芝士 simply
        # never answering.
        refused = await pg.evaluate(
            """
            async ({ topicId, token }) => {
              // window.__cxApi.base: the gateway strips one '/api', so the app
              // addresses itself doubled — see frontend/src/api.ts.
              const ws = new WebSocket(
                `ws://${location.host}${window.__cxApi.base}`
                + `/topics/${topicId}/chat?token=${encodeURIComponent(token)}`);
              await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
              const refusal = new Promise((res) => {
                ws.addEventListener('message', (ev) => {
                  const f = JSON.parse(ev.data);
                  if (f.type === 'error') res(f.code || f.message);
                }, { once: false });
                setTimeout(() => res(null), 1500);
              });
              ws.send(JSON.stringify({
                type: 'message', content: '芝士你好，用一句话介绍你自己',
                summon: true,
              }));
              window.__probeWs = ws;  // keep it open so frames keep flowing
              return await refusal;
            }
            """,
            {"topicId": topic["id"], "token": tok},
        )
        if refused:
            raise SystemExit(f"chat WS refused the summon: {refused}")
        # Indicator should appear at once (turn_active/awaiting path uses the
        # page's own ChatPanel socket, which receives the broker frames).
        await pg.wait_for_timeout(1200)
        await pg.screenshot(path=f"{SHOT_DIR}/discrete_a_working.png")
        indicator = await pg.locator(".im-row", has_text="正在看").count()
        print("[A] working indicator rows:", indicator)

        # Wait for the turn to complete: ✅ chip on the user message + at least
        # one complete 芝士 message block, and NO streaming caret afterwards.
        await pg.wait_for_selector(".rx-chip", timeout=120_000)
        await pg.screenshot(path=f"{SHOT_DIR}/discrete_b_receipt.png")
        try:
            # A REAL assistant message row renders markdown (.md-content) —
            # the working-indicator row does not, so this only matches the
            # landed message.
            await pg.wait_for_selector(".im-text.md-content", timeout=180_000)
        finally:
            await pg.wait_for_timeout(600)
            await pg.screenshot(path=f"{SHOT_DIR}/discrete_c_reply.png")

        chips = await pg.locator(".rx-chip").all_text_contents()
        ai_rows = await pg.locator(".im-text.md-content").count()
        print("[B] chips:", chips, "| 芝士 message rows:", ai_rows)

        blocks = _req("GET", f"/api/topics/{topic['id']}/blocks")["data"]
        ai_msgs = [
            b for b in blocks
            if b["author_type"] == "ai" and b["kind"] == "message"
        ]
        user_msg = next(b for b in blocks if b["author_type"] == "human")
        # The receipt is signed by THIS topic's 分身 (`cheese-<topic hex>`), not
        # by the one platform `cheese` account — that seat is what makes the
        # room's agent attributable. Read it off the roster instead of spelling
        # it out: hardcoding "cheese" is what made this assertion go stale.
        roster = _req("GET", f"/api/topics/{topic['id']}/members")["data"]
        agent_handle = next(m["member_handle"] for m in roster if m["agent"])
        print("[C] persisted ai messages:", len(ai_msgs))
        print("[C] user msg reactions:", user_msg["reactions"])
        print("[C] topic agent seat:", agent_handle)
        assert user_msg["reactions"] == [
            {"emoji": "✅", "count": 1, "authors": [agent_handle]}
        ]
        assert len(ai_msgs) >= 1 and ai_msgs[0]["reply_to"] == user_msg["id"]

        await b.close()
    print("PROBE OK")


if __name__ == "__main__":
    asyncio.run(main())
