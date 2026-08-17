"""One-off probe: what language does Monaco give scripts/redeploy.sh, and which
languages are registered? (debugging 无高亮)"""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


async def main() -> None:
    proj = _get("/api/projects")["data"][0]["id"]
    topics = _get(f"/api/topics?project_id={proj}")["data"]
    work = next(t for t in topics if t.get("kind", "topic") == "topic")

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        pg.on("console", lambda m: print("[console]", m.type, m.text[:200]) if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:300]))
        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me', JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj}?topic={work['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(2000)
        await pg.screenshot(path="tmp_review/probe-0.png")
        # open 文件 drawer
        await pg.click("button:has(i.mdi-folder-outline)", timeout=8000)
        await pg.wait_for_timeout(1500)
        # expand scripts/ and open redeploy.sh
        await pg.click("button:has-text(\"scripts\")", timeout=8000)
        await pg.wait_for_timeout(400)
        await pg.click("button:has-text(\"redeploy.sh\")", timeout=8000)
        await pg.wait_for_timeout(1500)
        probe = await pg.evaluate(
            """() => {
              const m = window.monaco
              if (!m) return {monacoOnWindow: false}
              const models = m.editor.getModels().map(x => ({lang: x.getLanguageId(), lines: x.getLineCount()}))
              const langs = m.languages.getLanguages().map(l => l.id)
              return {monacoOnWindow: true, models, hasShell: langs.includes('shell'), nLangs: langs.length}
            }"""
        )
        print(json.dumps(probe, indent=1))
        # DOM-level: are there colored tokens?
        toks = await pg.evaluate(
            """() => {
              const spans = [...document.querySelectorAll('.view-line span span')]
              const classes = new Set(spans.map(s => s.className))
              return [...classes].slice(0, 10)
            }"""
        )
        print("token classes:", toks)
        await pg.screenshot(path="tmp_review/monaco-probe.png")
        await b.close()


asyncio.run(main())
