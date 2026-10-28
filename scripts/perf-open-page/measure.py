"""Measure how long pages and core actions take to render in a running 知是 app.

Calibrated measurement harness for Phase 1:
* Distinguishes three cache/navigation modes:
  - `cold` (`true_cold`): fresh BrowserContext per run, Service Worker blocked
    (`service_workers="block"`), HTTP cache disabled via CDP (`Network.setCacheDisabled`),
    seeded only with auth credentials (no warm SPA state or query cache).
    Note: browser cold does not flush server-side or database caches.
  - `warm` (`warm_reload`): same-page reload in a warm context with HTTP cache
    and Service Worker enabled (`service_workers="allow"`).
  - `walk` (`spa_walk`): client-side route transition (`router.push`) from a
    distinct warm origin route inside an already-open SPA shell.
* Preserves every raw sample (`runs`), error, timeout, cancellation, and HTTP
  403/5xx status. Partial failures report exact attempt/failure counts; a
  complete failure (`n_ok == 0`) reports `ready_ms = None` (never 0 ms).
* Separates DOM readiness (`dom_ready_ms`) from actual next-frame paint
  (`paint_ms`, exposed as `ready_ms`), and verifies target route arrival,
  non-stale visible content, absence of skeletons/spinners, and usable key
  interactive controls (`action_selector`).
* Preserves full API request paths with query strings (`GET /path?a=1`), captures
  `X-Request-ID` and `Server-Timing` (`app;dur=...`), only flags true duplicate
  requests (identical method + path + query string), and separates critical-path
  blocking requests (`start_ms <= dom_ready_ms`) from background/idle requests.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pages import PAGES, PageSpec

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page

SERIAL_GAP_MS = 0.0

SIGNIN_URL = "/account/signin"
USER_FIELD = "input[autocomplete='username'], input[name='username'], #username, #signin-username"
PASSWORD_FIELD = "input[type='password'], #signin-password"
SUBMIT_BUTTON = "button[type='submit'], button.account-submit"

MODE_ALIASES = {
    "walk": "walk",
    "spa_walk": "walk",
    "warm": "warm",
    "warm_reload": "warm",
    "cold": "cold",
    "true_cold": "cold",
}

CACHE_POLICIES = {
    "cold": {
        "mode": "cold",
        "canonical_name": "true_cold",
        "browser_context": "isolated_new_context_per_run",
        "http_cache": "disabled_via_cdp",
        "service_worker": "block",
        "storage_state": "auth_tokens_only",
        "server_cache": "unmodified (browser cold != server cold)",
    },
    "warm": {
        "mode": "warm",
        "canonical_name": "warm_reload",
        "browser_context": "warm_context_same_page_reload",
        "http_cache": "enabled",
        "service_worker": "allow",
        "storage_state": "full_warm_storage",
        "server_cache": "warm",
    },
    "walk": {
        "mode": "walk",
        "canonical_name": "spa_walk",
        "browser_context": "warm_spa_client_route_push",
        "http_cache": "enabled",
        "service_worker": "allow",
        "storage_state": "full_warm_spa_memory",
        "server_cache": "warm",
    },
}

LONG_TASK_INIT = """
window.__lt = { total: 0, count: 0, entries: [] };
try {
  if (window.__ltObserver) window.__ltObserver.disconnect();
  window.__ltObserver = new PerformanceObserver((l) => {
    for (const e of l.getEntries()) {
      window.__lt.total += e.duration;
      window.__lt.count += 1;
      window.__lt.entries.push({ start: e.startTime, dur: e.duration });
    }
  });
  window.__ltObserver.observe({ entryTypes: ['longtask'] });
} catch (_e) {}
"""

PROBE_INIT = """
try {
  localStorage.setItem('cheese.tour.done', '1');
  localStorage.setItem('cheese.betaNotice.alice', 'seen');
} catch (_e) {}
window.__probe = {
  t0: 0,
  t1_dom: null,
  t1_paint: null,
  t1: null,
  path: location.pathname,
  beforePath: null,
  navSettled: true,
  navError: null,
  paintScheduled: false,
  outcome: null,
  emptySeen: false,
  errorText: null,
  marks: {}
};
window.__lt = window.__lt || { total: 0, count: 0, entries: [] };

window.__isVisible = (el) => {
  if (!el || !el.isConnected) return false;
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') === 0) {
    return false;
  }
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
};

window.__markStaleDom = () => {
  document.querySelectorAll('#app *').forEach((el) => {
    el.dataset.perfStale = '1';
  });
};

window.__nav = (path) => {
  const app = document.querySelector('#app');
  const router = app && app.__vue_app__ && app.__vue_app__.config.globalProperties.$router;
  if (!router) throw new Error('Vue router instance not found on #app');
  window.__markStaleDom();
  const beforePath = location.pathname + location.search;
  try { performance.clearResourceTimings(); } catch (_e) {}
  window.__lt = { total: 0, count: 0, entries: [] };
  const t0 = performance.now();
  try { performance.mark('perf_op_start'); } catch (_e) {}
  window.__probe = {
    t0,
    t1_dom: null,
    t1_paint: null,
    t1: null,
    path,
    beforePath,
    navSettled: false,
    navError: null,
    paintScheduled: false,
    outcome: null,
    emptySeen: false,
    errorText: null,
    marks: { op_start: t0 }
  };
  Promise.resolve(router.push(path)).then(
    () => { window.__probe.navSettled = true; },
    (err) => { window.__probe.navSettled = true; window.__probe.navError = String(err || 'router.push failed'); }
  );
  return t0;
};

window.__ready = (spec) => {
  const p = window.__probe;
  if (!p) return false;
  if (p.t1 !== null) return true;
  if (!p.navSettled) return false;
  if (p.navError) {
    p.outcome = 'error_state';
    p.errorText = p.navError;
    p.t1_dom = performance.now();
    p.t1_paint = p.t1_dom;
    p.t1 = p.t1_dom;
    return true;
  }
  if (spec.isWalk && p.beforePath && (location.pathname + location.search) === p.beforePath) {
    return false;
  }

  // 1. Check if any loading indicator is still visible.
  if (spec.notSel) {
    const busyEls = Array.from(document.querySelectorAll(spec.notSel));
    if (busyEls.some((el) => !el.dataset.perfStale && window.__isVisible(el))) {
      p.paintScheduled = false;
      p.t1_dom = null;
      return false;
    }
  }

  // 2. Check if content selector is present, visible, non-stale, and meets min_text.
  const candidates = Array.from(document.querySelectorAll(spec.sel)).filter(
    (el) => !el.dataset.perfStale && window.__isVisible(el)
  );
  const combinedText = candidates.map((el) => (el.innerText || '').trim()).join(' ').trim();
  let domSatisfied = combinedText.length >= (spec.min || 1);

  // 3. Check if empty state is visible when content is not yet satisfied.
  if (!domSatisfied && spec.emptySel) {
    const emptyEls = Array.from(document.querySelectorAll(spec.emptySel)).filter(
      (el) =>
        !el.dataset.perfStale &&
        (window.__isVisible(el) ||
          (el.isConnected && el.getBoundingClientRect().width > 0 && el.children.length === 0))
    );
    if (emptyEls.length > 0) {
      p.emptySeen = true;
      if (spec.allowEmpty) {
        domSatisfied = true;
        p.outcome = 'empty';
      }
    }
  }

  // 4. Check explicit error alert when content is absent.
  if (!domSatisfied) {
    const errEls = Array.from(
      document.querySelectorAll('.v-alert--type-error, .library__load-error, [role="alert"].c-danger')
    ).filter((el) => !el.dataset.perfStale && window.__isVisible(el));
    if (errEls.length > 0) {
      p.outcome = 'error_state';
      p.errorText = (errEls[0].innerText || 'error alert visible').trim().slice(0, 160);
      p.t1_dom = performance.now();
      p.t1_paint = p.t1_dom;
      p.t1 = p.t1_dom;
      return true;
    }
    p.paintScheduled = false;
    p.t1_dom = null;
    return false;
  }

  // 5. Check key interactive control usability if specified.
  if (spec.actionSel && p.outcome !== 'empty') {
    const actEls = Array.from(document.querySelectorAll(spec.actionSel)).filter(
      (el) => !el.dataset.perfStale && window.__isVisible(el)
    );
    const usable = actEls.some(
      (el) => !el.disabled && el.getAttribute('aria-disabled') !== 'true'
    );
    if (!usable) {
      p.paintScheduled = false;
      p.t1_dom = null;
      return false;
    }
  }

  // Frame k: DOM is ready. Stamp t1_dom and wait one animation frame so layout
  // and paint of frame k complete before stamping t1_paint (frame k+1).
  if (p.t1_dom === null) {
    p.t1_dom = performance.now();
    p.marks.dom_ready = p.t1_dom;
    try { performance.mark('perf_dom_ready'); } catch (_e) {}
    p.paintScheduled = true;
    return false;
  }

  p.t1_paint = performance.now();
  p.t1 = p.t1_paint;
  p.marks.paint_ready = p.t1_paint;
  if (!p.outcome) p.outcome = 'ready';
  try { performance.mark('perf_paint_ready'); } catch (_e) {}
  return true;
};

window.__collect = () => {
  const p = window.__probe || {};
  const res = performance.getEntriesByType('resource').map((e) => ({
    n: e.name,
    s: e.startTime,
    rs: e.responseStart || e.startTime,
    e: e.responseEnd,
    t: e.initiatorType,
    sz: e.transferSize,
    bsz: e.decodedBodySize || 0
  }));
  const navEntry = performance.getEntriesByType('navigation')[0];
  const paintEntries = performance.getEntriesByType('paint') || [];
  const fcpEntry = paintEntries.find((x) => x.name === 'first-contentful-paint');
  return {
    t0: p.t0 || 0,
    t1_dom: p.t1_dom,
    t1_paint: p.t1_paint,
    t1: p.t1,
    outcome: p.outcome,
    emptySeen: !!p.emptySeen,
    errorText: p.errorText,
    dcl: navEntry ? navEntry.domContentLoadedEventEnd : null,
    fcp: fcpEntry ? fcpEntry.startTime : null,
    res,
    origin: performance.timeOrigin
  };
};
"""


@dataclass
class Sample:
    page: str
    mode: str
    run_index: int = 0
    status: str = "ok"  # ok | empty | timeout | cancelled | forbidden | error
    dom_ready_ms: float | None = None
    paint_ms: float | None = None
    ready_ms: float | None = None
    dcl_ms: float | None = None
    fcp_ms: float | None = None
    last_response_ms: float | None = None
    network_ms: float | None = None
    critical_path_api_ms: float = 0.0
    requests: int = 0
    cached_requests: int = 0
    api_count: int = 0
    api_requests: list[str] = field(default_factory=list)
    duplicate_api_requests: list[str] = field(default_factory=list)
    long_task_ms: float = 0.0
    long_task_count: int = 0
    api_bursts: int = 0
    api_serial_ms: float = 0.0
    blocking_api_bursts: int = 0
    blocking_serial_ms: float = 0.0
    api_timeline: list[list[Any]] = field(default_factory=list)
    blocking_api_timeline: list[dict[str, Any]] = field(default_factory=list)
    background_api_timeline: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self, *, compact_run: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if compact_run:
            d.pop("blocking_api_timeline", None)
            d.pop("background_api_timeline", None)
        return d


def _parse_server_timing(header_val: str | None) -> float | None:
    if not header_val:
        return None
    m = re.search(r"dur=([0-9.]+)", header_val)
    return round(float(m.group(1)), 2) if m else None


def _compute_bursts(intervals: list[tuple[float, float]]) -> tuple[int, float, float]:
    """Return `(burst_count, later_bursts_ms, union_coverage_ms)` for sorted intervals."""
    if not intervals:
        return 0, 0.0, 0.0
    ordered = sorted(intervals)
    bursts: list[list[float]] = []
    for start_at, end_at in ordered:
        if end_at < start_at:
            end_at = start_at
        if bursts and start_at <= bursts[-1][1] + SERIAL_GAP_MS:
            bursts[-1][1] = max(bursts[-1][1], end_at)
        else:
            bursts.append([start_at, end_at])
    serial_ms = sum(end_at - start_at for start_at, end_at in bursts[1:])
    union_ms = sum(end_at - start_at for start_at, end_at in bursts)
    return len(bursts), round(serial_ms, 1), round(union_ms, 1)


def _quantiles(values: list[float]) -> dict[str, Any]:
    """Compute min, p50, p75, p95, max using inclusive linear interpolation."""
    clean = sorted(v for v in values if v is not None and not math.isnan(v))
    if not clean:
        return {}
    n = len(clean)
    p50 = round(statistics.median(clean), 1)
    if n == 1:
        p75 = clean[0]
        p95 = clean[0]
    else:
        qs = statistics.quantiles(clean, n=100, method="inclusive")
        p75 = round(qs[74], 1)
        p95 = round(qs[94], 1)
    return {
        "n": n,
        "min_ms": round(clean[0], 1),
        "p50_ms": p50,
        "p75_ms": p75,
        "p95_ms": p95,
        "max_ms": round(clean[-1], 1),
        "range_ms": [round(clean[0], 1), round(clean[-1], 1)],
        "small_sample": n < 5,
    }


def _attach_api_observer(page: Page):
    """Record HTTP method, status, X-Request-ID and Server-Timing for every /api/ call."""
    records: list[dict[str, Any]] = []

    def on_response(resp) -> None:
        try:
            url = resp.url
            if "/api/" not in url:
                return
            rel = url.split("/api/", 1)[1]
            headers = resp.headers
            records.append(
                {
                    "method": resp.request.method,
                    "path": rel,
                    "status": resp.status,
                    "request_id": headers.get("x-request-id"),
                    "server_timing_ms": _parse_server_timing(headers.get("server-timing")),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def on_request_failed(req) -> None:
        try:
            url = req.url
            if "/api/" not in url:
                return
            rel = url.split("/api/", 1)[1]
            failure = str(req.failure) if req.failure else "request_failed"
            aborted = "ERR_ABORTED" in failure or "NS_BINDING_ABORTED" in failure or "canceled" in failure.lower()
            records.append(
                {
                    "method": req.method,
                    "path": rel,
                    "status": 0,
                    "request_id": None,
                    "server_timing_ms": None,
                    "failure": failure,
                    "aborted": aborted,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    ctx = page.context
    ctx.on("response", on_response)
    ctx.on("requestfailed", on_request_failed)

    def detach() -> None:
        try:
            ctx.remove_listener("response", on_response)
            ctx.remove_listener("requestfailed", on_request_failed)
        except Exception:  # noqa: BLE001
            pass

    return records, detach


def _login_and_extract_auth(
    page: Page, base_url: str, username: str, password: str
) -> dict[str, str]:
    page.goto(base_url.rstrip("/") + SIGNIN_URL, wait_until="domcontentloaded")
    page.wait_for_function(
        """() => window.location.pathname !== '/account/signin'
                 || !!document.querySelector("input[name='username'], input[autocomplete='username'], #username, #signin-username")""",
        timeout=60000,
    )
    if page.url.split("?")[0].endswith(SIGNIN_URL):
        page.fill(USER_FIELD, username)
        page.fill(PASSWORD_FIELD, password)
        page.click(SUBMIT_BUTTON)
        page.wait_for_function(
            "() => window.location.pathname !== '/account/signin'", timeout=30000
        )
    page.wait_for_load_state("networkidle", timeout=30000)
    # If the demo user has not yet accepted the current legal terms, accept them
    # via the API once at sign-in so the modal scrim never blocks page interactions.
    page.evaluate(
        """async () => {
          try {
            const token = localStorage.getItem('accessToken') || localStorage.getItem('token');
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = `Bearer ${token}`;
            const r = await fetch('/api/users/me/consents', { headers });
            if (r.ok) {
              const body = await r.json();
              const pending = (body.data && body.data.pending) || body.pending || [];
              if (pending.length > 0) {
                const documents = Object.fromEntries(pending.map((d) => [d.document, d.version]));
                await fetch('/api/users/me/consents', {
                  method: 'POST',
                  headers,
                  body: JSON.stringify({ documents })
                });
              }
            }
          } catch (_e) {}
        }"""
    )
    try:
        alert_btns = page.locator("[role='alertdialog'] button")
        if alert_btns.count() >= 2:
            alert_btns.last.click(timeout=3000)
            page.wait_for_function(
                "() => !document.querySelector('.v-overlay--active')", timeout=10000
            )
    except Exception:  # noqa: BLE001
        pass
    # Extract only authentication keys from localStorage so cold runs do not
    # inherit cached query client data or UI state.
    auth_storage = page.evaluate(
        """() => {
          const out = {};
          for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (!k) continue;
            if (/token|auth|user|session|tour|betaNotice/i.test(k) && !/query|cache/i.test(k)) {
              out[k] = localStorage.getItem(k);
            }
          }
          return out;
        }"""
    )
    return auth_storage


def _auth_init_script(auth_storage: dict[str, str]) -> str:
    serialized = json.dumps(auth_storage, ensure_ascii=False)
    return f"""
    (() => {{
      const items = {serialized};
      try {{
        for (const [k, v] of Object.entries(items)) {{
          if (v !== null && v !== undefined) localStorage.setItem(k, v);
        }}
      }} catch (_e) {{}}
    }})();
    """


def _distinct_warm_origin(base_url: str, spec: PageSpec, params: dict[str, object]) -> str:
    """Pick a warm starting URL that is guaranteed not to be the target route itself."""
    target = spec.route_path(params).rstrip("/") or "/"
    if target == "/inbox":
        return base_url.rstrip("/") + "/users/settings/notifications"
    return base_url.rstrip("/") + "/inbox"


def _measure_send_message(
    page: Page,
    spec: PageSpec,
    base_url: str,
    mode: str,
    params: dict[str, object],
    run_index: int,
    timeout_ms: int,
) -> Sample:
    """Measure typing a message in the channel composer, local feedback, and server ack."""
    api_obs, detach = _attach_api_observer(page)
    try:
        return _measure_send_message_inner(
            page, spec, base_url, mode, params, run_index, timeout_ms, api_obs
        )
    finally:
        detach()


def _measure_send_message_inner(
    page: Page,
    spec: PageSpec,
    base_url: str,
    mode: str,
    params: dict[str, object],
    run_index: int,
    timeout_ms: int,
    api_obs: list[dict[str, Any]],
) -> Sample:
    page.goto(spec.path(base_url, params), wait_until="domcontentloaded")
    page.wait_for_function(
        """(sel) => {
          const el = document.querySelector(sel);
          return el && el.getBoundingClientRect().width > 0 && !document.querySelector('.skel--chat');
        }""",
        arg=spec.action_selector or ".composer-editor__input[role='textbox']",
        timeout=timeout_ms,
    )
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:  # noqa: BLE001
        pass

    try:
        alert_btns = page.locator("[role='alertdialog'] button")
        if alert_btns.count() >= 2:
            alert_btns.last.click(timeout=3000)
            page.wait_for_function(
                "() => !document.querySelector('.v-overlay--active')", timeout=10000
            )
    except Exception:  # noqa: BLE001
        pass

    api_obs.clear()
    probe_token = f"perf-send-{uuid.uuid4().hex[:8]}"
    input_sel = spec.action_selector or ".composer-editor__input[role='textbox']"
    page.click(input_sel)
    page.keyboard.type(probe_token)

    page.evaluate(
        """(token) => {
          try { performance.clearResourceTimings(); } catch (_e) {}
          window.__lt = { total: 0, count: 0, entries: [] };
          const t0 = performance.now();
          window.__sendProbe = {
            token,
            t0,
            t_local: null,
            t_ack_dom: null,
            t_ack_paint: null
          };
        }""",
        probe_token,
    )
    # Trigger send via Enter (or clicking .composer-send).
    send_btn = page.locator(".composer-send:not([disabled])")
    if send_btn.count() > 0:
        send_btn.first.click()
    else:
        page.keyboard.press("Enter")

    try:
        page.wait_for_function(
            """() => {
              const p = window.__sendProbe;
              if (!p) return false;
              const rows = Array.from(document.querySelectorAll('.im-row'));
              const matching = rows.filter((r) => (r.innerText || '').includes(p.token));
              if (p.t_local === null && matching.length > 0) {
                p.t_local = performance.now();
              }
              const committed = matching.find(
                (r) => r.getAttribute('data-row-id') && !r.getAttribute('data-cid')
              );
              if (!committed) return false;
              if (p.t_ack_dom === null) {
                p.t_ack_dom = performance.now();
                if (p.t_local === null) p.t_local = p.t_ack_dom;
                return false;
              }
              p.t_ack_paint = performance.now();
              return true;
            }""",
            timeout=timeout_ms,
            polling="raf",
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:200]
        status = "timeout" if "Timeout" in msg else "error"
        return Sample(
            page=spec.name,
            mode=mode,
            run_index=run_index,
            status=status,
            error=msg,
        )

    res_data = page.evaluate(
        """() => {
          const p = window.__sendProbe;
          const res = performance.getEntriesByType('resource').map((e) => ({
            n: e.name,
            s: e.startTime,
            rs: e.responseStart || e.startTime,
            e: e.responseEnd,
            t: e.initiatorType,
            sz: e.transferSize
          }));
          return {
            t0: p.t0,
            t_local: p.t_local,
            t_ack_dom: p.t_ack_dom,
            t_ack_paint: p.t_ack_paint,
            res,
            lt: window.__lt || { total: 0, count: 0 }
          };
        }"""
    )
    base = res_data["t0"]
    dom_ready = round(res_data["t_local"] - base, 1)
    paint_ready = round(res_data["t_ack_paint"] - base, 1)
    return _build_sample_from_timings(
        spec=spec,
        mode=mode,
        run_index=run_index,
        status="ok",
        base=base,
        dom_ready=dom_ready,
        paint_ready=paint_ready,
        dcl_ms=None,
        fcp_ms=None,
        raw_res=res_data["res"],
        lt=res_data["lt"],
        api_obs=api_obs,
        error=None,
    )


def _build_sample_from_timings(
    *,
    spec: PageSpec,
    mode: str,
    run_index: int,
    status: str,
    base: float,
    dom_ready: float | None,
    paint_ready: float | None,
    dcl_ms: float | None,
    fcp_ms: float | None,
    raw_res: list[dict[str, Any]],
    lt: dict[str, Any],
    api_obs: list[dict[str, Any]],
    error: str | None,
) -> Sample:
    res = [r for r in raw_res if r["s"] >= base - 1.0]
    api = [r for r in res if "/api/" in r["n"]]
    ends = [r["e"] - base for r in res if r["e"] >= base]
    starts = [r["s"] - base for r in res]
    last = round(max(ends), 1) if ends else None
    network = round(last - min(starts), 1) if (last is not None and starts) else None
    cached_count = sum(1 for r in res if r.get("sz", 0) == 0 and r.get("bsz", 0) > 0)

    # Match resource timing entries with Playwright response headers (method, status,
    # X-Request-ID, Server-Timing).
    obs_by_path: dict[str, list[dict[str, Any]]] = {}
    for item in api_obs:
        obs_by_path.setdefault(item["path"], []).append(item)

    api_requests_list: list[str] = []
    api_timeline: list[list[Any]] = []
    blocking_timeline: list[dict[str, Any]] = []
    background_timeline: list[dict[str, Any]] = []

    cutoff = dom_ready if dom_ready is not None else (paint_ready or float("inf"))

    for r in sorted(api, key=lambda x: x["s"]):
        rel_path = r["n"].split("/api/", 1)[-1]
        matched = obs_by_path.get(rel_path, [])
        meta = matched.pop(0) if matched else {}
        method = meta.get("method", "GET")
        http_status = meta.get("status", 200)
        req_id = meta.get("request_id")
        st_ms = meta.get("server_timing_ms")

        s_ms = round(max(0.0, r["s"] - base), 1)
        ttfb_ms = round(max(s_ms, r.get("rs", r["s"]) - base), 1)
        e_ms = round(max(s_ms, r["e"] - base), 1)
        dur_ms = round(e_ms - s_ms, 1)

        req_label = f"{method} {rel_path}"
        api_requests_list.append(req_label)
        api_timeline.append([rel_path, s_ms, e_ms, method, http_status, req_id, st_ms])

        entry_detail = {
            "request": req_label,
            "path": rel_path,
            "method": method,
            "status": http_status,
            "start_ms": s_ms,
            "ttfb_ms": ttfb_ms,
            "end_ms": e_ms,
            "duration_ms": dur_ms,
            "request_id": req_id,
            "server_timing_app_ms": st_ms,
        }
        # Only requests that started before DOM ready can be on the blocking path to DOM ready.
        if s_ms <= cutoff and e_ms <= (paint_ready or cutoff) + 5.0:
            blocking_timeline.append(entry_detail)
        else:
            background_timeline.append(entry_detail)

    counts = Counter(api_requests_list)
    duplicates = sorted(k for k, v in counts.items() if v > 1)

    all_intervals = [(max(0.0, r["s"] - base), max(0.0, r["e"] - base)) for r in api]
    api_bursts, api_serial_ms, _ = _compute_bursts(all_intervals)

    blocking_intervals = [(x["start_ms"], min(x["end_ms"], cutoff)) for x in blocking_timeline]
    blocking_bursts, blocking_serial_ms, critical_path_api_ms = _compute_bursts(blocking_intervals)

    return Sample(
        page=spec.name,
        mode=mode,
        run_index=run_index,
        status=status,
        dom_ready_ms=dom_ready,
        paint_ms=paint_ready,
        ready_ms=paint_ready,
        dcl_ms=round(dcl_ms, 1) if dcl_ms is not None else None,
        fcp_ms=round(fcp_ms, 1) if fcp_ms is not None else None,
        last_response_ms=last,
        network_ms=network,
        critical_path_api_ms=critical_path_api_ms,
        requests=len(res),
        cached_requests=cached_count,
        api_count=len(api),
        api_requests=api_requests_list,
        duplicate_api_requests=duplicates,
        long_task_ms=round(lt.get("total", 0.0), 1),
        long_task_count=int(lt.get("count", 0)),
        api_bursts=api_bursts,
        api_serial_ms=api_serial_ms,
        blocking_api_bursts=blocking_bursts,
        blocking_serial_ms=blocking_serial_ms,
        api_timeline=api_timeline,
        blocking_api_timeline=blocking_timeline,
        background_api_timeline=background_timeline,
        error=error,
    )


def _measure_one(
    page: Page,
    spec: PageSpec,
    base_url: str,
    mode: str,
    params: dict[str, object],
    run_index: int = 0,
    timeout_ms: int = 30000,
) -> Sample:
    if spec.kind == "action:send_message":
        return _measure_send_message(page, spec, base_url, mode, params, run_index, timeout_ms)

    api_obs, detach = _attach_api_observer(page)
    try:
        return _measure_page_inner(
            page, spec, base_url, mode, params, run_index, timeout_ms, api_obs
        )
    finally:
        detach()


def _measure_page_inner(
    page: Page,
    spec: PageSpec,
    base_url: str,
    mode: str,
    params: dict[str, object],
    run_index: int,
    timeout_ms: int,
    api_obs: list[dict[str, Any]],
) -> Sample:
    if mode == "walk":
        warm_origin = _distinct_warm_origin(base_url, spec, params)
        page.goto(warm_origin, wait_until="networkidle")
        page.wait_for_timeout(150)
        page.evaluate(LONG_TASK_INIT)
        page.evaluate(PROBE_INIT)
        api_obs.clear()
        page.evaluate("(p) => window.__nav(p)", spec.route_path(params))
    elif mode == "warm":
        # First visit populates HTTP cache, Service Worker and IndexedDB; then reload.
        target_url = spec.path(base_url, params)
        page.goto(target_url, wait_until="networkidle")
        page.wait_for_timeout(150)
        api_obs.clear()
        page.reload(wait_until="commit")
    else:
        # true_cold: fresh context with cache disabled and SW blocked.
        api_obs.clear()
        page.goto(spec.path(base_url, params), wait_until="commit")

    spec_payload = {
        "sel": spec.ready_selector,
        "min": spec.min_text,
        "notSel": spec.not_ready_selector,
        "actionSel": spec.action_selector,
        "emptySel": spec.empty_selector,
        "allowEmpty": spec.allow_empty,
        "isWalk": mode == "walk",
    }

    timed_out_exc: Exception | None = None
    try:
        page.wait_for_function(
            "(spec) => window.__ready(spec)",
            arg=spec_payload,
            timeout=timeout_ms,
            polling="raf",
        )
    except Exception as exc:  # noqa: BLE001
        timed_out_exc = exc

    try:
        probe = page.evaluate("() => window.__collect ? window.__collect() : null")
        lt = page.evaluate("() => window.__lt || { total: 0, count: 0 }")
    except Exception as exc:  # noqa: BLE001
        msg = str(timed_out_exc or exc)[:200]
        return Sample(
            page=spec.name,
            mode=mode,
            run_index=run_index,
            status="cancelled" if "closed" in msg.lower() or "target" in msg.lower() else "error",
            error=msg,
        )

    # Check if any non-aborted API response returned 403 or 5xx/network failure.
    forbidden_obs = [x for x in api_obs if x.get("status") == 403]
    server_err_obs = [
        x
        for x in api_obs
        if (x.get("status") or 0) >= 500
        or (x.get("status") == 0 and not x.get("aborted"))
    ]

    if timed_out_exc is not None or not probe or probe.get("t1") is None:
        raw_msg = str(timed_out_exc)[:200] if timed_out_exc else "probe did not complete"
        if forbidden_obs:
            status = "forbidden"
            err_msg = f"HTTP 403 on {forbidden_obs[0]['method']} /api/{forbidden_obs[0]['path']}"
        elif server_err_obs:
            status = "error"
            first_err = server_err_obs[0]
            err_msg = (
                f"HTTP {first_err.get('status')} ({first_err.get('failure', 'server error')}) "
                f"on {first_err['method']} /api/{first_err['path']}"
            )
        elif probe and probe.get("emptySeen") and not spec.allow_empty:
            status = "empty"
            err_msg = f"empty state ({spec.empty_selector}) rendered instead of expected content ({spec.ready_selector})"
        elif "closed" in raw_msg.lower() or "canceled" in raw_msg.lower() or "cancelled" in raw_msg.lower():
            status = "cancelled"
            err_msg = raw_msg
        elif "Timeout" in raw_msg:
            status = "timeout"
            err_msg = raw_msg
        else:
            status = "error"
            err_msg = raw_msg
        return _build_sample_from_timings(
            spec=spec,
            mode=mode,
            run_index=run_index,
            status=status,
            base=(probe.get("t0", 0.0) if probe and mode == "walk" else 0.0),
            dom_ready=None,
            paint_ready=None,
            dcl_ms=probe.get("dcl") if probe else None,
            fcp_ms=probe.get("fcp") if probe else None,
            raw_res=probe.get("res", []) if probe else [],
            lt=lt,
            api_obs=api_obs,
            error=err_msg,
        )

    outcome = probe.get("outcome") or "ready"
    t0 = probe["t0"] if mode == "walk" else 0.0
    t1_dom = probe.get("t1_dom")
    t1_paint = probe.get("t1_paint") or probe.get("t1")

    if outcome == "error_state":
        status = "forbidden" if forbidden_obs else "error"
        err_msg = (
            f"HTTP 403 on /api/{forbidden_obs[0]['path']}"
            if forbidden_obs
            else (
                f"HTTP {server_err_obs[0].get('status')} on /api/{server_err_obs[0]['path']}"
                if server_err_obs
                else (probe.get("errorText") or "error state")
            )
        )
        return _build_sample_from_timings(
            spec=spec,
            mode=mode,
            run_index=run_index,
            status=status,
            base=t0,
            dom_ready=None,
            paint_ready=None,
            dcl_ms=probe.get("dcl"),
            fcp_ms=probe.get("fcp"),
            raw_res=probe.get("res", []),
            lt=lt,
            api_obs=api_obs,
            error=err_msg,
        )

    dom_ready = round(t1_dom - t0, 1) if t1_dom is not None else None
    paint_ready = round(t1_paint - t0, 1) if t1_paint is not None else None
    sample_status = "empty" if outcome == "empty" else "ok"

    return _build_sample_from_timings(
        spec=spec,
        mode=mode,
        run_index=run_index,
        status=sample_status,
        base=t0,
        dom_ready=dom_ready,
        paint_ready=paint_ready,
        dcl_ms=probe.get("dcl") if mode != "walk" else None,
        fcp_ms=probe.get("fcp") if mode != "walk" else None,
        raw_res=probe.get("res", []),
        lt=lt,
        api_obs=api_obs,
        error=None,
    )


def summarize_samples(page_name: str, mode: str, samples: list[Sample]) -> dict[str, Any]:
    """Aggregate raw attempts while preserving every attempt, failure count, and quantile."""
    n_attempts = len(samples)
    ok = [
        s
        for s in samples
        if s.status in ("ok", "empty")
        and s.error is None
        and s.ready_ms is not None
        and not math.isnan(s.ready_ms)
    ]
    n_ok = len(ok)
    n_failed = n_attempts - n_ok
    failure_counts = dict(Counter(s.status for s in samples if s not in ok))
    errors = [f"run#{s.run_index}[{s.status}]: {s.error}" for s in samples if s.error]

    if not ok:
        # All attempts failed: never report 0 ms!
        first = samples[0] if samples else Sample(page=page_name, mode=mode)
        return {
            "page": page_name,
            "mode": mode,
            "cache_policy": CACHE_POLICIES.get(mode, {}),
            "status": first.status if samples else "error",
            "n_attempts": n_attempts,
            "n_ok": 0,
            "n_failed": n_failed,
            "success_rate": 0.0,
            "failure_breakdown": failure_counts,
            "dom_ready_ms": None,
            "paint_ms": None,
            "ready_ms": None,
            "last_response_ms": None,
            "network_ms": None,
            "critical_path_api_ms": None,
            "requests": first.requests,
            "cached_requests": first.cached_requests,
            "api_count": first.api_count,
            "api_requests": first.api_requests,
            "duplicate_api_requests": first.duplicate_api_requests,
            "long_task_ms": first.long_task_ms,
            "long_task_count": first.long_task_count,
            "api_bursts": first.api_bursts,
            "api_serial_ms": first.api_serial_ms,
            "blocking_api_bursts": first.blocking_api_bursts,
            "blocking_serial_ms": first.blocking_serial_ms,
            "api_timeline": first.api_timeline,
            "blocking_api_timeline": first.blocking_api_timeline,
            "background_api_timeline": first.background_api_timeline,
            "stats": None,
            "dom_ready_stats": None,
            "error": "; ".join(errors)[:240] if errors else "all attempts failed",
            "runs": [s.to_dict(compact_run=True) for s in samples],
        }

    pick = statistics.median
    ready_vals = [s.ready_ms for s in ok if s.ready_ms is not None]
    dom_vals = [s.dom_ready_ms for s in ok if s.dom_ready_ms is not None]
    last_vals = [s.last_response_ms for s in ok if s.last_response_ms is not None]
    net_vals = [s.network_ms for s in ok if s.network_ms is not None]
    crit_vals = [s.critical_path_api_ms for s in ok]

    med_ready = round(pick(ready_vals), 1)
    med_dom = round(pick(dom_vals), 1) if dom_vals else med_ready
    rep = min(ok, key=lambda s: abs((s.ready_ms or 0.0) - med_ready))

    return {
        "page": page_name,
        "mode": mode,
        "cache_policy": CACHE_POLICIES.get(mode, {}),
        "status": "partial_failure" if n_failed > 0 else rep.status,
        "n_attempts": n_attempts,
        "n_ok": n_ok,
        "n_failed": n_failed,
        "success_rate": round(n_ok / n_attempts, 4) if n_attempts else 0.0,
        "failure_breakdown": failure_counts,
        "dom_ready_ms": med_dom,
        "paint_ms": med_ready,
        "ready_ms": med_ready,
        "last_response_ms": round(pick(last_vals), 1) if last_vals else None,
        "network_ms": round(pick(net_vals), 1) if net_vals else None,
        "critical_path_api_ms": round(pick(crit_vals), 1) if crit_vals else 0.0,
        "requests": int(pick([s.requests for s in ok])),
        "cached_requests": int(pick([s.cached_requests for s in ok])),
        "api_count": int(pick([s.api_count for s in ok])),
        "api_requests": rep.api_requests,
        "duplicate_api_requests": rep.duplicate_api_requests,
        "long_task_ms": round(pick([s.long_task_ms for s in ok]), 1),
        "long_task_count": int(pick([s.long_task_count for s in ok])),
        "api_bursts": int(pick([s.api_bursts for s in ok])),
        "api_serial_ms": round(pick([s.api_serial_ms for s in ok]), 1),
        "blocking_api_bursts": int(pick([s.blocking_api_bursts for s in ok])),
        "blocking_serial_ms": round(pick([s.blocking_serial_ms for s in ok]), 1),
        "api_timeline": rep.api_timeline,
        "blocking_api_timeline": rep.blocking_api_timeline,
        "background_api_timeline": rep.background_api_timeline,
        "stats": _quantiles(ready_vals),
        "dom_ready_stats": _quantiles(dom_vals),
        "error": (f"{n_failed}/{n_attempts} failed: " + "; ".join(errors))[:240] if errors else None,
        "runs": [s.to_dict(compact_run=True) for s in samples],
    }


def _placeholders(route: str) -> set[str]:
    return {m for m in re.findall(r"\{(\w+)\}", route)}


def _detect_git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent, text=True, timeout=5
        ).strip()
        return out or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _detect_running_version(base_url: str) -> str:
    for path in ("/api/health", "/health"):
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ver = (data.get("data") or {}).get("version") or data.get("version")
                if ver:
                    return str(ver)
        except Exception:  # noqa: BLE001
            continue
    return "unknown"


def _launch_browser(pw) -> Browser:
    launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    try:
        return pw.chromium.launch(args=launch_args)
    except Exception:  # noqa: BLE001
        for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
            if shutil.which(candidate) or Path(candidate).is_file():
                return pw.chromium.launch(executable_path=candidate, args=launch_args)
        raise


def _open_true_cold_context(
    browser: Browser, auth_storage: dict[str, str]
) -> tuple[BrowserContext, Page]:
    """Create an isolated context with Service Worker blocked and HTTP cache disabled."""
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 900},
        service_workers="block",
    )
    ctx.add_init_script(_auth_init_script(auth_storage) + "\n" + PROBE_INIT + "\n" + LONG_TASK_INIT)
    fresh = ctx.new_page()
    try:
        cdp = ctx.new_cdp_session(fresh)
        cdp.send("Network.enable")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    except Exception:  # noqa: BLE001
        pass
    return ctx, fresh


def _collect_meta(
    *,
    base_url: str,
    browser_version: str,
    modes: list[str],
    runs: int,
    params: dict[str, object],
    started_at: str,
    finished_at: str | None = None,
    duration_s: float | None = None,
) -> dict[str, Any]:
    return {
        "build_version": _detect_git_sha(),
        "running_version": _detect_running_version(base_url),
        "browser": f"chromium {browser_version}",
        "device": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count() or "unknown",
            "viewport": {"width": 1440, "height": 900},
        },
        "network": {
            "base_url": base_url,
            "throttling": "none (local loopback / direct)",
        },
        "dataset": {
            "source": "params.json (seed_demo.py)",
            "project_id": params.get("projectId", "unknown"),
            "topic_id": params.get("topicId", "unknown"),
            "task_id": params.get("taskId", "unknown"),
            "doc_id": params.get("docId", "unknown"),
        },
        "cache_policies": {m: CACHE_POLICIES.get(m, {"mode": m}) for m in modes},
        "runs_per_mode": runs,
        "time_window": {
            "started_at": started_at,
            "finished_at": finished_at or "in_progress",
            "duration_s": round(duration_s, 2) if duration_s is not None else None,
        },
        "quantile_method": (
            "inclusive linear interpolation (statistics.quantiles method='inclusive'); "
            "when n_ok < 5, report p50 and [min, max] range instead of relying on p95"
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:3000")
    ap.add_argument("--username", default="alice")
    ap.add_argument("--password", default="demo12345")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument(
        "--params",
        default=str(Path(__file__).with_name("params.json")),
        help="ids the route templates are filled from (see README.md)",
    )
    ap.add_argument("--out", default="perf-open-page.json")
    ap.add_argument(
        "--max-seconds",
        type=float,
        default=2400,
        help="stop starting new pages after this long, so a long run still reports",
    )
    ap.add_argument("--only", default="", help="comma-separated page names")
    ap.add_argument("--modes", default="walk,warm,cold", help="walk,warm,cold")
    ap.add_argument("--timeout-ms", type=int, default=30000)
    args = ap.parse_args()

    wanted = [n.strip() for n in args.only.split(",") if n.strip()]
    specs = [p for p in PAGES if not wanted or p.name in wanted]
    raw_modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    modes: list[str] = []
    for m in raw_modes:
        if m not in MODE_ALIASES:
            raise SystemExit(f"Unknown mode {m!r}; choose from {', '.join(sorted(MODE_ALIASES))}")
        canon = MODE_ALIASES[m]
        if canon not in modes:
            modes.append(canon)

    params: dict[str, object] = json.loads(Path(args.params).read_text())
    missing = sorted({p for s in specs for p in _placeholders(s.route) if not params.get(p)})
    if missing:
        raise SystemExit(f"{args.params} is missing required placeholder(s): {', '.join(missing)}")

    boot = time.time()
    started_iso = datetime.now(UTC).isoformat()
    results: dict[str, Any] = {}

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        browser_version = browser.version
        warm_context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            service_workers="allow",
        )
        warm_context.add_init_script(PROBE_INIT + "\n" + LONG_TASK_INIT)
        page = warm_context.new_page()
        page.on("pageerror", lambda e: print("[pageerror]", str(e)[:200], flush=True))
        auth_storage = _login_and_extract_auth(page, args.base_url, args.username, args.password)

        results["_meta"] = _collect_meta(
            base_url=args.base_url,
            browser_version=browser_version,
            modes=modes,
            runs=args.runs,
            params=params,
            started_at=started_iso,
        )
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2))

        for spec in specs:
            if time.time() - boot > args.max_seconds:
                print(f"[stopping] out of time before {spec.name}", flush=True)
                break
            for mode in modes:
                samples: list[Sample] = []
                for i in range(args.runs):
                    if mode in ("walk", "warm"):
                        try:
                            samples.append(
                                _measure_one(
                                    page,
                                    spec,
                                    args.base_url,
                                    mode,
                                    params,
                                    run_index=i + 1,
                                    timeout_ms=args.timeout_ms,
                                )
                            )
                        except Exception as exc:  # noqa: BLE001
                            samples.append(
                                Sample(
                                    page=spec.name,
                                    mode=mode,
                                    run_index=i + 1,
                                    status="error",
                                    error=str(exc)[:200],
                                )
                            )
                    else:
                        cold_ctx, fresh = _open_true_cold_context(browser, auth_storage)
                        fresh.on("pageerror", lambda e: print("[pageerror]", str(e)[:200], flush=True))
                        try:
                            samples.append(
                                _measure_one(
                                    fresh,
                                    spec,
                                    args.base_url,
                                    mode,
                                    params,
                                    run_index=i + 1,
                                    timeout_ms=args.timeout_ms,
                                )
                            )
                        except Exception as exc:  # noqa: BLE001
                            samples.append(
                                Sample(
                                    page=spec.name,
                                    mode=mode,
                                    run_index=i + 1,
                                    status="error",
                                    error=str(exc)[:200],
                                )
                            )
                        finally:
                            cold_ctx.close()

                time.sleep(0.2)
                summary = summarize_samples(spec.name, mode, samples)
                results.setdefault(spec.name, {})[mode] = summary
                ready_str = f"{summary['ready_ms']:>7.1f} ms" if summary["ready_ms"] is not None else "   FAIL   "
                dom_str = f"{summary['dom_ready_ms']:>7.1f} ms" if summary["dom_ready_ms"] is not None else "   —      "
                print(
                    f"{spec.name:24s} {mode:5s} ok={summary['n_ok']}/{summary['n_attempts']}  "
                    f"dom={dom_str}  paint={ready_str}  "
                    f"crit_api={summary['critical_path_api_ms']}  api={summary['api_count']}  "
                    f"lt={summary['long_task_ms']}  "
                    f"{'ERROR ' + (summary['error'] or '') if summary['error'] else ''}",
                    flush=True,
                )
                results["_meta"] = _collect_meta(
                    base_url=args.base_url,
                    browser_version=browser_version,
                    modes=modes,
                    runs=args.runs,
                    params=params,
                    started_at=started_iso,
                    finished_at=datetime.now(UTC).isoformat(),
                    duration_s=time.time() - boot,
                )
                Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2))

        warm_context.close()
        browser.close()

    results["_meta"] = _collect_meta(
        base_url=args.base_url,
        browser_version=browser_version,
        modes=modes,
        runs=args.runs,
        params=params,
        started_at=started_iso,
        finished_at=datetime.now(UTC).isoformat(),
        duration_s=time.time() - boot,
    )
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
