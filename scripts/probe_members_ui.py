"""Probe: 话题成员名册 + @all/@here (fusion-design §3).

Creates a fresh topic owned by the probe user, then in the UI: sees the member
roster + count, adds a project member through the roster, and opens the @-menu
to confirm @all/@here appear. Screenshots land in tmp_review/members-*.png.
"""
import asyncio
import json
import os
import urllib.request

from playwright.async_api import async_playwright

# The self-contained dogfood stack (SQLite): frontend 5174 → backend 8097.
# (8099/5173 is the long-running Postgres stack and must not be restarted.)
API = os.environ.get("PROBE_API", "http://127.0.0.1:8097")
BASE = os.environ.get("PROBE_BASE", "http://localhost:5174")
ME = {"id": "probe", "handle": "andyl", "name": "Andy"}
OUT = os.path.join(os.path.dirname(__file__), "..", "tmp_review")


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:  # duplicates etc. are fine
        return {"error": e.code}


def _get(path: str) -> dict:
    return json.load(urllib.request.urlopen(API + path))["data"]


async def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    proj = _get("/api/projects")["data"][0]["id"]
    # Ensure the project has a couple of members so the roster "add" dropdown
    # has options.
    for h in ("bob", "carol"):
        _post(f"/api/projects/{proj}/members", {"user_handle": h, "role": "member"})
    # A fresh topic owned by the probe user (seed → andyl=owner, cheese=member).
    topic = _post(
        "/api/topics",
        {"project_id": proj, "title": "群聊测试话题", "created_by": ME["handle"]},
    )["data"]
    tid = topic["id"]
    print("topic:", tid)

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 900})
        await pg.goto(BASE)
        await pg.evaluate(
            "(v) => localStorage.setItem('cheesex.me', v)", json.dumps(ME)
        )
        await pg.goto(f"{BASE}/project/{proj}?topic={tid}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(1500)

        # 1) Member roster bar + count ("N 人 + 芝士").
        await pg.wait_for_selector(".topic-members-bar", timeout=15000)
        pill = pg.locator(".members-pill").first
        print("count label:", (await pill.inner_text()).strip())
        await pg.screenshot(path=os.path.join(OUT, "members-bar.png"))

        # 2) Open the roster drawer.
        await pill.click()
        await pg.wait_for_selector(".roster", timeout=5000)
        await pg.wait_for_timeout(400)
        rows = await pg.locator(".roster__item").count()
        print("roster rows:", rows)
        await pg.screenshot(path=os.path.join(OUT, "members-roster.png"))

        # 3) Add a project member through the roster's select.
        await pg.locator(".roster__select .v-field").first.click()
        await pg.wait_for_timeout(500)
        opt = pg.locator(".v-overlay-container .v-list-item").first
        if await opt.count():
            print("adding:", (await opt.inner_text()).strip().split("\n")[0])
            await opt.click()
            await pg.wait_for_timeout(400)
            await pg.locator(".roster__add").get_by_role(
                "button", name="加入"
            ).last.click()
            await pg.wait_for_timeout(1000)
            rows_after = await pg.locator(".roster__item").count()
            print("roster rows after add:", rows_after)
        else:
            print("no addable options (all project members already in room)")
        await pg.screenshot(path=os.path.join(OUT, "members-added.png"))

        # Close the roster menu.
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(300)

        # 4) @-menu shows @all / @here at the top.
        composer = pg.locator(".composer-input textarea").first
        await composer.click()
        await composer.type("@")
        await pg.wait_for_selector(".mention-menu", timeout=5000)
        await pg.wait_for_timeout(400)
        menu_text = await pg.locator(".mention-menu").inner_text()
        print("mention menu has 所有人:", "所有人" in menu_text)
        print("mention menu has 在线成员:", "在线成员" in menu_text)
        await pg.screenshot(path=os.path.join(OUT, "members-mention-all.png"))

        await b.close()
    print("screenshots →", os.path.abspath(OUT))


asyncio.run(main())
