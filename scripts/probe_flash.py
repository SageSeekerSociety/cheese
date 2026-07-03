"""Repro + regression probe for: "switch back to a topic -> the LAST message
misses the first frame and pops in a beat later".

Root cause (found with this probe): on switch-back the first frame restored a
STALE pixel scrollTop while the timeline's height differs from when we left —
(a) blocks that landed while away are already in blockCache (unread-poll
prefetch) but sat below the fold, and (b) the timeline-end slot (merge box)
fills in async one frame later. The correction only came from later async
scrolls (fetch completion + the 200ms catch-up timer), so the tail visibly
popped in late, on every switch.

Technique: intercept GET /topics/*/blocks with an injected delay, stretching
the "first frame" (cache-only render) into a window we can screenshot and
assert on. A rAF logger inside the page records per rendered frame the row
count, last row identity, scroll geometry and last-row visibility.

Scenarios:
  A: switch back to a long topic whose timeline-end slot holds a merge box
     (last block = system event 通知块).
  B: switch back to a short topic (last block = 芝士 message).
  C: a new message lands in the away topic (real WS post + cache refresh the
     way the unread poll does) -> switch back: the new last message must be
     visible on the very first frame.

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
# (.im-row message rows, .im-event system lines, .action-card cards) and
# records geometry so a frame-to-frame diff pinpoints late layout changes.
FRAME_LOGGER = """
() => {
  window.__frames = [];
  const snap = () => {
    const scroll = document.querySelector('[data-testid="chat-scroll"]');
    const rows = scroll
      ? [...scroll.querySelectorAll('.im-row, .im-event, .action-card')]
      : [];
    const last = rows[rows.length - 1];
    const slotEnd = scroll
      ? scroll.querySelector(':scope > div > div:last-child')
      : null;
    window.__frames.push({
      t: Math.round(performance.now()),
      n: rows.length,
      lastMid: last?.dataset?.mid ?? null,
      lastText: last ? last.textContent.trim().slice(0, 40) : null,
      lastVisible: last && scroll
        ? last.getBoundingClientRect().bottom
          <= scroll.getBoundingClientRect().bottom + 2
        : null,
      scrollTop: scroll ? Math.round(scroll.scrollTop) : -1,
      scrollHeight: scroll ? Math.round(scroll.scrollHeight) : -1,
      scrollBottomGap: scroll
        ? Math.round(scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight)
        : -1,
      slotEndH: slotEnd ? Math.round(slotEnd.getBoundingClientRect().height) : -1,
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
    lastContent: (last?.content ?? '').slice(0, 40),
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
    lastText: last ? last.textContent.trim().slice(0, 40) : null,
    lastVisible: last && scroll
      ? last.getBoundingClientRect().bottom
        <= scroll.getBoundingClientRect().bottom + 2
      : null,
    scrollTop: scroll ? Math.round(scroll.scrollTop) : -1,
    scrollBottomGap: scroll
      ? Math.round(scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight)
      : -1,
  };
}
"""

# Post a message into a topic over the chat WS (summon=false: persist only, no
# AI turn) — a real "message landed while the user was in another topic".
POST_MESSAGE = """
async ({ topicId, content }) => {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/api/topics/${topicId}/chat`);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.send(JSON.stringify({ type: 'message', content, author: 'user-1', summon: false }));
  // Wait for the persisted user_block echo so we know it's in the DB.
  const echoed = await new Promise((res) => {
    const timer = setTimeout(() => res(false), 4000);
    ws.onmessage = (ev) => {
      try {
        const f = JSON.parse(ev.data);
        if (f.type === 'user_block' && f.block?.content === content) {
          clearTimeout(timer); res(true);
        }
      } catch {}
    };
  });
  ws.close();
  return echoed;
}
"""

# Refresh a topic's blockCache entry exactly the way the unread poll does
# (fetch fresh blocks, replace the cache array).
REFRESH_CACHE = """
async (topicId) => {
  const r = await fetch(`/api/topics/${topicId}/blocks`);
  const payload = (await r.json()).data;
  window.__blockCache.set(topicId, payload.data);
  return payload.data.length;
}
"""


async def main() -> None:
    projects = _get("/api/projects")["data"]
    proj = next(p for p in projects if p.get("name") == "AI 课程推荐系统")
    topics = _get(f"/api/topics?project_id={proj['id']}")["data"]
    # A: long topic, merge box in the timeline-end slot, last block = event.
    # B: short topic, last block = 芝士 message.
    topic_a = next(t for t in topics if t["title"] == "搭建推荐算法原型")
    topic_b = next(t for t in topics if t["title"] == "远程节点跑通")
    print(f"[setup] A={topic_a['id']} ({topic_a['title']})")
    print(f"[setup] B={topic_b['id']} ({topic_b['title']})")

    failures: list[str] = []

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

        # Delay the blocks fetch only while a flag is up, so scenario setup
        # (cache refresh fetch) isn't slowed down.
        delay_on = False

        async def maybe_delay(route):
            if delay_on:
                await asyncio.sleep(DELAY_MS / 1000)
            await route.continue_()

        await pg.route("**/api/topics/*/blocks*", maybe_delay)

        async def probe_switch(target, label: str) -> None:
            """Switch to `target`; assert the first content frame shows the
            cache's last block inside the viewport."""
            cache = await pg.evaluate(READ_CACHE, target["id"])
            await pg.evaluate(FRAME_LOGGER)
            await switch_to(target["title"])
            await pg.wait_for_timeout(600)  # well inside the delay window
            during = await pg.evaluate(DOM_STATE)
            await pg.screenshot(path=f"{SHOT_DIR}/flash-{label}-during.png")
            await pg.wait_for_timeout(DELAY_MS + 1500)  # fetch + catch-up done
            after = await pg.evaluate(DOM_STATE)
            await pg.screenshot(path=f"{SHOT_DIR}/flash-{label}-after.png")
            frames = await pg.evaluate("() => window.__frames")

            print(f"\n=== switch back to {label}: {target['title']} ===")
            print(f"[cache before click] {json.dumps(cache, ensure_ascii=False)}")
            print(f"[DOM during delay]   {json.dumps(during, ensure_ascii=False)}")
            print(f"[DOM after fetch]    {json.dumps(after, ensure_ascii=False)}")
            prev = None
            for f in frames:
                key = (f["n"], f["lastMid"], f["scrollHeight"], f["slotEndH"], f["lastVisible"])
                if key != prev:
                    print(f"[frame] {json.dumps(f, ensure_ascii=False)}")
                    prev = key

            # The first frame that shows the target topic's content.
            first = next(
                (
                    f
                    for f in frames
                    if f["n"] and (f["lastMid"] or f["lastText"]) and f["lastText"] == during["lastText"]
                ),
                None,
            )
            checks = {
                # Cache's last block is what the DOM shows during the delay
                # window (no missing tail row on the cache-only render).
                "tail row present in first paint": bool(
                    cache.get("cached")
                    and during["lastText"]
                    and cache["lastContent"][:20] in during["lastText"]
                ),
                # ...and it sits inside the viewport from the first content
                # frame on (the reported bug: it didn't).
                "tail visible in first content frame": bool(first and first["lastVisible"]),
                "tail visible during delay window": bool(during["lastVisible"]),
                "tail visible after fetch": bool(after["lastVisible"]),
            }
            for name, ok in checks.items():
                print(f"[check {label}] {'PASS' if ok else 'FAIL'}  {name}")
                if not ok:
                    failures.append(f"{label}: {name}")

        # --- Scenario A/B: plain bounce with height-shifting slot content ---
        delay_on = True
        await probe_switch(topic_a, "A")
        await probe_switch(topic_b, "B")
        delay_on = False

        # --- Scenario C: a message lands in A while we sit in B (we are in B
        # now after scenario B). Post for real over WS, refresh the cache the
        # way the unread poll does, then switch back under delay. ---
        marker = f"探针消息 probe-flash {int(asyncio.get_event_loop().time() * 1000)}"
        echoed = await pg.evaluate(POST_MESSAGE, {"topicId": topic_a["id"], "content": marker})
        print(f"\n[C setup] posted new message to A over WS, echoed={echoed}")
        n = await pg.evaluate(REFRESH_CACHE, topic_a["id"])
        print(f"[C setup] refreshed A's blockCache (unread-poll style), len={n}")
        delay_on = True
        await probe_switch(topic_a, "C-newtail")
        delay_on = False

        await b.close()

    print()
    if failures:
        print("RESULT: FAIL")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("RESULT: PASS — first frame shows the complete tail in every scenario")


asyncio.run(main())
