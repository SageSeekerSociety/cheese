"""Reproduce the 登录 → 请重新登录 loop and capture what the browser actually sends.

Logs every request to :8799 (path, Authorization header presence/prefix, cookies)
and every response status, plus console errors and the visible toast. Run while
the demo stack (:5200 vite, :8799 backend) is up:

    cd frontend && npx playwright … is NOT needed — uses the repo's playwright.
    uv run --project backend python scripts/probe_login_fail.py [base_url]
"""

import asyncio
import sys

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5200"


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 900})

        def on_request(req):
            if "8799" not in req.url and "/api" not in req.url:
                return
            auth = req.headers.get("authorization", "-")
            if auth != "-":
                auth = auth[:28] + "…" + auth[-8:]
            cookie = req.headers.get("cookie", "-")
            cookie = ",".join(c.split("=")[0] for c in cookie.split("; ")) if cookie != "-" else "-"
            print(f">> {req.method} {req.url.replace(BASE, '')}  auth={auth}  cookies={cookie}")

        def on_response(resp):
            if "8799" not in resp.url and "/api" not in resp.url:
                return
            print(f"<< {resp.status} {resp.url}")

        pg.on("request", on_request)
        pg.on("response", on_response)
        pg.on("console", lambda m: m.type in ("error", "warning") and print(f"[console.{m.type}] {m.text[:200]}"))

        await pg.goto(f"{BASE}/account/signin")
        await pg.wait_for_load_state("networkidle")
        print("--- filling credentials ---")
        await pg.fill("input[type='text'], input[name='username'], input:not([type='password'])", "alice")
        await pg.fill("input[type='password']", "demo12345")
        print("--- clicking 登录 ---")
        await pg.click("button:has-text('登录'), button:has-text('登 录'), button[type='submit']")
        await pg.wait_for_timeout(4000)

        print("--- after click ---")
        print("url:", pg.url)
        print("localStorage.accessToken:", await pg.evaluate("(localStorage.getItem('accessToken')||'').slice(0,32)"))
        # any toast/snackbar text on screen
        for sel in (".v-snackbar__content", ".toast", "[class*=message]"):
            for el in await pg.query_selector_all(sel):
                t = (await el.inner_text()).strip()
                if t:
                    print(f"toast[{sel}]: {t[:120]}")
        await b.close()


asyncio.run(main())
