"""Verify the emoji picker opens (was closing-on-open: toggle lost rx-toggle class)."""
import asyncio, json, urllib.request
from playwright.async_api import async_playwright
API="http://127.0.0.1:8099"; BASE="http://localhost:5173"
def g(p): return json.load(urllib.request.urlopen(API+p))["data"]
async def main():
    proj=g("/api/projects")["data"][0]["id"]
    tops=g(f"/api/topics?project_id={proj}")["data"]
    t=next(x for x in tops if x["id"].startswith("a43f5955"))
    async with async_playwright() as p:
        b=await p.chromium.launch(); pg=await b.new_page(viewport={"width":1400,"height":900})
        await pg.goto(BASE); await pg.evaluate("localStorage.setItem('cheesex.me', JSON.stringify({id:'probe',handle:'mentor-1',name:'张衡'}))")
        await pg.goto(f"{BASE}/project/{proj}?topic={t['id']}"); await pg.wait_for_load_state("networkidle"); await pg.wait_for_timeout(2000)
        await pg.wait_for_selector(".im-row", timeout=15000)
        row=pg.locator(".im-row").first
        await row.hover(force=True); await pg.wait_for_timeout(400)
        await pg.locator(".rx-toggle").first.click(); await pg.wait_for_timeout(500)
        picker=await pg.locator(".rx-picker").count()
        print("picker opened:", picker)
        if picker:
            await pg.locator(".rx-pick").first.click(); await pg.wait_for_timeout(800)
            chips=await pg.locator(".rx-chip").count()
            print("chip after react:", chips)
        await b.close()
asyncio.run(main())
