"""Probe: Notion-style slash menu + turn-into + unified doc comments.

Scenarios (screenshots land in tmp_review/slash-*.png):
  A. slash topic  — "/" opens the menu; ↑↓ navigate; "/代码" filters; Enter
                    turns the block into a code block; ＋ handle opens the same
                    menu (and cleans its "/" on Esc); "/h2" at the head of a
                    non-empty paragraph turns it into 标题 2 (text kept).
  B. comment topic — bottom 常驻评论区 lists ALL comments: anchored ones show a
                    quote chip (click → flash the paragraph), a stale anchor
                    shows 原段落已改动, page-level ones render plain. The 评论
                    drawer tool is gone from the toolbar.

Run with /usr/bin/python3 (has playwright). Both topics are archived at the end.
"""

import asyncio
import json
import pathlib
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"
SHOTS = pathlib.Path(__file__).resolve().parent.parent / "tmp_review"


# No system proxy for localhost: on this machine urllib picks up the macOS
# proxy settings (Clash) and 127.0.0.1 requests get swallowed with a 404.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _req(url, method="GET", body=None):
    r = urllib.request.Request(url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    return json.load(_OPENER.open(r))["data"]


CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


async def open_topic(pg, proj, tid):
    await pg.goto(f"{BASE}/project/{proj}?topic={tid}")
    await pg.wait_for_load_state("networkidle")
    await pg.wait_for_timeout(1500)


async def main() -> None:
    SHOTS.mkdir(exist_ok=True)
    proj = _req(f"{API}/api/projects")["data"][0]["id"]

    # --- Topic A: slash menu / turn-into (no comments → no AI turn noise) ---
    ta = _req(f"{API}/api/topics", "POST", {"project_id": proj, "title": "slash 探针"})["id"]
    _req(f"{API}/api/topics/{ta}/doc", "PUT", {
        "content": "段一普通内容。\n\n段二待转换标题。\n", "author": "mentor-1"})

    # --- Topic B: unified comments (anchored + stale + page-level) ---
    tb = _req(f"{API}/api/topics", "POST", {"project_id": proj, "title": "评论归一探针"})["id"]
    _req(f"{API}/api/topics/{tb}/doc", "PUT", {
        "content": "评论段落甲，用于锚定测试。\n\n评论段落乙，稍后会被改动。\n",
        "author": "mentor-1"})
    nodes = _req(f"{API}/api/topics/{tb}/docs")["data"]
    _req(f"{API}/api/topics/{tb}/comments", "POST", {
        "content": "锚点评论：这里再斟酌一下", "author": "mentor-1",
        "anchor": nodes[0]["id"], "quote": "评论段落甲，用于锚定测试。"})
    _req(f"{API}/api/topics/{tb}/comments", "POST", {
        "content": "这个锚点将失效", "author": "mentor-1",
        "anchor": nodes[1]["id"], "quote": "评论段落乙"})
    _req(f"{API}/api/topics/{tb}/comments", "POST", {
        "content": "页级评论照旧", "author": "mentor-1"})
    # Rewrite 段乙 → its node is replaced in the diff-sync → anchor 2 goes stale.
    _req(f"{API}/api/topics/{tb}/doc", "PUT", {
        "content": "评论段落甲，用于锚定测试。\n\n段乙已被彻底改写，原锚点节点没了。\n",
        "author": "mentor-1"})

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))")

        # ================= A. slash menu =================
        await open_topic(pg, proj, ta)

        # 1) new empty paragraph at doc end, type "/" → menu appears
        await pg.click(".doc-editor p:has-text('段二待转换标题')")
        await pg.keyboard.press("End")
        await pg.keyboard.press("Enter")
        await pg.keyboard.type("/")
        await pg.wait_for_selector(".doc-slash__menu", timeout=3000)
        n_items = await pg.eval_on_selector_all(".doc-slash__item", "els => els.length")
        check("slash menu opens on '/' in empty paragraph", n_items == 11, f"items={n_items}")
        await pg.screenshot(path=str(SHOTS / "slash-menu.png"))

        # 2) ↓↓ keyboard navigation → third item (标题 2) becomes active
        await pg.keyboard.press("ArrowDown")
        await pg.keyboard.press("ArrowDown")
        active = await pg.eval_on_selector(
            ".doc-slash__item--active .doc-slash__label", "el => el.textContent.trim()")
        check("arrow keys move the active item", active == "标题 2", f"active={active!r}")

        # 3) type 代码 → filters to 代码块; Enter → block becomes <pre>
        await pg.keyboard.type("代码")
        await pg.wait_for_timeout(200)
        labels = await pg.eval_on_selector_all(
            ".doc-slash__item .doc-slash__label", "els => els.map(e => e.textContent.trim())")
        check("'/代码' filters to 代码块", labels == ["代码块"], f"labels={labels}")
        await pg.screenshot(path=str(SHOTS / "slash-filter.png"))
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(300)
        has_pre = await pg.evaluate("() => !!document.querySelector('.doc-editor pre')")
        check("Enter turns the block into a code block", has_pre)
        await pg.keyboard.type("print('hi')")
        await pg.screenshot(path=str(SHOTS / "slash-codeblock.png"))

        # 4) ＋ handle → same menu opens on a fresh "/" paragraph
        await pg.hover(".doc-editor p:has-text('段一普通内容')")
        await pg.wait_for_selector(".doc-handle__add", timeout=3000)
        await pg.click(".doc-handle__add")
        await pg.wait_for_selector(".doc-slash__menu", timeout=3000)
        check("＋ handle opens the slash menu", True)
        await pg.screenshot(path=str(SHOTS / "slash-plus.png"))
        # Esc → menu closes AND the planted "/" is cleaned up
        await pg.keyboard.press("Escape")
        await pg.wait_for_timeout(300)
        menu_gone = await pg.evaluate("() => !document.querySelector('.doc-slash__menu')")
        md = await pg.evaluate("() => window.__docPanel.getMarkdown()")
        lone_slash = any(line.strip() == "/" or line.strip() == "\\/" for line in md.splitlines())
        check("Esc closes menu and removes the planted '/'", menu_gone and not lone_slash,
              f"menu_gone={menu_gone} md={md!r}")

        # 5) turn-into: "/h2" at the head of a NON-EMPTY paragraph → 标题 2
        await pg.click(".doc-editor p:has-text('段二待转换标题')")
        await pg.keyboard.press("Home")
        await pg.keyboard.type("/h2")
        await pg.wait_for_selector(".doc-slash__menu", timeout=3000)
        await pg.keyboard.press("Enter")
        await pg.wait_for_timeout(300)
        md = await pg.evaluate("() => window.__docPanel.getMarkdown()")
        check("turn-into: paragraph → 标题2, text kept", "## 段二待转换标题。" in md, f"md={md!r}")
        await pg.screenshot(path=str(SHOTS / "slash-turninto.png"))

        # 6) autosave lands; disk has the fence + the heading
        await pg.wait_for_timeout(3500)
        dirty = await pg.evaluate("() => window.__docPanel.isDirty()")
        disk = (_req(f"{API}/api/topics/{ta}/doc") or {}).get("content", "")
        check("autosave clean after slash edits", dirty is False, f"dirty={dirty}")
        check("disk doc has code fence + h2",
              "```" in disk and "## 段二待转换标题。" in disk, f"disk={disk!r}")

        # ================= B. unified comments =================
        await open_topic(pg, proj, tb)
        toolbar_titles = await pg.evaluate(
            "() => [...document.querySelectorAll('.doc .v-toolbar .v-btn')].map(b => b.getAttribute('title'))")
        check("评论 tool removed from toolbar", "评论" not in toolbar_titles,
              f"titles={toolbar_titles}")

        await pg.eval_on_selector(".doc-comments", "el => el.scrollIntoView()")
        await pg.wait_for_timeout(400)
        n_cards = await pg.eval_on_selector_all(".doc-comments__item", "els => els.length")
        check("bottom section lists ALL comments (3)", n_cards == 3, f"cards={n_cards}")
        chip_txt = await pg.eval_on_selector(
            ".doc-comments__chip", "el => el.textContent.trim()")
        check("anchored comment shows quote chip", "评论段落甲" in chip_txt, f"chip={chip_txt!r}")
        stale = await pg.evaluate(
            "() => [...document.querySelectorAll('.doc-comments__stale')].map(e => e.textContent.trim())")
        check("stale anchor shows 原段落已改动", stale == ["原段落已改动"], f"stale={stale}")
        await pg.screenshot(path=str(SHOTS / "slash-comments.png"))

        # chip click → scroll + flash overlay on the anchored paragraph
        await pg.click(".doc-comments__chip")
        await pg.wait_for_selector(".node-flash-overlay", timeout=3000)
        check("chip click flashes the anchored paragraph", True)
        await pg.screenshot(path=str(SHOTS / "slash-comment-flash.png"))

        await b.close()

    _req(f"{API}/api/topics/{ta}/archive", "POST", {"by": "mentor-1"})
    _req(f"{API}/api/topics/{tb}/archive", "POST", {"by": "mentor-1"})

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        raise SystemExit(1)


asyncio.run(main())
