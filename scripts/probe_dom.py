import asyncio
from playwright.async_api import async_playwright

BASE = "http://localhost:5200"

async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page()
        await pg.goto(f"{BASE}/account/signin")
        await pg.wait_for_load_state("networkidle")
        inputs = await pg.query_selector_all("input")
        await inputs[0].fill("alice"); await inputs[1].fill("demo12345")
        cb = await pg.query_selector("input[type=checkbox]")
        if cb: await cb.check()
        await pg.click("button:has-text('立即登录')")
        await pg.wait_for_timeout(2500)
        await pg.goto(f"{BASE}/teams/3314")
        await pg.wait_for_timeout(3000)
        html = await pg.evaluate("document.querySelector('.layout-container')?.outerHTML?.slice(0,1200) ?? 'NO .layout-container'")
        print(html)
        texts = await pg.evaluate("[...document.querySelectorAll('h2,h3')].map(e=>e.textContent.trim())")
        print("headings:", texts)
        await pg.screenshot(path=".tmp/shots/team_projects_direct.png")
        btn = await pg.query_selector("button:has-text('新建项目')")
        print("btn found:", btn is not None)
        if btn:
            await btn.click()
            await pg.wait_for_timeout(3500)
            print("after create url:", pg.url)
            await pg.screenshot(path=".tmp/shots/team_projects_created.png")
        await b.close()

asyncio.run(main())
