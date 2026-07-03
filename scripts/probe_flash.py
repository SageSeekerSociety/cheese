"""Repro probe for: "switch back to a topic -> the LAST message misses the
first frame and pops in one frame later".

Technique: intercept GET /topics/*/blocks and inject a delay, stretching the
"first frame" (cache-only render) into a seconds-long window we can screenshot
and inspect. A rAF logger inside the page records, per rendered frame, the
visible row count + last row identity + scroll geometry, so we can tell
DOM-missing apart from scrolled-out-of-view.

Run:  uv run --with playwright python scripts/probe_flash.py
"""

import asyncio
import json
import urllib.request

from playwright.async_api import async_playwright

API = "http://127.0.0.1:8099"
BASE = "http://localhost:5173"
DELAY_MS = 2500
SHOT_DIR = "tmp_review"


def _get(path: str):
    with urllib.request.urlopen(API + path) as r:
        return json.load(r)["data"]


# rAF logger: one entry per rendered frame. Counts every timeline row kind
# (.im-row message rows, .im-event system lines, .action-card cards) and, for
# height forensics, records every row's offsetTop/offsetHeight so a frame-to-
# frame diff pinpoints WHICH element grew late.
FRAME_LOGGER = """
() => {
  window.__frames = [];
  const snap = () => {
    const scroll = document.querySelector('[data-testid="chat-scroll"]');
    const rows = scroll
      ? [...scroll.querySelectorAll('.im-row, .im-event, .action-card')]
      : [];
    const last = rows[rows.length - 1];
    const slotEnd = scroll ? scroll.lastElementChild : null;
    window.__frames.push({
      t: Math.round(performance.now()),
      n: rows.length,
      lastMid: last?.dataset?.mid ?? null,
      lastText: last ? last.textContent.trim().slice(0, 50) : null,
      scrollTop: scroll ? Math.round(scroll.scrollTop) : -1,
      scrollHeight: scroll ? Math.round(scroll.scrollHeight) : -1,
      scrollBottomGap: scroll
        ? Math.round(scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight)
        : -1,
      slotEndH: slotEnd ? Math.round(slotEnd.getBoundingClientRect().height) : -1,
      rows: rows.map((r) => [
        r.dataset?.mid?.slice(0, 8) ?? r.textContent.trim().slice(0, 16),
        Math.round(r.offsetTop),
        Math.round(r.offsetHeight),
      ]),
    });
    requestAnimationFrame(snap);
  };
  requestAnimationFrame(snap);
}
"""

# Read the app's REAL blockCache instance via the dev-only window hook
# (a dynamic import can resolve to a second module instance under Vite HMR).
READ_CACHE = """
(topicId) => {
  const cache = window.__blockCache;
  if (!cache) return { hook: false };
  const arr = cache.get(topicId);
  if (!arr) return { cached: false };
  const last = arr[arr.length - 1];
  return {
    cached: true,
    len: arr.length,
    lastId: last?.id ?? null,
    lastKind: last?.kind ?? null,
    lastAuthorType: last?.author_type ?? null,
    lastContent: (last?.content ?? '').slice(0, 50),
  };
}
"""

DOM_STATE = """
() => {
  const scroll = document.querySelector('[data-testid="chat-scroll"]');
  const rows = scroll
    ? [...scroll.querySelectorAll('.im-row, .im-event, .action-card')]
    : [];
  const last = rows[rows.length - 1];
  return {
    n: rows.length,
    lastMid: last?.dataset?.mid ?? null,
    lastText: last ? last.textContent.trim().slice(0, 50) : null,
    scrollTop: scroll ? Math.round(scroll.scrollTop) : -1,
    scrollBottomGap: scroll
      ? Math.round(scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight)
      : -1,
  };
}
"""


async def main() -> None:
    projects = _get("/api/projects")["data"]
    proj = next(p for p in projects if p.get("name") == "AI 课程推荐系统")
    topics = _get(f"/api/topics?project_id={proj['id']}")["data"]
    # A: last block is a system event (通知块); B: last block is an AI message.
    topic_a = next(t for t in topics if t["title"] == "搭建推荐算法原型")
    topic_b = next(t for t in topics if t["title"] == "远程节点跑通")
    print(f"[setup] A={topic_a['id']} ({topic_a['title']})")
    print(f"[setup] B={topic_b['id']} ({topic_b['title']})")

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1500, "height": 950})
        pg.on(
            "console",
            lambda m: print("[console]", m.type, m.text[:200])
            if m.type in ("error", "warning")
            else None,
        )
        pg.on("pageerror", lambda e: print("[pageerror]", str(e)[:300]))

        await pg.goto(BASE)
        await pg.evaluate(
            "localStorage.setItem('cheesex.me',"
            " JSON.stringify({id:'probe', handle:'mentor-1', name:'张衡'}))"
        )
        await pg.goto(f"{BASE}/project/{proj['id']}?topic={topic_a['id']}")
        await pg.wait_for_load_state("networkidle")
        await pg.wait_for_timeout(1500)  # topic A loaded -> cached

        async def switch_to(title: str) -> None:
            await pg.click(f".topic-row:has-text('{title}')", timeout=8000)

        # Visit B so it's cached too, then we'll bounce back and forth.
        await switch_to(topic_b["title"])
        await pg.wait_for_timeout(1500)

        # Inject the delay on the blocks fetch from now on.
        async def delayed(route):
            await asyncio.sleep(DELAY_MS / 1000)
            await route.continue_()

        await pg.route("**/api/topics/*/blocks*", delayed)

        async def probe_switch(target, label: str) -> None:
            """Switch to `target` and record what the first frames show."""
            cache = await pg.evaluate(READ_CACHE, target["id"])
            await pg.evaluate(FRAME_LOGGER)
            await switch_to(target["title"])
            await pg.wait_for_timeout(600)  # well inside the delay window
            during = await pg.evaluate(DOM_STATE)
            await pg.screenshot(path=f"{SHOT_DIR}/flash-{label}-during.png")
            await pg.wait_for_timeout(DELAY_MS + 1500)  # fetch landed
            after = await pg.evaluate(DOM_STATE)
            await pg.screenshot(path=f"{SHOT_DIR}/flash-{label}-after.png")
            frames = await pg.evaluate("() => window.__frames")

            print(f"\n=== switch back to {label}: {target['title']} ===")
            print(f"[cache before click] {json.dumps(cache, ensure_ascii=False)}")
            print(f"[DOM during delay]   {json.dumps(during, ensure_ascii=False)}")
            print(f"[DOM after fetch]    {json.dumps(after, ensure_ascii=False)}")
            # Print frames where geometry changed, and diff row heights between
            # consecutive changed frames to name the element that grew late.
            prev = None
            prev_frame = None
            for f in frames:
                key = (f["n"], f["lastMid"], f["scrollHeight"], f["slotEndH"])
                if key != prev:
                    slim = {k: v for k, v in f.items() if k != "rows"}
                    print(f"[frame] {json.dumps(slim, ensure_ascii=False)}")
                    if (
                        prev_frame is not None
                        and prev_frame["n"] == f["n"]
                        and prev_frame["scrollHeight"] != f["scrollHeight"]
                    ):
                        for a, c in zip(prev_frame["rows"], f["rows"]):
                            if a[1] != c[1] or a[2] != c[2]:
                                print(
                                    f"  [row-diff] {c[0]!r}: top {a[1]}->{c[1]}, "
                                    f"height {a[2]}->{c[2]}"
                                )
                    prev = key
                    prev_frame = f
            verdict = (
                "FIRST FRAME MISSING LAST BLOCK"
                if during["n"] < after["n"] or during["lastText"] != after["lastText"]
                else "first frame complete"
            )
            print(f"[verdict {label}] {verdict}  (during n={during['n']} after n={after['n']})")

        # B -> A (last block: system event) then A -> B (last block: AI message).
        await probe_switch(topic_a, "A")
        await probe_switch(topic_b, "B")

        await b.close()


asyncio.run(main())
