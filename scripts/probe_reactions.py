"""UI probe: Slack-style reactions + discrete-message chat (no streaming).

Runs against an EPHEMERAL stack (sqlite backend on :8098 + vite on :5175) so
the running dogfood backend/frontend and its data are never touched.

Prereqs (started separately, see the redeploy notes / task transcript):
  1. ephemeral backend:
       cd backend && DATABASE_URL=sqlite+aiosqlite:///<tmp>/probe.db \
         WORKSPACE_ROOT=<tmp>/ws uv run uvicorn app.main:app --port 8098
     (this script creates the schema in that sqlite file first — run it with
      the same DATABASE_URL before/while the server is up)
  2. probe vite: cd frontend && npx vite --config vite.probe.config.mts

Run:  cd backend && DATABASE_URL=... uv run --with playwright python \
        ../scripts/probe_reactions.py

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


def _req(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r) as resp:
        return json.load(resp)["data"]


async def _create_schema() -> None:
    """Create all tables in the ephemeral sqlite DB (no alembic — probe only)."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    import app.models  # noqa: F401  (registers tables)
    from app.core.db import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def main() -> None:
    from playwright.async_api import async_playwright

    await _create_schema()
    os.makedirs(SHOT_DIR, exist_ok=True)

    # ---- seed: probe-only project + topic + messages (summon=False → no AI) ----
    proj = _req("POST", "/api/projects", {"name": "probe-reactions"})
    topic = _req(
        "POST", "/api/topics",
        {"project_id": proj["id"], "title": "表情与离散消息 probe"},
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
        await pg.evaluate(
            "localStorage.setItem('cheesex.me',"
            " JSON.stringify({id:'probe', handle:'user-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj['id']}?topic={topic['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(800)

        # Post two messages over the chat WS from the page itself.
        post = """
        async ({ topicId, content, author }) => {
          // The author now comes from the socket's token, not from the frame.
          const token = localStorage.getItem('accessToken') || '';
          const ws = new WebSocket(
            `ws://${location.host}/api/topics/${topicId}/chat`
            + `?token=${encodeURIComponent(token)}`);
          await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
          ws.send(JSON.stringify({ type: 'message', content, author, summon: false }));
          const id = await new Promise((res) => {
            const t = setTimeout(() => res(null), 4000);
            ws.onmessage = (ev) => {
              const f = JSON.parse(ev.data);
              if (f.type === 'user_block' && f.block?.content === content) {
                clearTimeout(t); res(f.block.id);
              }
            };
          });
          ws.close();
          return id;
        }
        """
        m1 = await pg.evaluate(
            post,
            {"topicId": topic["id"], "content": "这个方案大家觉得怎么样？",
             "author": "user-1"},
        )
        m2 = await pg.evaluate(
            post,
            {"topicId": topic["id"], "content": "我觉得可以，芝士记一下结论",
             "author": "user-2"},
        )
        assert m1 and m2, "seed messages failed"
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
        await pg.wait_for_timeout(500)
        mine = row.locator(".rx-chip--mine")
        assert await mine.count() == 1, "my 👍 chip not highlighted"
        assert "2" in (await mine.text_content() or ""), "👍 count should be 2"
        await pg.screenshot(path=f"{SHOT_DIR}/reactions_c_mine_highlight.png")

        # Toggle off: count back to 1, highlight gone.
        await mine.click()
        await pg.wait_for_timeout(500)
        assert await row.locator(".rx-chip--mine").count() == 0
        chips = await row.locator(".rx-chip").all_text_contents()
        print("[B] after toggle-off:", chips)
        await pg.screenshot(path=f"{SHOT_DIR}/reactions_d_toggled_off.png")

        await b.close()
    print("PROBE OK")


if __name__ == "__main__":
    asyncio.run(main())
