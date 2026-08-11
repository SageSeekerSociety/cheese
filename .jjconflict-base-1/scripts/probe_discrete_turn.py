"""UI probe: a REAL summoned turn on the ephemeral stack (:8098/:5175) to see
the Slack-style flow live: instant 正在看… indicator → 芝士's ✅ receipt on the
summoning message → the reply landing as complete message block(s), no
token-streaming bubble.

Prereqs: same ephemeral backend + probe vite as scripts/probe_reactions.py
(and that script must have been run first — it creates the schema).

Run:  DATABASE_URL=... uv run --project backend --with playwright python \
        scripts/probe_discrete_turn.py

Screenshots land in tmp_review/discrete_*.png.
"""

import asyncio
import json
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


async def main() -> None:
    from playwright.async_api import async_playwright

    proj = _req("POST", "/api/projects", {"name": "probe-discrete"})
    topic = _req(
        "POST", "/api/topics",
        {"project_id": proj["id"], "title": "离散消息实况 probe"},
    )
    print(f"[setup] project={proj['id']} topic={topic['id']}")

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:300]))

        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me',"
            " JSON.stringify({id:'probe', handle:'user-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj['id']}?topic={topic['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(800)

        # Summon 芝士 through the real composer path (WS message, summon=True).
        await pg.evaluate(
            """
            async ({ topicId }) => {
              const ws = new WebSocket(
                `ws://${location.host}/api/topics/${topicId}/chat`);
              await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
              ws.send(JSON.stringify({
                type: 'message', content: '芝士你好，用一句话介绍你自己',
                author: 'user-1', summon: true,
              }));
              window.__probeWs = ws;  // keep it open so frames keep flowing
            }
            """,
            {"topicId": topic["id"]},
        )
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
        print("[C] persisted ai messages:", len(ai_msgs))
        print("[C] user msg reactions:", user_msg["reactions"])
        assert user_msg["reactions"] == [
            {"emoji": "✅", "count": 1, "authors": ["cheese"]}
        ]
        assert len(ai_msgs) >= 1 and ai_msgs[0]["reply_to"] == user_msg["id"]

        await b.close()
    print("PROBE OK")


if __name__ == "__main__":
    asyncio.run(main())
