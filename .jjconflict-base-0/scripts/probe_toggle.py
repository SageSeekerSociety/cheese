"""Reproduce: 编辑/只读 toggle leaves status stuck at 编辑中…"""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"


# No system proxy for localhost: urllib picks up the macOS proxy (Clash) and
# 127.0.0.1 requests get swallowed with a 404.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _req(url, method="GET", body=None):
    r = urllib.request.Request(url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    return json.load(_OPENER.open(r))["data"]


async def main() -> None:
    proj = _req(f"{API}/api/projects")["data"][0]["id"]
    topic = _req(f"{API}/api/topics", "POST", {"project_id": proj, "title": "切换按钮复现"})
    tid = topic["id"]
    _req(f"{API}/api/topics/{tid}/doc", "PUT", {"content": "# 无关标题\n\n一段普通内容。\n", "author": "mentor-1"})

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        await pg.goto(BASE)
        await pg.evaluate("localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))")
        await pg.goto(f"{BASE}/project/{proj}?topic={tid}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(1500)

        async def snap(label):
            st = await pg.evaluate("() => ({dirty: window.__docPanel?.isDirty(), updates: window.__docPanel?.updates})")
            print(label, st)

        await snap("initial:")
        label = await pg.evaluate("() => [...document.querySelectorAll('.doc .v-btn')].map(b=>b.textContent.trim()).filter(t=>t==='只读'||t==='编辑')")
        print("toggle label:", label)
        await pg.click(f".doc .v-btn:has-text('{label[0]}')", timeout=3000)
        await pg.wait_for_timeout(600)
        await snap("after toggle 1:")
        status = await pg.evaluate("() => [...document.querySelectorAll('.doc span')].map(e=>e.textContent.trim()).filter(t=>['编辑中…','保存中…','已保存'].includes(t))")
        print("status text:", status)
        await pg.wait_for_timeout(3500)
        await snap("after 3.5s:")
        label2 = await pg.evaluate("() => [...document.querySelectorAll('.doc .v-btn')].map(b=>b.textContent.trim()).filter(t=>t==='只读'||t==='编辑')")
        print("toggle label now:", label2)
        await pg.click(f".doc .v-btn:has-text('{label2[0]}')", timeout=3000)
        await pg.wait_for_timeout(600)
        await snap("after toggle 2:")
        status2 = await pg.evaluate("() => [...document.querySelectorAll('.doc span')].map(e=>e.textContent.trim()).filter(t=>['编辑中…','保存中…','已保存'].includes(t))")
        print("status text 2:", status2)
        doc = _req(f"{API}/api/topics/{tid}/doc")
        print("disk:", json.dumps((doc or {}).get("content", "")))
        _req(f"{API}/api/topics/{tid}/archive", "POST", {"by": "mentor-1"})
        await b.close()


asyncio.run(main())
