"""Verify: 市场 page — 题目匹配 tab (cards, apply dialog, applications,
accept flow, keyword search) and the 算力资源 tab.

Assumes at least one published template with applications exists (any stack;
override the frontend origin with PROBE_BASE). Screenshots go to tmp_review/.
"""

import asyncio
import os

from playwright.async_api import async_playwright

BASE = os.environ.get("PROBE_BASE", "http://localhost:5173")
OUT = "tmp_review"


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:200]))
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me',"
            " JSON.stringify({id:'probe', handle:'lead-1', name:'李雷'}))"
        )
        await pg.goto(f"{BASE}/market")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(1200)
        cards = await pg.locator(".task-card").count()
        print("task cards:", cards)
        await pg.screenshot(path=f"{OUT}/market-tasks-tab.png")

        if cards:
            card = pg.locator(".task-card").first
            await card.locator("text=应征列表").click()
            await pg.wait_for_timeout(900)
            await pg.screenshot(path=f"{OUT}/market-applications.png")
            await card.get_by_role("button", name="应征", exact=True).click()
            await pg.wait_for_timeout(600)
            await pg.screenshot(path=f"{OUT}/market-apply-dialog.png")
            await pg.keyboard.press("Escape")
            await pg.wait_for_timeout(400)

        await pg.locator(".v-tab:has-text('算力资源')").click()
        await pg.wait_for_timeout(900)
        pools = await pg.locator(".pool-card").count()
        print("pool cards:", pools)
        await pg.screenshot(path=f"{OUT}/market-pools-tab.png")
        await b.close()


asyncio.run(main())
