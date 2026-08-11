"""Headed Porkbun session on the user's display: login (env password),
drive to okcheese.com checkout, wait for the human to finish payment,
then verify the domain landed and save API-ready session state."""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

STATE = "/Users/andyl/Projects/cheese-backend-py/tmp/cheesex/.porkbun-state.json"
SHOT = "/Users/andyl/Projects/cheese-backend-py/tmp/cheesex/tmp_review"
EMAIL = "andylizf@gmail.com"
DOMAIN = "okcheese.com"


async def main() -> None:
    pw = os.environ.pop("PB_PASS", "")
    if not pw:
        print("no password on stdin", file=sys.stderr)
        sys.exit(2)
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=False, slow_mo=150, args=["--window-size=1280,900"])
        ctx = await b.new_context(viewport=None)
        pg = await ctx.new_page()
        await pg.goto("https://porkbun.com/account/login", timeout=60000)
        await pg.wait_for_timeout(3000)
        # login form (best-effort selectors; screenshots for forensics)
        try:
            await pg.fill("input[type=email], input[name*=mail i], input[name=username], #loginUsername", EMAIL, timeout=10000)
            await pg.fill("input[type=password]", pw, timeout=8000)
            pw = ""
            await pg.keyboard.press("Enter")
        except Exception as e:
            print("login form issue:", str(e)[:150], "— 窗口留给人工", flush=True)
        await pg.wait_for_timeout(6000)
        await pg.screenshot(path=f"{SHOT}/porkbun-b1-after-login.png")
        print("after-login url:", pg.url, flush=True)
        # go search the domain
        await pg.goto(f"https://porkbun.com/checkout/search?q={DOMAIN}", timeout=60000)
        await pg.wait_for_timeout(5000)
        await pg.screenshot(path=f"{SHOT}/porkbun-b2-search.png")
        # try add-to-cart (label unknown — try a few)
        for sel in ["button:has-text('Add to Cart')", "text=+ Add to Cart", "button:has-text('add')"]:
            try:
                await pg.click(sel, timeout=4000)
                print("added to cart via", sel, flush=True)
                break
            except Exception:
                continue
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=f"{SHOT}/porkbun-b3-cart.png")
        for sel in ["a:has-text('Checkout')", "button:has-text('Checkout')", "a[href*=cart]"]:
            try:
                await pg.click(sel, timeout=4000)
                break
            except Exception:
                continue
        await pg.wait_for_timeout(4000)
        await pg.screenshot(path=f"{SHOT}/porkbun-b4-checkout.png")
        print("checkout url:", pg.url, "— 付款交给窗口前的人类，最长等 12 分钟", flush=True)
        # poll for ownership: domain management should list it after purchase
        for i in range(72):
            await asyncio.sleep(10)
            try:
                r = await ctx.request.get("https://porkbun.com/account/domainsSpeedy")
                if DOMAIN in (await r.text()):
                    print("DOMAIN OWNED ✓", flush=True)
                    await ctx.storage_state(path=STATE)
                    await pg.screenshot(path=f"{SHOT}/porkbun-b5-owned.png")
                    await b.close()
                    return
            except Exception:
                pass
        print("timeout waiting for purchase — 窗口截图留档", flush=True)
        await pg.screenshot(path=f"{SHOT}/porkbun-b6-timeout.png")
        await ctx.storage_state(path=STATE)
        await b.close()
        sys.exit(4)


asyncio.run(main())
