"""Measure how long the five pages take to show their content on dev.

One browser tab carries the whole run (tab churn made Chrome abort here).
Per page, two modes:
  cold   - a full document load of the page URL
  switch - vue-router push from another page in the same document

Every number is taken in the page's own performance timeline, so network and
main-thread work partition the same window:
  visibleIn    window until the target route's own content is on screen
  apiLast      when the last API response of the window arrived (network floor)
  apiSum       API request durations added up (network work, overlapping)
  mainThread   long-task time on the main thread (script + layout + paint)
  idle         visibleIn - apiLast - mainThread (waiting on the server)
"""
import json
import os
import sys
import time

sys.path.insert(0, "/var/tmp/perf")
from cdp import new_tab  # noqa: E402

BASE = "https://okcheese.com"

DEFER_LIST = [
    "/users/me/timezone",
    "/avatars/",
    "/addresses/",
    "/users/me/consents",
    "/awaiting-me",
    "/notifications/unread-count",
    "/feedback/meta",
    "/feedback/counts",
    "/topic-unread",
    "/private-unread",
    "/topic-notify-levels",
]

DEFER_JS = r"""
(() => {
  const st = { on: __ON__, ms: __MS__, list: __LIST__ };
  window.__defer = st;
  const _open = XMLHttpRequest.prototype.open;
  const _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) {
    try { this.__cxU = String(u); } catch (e) {}
    return _open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    try {
      if (st.on && this.__cxU && st.list.some(s => this.__cxU.includes(s))) {
        const self = this, a = arguments;
        return setTimeout(() => _send.apply(self, a), st.ms);
      }
    } catch (e) {}
    return _send.apply(this, arguments);
  };
  window.__deferSet = (on, ms) => { st.on = !!on; if (ms) st.ms = ms; };
})();
"""

DEFER_ON = os.environ.get("DEFER_ON") == "1"
DEFER_MS = int(os.environ.get("DEFER_MS", "3000"))

def defer_source():
    return (DEFER_JS.replace("__ON__", "true" if DEFER_ON else "false")
                   .replace("__MS__", str(DEFER_MS))
                   .replace("__LIST__", json.dumps(DEFER_LIST)))


PORT = int(os.environ.get("CDP_PORT", "9333"))

PROBE = r"""
(() => {
  try { performance.setResourceTimingBufferSize(10000); } catch (e) {}
  const p = { visibleAt: null, t0: 0, len: 0, busy: false, baseText: null, basePath: null,
              routeAt: null, samples: [], routeLog: [] };
  window.__probe = p;
  window.__longs = window.__longs || [];
  try {
    new PerformanceObserver(list => {
      for (const e of list.getEntries()) window.__longs.push([Math.round(e.startTime), Math.round(e.duration)]);
    }).observe({ type: 'longtask', buffered: true });
  } catch (e) {}
  const host = () => document.querySelector('main') || document.querySelector('#app');
  const text = () => { const m = host(); return m ? ((m.innerText || '').trim()) : ''; };
  const busy = () => !!document.querySelector('.v-progress-circular, .v-skeleton-loader, .v-overlay__scrim');
  const moved = () => !p.basePath || location.pathname !== p.basePath;
  let lastSample = -1e9;
  p.tick = () => {
    const t = text(), b = busy(), now = performance.now();
    p.len = t.length; p.busy = b;
    if (p.routeAt === null && moved()) p.routeAt = now;
    if (now - lastSample > 250) { lastSample = now; p.samples.push([Math.round(now - p.t0), location.pathname, t.length]); }
    if (p.visibleAt === null && moved() && !b && t.length > 60 && t !== p.baseText) p.visibleAt = now;
    if (p.watch) requestAnimationFrame(p.tick);
  };
  p.listen = () => { p.watch = true; requestAnimationFrame(p.tick); };
  p.arm = (target) => {
    const t = text();
    p.visibleAt = null; p.routeAt = null; p.watch = false; p.t0 = performance.now();
    p.baseText = t || null; p.basePath = location.pathname || null;
    p.samples = [[0, location.pathname, t.length]]; p.routeLog = [];
    try {
      const r = document.querySelector('#app').__vue_app__.config.globalProperties.$router;
      if (!p.hooked) { p.hooked = true; r.afterEach((to) => p.routeLog.push([Math.round(performance.now() - p.t0), to.fullPath])); }
    } catch (e) {}
    p.listen();
  };
  p.watch = true; requestAnimationFrame(p.tick);
})();
"""

REPORT = r"""
() => {
  const p = window.__probe;
  const t0 = p.t0 || 0;
  const nav = performance.getEntriesByType('navigation')[0];
  const paint = Object.fromEntries(performance.getEntriesByType('paint').map(e => [e.name, Math.round(e.startTime)]));
  const res = performance.getEntriesByType('resource').filter(r => r.startTime >= t0);
  const isStream = r => (r.responseEnd - r.startTime) > 10000;
  const apiAll = res.filter(r => r.name.includes('/api/'));
  const streams = apiAll.filter(isStream);
  const api = apiAll.filter(r => !isStream(r));
  const SHELL = ['/api/users/me', '/api/projects', '/api/awaiting-me', '/api/feedback/', '/api/notifications/',
                 '/api/avatars/', '/api/addresses/', '/api/admin/stats', '/api/users/me/consents'];
  const shell = api.filter(r => SHELL.some(s2 => r.name.replace('https://okcheese.com','').startsWith(s2)));
  const own = api.filter(r => !SHELL.some(s2 => r.name.replace('https://okcheese.com','').startsWith(s2)));
  const chunk = res.filter(r => /\/assets\/.*\.(js|css)$/.test(r.name));
  const longs = (window.__longs || []).filter(e => e[0] >= t0);
  const sum = (a, f) => Math.round(a.reduce((x, e) => x + f(e), 0));
  const slow = api.slice().sort((a, b) => b.duration - a.duration).slice(0, 8)
    .map(r => [r.name.replace('https://okcheese.com', ''), Math.round(r.duration), r.transferSize || 0]);
  const host = document.querySelector('main') || document.querySelector('#app');
  return {
    visibleIn: p.visibleAt === null ? null : Math.round(p.visibleAt - t0),
    total: Math.round(performance.now() - t0),
    textLen: p.len, busy: p.busy, path: location.pathname,
    textHead: host ? (host.innerText || '').trim().slice(0, 120) : '',
    apiCount: api.length, apiSum: sum(api, r => r.duration),
    apiBytes: sum(api, r => r.transferSize || 0),
    apiMax: api.length ? Math.round(Math.max(...api.map(r => r.duration))) : 0,
    apiLast: api.length ? Math.round(Math.max(...api.map(r => r.responseEnd)) - t0) : 0,
    apiOwnFirst: own.length ? Math.round(Math.min(...own.map(r => r.startTime)) - t0) : 0,
    apiShellFirst: shell.length ? Math.round(Math.min(...shell.map(r => r.startTime)) - t0) : 0,
    apiShellLast: shell.length ? Math.round(Math.max(...shell.map(r => r.responseEnd)) - t0) : 0,
    apiShellCount: shell.length,
    apiShellSum: sum(shell, r => r.duration),
    apiOwnCount: own.length,
    apiOwnSum: sum(own, r => r.duration),
    apiOwnLast: own.length ? Math.round(Math.max(...own.map(r => r.responseEnd)) - t0) : 0,
    apiOwnNames: own.map(r => r.name.replace('https://okcheese.com','').slice(0, 70)).slice(0, 12),
    streamCount: streams.length,
    routeAt: p.routeAt === null ? null : Math.round(p.routeAt - t0),
    routeLog: (p.routeLog || []).slice(0, 4),
    samples: (p.samples || []).slice(-12),
    apiSlow: slow,
    apiAll: api.slice().sort((a, b) => a.startTime - b.startTime).slice(0, 40).map(r => [
      r.name.replace('https://okcheese.com/api/', '').slice(0, 130),
      Math.round(r.startTime - t0), Math.round(r.duration), Math.round(r.responseEnd - t0)]),
    chunkCount: chunk.length, chunkSum: sum(chunk, r => r.duration),
    chunkFirstAt: chunk.length ? Math.round(Math.min(...chunk.map(r => r.startTime)) - t0) : 0,
    chunkLastAt: chunk.length ? Math.round(Math.max(...chunk.map(r => r.responseEnd)) - t0) : 0,
    chunkSlow: chunk.slice().sort((a, b) => a.startTime - b.startTime).slice(-6)
      .map(r => [Math.round(r.startTime - t0), Math.round(r.duration), r.name.split('/').pop().slice(-24)]),
    chunkBytes: sum(chunk, r => r.transferSize || 0),
    longCount: longs.length, longMs: sum(longs, e => e[1]),
    longSlow: longs.slice().sort((a, b) => b[1] - a[1]).slice(0, 6),
    ttfb: nav ? Math.round(nav.responseStart) : null,
    loadEnd: nav ? Math.round(nav.loadEventEnd) : null,
    paint,
  };
}
"""


def main():
    ids = json.load(open("/var/tmp/perf/ids.json"))
    pid = ids["pid"]
    pages = [
        ("频道", f"/projects/{pid}/channels/{ids['root_topic']}"),
        ("任务对话", f"/projects/{pid}/tasks/{ids['task_number']}"),
        ("空间详情", f"/spaces/{ids['space']}/tasks"),
        ("资料库", f"/projects/{pid}/library"),
        ("管理后台", "/admin/overview"),
    ]
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    reps = int(os.environ.get("REPS", "3"))
    phases = os.environ.get("PHASES", "cold,switch").split(",")
    out_file = os.environ.get("OUT", "/var/tmp/perf/results.json")
    results = {}

    tab = new_tab(PORT)
    for m in ("Page.enable", "Runtime.enable", "Network.enable"):
        tab.send(m)
    tab.send("Page.addScriptToEvaluateOnNewDocument", source=PROBE)
    tab.send("Page.addScriptToEvaluateOnNewDocument", source=defer_source())

    # The SPA renders a project page only for a signed-in account and reads the
    # account from localStorage['user'] (frontend/src/me.ts).
    tab.send("Page.navigate", url=BASE + "/")
    time.sleep(5)
    tab.eval(
        f"""() => {{ localStorage.setItem('accessToken', {json.dumps(ids['token'])});
        localStorage.setItem('user', {json.dumps(json.dumps(ids['user'], ensure_ascii=False))});
        return true; }}"""
    )
    print("auth set", flush=True)

    def run(name, path, kind):
        r = {"page": name, "kind": kind, "defer": DEFER_ON, "deferMs": DEFER_MS}
        tab.eval("() => window.__probe.arm(%s)" % json.dumps(path))
        if kind == "switch":
            tab.eval(
                f"""() => {{ const app = document.querySelector('#app').__vue_app__;
                app.config.globalProperties.$router.push({json.dumps(path)}); return true; }}"""
            )
        else:
            tab.send("Page.navigate", url=BASE + path)
        t0 = time.time()
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                v = tab.eval("() => window.__probe.visibleAt", retries=2)
            except Exception:
                v = None
            if v:
                break
            time.sleep(0.05)
        wall = time.time() - t0
        rep = tab.eval(REPORT)
        r.update(rep)
        r["wall"] = round(wall * 1000)
        r["shown"] = rep["visibleIn"] is not None
        r["networkFloor"] = rep["apiLast"]
        r["mainThread"] = rep["longMs"]
        r["idle"] = max(0, (rep["visibleIn"] or rep["total"]) - rep["apiLast"] - rep["longMs"])
        return r

    KEYS = ("wall", "visibleIn", "routeAt", "apiLast", "apiOwnLast", "apiOwnFirst", "apiShellFirst",
            "apiShellLast", "mainThread", "apiCount", "apiSum", "apiOwnCount", "apiOwnSum",
            "apiShellCount", "apiShellSum", "apiMax", "chunkCount", "chunkSum", "chunkFirstAt",
            "chunkLastAt", "longCount",
            "textLen", "shown")

    def loop(pages_, kind):
        for name, path in pages_:
            if only and not any(o in name for o in only):
                continue
            runs = []
            for i in range(reps):
                try:
                    r = run(name, path, kind)
                except Exception as e:
                    r = {"page": name, "kind": kind, "error": repr(e)[:200]}
                runs.append(r)
                print(kind.upper(), name, i, json.dumps({k: r.get(k) for k in KEYS}, ensure_ascii=False), flush=True)
                print("   detail", json.dumps({"path": r.get("path"), "routeLog": r.get("routeLog"),
                      "apiOwnNames": r.get("apiOwnNames"), "apiSlow": (r.get("apiSlow") or [])[:4],
                      "samples": r.get("samples")}, ensure_ascii=False), flush=True)
                time.sleep(1.5)
            results.setdefault(name, {})[kind] = runs

    if "cold" in phases:
        loop(pages, "cold")

    if "switch" in phases:
        tab.send("Page.navigate", url=BASE + pages[0][1])
        time.sleep(8)
        print("switch base:", pages[0][0], flush=True)
        loop(pages[1:], "switch")

    json.dump(results, open(out_file, "w"), ensure_ascii=False, indent=1)
    print("saved", out_file, flush=True)


main()
