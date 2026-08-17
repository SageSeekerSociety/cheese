"""Browser-level acceptance for the etrip production deploy.

Drives the REAL UI end-to-end through an SSH tunnel (localhost:18080 ->
etrip:8080 Caddy -> dist + 127.0.0.1:8099 backend -> cheesex-pg + Zhipu
gateway):

  1. login gate (handle registration)
  2. create project (sidebar footer input)
  3. create topic (sidebar + button)
  4. send a message (composer defaults to @芝士 summon) -> real agent turn
  5. wait for 芝士's reply

Screenshots land in tmp_review/etrip-*.png (repo-local).

Run:  ssh -N -L 18080:127.0.0.1:8080 -f etrip
      uv run --with playwright python scripts/ops/etrip-acceptance.py [BASE]
"""

import asyncio
import sys
from datetime import datetime

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18080"
SHOT = "tmp_review"
HANDLE = "andy"
NAME = "Andy"
PROJECT = "首航项目"
MSG = "芝士你好，请用一句话介绍你自己。"
REPLY_TIMEOUT_S = 240


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("pageerror", lambda e: log(f"pageerror: {str(e)[:200]}"))

        # 1. login gate
        await pg.goto(BASE)
        await pg.wait_for_selector(".login-gate", timeout=15000)
        await pg.screenshot(path=f"{SHOT}/etrip-01-login-gate.png")
        log("login gate shown")
        await pg.get_by_label("名字").fill(NAME)
        await pg.get_by_label("handle（小写字母/数字/横线）").fill(HANDLE)
        await pg.get_by_role("button", name="进入").click()
        await pg.wait_for_selector(".login-gate", state="detached", timeout=15000)
        log(f"signed in as @{HANDLE}")
        await pg.wait_for_timeout(1000)
        await pg.screenshot(path=f"{SHOT}/etrip-02-workspace.png")

        # 2. create project via sidebar footer input
        proj_input = pg.get_by_placeholder("新建项目…")
        await proj_input.fill(PROJECT)
        await proj_input.press("Enter")
        proj_item = pg.get_by_text(PROJECT, exact=False).first
        await proj_item.wait_for(timeout=15000)
        log(f"project created: {PROJECT}")
        await proj_item.click()
        await pg.wait_for_timeout(1500)

        # 3. create topic (plus button next to 话题 subhead)
        await pg.locator('button[title="新建话题"]').click()
        await pg.wait_for_selector(".composer-input textarea", timeout=20000)
        log("topic created, composer ready")
        await pg.wait_for_timeout(1000)
        await pg.screenshot(path=f"{SHOT}/etrip-03-topic.png")

        # 4. send @芝士 message — the workspace composer defaults summon OFF
        # (spec §7.1 人与人对话为主), so click the @芝士 chip first.
        chip = pg.locator(".summon-chip")
        await chip.click()
        await pg.wait_for_selector(".summon-chip--on", timeout=5000)
        log("summon chip ON (@芝士)")
        box = pg.locator(".composer-input textarea").first
        await box.fill(MSG)
        await box.press("Enter")
        log(f"sent: {MSG}")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=f"{SHOT}/etrip-04-sent.png")

        # 5. wait for 芝士's reply: a second .im-row appears (first = ours)
        deadline = asyncio.get_event_loop().time() + REPLY_TIMEOUT_S
        reply = None
        while asyncio.get_event_loop().time() < deadline:
            rows = pg.locator(".im-row")
            n = await rows.count()
            if n >= 2:
                text = (await rows.nth(n - 1).inner_text()).strip()
                # skip the "正在看…" / typing indicator rows — a real reply
                # is a non-empty assistant message.
                if text and "正在看" not in text and "正在输入" not in text:
                    reply = text[:300]
                    break
            await pg.wait_for_timeout(3000)
        await pg.screenshot(path=f"{SHOT}/etrip-05-reply.png", full_page=False)
        if reply:
            log(f"REPLY OK: {reply}")
        else:
            log("FAIL: no reply within timeout")
            sys.exit(1)
        await b.close()


asyncio.run(main())
