"""Full-element living-doc editor probe (打磨验收).

Creates a test topic whose doc covers the whole round-trip corpus (headings,
marks, code, lists, task list, table w/ alignment, quotes, hr, link, image,
tokens), screenshots each element, then runs the end-to-end fidelity checks:

  1. visual edit (click a task checkbox) → autosave → GET doc: only the
     checkbox state changed, everything else byte-identical
  2. lossy doc (setext heading) → banner shows + autosave stays paused
  3. source mode → Monaco shows the raw file verbatim

Usage:
  python3 scripts/probe_editor_full.py            # run everything
Screenshots land in tmp_review/editor-full-*.png. The test topic is archived
at the end.
"""

import asyncio
import json
import sys
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"
TOPIC_TITLE = "编辑器全要素验证"
AUTHOR = "mentor-1"
SHOTS = "tmp_review/editor-full"

PROBE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="360" height="120">'
    '<rect width="360" height="120" rx="12" fill="#f5e6cc"/>'
    '<circle cx="60" cy="60" r="34" fill="#e08a34"/>'
    '<text x="120" y="70" font-size="26" fill="#6b4a12">CheeseX 测试图</text></svg>'
)

# {topic} placeholder is replaced with the probe topic's own id so the <#> chip
# resolves to a real title.
DOC_MD = """## 综合语料

这是**粗体**、*斜体*、~~删除线~~ 和 `inline_code()` 的混排段落，
还有一个 [产品 spec 链接](https://example.com/spec) 和裸地址 https://example.com/page 。

### 任务清单

- [ ] 未完成的任务甲
- [x] 已完成的任务乙
- [ ] 探针要点它的任务丙

### 代码

```python
def hello(name: str) -> str:
    # greeting
    return f"hi {name}"
```

### 对比表

| 方案 | 优点 | 缺点 |
| :--- | :---: | ---: |
| offset 分页 | 实现简单 | 深分页慢 |
| cursor 分页 | 性能稳定 | 无法跳页 |

### 引用与分割线

> 外层引用：文档是状态，对话是过程。
> > 内层引用（嵌套）。

---

#### 图片与 token

![测试图](uploads/probe.svg)

请 <@mentor-1> 关注 <#{topic}>，细节见 <&uploads/probe.svg>。
"""

LOSSY_MD = """Setext 标题
===

这个文档用了 setext 标题语法，可视化编辑器会把它改写成 ATX——
lossy 检测必须亮横幅、暂停自动保存。
"""


def _req(path: str, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(API + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data) as r:
        return json.load(r)["data"]


def normalize(md: str) -> str:
    """Python twin of docMarkdown.ts normalizeMarkdown (the documented
    save-time tolerances): visual save writes the canonical serialization, so
    fidelity is asserted modulo these rules, not byte-for-byte."""
    import re

    out: list[str] = []
    in_fence = False
    for raw in md.replace("\r\n", "\n").split("\n"):
        fence = re.match(r"^(\s*)(```|~~~)", raw)
        if fence:
            in_fence = not in_fence
            out.append(raw.rstrip())
            continue
        if in_fence:
            out.append(raw)
            continue
        raw = raw.rstrip()
        if re.match(r"^\s*\|.*\|\s*$", raw):
            cells = []
            for c in raw.strip()[1:-1].split("|"):
                m = re.match(r"^\s*(:?)-+(:?)\s*$", c)
                cells.append(f"{m.group(1)}---{m.group(2)}" if m else re.sub(r"\s+", " ", c.strip()))
            out.append("| " + " | ".join(cells) + " |")
            continue
        if re.match(r"^(\s{0,3}>\s*)+$", raw):
            continue  # quote-only separator line
        bq = re.match(r"^((?:\s{0,3}>\s?)+)(.*)$", raw)
        if bq:
            depth = bq.group(1).count(">")
            out.append(("> " * depth + bq.group(2).strip()).rstrip())
            continue
        raw = re.sub(r"^(\s*)[*+](\s)", r"\1-\2", raw)
        out.append(raw)
    collapsed: list[str] = []
    for line in out:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    while collapsed and collapsed[0] == "":
        collapsed.pop(0)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    return "\n".join(collapsed)


def get_doc(tid: str) -> str:
    try:
        doc = _req(f"/api/topics/{tid}/doc")
        return (doc or {}).get("content") or ""
    except Exception:
        return ""


def setup() -> tuple[str, str]:
    proj = _req("/api/projects")["data"][0]["id"]
    topics = _req(f"/api/topics?project_id={proj}")["data"]
    topic = next((t for t in topics if t["title"] == TOPIC_TITLE), None)
    if topic is None:
        topic = _req("/api/topics", "POST", {"project_id": proj, "title": TOPIC_TITLE})
    tid = topic["id"]
    # A previous run archives the probe topic on exit — revive it.
    if topic.get("status") == "archived":
        _req(f"/api/topics/{tid}/unarchive", "POST", {"by": AUTHOR})
    # Workspace image the doc references relatively (must render via raw API,
    # must serialize back as the SAME relative path).
    _req(
        f"/api/projects/{proj}/file?topic={tid}",
        "PUT",
        {"path": "uploads/probe.svg", "content": PROBE_SVG},
    )
    md = DOC_MD.replace("{topic}", tid)
    if get_doc(tid).strip() != md.strip():
        _req(f"/api/topics/{tid}/doc", "PUT", {"content": md, "author": AUTHOR})
    print("topic:", tid, "project:", proj)
    return proj, tid


async def shoot(pg, selector: str, name: str, pad: int = 8) -> bool:
    """Screenshot one element (with a little context padding)."""
    el = await pg.query_selector(selector)
    if not el:
        print(f"  [miss] {name}: selector not found: {selector}")
        return False
    await el.scroll_into_view_if_needed()
    await pg.wait_for_timeout(250)
    box = await el.bounding_box()
    if not box:
        print(f"  [miss] {name}: no bounding box")
        return False
    vp = pg.viewport_size
    clip = {
        "x": max(box["x"] - pad, 0),
        "y": max(box["y"] - pad, 0),
        "width": min(box["width"] + 2 * pad, vp["width"]),
        "height": min(box["height"] + 2 * pad, vp["height"]),
    }
    await pg.screenshot(path=f"{SHOTS}-{name}.png", clip=clip)
    print(f"  [shot] {name}")
    return True


async def main() -> None:
    proj, tid = setup()
    original_md = get_doc(tid)
    ok = True
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
        await pg.screenshot(path=f"{SHOTS}-doc.png", full_page=False)

        # ---- element screenshots ----
        print("element shots:")
        await shoot(pg, ".doc-editor table", "table")
        await shoot(pg, ".doc-editor ul[data-type='taskList']", "tasklist")
        await shoot(pg, ".doc-editor blockquote", "quote")
        await shoot(pg, ".doc-editor img", "image")
        await shoot(pg, ".doc-editor a[href]", "link", pad=16)
        await shoot(pg, ".doc-editor .mention", "tokens", pad=16)

        # code block: hover first so the copy button shows in the shot
        pre = await pg.query_selector(".doc-editor pre")
        if pre:
            await pre.scroll_into_view_if_needed()
            await pg.wait_for_timeout(200)
            await pre.hover()
            await pg.wait_for_timeout(300)
            await shoot(pg, ".doc-editor pre", "codeblock")
            has_copy = await pg.evaluate("() => !!document.querySelector('.doc-codecopy')")
            has_lang = await pg.evaluate(
                "() => document.querySelector('.doc-editor pre')?.dataset.language || null"
            )
            print("  copy button on hover:", has_copy, "| data-language:", has_lang)
            ok &= has_copy and has_lang == "python"

        # highlight actually applied?
        hljs = await pg.evaluate(
            "() => document.querySelectorAll('.doc-editor pre .hljs-keyword, .doc-editor pre .hljs-string').length"
        )
        print("  hljs tokens in code block:", hljs)
        ok &= hljs > 0

        # image resolves through the raw-file API (not a broken relative URL)
        img = await pg.evaluate(
            """() => {
              const i = document.querySelector('.doc-editor img')
              return i ? { src: i.getAttribute('src'), loaded: i.complete && i.naturalWidth > 0 } : null
            }"""
        )
        print("  image:", img)
        ok &= bool(img and img["loaded"] and "/file/raw?path=uploads%2Fprobe.svg" in img["src"])

        # no lossy banner on the clean corpus
        banner = await pg.evaluate("() => !!document.querySelector('.doc-lossy-banner')")
        print("  lossy banner on clean doc (expect False):", banner)
        ok &= not banner

        # ---- E2E round trip: click the 3rd task checkbox → autosave → GET doc.
        # ONLY that checkbox's state may change; every other byte stays. ----
        print("round trip via checkbox click:")
        boxes = await pg.query_selector_all(".doc-editor ul[data-type='taskList'] input[type='checkbox']")
        print("  checkboxes:", len(boxes))
        if len(boxes) >= 3:
            await boxes[2].click()
            await pg.wait_for_timeout(3800)  # autosave debounce is 2.5s
            after = get_doc(tid)
            expected = original_md.replace("- [ ] 探针要点它的任务丙", "- [x] 探针要点它的任务丙")
            # Visual save writes the canonical serialization — assert equality
            # under the documented tolerances (same rules as the lossy check).
            same = normalize(after) == normalize(expected)
            toggled = "- [x] 探针要点它的任务丙" in after
            print("  saved ≡ original except the toggled checkbox:", same, "| toggled:", toggled)
            if not (same and toggled):
                ok = False
                for i, (a, c) in enumerate(
                    zip(normalize(expected).split("\n"), normalize(after).split("\n"))
                ):
                    if a != c:
                        print(f"    L{i + 1}: expected {a!r} / got {c!r}")
        else:
            ok = False

        # ---- 源码模式 ----
        print("source mode:")
        await pg.click('button:has-text("源码")')
        await pg.wait_for_timeout(1500)
        has_monaco = await pg.evaluate("() => !!document.querySelector('.doc-source .monaco-editor')")
        print("  Monaco mounted:", has_monaco)
        ok &= has_monaco
        await pg.screenshot(path=f"{SHOTS}-source.png")
        # raw text visible (spot-check a line that the visual editor rewrites)
        raw_has = await pg.evaluate(
            "() => document.querySelector('.doc-source').textContent.includes(':---')"
        )
        print("  raw alignment row visible in source:", raw_has)
        ok &= raw_has
        await pg.click('button:has-text("源码")')  # back to visual
        await pg.wait_for_timeout(800)

        # ---- input rules, each typed in a real browser (B5) ----
        print("input rules:")
        await pg.click(".doc-editor .ProseMirror")
        await pg.keyboard.press("Meta+a")
        await pg.keyboard.press("Delete")
        await pg.wait_for_timeout(200)

        async def type_line(s: str, enters: int = 1) -> None:
            await pg.keyboard.type(s, delay=12)
            for _ in range(enters):
                await pg.keyboard.press("Enter")

        await type_line("# 一级标题")
        await type_line("## 二级标题")
        await type_line("- 无序项", 2)  # second Enter exits the list
        await type_line("1. 有序项", 2)
        await type_line("[ ] 任务项", 2)
        await type_line("> 引用行", 2)
        await pg.keyboard.type("---")  # hr input rule fires on the 3rd dash
        await pg.wait_for_timeout(300)
        await pg.keyboard.press("Enter")
        await type_line("**粗体** 和 `行内码` 和 [链接](https://e.com/x)")
        await pg.keyboard.type("```py ")
        await pg.keyboard.type("code()")
        await pg.wait_for_timeout(400)
        rules = await pg.evaluate(
            """() => {
              const q = (sel) => !!document.querySelector('.doc-editor ' + sel)
              return {
                h1: q('h1'), h2: q('h2'),
                ul: q("ul:not([data-type='taskList']) li"),
                ol: q('ol li'),
                task: q("ul[data-type='taskList'] input[type=checkbox]"),
                quote: q('blockquote'), hr: q('hr'),
                bold: q('strong'), code: q('p code'),
                link: q("a[href='https://e.com/x']"),
                codeblock: q("pre[data-language='py']"),
              }
            }"""
        )
        print("  " + json.dumps(rules, ensure_ascii=False))
        ok &= all(rules.values())
        await pg.screenshot(path=f"{SHOTS}-inputrules.png")
        await pg.wait_for_timeout(3200)  # let the scratch autosave settle

        # ---- lossy detection on a setext-heading doc ----
        print("lossy detection:")
        _req(f"/api/topics/{tid}/doc", "PUT", {"content": LOSSY_MD, "author": AUTHOR})
        await pg.reload()
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(2500)
        banner = await pg.evaluate("() => !!document.querySelector('.doc-lossy-banner')")
        print("  banner on setext doc:", banner)
        ok &= banner
        await shoot(pg, ".doc-lossy-banner", "lossy-banner")
        # type into the doc, wait past the autosave debounce — the file must NOT change
        await pg.click(".doc-editor .ProseMirror p")
        await pg.keyboard.type("x")
        await pg.wait_for_timeout(3800)
        unchanged = get_doc(tid).strip() == LOSSY_MD.strip()
        print("  autosave paused (file unchanged):", unchanged)
        ok &= unchanged
        # manual ⌘S → confirm dialog appears; choose 源码模式 (no overwrite)
        await pg.keyboard.press("Meta+s")
        await pg.wait_for_timeout(600)
        dialog = await pg.evaluate("() => !!document.querySelector('.v-dialog .v-card')")
        print("  manual save asks for confirmation:", dialog)
        ok &= dialog
        await pg.screenshot(path=f"{SHOTS}-lossy-confirm.png")
        if dialog:
            await pg.click('button:has-text("用源码模式")')
            await pg.wait_for_timeout(1200)
            in_source = await pg.evaluate("() => !!document.querySelector('.doc-source .monaco-editor')")
            print("  dialog → source mode:", in_source)
            ok &= in_source
        still_unchanged = get_doc(tid).strip() == LOSSY_MD.strip()
        print("  file still unchanged after dialog:", still_unchanged)
        ok &= still_unchanged

        await b.close()

    # restore the corpus doc, then archive the probe topic
    _req(f"/api/topics/{tid}/doc", "PUT", {"content": original_md, "author": AUTHOR})
    _req(f"/api/topics/{tid}/archive", "POST", {"by": AUTHOR})
    print("topic archived.")
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
