import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 800})
        await pg.goto("http://127.0.0.1:7681/", timeout=30000)
        await pg.wait_for_timeout(4000)
        txt = await pg.evaluate("() => document.querySelector('.xterm-rows')?.innerText?.slice(0,400) || 'no xterm rows'")
        print("=== 终端可见内容(前400字)===")
        print(txt)
        await pg.screenshot(path="tmp_review/ttyd-mirror.png")
        await b.close()

asyncio.run(main())
