"""Verify comment underlines: decorations render, click scrolls to card."""

import asyncio
import json
import urllib.request

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"

from playwright.async_api import async_playwright


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


async def main() -> None:
    proj = _get("/api/projects")["data"][0]["id"]
    tops = _get(f"/api/topics?project_id={proj}")["data"]
    t = next(x for x in tops if x["title"] == "编辑器质量验证")
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        await pg.goto(BASE)
        await pg.evaluate("localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))")
        await pg.goto(f"{BASE}/project/{proj}?topic={t['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(2000)
        marks = await pg.locator(".doc-editor .comment-anchor").count()
        print("comment-anchor marks:", marks)
        if marks:
            await pg.locator(".doc-editor .comment-anchor").first.click()
            await pg.wait_for_timeout(700)
            pulsing = await pg.locator(".comment-card--pulse").count()
            print("card pulsing after click:", pulsing)
            await pg.screenshot(path="tmp_review/comment-underline.png",
                clip={"x": 900, "y": 100, "width": 600, "height": 700})
        await b.close()


asyncio.run(main())
