"""Which exact lines make a doc lossy? Capture the round-trip diff debug log."""

import asyncio
import json
import sys
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


async def main() -> None:
    title = sys.argv[1] if len(sys.argv) > 1 else "列表接口分页调研"
    proj = _get("/api/projects")["data"][0]["id"]
    tops = _get(f"/api/topics?project_id={proj}")["data"]
    t = next(x for x in tops if x["title"] == title)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        logs: list[str] = []
        pg.on("console", lambda m: logs.append(m.text) if "lossy" in m.text.lower() or "diff" in m.text.lower() or "round" in m.text.lower() else None)
        await pg.goto(BASE)
        await pg.evaluate("localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))")
        await pg.goto(f"{BASE}/project/{proj}?topic={t['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(2500)
        banner = await pg.locator("text=暂不完全支持").count()
        print("banner shown:", banner)
        for line in logs[:40]:
            print("LOG:", line[:400])
        await b.close()


asyncio.run(main())
