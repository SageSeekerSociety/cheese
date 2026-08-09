"""Verify: 专家角色 section in 项目设置 — dropdown from /api/roles, 新建角色
dialog creates a custom role and selects it (persisted on the project).

Assumes an isolated demo stack (see report): backend on :8199 (sqlite),
vite on :5273 proxying to it. Screenshots land in tmp_review/.
"""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8199"
BASE = "http://localhost:5273"


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)["data"]


def _get(path: str) -> dict:
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


def _delete_quiet(path: str) -> None:
    """DELETE, ignoring errors — idempotent cleanup between probe runs."""
    req = urllib.request.Request(API + path, method="DELETE")
    try:
        urllib.request.urlopen(req)
    except OSError:
        pass


async def main() -> None:
    _delete_quiet("/api/roles/data-science")
    _post("/api/users/login", {"handle": "mentor-1", "name": "张衡"})
    proj = _post("/api/projects", {"name": "角色演示", "owner_handle": "mentor-1"})

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:150]))
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', "
            "JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj['id']}/settings")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(800)

        # 1) The 专家角色 dropdown, opened: built-ins with title + description.
        role_section = pg.locator("section", has_text="专家角色").first
        await role_section.locator(".v-select").click()
        await pg.wait_for_timeout(600)
        await pg.screenshot(path="tmp_review/roles-dropdown.png")
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(300)

        # 2) 新建角色 dialog, filled in (type, so Vuetify labels float).
        await pg.click("text=新建角色")
        await pg.wait_for_timeout(600)
        for placeholder, value in [
            ("data-science", "data-science"),
            ("数据科学", "数据科学"),
            ("统计分析、机器学习与数据可视化", "统计分析、机器学习与数据可视化"),
        ]:
            loc = pg.locator(f"input[placeholder='{placeholder}']")
            await loc.click()
            await loc.fill(value)
        body_loc = pg.locator(".v-dialog textarea").first
        await body_loc.click()
        await body_loc.fill(
            "你是一位数据科学导师，擅长统计分析、机器学习与数据可视化。"
        )
        await pg.wait_for_timeout(500)
        await pg.screenshot(path="tmp_review/roles-create-dialog.png")

        # 3) Create → auto-selected; check persistence through the API.
        await pg.click("text=创建并使用")
        await pg.wait_for_timeout(1000)
        await pg.screenshot(path="tmp_review/roles-selected.png")

        current = _get(f"/api/projects/{proj['id']}")["expert_role"]
        catalog = [r["name"] for r in _get("/api/roles")["data"]]
        print(f"expert_role persisted: {current} | catalog: {catalog}")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
