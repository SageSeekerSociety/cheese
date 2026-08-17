"""Screenshot: de-PR'd topic header + Feishu-style mention menu."""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


async def main() -> None:
    proj = _get("/api/projects")["data"][0]["id"]
    topics = _get(f"/api/topics?project_id={proj}")["data"]
    work = next(t for t in topics if t.get("kind") != "root" and t["status"] == "active")

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj}?topic={work['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(1500)
        # header shot
        await pg.screenshot(path="tmp_review/header-depr.png", clip={"x": 280, "y": 50, "width": 700, "height": 90})
        # mention menu: type @ in composer
        composer = pg.locator("textarea:not([readonly])").first
        await composer.click()
        await composer.type("@")
        await pg.wait_for_timeout(600)
        await pg.screenshot(path="tmp_review/mention-menu.png", full_page=False)
        await b.close()


asyncio.run(main())
