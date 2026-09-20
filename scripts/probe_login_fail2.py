"""Login-loop probe v2: screenshots + explicit step-through of the signin form."""
import asyncio, sys
from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5200"
SHOT = ".tmp/shots/login_probe"

async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 900})
        reqs = []
        def on_request(req):
            if "8799" in req.url or "/api/" in req.url or "/users/" in req.url:
                auth = req.headers.get("authorization", "-")
                if auth != "-": auth = auth[:24] + "…"
                reqs.append(f">> {req.method} {req.url}  auth={auth}")
        pg.on("request", on_request)
        pg.on("response", lambda r: ("8799" in r.url or "/users/" in r.url) and reqs.append(f"<< {r.status} {r.url}"))
        pg.on("console", lambda m: m.type == "error" and reqs.append(f"[console.error] {m.text[:300]}"))
        pg.on("pageerror", lambda e: reqs.append(f"[pageerror] {str(e)[:300]}"))

        await pg.goto(f"{BASE}/account/signin")
        await pg.wait_for_load_state("networkidle")
        await pg.screenshot(path=f"{SHOT}_1_signin.png")
        # dump form controls
        for el in await pg.query_selector_all("input"):
            print("input:", await el.get_attribute("type"), await el.get_attribute("placeholder") or await el.get_attribute("name"))
        for el in await pg.query_selector_all("button"):
            t = (await el.inner_text()).strip().replace("\n", " ")
            if t: print("button:", t[:40])

        inputs = await pg.query_selector_all("input")
        await inputs[0].fill("alice")
        if len(inputs) > 1 and await inputs[1].get_attribute("type") == "password":
            await inputs[1].fill("demo12345")
        cb = await pg.query_selector("input[type=checkbox]")
        if cb: await cb.check()
        await pg.screenshot(path=f"{SHOT}_2_filled.png")
        btn = await pg.query_selector("button[type=submit]") or await pg.query_selector("button:has-text('登')")
        print("clicking:", (await btn.inner_text()).strip() if btn else None)
        await btn.click()
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=f"{SHOT}_3_after1.png")
        # a second step may have appeared (password after methods lookup)
        pw = await pg.query_selector("input[type=password]")
        if pw and not await pw.input_value():
            await pw.fill("demo12345")
            btn = await pg.query_selector("button[type=submit]") or await pg.query_selector("button:has-text('登')")
            await btn.click()
            await pg.wait_for_timeout(1500)
        await pg.wait_for_timeout(3500)
        await pg.screenshot(path=f"{SHOT}_4_final.png")
        print("url:", pg.url)
        print("token:", await pg.evaluate("(localStorage.getItem('accessToken')||'').slice(0,24)"))
        for sel in (".v-snackbar__content", "[class*=toast]", "[class*=Message]"):
            for el in await pg.query_selector_all(sel):
                t = (await el.inner_text()).strip()
                if t: print(f"toast: {t[:150]}")
        print("--- network ---")
        print("\n".join(reqs[-25:]))
        await b.close()

asyncio.run(main())
