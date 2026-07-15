"""Where does 对话历史 loading time go? Cold page load + topic switch timing."""

import asyncio
import json
import time
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


async def main() -> None:
    proj = _get("/api/projects")["data"][0]["id"]
    tops = _get(f"/api/topics?project_id={proj}")["data"]
    heavy = max(tops, key=lambda t: 0 if t["kind"] == "root" else 1)  # root = heaviest
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:300]))
        pg.on("console", lambda m: print("[console.err]", m.text[:250]) if m.type == "error" else None)
        pg.on("requestfailed", lambda r: print("[reqfail]", r.url[-80:], r.failure))
        await pg.goto(BASE)
        await pg.evaluate("localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))")
        t0 = time.time()
        await pg.goto(f"{BASE}/project/{proj}?topic={heavy['id']}")
        # first message row visible
        try:
            await pg.wait_for_selector(".im-row, .im-event, .action-card", timeout=20000)
        except Exception:
            await pg.screenshot(path="tmp_review/load-stuck.png")
            print("STUCK — screenshot saved")
        t1 = time.time()
        print(f"cold load → first message visible: {t1-t0:.2f}s")
        perf = await pg.evaluate("""() => {
          const nav = performance.getEntriesByType('navigation')[0]
          const res = performance.getEntriesByType('resource')
          const slow = res.filter(r => r.duration > 300).map(r => `${Math.round(r.duration)}ms ${r.name.split('/').slice(-2).join('/')}`).slice(0, 10)
          return {domContentLoaded: Math.round(nav.domContentLoadedEventEnd), resources: res.length, slow}
        }""")
        print(json.dumps(perf, ensure_ascii=False, indent=1))
        # warm topic switch
        other = next(t for t in tops if t["id"] != heavy["id"] and t["status"] == "active")
        t2 = time.time()
        await pg.click(f"text={other['title'][:10]}")
        await pg.wait_for_timeout(100)
        t3 = time.time()
        print(f"topic switch: {t3-t2:.2f}s")
        await b.close()


asyncio.run(main())
