"""UI probe: Slack-style reactions + discrete-message chat (no streaming).

Runs against an EPHEMERAL stack (throwaway backend on :8098 + vite on :5175) so
the running dogfood backend/frontend and its data are never touched.

Prereqs (started separately). The DB must be POSTGRES, not sqlite: the fused
models declare real sequences (`app/domain/*/models.py`), so `create_all` on
sqlite dies with "Dialect 'sqlite' does not support sequence increments" — and
alembic, not `create_all`, is what builds a schema this app can actually boot on.

  1. throwaway database + schema:
       createdb -h 127.0.0.1 -p 5432 -U <user> cheesex_probe
       cd backend && DATABASE_URL=postgresql+asyncpg://<user>:<pw>@127.0.0.1:5432/cheesex_probe \
         uv run alembic upgrade head
  2. ephemeral backend, same DATABASE_URL:
       cd backend && DATABASE_URL=... WORKSPACE_ROOT=<tmp>/ws \
         uv run uvicorn app.main:app --host 127.0.0.1 --port 8098
  3. probe vite: cd frontend && npx vite --config vite.probe.config.mts

Run:  cd backend && DATABASE_URL=... uv run --with playwright python \
        ../scripts/probe_reactions.py

Run it from `backend/` (as above), not the repo root: the token this script
mints is signed with `settings.jwt_secret`, pydantic-settings reads `.env`
relative to the CWD, and the server was started from `backend/`. A different CWD
picks up a different secret and every socket is refused `auth_expired`.

Scenarios:
  A. Seed a topic with a few human messages; add reactions via the API
     (multiple authors + 芝士's ✅) → chips render with counts, own reactions
     highlighted amber.
  B. Hover a message → action bar shows the emoji button; open the picker,
     click 👍 → chip appears (mine), click the chip → toggles off.
  C. While the page is open, another author reacts via the API → the chip
     updates LIVE through the `reaction` WS frame (no reload).

Screenshots land in tmp_review/reactions_*.png.
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


def _backend_on_path() -> None:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def _token(handle: str) -> str:
    """A session token for `handle`, minted the way a real login would.

    The chat WS now REFUSES a socket it can't identify, so the probe needs a real
    credential. It cannot get one the browser's way: this stack has no user table
    to log into, so nothing ever writes `accessToken` into localStorage — reading
    it there yields '' and the socket closes with `auth_required`. Minting is the
    honest substitute, and it also fixes the page's OWN ChatPanel socket, which
    reads the same key (frontend/src/api.ts `authToken`).
    """
    _backend_on_path()
    from app.core.tokens import mint_session_token

    return mint_session_token(handle=handle, user_id=None)


async def main() -> None:
    from playwright.async_api import async_playwright

    os.makedirs(SHOT_DIR, exist_ok=True)

    # ---- seed: probe-only project + topic + messages (summon=False → no AI) ----
    # Two identities, because authorship now comes from the SOCKET: one page can
    # no longer post as two people by varying the frame's `author`, it needs two
    # tokens. user-1 creates (→ project owner + topic roster owner); user-2 is
    # added to the roster, without which their socket is refused `forbidden`.
    tok1, tok2 = _token("user-1"), _token("user-2")
    proj = _req("POST", "/api/projects", {"name": "probe-reactions"}, token=tok1)
    topic = _req(
        "POST", "/api/topics",
        {"project_id": proj["id"], "title": "表情与离散消息 probe"},
        token=tok1,
    )
    _req(
        "POST", f"/api/topics/{topic['id']}/members",
        {"handle": "user-2", "role": "member", "actor": "user-1"}, token=tok1,
    )
    print(f"[setup] project={proj['id']} topic={topic['id']}")

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:300]))
        pg.on(
            "console",
            lambda m: print("[console]", m.type, m.text[:200])
            if m.type == "error"
            else None,
        )

        await pg.goto(BASE)
        # `accessToken` is what the app reads for BOTH its REST calls and its
        # chat socket, so the page must carry it or ChatPanel's own connection is
        # refused and none of the live-update scenarios below can work.
        await pg.evaluate(
            """([token, me]) => {
              localStorage.setItem('accessToken', token);
              localStorage.setItem('cheesex.me', JSON.stringify(me));
            }""",
            [tok1, {"id": "probe", "handle": "user-1", "name": "张衡"}],
        )
        await pg.goto(f"{BASE}/project/{proj['id']}?topic={topic['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(800)

        # Post two messages over the chat WS from the page itself. The token is
        # passed IN rather than read from localStorage: an empty token is not an
        # error, it is a refusal 4 seconds later with no reason attached, and
        # that silence is exactly how this probe broke once.
        post = """
        async ({ topicId, content, token }) => {
          // window.__cxApi.base, not a hand-written path: the gateway strips
          // exactly one `/api`, so the base the app dials with is spelled once,
          // in frontend/src/api.ts — read it, don't reconstruct it here.
          const ws = new WebSocket(
            `ws://${location.host}${window.__cxApi.base}`
            + `/topics/${topicId}/chat?token=${encodeURIComponent(token)}`);
          await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
          ws.send(JSON.stringify({ type: 'message', content, summon: false }));
          const out = await new Promise((res) => {
            const t = setTimeout(() => res({ error: 'timeout' }), 4000);
            ws.onmessage = (ev) => {
              const f = JSON.parse(ev.data);
              // A refused socket answers one error frame and closes; surface the
              // code instead of letting it look like a timeout.
              if (f.type === 'error') {
                clearTimeout(t); res({ error: f.code || f.message });
              }
              if (f.type === 'user_block' && f.block?.content === content) {
                clearTimeout(t); res({ id: f.block.id });
              }
            };
          });
          ws.close();
          return out;
        }
        """

        async def seed(content: str, token: str) -> str:
            out = await pg.evaluate(
                post, {"topicId": topic["id"], "content": content, "token": token}
            )
            if out.get("error"):
                raise SystemExit(f"chat WS refused {content!r}: {out['error']}")
            return out["id"]

        m1 = await seed("这个方案大家觉得怎么样？", tok1)
        m2 = await seed("我觉得可以，芝士记一下结论", tok2)
        await pg.wait_for_timeout(400)

        # ---- A: reactions from other authors + 芝士's ✅ (via API) — the page
        #      must update LIVE from the `reaction` WS frames, no reload. ----
        _req("POST", f"/api/blocks/{m2}/reactions",
             {"emoji": "✅", "author": "cheese"})
        _req("POST", f"/api/blocks/{m1}/reactions",
             {"emoji": "👀", "author": "user-2"})
        _req("POST", f"/api/blocks/{m1}/reactions",
             {"emoji": "👍", "author": "user-3"})
        await pg.wait_for_timeout(600)
        chips = await pg.locator(".rx-chip").all_text_contents()
        print("[A] live chips:", chips)
        assert any("✅" in c for c in chips), "✅ chip missing"
        assert any("👀" in c for c in chips), "👀 chip missing"
        await pg.screenshot(path=f"{SHOT_DIR}/reactions_a_live_chips.png")

        # ---- B: hover → picker → toggle my own 👍 on m1 (count 1 → 2, mine) ----
        row = pg.locator(f'[data-mid="{m1}"]')
        await row.hover()
        await row.locator(".rx-toggle").click()
        await pg.wait_for_selector(".rx-picker")
        await pg.screenshot(path=f"{SHOT_DIR}/reactions_b_picker.png")
        await pg.locator(".rx-pick", has_text="👍").click()
        # Wait for the state, not for a duration: the chip lands when the POST
        # round-trips, and a fixed 500ms lost that race often enough to read as
        # a real failure ("my 👍 chip not highlighted") on a cold dev server.
        mine = row.locator(".rx-chip--mine")
        await mine.wait_for(timeout=10_000)
        assert await mine.count() == 1, "my 👍 chip not highlighted"
        assert "2" in (await mine.text_content() or ""), "👍 count should be 2"
        await pg.screenshot(path=f"{SHOT_DIR}/reactions_c_mine_highlight.png")

        # Toggle off: count back to 1, highlight gone.
        await mine.click()
        await mine.wait_for(state="detached", timeout=10_000)
        assert await row.locator(".rx-chip--mine").count() == 0
        chips = await row.locator(".rx-chip").all_text_contents()
        print("[B] after toggle-off:", chips)
        await pg.screenshot(path=f"{SHOT_DIR}/reactions_d_toggled_off.png")

        await b.close()
    print("PROBE OK")


if __name__ == "__main__":
    asyncio.run(main())
