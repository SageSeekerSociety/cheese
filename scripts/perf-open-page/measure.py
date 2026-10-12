"""Measure how long a page takes to show its content, in a running 知是 app.

What it answers: when you click a nav entry, how many milliseconds pass before
that page's own content is on screen — and how much of that time was waiting on
the network, how many API requests the page fired, and whether any of them were
serial (one request's answer deciding the next one's URL).

    cd frontend && npx vite --port 3000 --strictPort     # or any running build
    uv run --with playwright python scripts/perf-open-page/measure.py \
        --base-url http://127.0.0.1:3000 --runs 3 --out /tmp/perf-open-page.json

Two modes per page:

* walk  — the app is already open (cache warm); the script pushes the route
          client-side and times it. This is the "click a nav entry" case.
* cold  — a fresh page load of that URL with an empty browser cache.

Both report `ready_ms` = the frame at which the page's own content was on
screen (its root selector present and holding real text), taken from the
document's own performance timeline. `last_response_ms` is the largest
`responseEnd` in the window; when it is close to `ready_ms` the page is waiting
on the network rather than on the main thread.

The page list lives in pages.py — a page joins the measurement by gaining a
row there (its route and the selector that means "its content is up").
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from pages import PageSpec, PAGES

# The sign-in form. Kept here (not in pages.py) because it is about the app
# shell rather than about any one page; the fallbacks cover the current markup
# and the plain-HTML shape the Vue components render.
SIGNIN_URL = "/account/signin"
USER_FIELD = "input[autocomplete='username'], input[name='username'], #username"
PASSWORD_FIELD = "input[type='password']"
SUBMIT_BUTTON = "button[type='submit']"

# Long tasks: the app's own main-thread stalls, summed over the window.
LONG_TASK_INIT = """
window.__lt = { total: 0, count: 0 };
new PerformanceObserver((l) => {
  for (const e of l.getEntries()) { window.__lt.total += e.duration; window.__lt.count += 1; }
}).observe({ entryTypes: ['longtask'] });
"""

# In-page helpers. `__nav` starts a walk: it stamps t0 and pushes the route
# through the app's own router, so every beforeEach/onMounted the app would run
# on a real click runs here too. `__ready` is polled on animation frames and
# freezes t1 on the first frame where the page's content is up, which is why
# the numbers come from the browser's clock and not from ours.
PROBE_INIT = """
window.__probe = null;
window.__lt = window.__lt || { total: 0, count: 0 };
window.__nav = (path) => {
  const app = document.querySelector('#app');
  const router = app.__vue_app__.config.globalProperties.$router;
  window.__probe = { t0: performance.now(), t1: null, path };
  router.push(path);
  return window.__probe.t0;
};
window.__ready = (spec) => {
  if (!window.__probe) return false;
  if (window.__probe.t1 !== null) return true;
  const el = document.querySelector(spec.sel);
  const text = el ? (el.innerText || '').trim() : '';
  if (text.length < spec.min) return false;
  if (spec.notSel && document.querySelector(spec.notSel)) return false;
  window.__probe.t1 = performance.now();
  return true;
};
window.__collect = () => {
  const p = window.__probe || {};
  return { t0: p.t0, t1: p.t1 };
};
"""


@dataclass
class Sample:
    page: str
    mode: str
    ready_ms: float
    last_response_ms: float | None
    requests: int
    api_requests: list[str] = field(default_factory=list)
    long_task_ms: float = 0.0
    long_task_count: int = 0
    network_ms: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


def _request_tracker(page: Page, sink: list[dict[str, Any]]) -> None:
    """Record every request the page makes, with when it started and ended."""

    def on_request(req) -> None:
        sink.append({"url": req.url, "start": None, "end": None, "type": req.resource_type})

    page.on("request", on_request)

    def on_finished(req) -> None:
        timing = None
        try:
            timing = req.timing
        except Exception:  # a redirected or cached request may have none
            timing = None
        for row in reversed(sink):
            if row["url"] == req.url and row["start"] is None and timing:
                row["start"] = timing.get("startTime")
                row["end"] = (timing.get("startTime") or 0) + (timing.get("responseEnd") or 0)
                break

    page.on("requestfinished", on_finished)


def _login(page: Page, base_url: str, username: str, password: str) -> None:
    page.goto(base_url + SIGNIN_URL, wait_until="domcontentloaded")
    page.fill(USER_FIELD, username)
    page.fill(PASSWORD_FIELD, password)
    page.click(SUBMIT_BUTTON)
    # The shell is up when the workspace rail or the home nav is there; either
    # way the sign-in form must be gone.
    page.wait_for_function(
        "() => !document.querySelector(\"input[type='password']\")", timeout=30000
    )
    page.wait_for_load_state("networkidle", timeout=30000)


def _measure_one(
    page: Page,
    spec: PageSpec,
    url: str,
    mode: str,
    warm_url: str,
    timeout_ms: int = 30000,
) -> Sample:
    requests: list[dict[str, Any]] = []
    sink: list[dict[str, Any]] = []
    _request_tracker(page, sink)

    page.evaluate(LONG_TASK_INIT)
    page.evaluate(PROBE_INIT)

    if mode == "walk":
        page.goto(warm_url, wait_until="networkidle")
        page.wait_for_timeout(300)
        page.evaluate(LONG_TASK_INIT)
        page.evaluate(PROBE_INIT)
        sink.clear()
        page.evaluate("(p) => window.__nav(p)", spec.path(url))
    else:
        sink.clear()
        page.evaluate("() => { window.__lt = { total: 0, count: 0 }; }")
        page.goto(url, wait_until="commit")

    spec_payload = {"sel": spec.ready_selector, "min": spec.min_text, "notSel": spec.not_ready_selector}
    try:
        page.wait_for_function(
            "(spec) => window.__ready(spec)", arg=spec_payload, timeout=timeout_ms, polling="raf"
        )
    except Exception as exc:  # noqa: BLE001 — a page that never shows content is a result too
        return Sample(spec.name, mode, float("nan"), None, len(sink), error=str(exc)[:200])

    probe = page.evaluate("() => window.__collect()")
    lt = page.evaluate("() => window.__lt")
    t0, t1 = probe["t0"], probe["t1"]
    ready = t1 - t0 if mode == "walk" else t1

    api = [r["url"] for r in sink if "/api/" in r["url"]]
    ends = [r["end"] for r in sink if r["end"] is not None and r["start"] is not None and r["start"] >= (t0 or 0)]
    last = max(ends) if ends else None
    starts_ms = [r["start"] for r in sink if r["start"] is not None and r["start"] >= (t0 or 0)]
    # The network share: from the first request's start to the last response's
    # end. Rendering that finishes earlier than the last response means the page
    # drew something and kept fetching behind it, which is a different problem
    # from waiting on the network.
    network = (last - min(starts_ms)) if (last and starts_ms) else None

    return Sample(
        page=spec.name,
        mode=mode,
        ready_ms=round(ready, 1),
        last_response_ms=round(last, 1) if last else None,
        requests=len(sink),
        api_requests=sorted({r["url"].split("/api/", 1)[1].split("?")[0] for r in sink if "/api/" in r["url"]}),
        long_task_ms=round(lt["total"], 1),
        long_task_count=lt["count"],
        network_ms=round(network, 1) if network else None,
    )


def _median(samples: list[Sample]) -> Sample:
    ok = [s for s in samples if s.error is None and s.ready_ms == s.ready_ms]
    if not ok:
        return samples[0]
    pick = statistics.median
    last_vals = [s.last_response_ms for s in ok if s.last_response_ms is not None]
    net_vals = [s.network_ms for s in ok if s.network_ms is not None]
    return Sample(
        page=ok[0].page,
        mode=ok[0].mode,
        ready_ms=round(pick([s.ready_ms for s in ok]), 1),
        last_response_ms=round(pick(last_vals), 1) if last_vals else None,
        requests=int(pick([s.requests for s in ok])),
        api_requests=sorted({u for s in ok for u in s.api_requests}),
        long_task_ms=round(pick([s.long_task_ms for s in ok]), 1),
        long_task_count=int(pick([s.long_task_count for s in ok])),
        network_ms=round(pick(net_vals), 1) if net_vals else None,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:3000")
    ap.add_argument("--username", default="alice")
    ap.add_argument("--password", default="demo12345")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", default="perf-open-page.json")
    ap.add_argument("--only", default="", help="comma-separated page names")
    ap.add_argument("--modes", default="walk", help="walk,cold")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    wanted = [n.strip() for n in args.only.split(",") if n.strip()]
    specs = [p for p in PAGES if not wanted or p.name in wanted]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    results: dict[str, dict[str, Sample]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda e: print("[pageerror]", str(e)[:200], flush=True))
        _login(page, args.base_url, args.username, args.password)
        home = page.url

        # Every page is walked from the same warm origin (the workspace root),
        # so one page's leftovers do not become the next page's head start.
        warm_url = home
        for spec in specs:
            for mode in modes:
                samples: list[Sample] = []
                for i in range(args.runs):
                    try:
                        samples.append(
                            _measure_one(page, spec, args.base_url, mode, warm_url, args.timeout_ms)
                        )
                    except Exception as exc:  # noqa: BLE001
                        samples.append(Sample(spec.name, mode, float("nan"), None, 0, error=str(exc)[:200]))
                    page.goto(warm_url, wait_until="networkidle")
                    time.sleep(0.4)
                med = _median(samples)
                results.setdefault(spec.name, {})[mode] = med
                print(
                    f"{spec.name:28s} {mode:4s} {med.ready_ms:>8} ms  "
                    f"last_resp={med.last_response_ms}  reqs={med.requests}  lt={med.long_task_ms}  "
                    f"{'ERROR ' + (med.error or '') if med.error else ''}",
                    flush=True,
                )

        browser.close()

    payload = {p: {m: s.to_dict() for m, s in per_mode.items()} for p, per_mode in results.items()}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
