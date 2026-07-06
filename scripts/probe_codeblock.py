"""Verify: Tab indents inside code block (focus stays); language picker works."""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"


def _req(url, method="GET", body=None):
    r = urllib.request.Request(url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))["data"]


async def main() -> None:
    proj = _req(f"{API}/api/projects")["data"][0]["id"]
    topic = _req(f"{API}/api/topics", "POST", {"project_id": proj, "title": "代码块交互验证"})
    tid = topic["id"]
    _req(f"{API}/api/topics/{tid}/doc", "PUT",
         {"content": "```python\ndef f():\npass\n```\n", "author": "mentor-1"})

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950}, device_scale_factor=2)
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj}?topic={tid}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(1500)
        try:
            await pg.click(".doc .v-btn:has-text('编辑')", timeout=3000)
        except Exception:
            pass
        await pg.wait_for_timeout(400)
        # caret at start of "pass" line, press Tab
        code = pg.locator(".doc-editor pre code").first
        await code.click()
        await pg.keyboard.press("Control+End" if False else "End")
        # click直接定位 pass 行首:双击 pass 选词后 Home
        await pg.locator("text=pass").click()
        await pg.keyboard.press("Home")
        await pg.keyboard.press("Tab")
        await pg.wait_for_timeout(200)
        focus_in_editor = await pg.evaluate(
            "() => !!document.activeElement?.closest('.doc-editor')")
        text = await code.inner_text()
        print("focus stays in editor:", focus_in_editor)
        print("code now:", json.dumps(text))
        # language picker: hover pre, open chip, pick typescript
        await pg.locator(".doc-editor pre").first.hover()
        await pg.wait_for_timeout(300)
        await pg.click(".doc-codelang__chip", timeout=3000)
        await pg.click(".doc-codelang__item:has-text('typescript')", timeout=3000)
        await pg.wait_for_timeout(300)
        lang = await pg.locator(".doc-editor pre").first.get_attribute("data-language")
        print("language after pick:", lang)
        # long language name must not overlap the copy button
        await pg.locator(".doc-editor pre").first.hover()
        await pg.wait_for_timeout(300)
        rects = await pg.evaluate("""() => {
          const chip = document.querySelector('.doc-codelang__chip')?.getBoundingClientRect()
          const copy = document.querySelector('.doc-codecopy')?.getBoundingClientRect()
          return chip && copy ? {overlap: chip.right > copy.left, gap: Math.round(copy.left - chip.right)} : null
        }""")
        print("chip/copy overlap check:", rects)
        # open menu, click far outside (chat panel) → must dismiss
        await pg.click(".doc-codelang__chip")
        await pg.wait_for_timeout(200)
        await pg.mouse.click(500, 400)
        await pg.wait_for_timeout(200)
        menu_open = await pg.locator(".doc-codelang__menu").count()
        print("menu after outside click:", menu_open)
        await pg.screenshot(path="tmp_review/codeblock-tab-lang.png",
            clip={"x": 930, "y": 100, "width": 560, "height": 400})
        # autosave (2.5s debounce) must persist the language change even
        # though focus sits on the picker button (outside the ⌘S listener).
        state = await pg.evaluate("() => ({dirty: window.__docPanel?.isDirty(), updates: window.__docPanel?.updates, md: window.__docPanel?.getMarkdown()?.slice(0, 40)})")
        print("after pick:", state)
        await pg.wait_for_timeout(4000)
        state2 = await pg.evaluate("() => ({dirty: window.__docPanel?.isDirty()})")
        print("after 4s:", state2)
        doc = _req(f"{API}/api/topics/{tid}/doc")
        print("disk markdown:", json.dumps((doc or {}).get("content", "")))
        _req(f"{API}/api/topics/{tid}/archive", "POST", {"by": "mentor-1"})
        await b.close()


asyncio.run(main())
