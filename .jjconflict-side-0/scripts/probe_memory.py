"""Verify: 记忆 page renders entries after sidebar click (kind switch reload)."""

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
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:150]))
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(1200)
        await pg.click("text=记忆")
        await pg.wait_for_timeout(1200)
        cards = await pg.locator(".memory-card").count()
        weekly_bleed = await pg.locator("text=周报由芝士定期产出").count()
        print(f"memory cards: {cards} | weeklies bleed: {weekly_bleed}")
        await pg.screenshot(path="tmp_review/memory-page.png")
        await pg.click("text=章程")
        await pg.wait_for_timeout(800)
        charter_visible = await pg.locator(".charter-card").count()
        await pg.click("text=记忆")
        await pg.wait_for_timeout(1000)
        cards2 = await pg.locator(".memory-card").count()
        print(f"charter card: {charter_visible} | memory after round-trip: {cards2}")
        await b.close()


asyncio.run(main())
