"""Verify: doc panel renders token chips (no Decoration crash), title not duplicated."""

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
    # find a topic whose doc contains a file token
    target = None
    for t in topics:
        try:
            doc = _get(f"/api/topics/{t['id']}/doc")
            if doc and "<&" in (doc.get("content") or ""):
                target = t
                break
        except Exception:
            continue
    if not target:
        target = topics[0]
    print("topic:", target["id"], target["title"])

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:200]))
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj}?topic={target['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(2000)
        chips = await pg.evaluate(
            """() => [...document.querySelectorAll('.doc-editor .mention')].map(e => e.textContent.slice(0,40))"""
        )
        h1s = await pg.evaluate(
            """() => [...document.querySelectorAll('.doc-page__title, .doc-editor h1')].map(e => e.textContent.trim().slice(0,30))"""
        )
        print("chips:", chips[:8])
        print("titles:", h1s)
        await pg.screenshot(path="tmp_review/doc-tokens.png", full_page=False)
        await b.close()


asyncio.run(main())
