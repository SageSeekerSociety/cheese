"""Close-up study of the drag-handle / placeholder geometry on an empty doc."""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"


def _req(url, method="GET", body=None):
    r = urllib.request.Request(url, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))["data"]


async def main() -> None:
    proj = _req(f"{API}/api/projects")["data"][0]["id"]
    topic = _req(f"{API}/api/topics", "POST", {"project_id": proj, "title": "把手几何研究"})
    tid = topic["id"]
    print("topic:", tid)

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950}, device_scale_factor=2)
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj}?topic={tid}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(1500)
        # enter edit mode (只读 toggle)
        try:
            # Scope to the doc header — bare text=编辑 substring-matched the
            # sidebar row 「编辑器质量验证」 and hijacked the whole probe.
            await pg.click(".doc .v-btn:has-text('编辑')", timeout=3000)
            await pg.wait_for_timeout(500)
        except Exception:
            print("already editable or toggle missing")
        # hover the first paragraph to summon handles
        para = pg.locator(".doc-editor .ProseMirror p").first
        await para.hover()
        await pg.wait_for_timeout(500)
        print("URL now:", pg.url)
        header = await pg.evaluate("() => document.querySelector('.pr-title')?.textContent")
        print("header title:", header)
        geo = await pg.evaluate("""() => {
          const pm = document.querySelector('.doc-editor .ProseMirror')
          const p = pm?.querySelector('p')
          const handle = document.querySelector('.doc-handle')
          const ph = document.querySelector('.placeholder')
          const rect = (el) => el ? (({x,y,width,height}) => ({x:Math.round(x),y:Math.round(y),w:Math.round(width),h:Math.round(height)}))(el.getBoundingClientRect()) : null
          const styles = handle ? getComputedStyle(handle) : null
          return {
            pm: rect(pm), para: rect(p), handle: rect(handle), placeholder: rect(ph),
            pmPaddingLeft: pm ? getComputedStyle(pm).paddingLeft : null,
            handlePos: styles ? {position: styles.position, left: styles.left, top: styles.top} : null,
            paraText: p?.textContent?.slice(0, 30),
            phText: ph?.textContent?.slice(0, 30),
          }
        }""")
        print(json.dumps(geo, ensure_ascii=False, indent=1))
        # close-up clips
        if geo["para"]:
            y = geo["para"]["y"]
            await pg.screenshot(path="tmp_review/handles-closeup.png",
                clip={"x": max(0, geo["para"]["x"] - 120), "y": y - 40, "width": 800, "height": 130})
        await pg.screenshot(path="tmp_review/handles-context.png",
            clip={"x": 900, "y": 60, "width": 600, "height": 400})
        await b.close()


asyncio.run(main())
