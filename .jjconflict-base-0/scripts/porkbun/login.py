"""Porkbun login: password from env (write-only), session saved for reuse.
Screenshots every step to tmp_review/ — selectors are unknown territory."""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

STATE = "/Users/andyl/Projects/cheese-backend-py/tmp/cheesex/.porkbun-state.json"
SHOT = "/Users/andyl/Projects/cheese-backend-py/tmp/cheesex/tmp_review"
EMAIL = "andylizf@gmail.com"


async def main() -> None:
    pw = os.environ.pop("PB_PASS", "")
    if not pw:
        print("no password on stdin", file=sys.stderr)
        sys.exit(2)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context()
        pg = await ctx.new_page()
        await pg.goto("https://porkbun.com/account/login", timeout=60000)
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=f"{SHOT}/porkbun-1-login.png")
        # dump form field names for forensics (no secrets)
        fields = await pg.evaluate(
            "() => [...document.querySelectorAll('input')].map(i => `${i.type}:${i.name || i.id}`)"
        )
        print("fields:", fields[:10])
        try:
            await pg.fill("input[type=email], input[name*=mail i], #loginUsername", EMAIL, timeout=8000)
            await pg.fill("input[type=password]", pw, timeout=8000)
            pw = ""  # drop the reference asap
            await pg.screenshot(path=f"{SHOT}/porkbun-2-filled.png")
            await pg.click("button[type=submit], input[type=submit], button:has-text('Sign in'), button:has-text('Log in')", timeout=8000)
        except Exception as e:
            print("form interaction failed:", str(e)[:200])
            await pg.screenshot(path=f"{SHOT}/porkbun-err.png")
            await b.close()
            sys.exit(3)
        await pg.wait_for_timeout(5000)
        await pg.screenshot(path=f"{SHOT}/porkbun-3-after-login.png")
        print("after-login url:", pg.url)
        await ctx.storage_state(path=STATE)
        print("state saved")
        await b.close()


asyncio.run(main())
