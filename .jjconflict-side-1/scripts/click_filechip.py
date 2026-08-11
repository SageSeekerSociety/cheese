"""Headless repro: click a 📄 file chip in the chat and verify the file tree
auto-expands (话题「文件跳转自动展开路径」验收脚本).

Run: uv run --with playwright python scripts/click_filechip.py
(Backend on :8099, vite dev on :3000 must be running.)
"""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:3000"
OUT = "tmp_review"


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


async def main() -> None:
    proj = _get("/api/projects")["data"][0]["id"]
    topics = _get(f"/api/topics?project_id={proj}")["data"]
    work = next(t for t in topics if t["title"] == "搭建推荐算法原型")["id"]

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1600, "height": 950})
        pg.on("console", lambda m: print(f"[console:{m.type}] {m.text}"))
        pg.on("pageerror", lambda e: print(f"[pageerror] {e}"))
        await pg.goto(f"{BASE}/project/{proj}?topic={work}")
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=f"{OUT}/1-topic.png")

        chips = pg.locator(".messages .file-ref")
        n = await chips.count()
        print(f"file chips in chat: {n}")
        if n == 0:
            print("NO CHIPS — dump last message html:")
            msgs = pg.locator(".messages .im-text")
            c = await msgs.count()
            for i in range(max(0, c - 3), c):
                print(await msgs.nth(i).inner_html())
            await b.close()
            return

        # click the deepest one (recall.py, 4 dirs deep)
        target = chips.filter(has_text="recall.py")
        await (target.first if await target.count() else chips.first).click()
        await pg.wait_for_timeout(2000)
        await pg.screenshot(path=f"{OUT}/2-after-click.png")

        rows = pg.locator(".file-list .file-item")
        print(f"visible tree rows after click: {await rows.count()}")
        for i in range(await rows.count()):
            print("  row:", (await rows.nth(i).inner_text()).strip())
        active = pg.locator(".file-item--active")
        print(f"active rows: {await active.count()}")
        await b.close()


asyncio.run(main())
