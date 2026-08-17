"""Visual smoke: personal team first in 我的小队; team page = 项目 tab (no 频道);
one-click 新建项目 lands inside the new project."""
import asyncio, sys
from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5200"
SHOT = "tmp/shots/team_projects"

async def login(pg):
    await pg.goto(f"{BASE}/account/signin")
    await pg.wait_for_load_state("networkidle")
    inputs = await pg.query_selector_all("input")
    await inputs[0].fill("alice")
    await inputs[1].fill("demo12345")
    cb = await pg.query_selector("input[type=checkbox]")
    if cb: await cb.check()
    await pg.click("button:has-text('立即登录')")
    await pg.wait_for_timeout(2500)

async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        pg.on("console", lambda m: print(f"[console.{m.type}] {m.text[:300]}") if m.type in ("error","warning") else None)
        pg.on("pageerror", lambda e: print(f"[pageerror] {str(e)[:300]}"))
        await login(pg)
        print("logged in, url:", pg.url)

        # 我的小队 list — personal team should be first with badge
        await pg.goto(f"{BASE}/teams")
        await pg.wait_for_timeout(1500)
        await pg.click("text=我的")
        await pg.wait_for_timeout(2000)
        await pg.screenshot(path=f"{SHOT}_1_mine.png")

        # click the first team (个人)
        await pg.click(".my-team-item >> nth=0")
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=f"{SHOT}_2_personal_team.png")
        print("personal team url:", pg.url)

        # one-click 新建项目
        btn = await pg.query_selector("button:has-text('新建项目')")
        if btn:
            await btn.click()
            await pg.wait_for_timeout(3500)
            print("after create url:", pg.url)
            await pg.screenshot(path=f"{SHOT}_3_new_project.png")
        else:
            print("NO 新建项目 button found")
        await b.close()

asyncio.run(main())
