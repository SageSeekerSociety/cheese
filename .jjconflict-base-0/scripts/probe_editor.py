"""Editor quality sprint probe: dead zones, table-selection comments, comment mode.

Usage:
  python3 scripts/probe_editor.py setup             # idempotent test-topic creation
  python3 scripts/probe_editor.py probe <prefix>    # run checks + screenshots
                                                    # (prefix = before / after)

Screenshots land in tmp_review/editor-<prefix>-*.png.
"""

import asyncio
import json
import sys
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"
TOPIC_TITLE = "编辑器质量验证"
AUTHOR = "mentor-1"

DOC_MD = """## 背景

这是编辑器质量验证文档，用于测试光标死区、表格选区评论和 live-ref 徽章。

## 待办

- 第一项待办：验证光标可以落进来，这一行要足够长足够长足够长，长到延伸进右侧徽章原本覆盖的区域
- 第二项待办：徽章不再挡点击

这里是一段普通文字，本段已被升级为子话题，右侧会出现 live-ref 徽章。这一段也写得足够长，让文字一直延伸到文档列的最右侧区域去。

## 分页方案对比

| 方案 | 优点 | 缺点 |
| --- | --- | --- |
| offset 分页 | 实现简单 | 深分页慢 |
| cursor 分页 | 性能稳定 | 无法跳页 |

结尾段落，用于确认表格之后仍能正常评论。
"""


def _req(path: str, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(API + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data) as r:
        return json.load(r)["data"]


def setup() -> tuple[str, str]:
    """Find-or-create the probe topic (with doc + one upgraded node)."""
    proj = _req("/api/projects")["data"][0]["id"]
    topics = _req(f"/api/topics?project_id={proj}")["data"]
    topic = next((t for t in topics if t["title"] == TOPIC_TITLE), None)
    if topic is None:
        topic = _req("/api/topics", "POST", {"project_id": proj, "title": TOPIC_TITLE})
    tid = topic["id"]
    doc = None
    try:
        doc = _req(f"/api/topics/{tid}/doc")
    except Exception:
        pass
    if not doc or (doc.get("content") or "").strip() != DOC_MD.strip():
        _req(f"/api/topics/{tid}/doc", "PUT", {"content": DOC_MD, "author": AUTHOR})
    nodes = _req(f"/api/topics/{tid}/docs")["data"]
    # Upgrade the "这里是一段普通文字" paragraph so it carries a live-ref badge.
    target = next(n for n in nodes if n["content"].startswith("这里是一段普通文字"))
    if not target.get("upgraded_to_topic_id"):
        _req(f"/api/blocks/{target['id']}/upgrade", "POST", {"created_by": AUTHOR})
    print("topic:", tid, "project:", proj)
    return proj, tid


async def probe(prefix: str) -> None:
    proj, tid = setup()
    shots = f"tmp_review/editor-{prefix}"
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:200]))
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj}?topic={tid}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(2500)
        await pg.screenshot(path=f"{shots}-doc.png")

        # ---- 1. dead zone: the upgraded paragraph's FIRST-LINE right side is
        # where the old absolute badge floated. After the widget-decoration fix
        # the badge is in flow (position: static, inside .ProseMirror), so a
        # click there must land the caret in the paragraph text. ----
        badge = await pg.evaluate(
            """() => {
              const el = document.querySelector('.doc-liveref')
              if (!el) return null
              const r = el.getBoundingClientRect()
              return {
                x: r.x, y: r.y, w: r.width, h: r.height,
                position: getComputedStyle(el).position,
                inEditor: !!el.closest('.ProseMirror'),
                topic: el.dataset.topic || null,
              }
            }"""
        )
        print("badge:", badge)
        para = await pg.evaluate(
            """() => {
              const ps = [...document.querySelectorAll('.doc-editor .ProseMirror > p')]
              const p = ps.find((el) => el.textContent.includes('这里是一段普通文字'))
              if (!p) return null
              const r = p.getBoundingClientRect()
              return { x: r.x, y: r.y, w: r.width, h: r.height }
            }"""
        )
        assert para, "upgraded paragraph not found"
        # The old badge anchored at the paragraph's top, right edge (right:-6px,
        # max-width 220): probe that band.
        hits = await pg.evaluate(
            """(p) => {
              const y = p.y + 12
              const out = []
              for (const dx of [180, 110, 40]) {
                const x = p.x + p.w - dx
                const el = document.elementFromPoint(x, y)
                out.push({ x: Math.round(x), hit: el ? (el.className.toString().slice(0, 30) || el.tagName) : 'none' })
              }
              return out
            }""",
            para,
        )
        print("elementFromPoint on first-line right band:", json.dumps(hits, ensure_ascii=False))
        await pg.mouse.click(para["x"] + para["w"] - 60, para["y"] + 12)
        await pg.wait_for_timeout(300)
        sel = await pg.evaluate(
            """() => {
              const s = document.getSelection()
              if (!s || !s.anchorNode) return null
              const el = s.anchorNode.nodeType === 3 ? s.anchorNode.parentElement : s.anchorNode
              return { inEditor: !!(el && el.closest && el.closest('.doc-editor')), text: (s.anchorNode.textContent || '').slice(0, 20) }
            }"""
        )
        print("caret after click in old dead zone:", sel)
        await pg.screenshot(path=f"{shots}-deadzone.png")

        # Badge click still opens the subtopic (delegated handler).
        if badge:
            await pg.mouse.click(badge["x"] + badge["w"] / 2, badge["y"] + badge["h"] / 2)
            await pg.wait_for_timeout(800)
            print("badge click → url topic:", pg.url.split("topic=")[-1][:8], "(expect != probe topic)")
            # go back to the probe topic for the table tests
            await pg.goto(f"{BASE}/project/{proj}?topic={tid}")
            await pg.wait_for_load_state("networkidle")
            await pg.wait_for_timeout(2000)

        # ---- 2. table selection → comment CTA ----
        table = await pg.wait_for_selector(".doc-editor table", timeout=5000)
        await table.scroll_into_view_if_needed()
        cells = await pg.evaluate(
            """() => [...document.querySelectorAll('.doc-editor table td')].slice(0, 4).map(td => {
                const r = td.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
            })"""
        )
        # cross-cell drag: cell[0] → cell[2] (next row) = CellSelection
        await pg.mouse.move(cells[0]["x"] - 20, cells[0]["y"])
        await pg.mouse.down()
        await pg.mouse.move(cells[2]["x"], cells[2]["y"], steps=8)
        await pg.mouse.up()
        await pg.wait_for_timeout(400)
        cta = await pg.evaluate(
            """() => {
              const el = document.querySelector('.doc-comment-cta')
              if (!el) return null
              const r = el.getBoundingClientRect()
              return { x: Math.round(r.x), y: Math.round(r.y), visible: r.width > 0 }
            }"""
        )
        sel_type = await pg.evaluate(
            """() => {
                const s = document.getSelection()
                return { ranges: s ? s.rangeCount : 0, cellSel: document.querySelectorAll('.doc-editor .selectedCell').length }
            }"""
        )
        print("cross-cell drag → CTA:", cta, "selectedCell count:", sel_type)
        await pg.screenshot(path=f"{shots}-table-cellsel.png")

        # CellSelection CTA → comment mode chip quotes ALL selected cells; Esc exits.
        if cta:
            await pg.click(".doc-comment-cta")
            await pg.wait_for_timeout(400)
            chip_q = await pg.evaluate(
                "() => document.querySelector('.comment-mode-chip__quote')?.textContent || null"
            )
            print("cross-cell CTA click → chip quote:", chip_q)
            await pg.focus(".composer textarea")
            await pg.keyboard.press("Escape")
            await pg.wait_for_timeout(300)
            print(
                "Esc exits comment mode:",
                await pg.evaluate("() => !document.querySelector('.comment-mode-chip')"),
            )

        # single-cell text selection
        first_td = await pg.query_selector(".doc-editor table td")
        assert first_td
        r = await first_td.bounding_box()
        await pg.mouse.move(r["x"] + 4, r["y"] + r["height"] / 2)
        await pg.mouse.down()
        await pg.mouse.move(r["x"] + r["width"] - 8, r["y"] + r["height"] / 2, steps=6)
        await pg.mouse.up()
        await pg.wait_for_timeout(400)
        cta1 = await pg.evaluate(
            "() => !!document.querySelector('.doc-comment-cta')"
        )
        print("single-cell text selection → CTA present:", cta1)
        await pg.screenshot(path=f"{shots}-table-textsel.png")

        # ---- 3. comment mode (after only — .comment-mode-chip is new UI) ----
        if cta1 or cta:
            n_before = len(_req(f"/api/topics/{tid}/comments")["data"])
            await pg.click(".doc-comment-cta")
            await pg.wait_for_timeout(500)
            chip = await pg.evaluate(
                "() => { const el = document.querySelector('.comment-mode-chip'); return el ? el.textContent.trim().slice(0, 60) : null }"
            )
            drawer = await pg.evaluate("() => !!document.querySelector('.tool-panel')")
            print("after CTA click → composer chip:", chip, "| drawer opened:", drawer)
            await pg.screenshot(path=f"{shots}-comment-mode.png")
            if chip:
                await pg.fill(".composer textarea", "表格这两格的数据需要再核实一下")
                await pg.keyboard.press("Enter")
                await pg.wait_for_timeout(800)
                latest = _req(f"/api/topics/{tid}/comments")["data"]
                n_after = len(latest)
                anchored = max(latest, key=lambda c: c["created_at"]) if latest else None
                print(
                    "comment sent:", n_before, "→", n_after,
                    "| anchor:", anchored and anchored.get("reply_to"),
                    "| quote:", anchored and (anchored.get("anchor_quote") or "")[:40],
                )
                chip_gone = await pg.evaluate(
                    "() => !document.querySelector('.comment-mode-chip')"
                )
                print("comment mode exited after send:", chip_gone)
                await pg.screenshot(path=f"{shots}-comment-sent.png")

        # ---- 4. doc-bottom resident comments (after only) ----
        bottom = await pg.evaluate(
            """() => {
              const el = document.querySelector('.doc-comments')
              return el ? { items: el.querySelectorAll('.doc-comments__item').length,
                            hasInput: !!el.querySelector('textarea, input') } : null
            }"""
        )
        print("doc-bottom comments section:", bottom)
        if bottom:
            await pg.fill(".doc-comments__input", "页级评论：整体结构可以，补一版数据来源")
            await pg.press(".doc-comments__input", "Enter")
            await pg.wait_for_timeout(800)
            items = await pg.evaluate(
                "() => document.querySelectorAll('.doc-comments__item').length"
            )
            page_level = [
                c for c in _req(f"/api/topics/{tid}/comments")["data"] if not c.get("reply_to")
            ]
            print("page comment sent → items rendered:", items, "| page-level in API:", len(page_level))
            await pg.evaluate(
                "() => document.querySelector('.doc-comments').scrollIntoView({block:'center'})"
            )
            await pg.wait_for_timeout(300)
            await pg.screenshot(path=f"{shots}-doc-comments.png")

        # ---- 5. drawer head + comment cards (视觉打磨截图) ----
        await pg.click('button[title="评论"]')  # header tool icon (v-btn title)
        await pg.wait_for_timeout(700)
        await pg.screenshot(path=f"{shots}-drawer-comments.png")
        await b.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "setup":
        setup()
    else:
        prefix = sys.argv[2] if len(sys.argv) > 2 else "probe"
        asyncio.run(probe(prefix))
