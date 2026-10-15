"""Interleave unchanged base/head artifacts; keep raw samples after each open.

The original ready selector and timing definitions come from the saved harness.
Only local probe fixes are applied, not changes to the product or CI artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import types
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

LONG_TASK_RESET = """
if (window.__ltObserver) window.__ltObserver.disconnect();
window.__lt = { total: 0, count: 0, entries: [] };
window.__ltObserver = new PerformanceObserver((l) => {
  for (const e of l.getEntries()) {
    window.__lt.total += e.duration;
    window.__lt.count += 1;
    window.__lt.entries.push({start: e.startTime, duration: e.duration});
  }
});
window.__ltObserver.observe({ entryTypes: ['longtask'] });
"""


def load_harness(directory: Path):
    spec = importlib.util.spec_from_file_location('pages', directory / 'pages.py')
    pages = importlib.util.module_from_spec(spec)
    sys.modules['pages'] = pages
    spec.loader.exec_module(pages)
    source = (directory / 'measure.py').read_text()
    replacements = [
        ('if "/api/" in r["n"]]', 'if "/api/" in r["n"] or r["t"] == "xmlhttprequest"]'),
        ('r["n"].split("/api/", 1)[1]', 'r["n"].split("/api/", 1)[-1]'),
        ('        page.evaluate("(p) => window.__nav(p)", spec.route_path(params))',
         '        page._measure_phase = "measured"\n        page.evaluate("(p) => window.__nav(p)", spec.route_path(params))'),
    ]
    for old, new in replacements:
        if source.count(old) != 1:
            raise RuntimeError(f'harness replacement must match once: {old}')
        source = source.replace(old, new)
    measure = types.ModuleType('perf_saved_measure')
    sys.modules[measure.__name__] = measure
    exec(compile(source, str(directory / 'measure.py'), 'exec'), measure.__dict__)
    measure.LONG_TASK_INIT = LONG_TASK_RESET
    return pages, measure


def persist(path: Path, payload: dict):
    temporary = path.with_suffix('.partial')
    with temporary.open('w') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--harness', type=Path, required=True)
    ap.add_argument('--params', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--cold-pairs', type=int, default=8)
    ap.add_argument('--walk-pairs', type=int, default=4)
    ap.add_argument('--only', default='')
    args = ap.parse_args()
    for count in [args.cold_pairs, args.walk_pairs]:
        if count < 0 or count % 2:
            ap.error('pair counts must be nonnegative and even for balanced order')
    pages, measure = load_harness(args.harness)
    params = json.loads(args.params.read_text())
    wanted = args.only.split(',') if args.only else []
    specs = [p for p in pages.PAGES if not wanted or p.name in wanted]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    urls = {'base': 'http://127.0.0.1:3000', 'head': 'http://127.0.0.1:3001'}
    launch_args = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
    payload = {'metadata': {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'run_id': 38582677052, 'pr': 3724,
        'base_ref': 'e1dc25047d3597414b301a77b30526f153163eff',
        'head_ref': '433848defa1c466582ff58473dca45842dac73dc',
        'viewport': {'width': 1440, 'height': 900}, 'locale': 'en-US',
        'service_workers': 'block', 'launch_args': launch_args,
        'cold_pairs': args.cold_pairs, 'walk_pairs': args.walk_pairs,
        'order': 'base first iff (pair_index + page_index) % 2 == 0',
        'cold': 'fresh context with login storage; cold HTTP cache, not first-ever storage',
        'walk': 'fresh context; warm logged-in home, networkidle + 300 ms, then real router push',
        'probe_fixes': ['legacy XHR API collection', 'disconnect old long-task observer on reset',
                        'separate warm-up failures from measured failures'],
        'harness_sha256': {name: hashlib.sha256((args.harness / name).read_bytes()).hexdigest()
                           for name in ['measure.py', 'pages.py']},
        'params_sha256': hashlib.sha256(args.params.read_bytes()).hexdigest(),
        'backend': 'shared local recovered snapshot; PostgreSQL 17.11.0, fakeredis 2.39.0',
        'metric_limit': 'later API bursts are not proven blocking; long tasks sampled just after ready',
    }, 'samples': []}
    if args.out.exists():
        raise SystemExit(f'will not overwrite existing evidence: {args.out}')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=launch_args)
        payload['metadata']['browser_version'] = browser.version
        states, homes = {}, {}
        for variant, url in urls.items():
            context = browser.new_context(viewport=payload['metadata']['viewport'], locale='en-US', service_workers='block')
            page = context.new_page()
            measure._login(page, url, 'alice', 'demo12345')
            agree = page.get_by_role('button', name='Agree and continue', exact=True)
            if agree.count() and agree.is_visible():
                agree.click()
                agree.wait_for(state='hidden')
                page.wait_for_load_state('networkidle')
            states[variant] = context.storage_state()
            homes[variant] = page.url
            context.close()
        payload['metadata']['warm_paths'] = {v: homes[v].removeprefix(urls[v]) for v in urls}
        persist(args.out, payload)
        for mode, count in [('cold', args.cold_pairs), ('walk', args.walk_pairs)]:
            for pair in range(count):
                for page_index, spec in enumerate(specs):
                    order = ['base', 'head'] if (pair + page_index) % 2 == 0 else ['head', 'base']
                    for position, variant in enumerate(order):
                        context = browser.new_context(viewport=payload['metadata']['viewport'], locale='en-US',
                                                      service_workers='block', storage_state=states[variant])
                        page = context.new_page()
                        page._measure_phase = 'warmup' if mode == 'walk' else 'measured'
                        page.add_init_script(measure.PROBE_INIT + '\n' + LONG_TASK_RESET)
                        failures, errors = [], []
                        page.on('response', lambda r: failures.append({'phase': page._measure_phase,
                                                                       'status': r.status, 'url': r.url}) if r.status >= 400 else None)
                        page.on('pageerror', lambda e: errors.append({'phase': page._measure_phase, 'error': str(e)[:500]}))
                        started = datetime.now(timezone.utc).isoformat()
                        try:
                            sample = measure._measure_one(page, spec, urls[variant], mode, homes[variant], params).to_dict()
                            sample['long_task_entries'] = page.evaluate('() => window.__lt.entries')
                            sample['collected_at_ms'] = page.evaluate('() => performance.now() - (window.__probe.t0 || 0)')
                        except Exception as exc:
                            sample = {'page': spec.name, 'mode': mode, 'error': str(exc)[:500]}
                        sample.update(variant=variant, pair_index=pair, position=position,
                                      started_at=started, http_failures=failures, page_errors=errors)
                        payload['samples'].append(sample)
                        persist(args.out, payload)
                        print(f'{mode} pair={pair} {spec.name} {variant} ready={sample.get("ready_ms")} '
                              f'lt={sample.get("long_task_ms")} serial={sample.get("api_serial_ms")} '
                              f'error={sample.get("error")}', flush=True)
                        if pair == 0 and spec.name in ['inbox', 'channel', 'thread', 'project-docs-charter', 'workspace-task']:
                            page.screenshot(path=str(args.out.parent / f'{args.out.stem}-{mode}-{spec.name}-{variant}.png'))
                        context.close()
                    time.sleep(0.4)
        payload['metadata']['finished_at'] = datetime.now(timezone.utc).isoformat()
        persist(args.out, payload)
        browser.close()


if __name__ == '__main__':
    main()
