"""Design-review screenshot harness — captures every key screen to tmp_review/.

Reusable across design rounds: `uv run --with playwright python scripts/shots.py`.
Reads ids from the seeded DB via the backend. Light + a few interaction states.
"""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"
OUT = "tmp_review"


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


async def main() -> None:
    proj = _get("/api/projects")["data"][0]["id"]
    space = _get("/api/spaces")["data"][0]["id"]
    topics = _get(f"/api/topics?project_id={proj}")["data"]
    work = next((t for t in topics if t["kind"] == "topic"), topics[0])["id"]

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 860})

        async def shot(url: str, name: str, click: str | None = None) -> None:
            await pg.goto(BASE + url, wait_until="networkidle")
            await pg.wait_for_timeout(1400)
            if click:
                try:
                    await pg.get_by_text(click).first.click(timeout=4000)
                    await pg.wait_for_timeout(1600)
                except Exception as e:  # noqa
                    print("  click miss:", name, e)
            await pg.screenshot(path=f"{OUT}/{name}.png")
            print("shot", name)

        await shot(f"/project/{proj}", "01_workspace_root")
        await shot(f"/project/{proj}", "02_workspace_topic", click="搭建推荐")
        await shot(f"/project/{proj}/overview", "03_overview")
        await shot(f"/project/{proj}/calendar", "04_calendar")
        await shot(f"/space/{space}", "05_board")
        await shot(f"/project/{proj}/members/user-1", "06_profile")
        await shot(f"/project/{proj}/charter", "07_charter")
        await shot(f"/project/{proj}/decisions", "08_decisions")
        await shot(f"/project/{proj}", "09_private", click="与芝士私聊")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
