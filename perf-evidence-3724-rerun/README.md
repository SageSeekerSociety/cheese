# Cold-open evidence for PR #3724

This bundle remeasures the existing CI artifact pair after the first run's raw files were lost with its temporary environment. It does not rebuild, relabel, or manually retrigger the original CI run. Old and new results must not be pooled or compared as measurements on the same machine.

## Inputs

- CI run: `38582677052`, PR #3724.
- Base artifact: `frontend-dist-base-e1dc25047d3597414b301a77b30526f153163eff`.
- Head artifact: `frontend-dist-head-433848defa1c466582ff58473dca45842dac73dc`.
- `artifacts.json` records each artifact's BUILD_REF, every served file's SHA-256, and a deterministic tree digest. Downloaded files were kept outside this authored bundle and served unchanged.
- Shared backend and collaboration source: recovered snapshot `11965e0858371538796675f49aaf08bac647fcb8`. This is a local test deployment, not either frontend artifact's production deployment.
- `params.json` identifies newly seeded demo data. It contains no login/session credentials.
- `harness/` is a frozen copy of the existing 19-page selectors and timing code. Its hashes are recorded in the results.

## Interpretation after independent review

The saved pair is not a clean before/after isolation of the Vuetify change. Both refs descend from `aca6674e58bdbbd8e05c1ab670dea5721a831d39`, but only base contains navigation fix #3720 (`e1dc2504`). The reported deltas describe these exact artifacts and cannot be attributed to the chunk change alone. Its numerical confounding effect has not been measured; the formal warm paths are identical.

The head artifact still modulepreloads `vuetify-components-BqPsGN9T.js`, and `main-BCPFBE01.js` statically imports it. Modulepreloaded JavaScript is 1,663,233 bytes in base and 1,668,029 bytes in head. The components were not deferred beyond first paint. The ineffective grouping is withdrawn from the branch's current Vite configuration; this does not change either saved artifact or any recorded sample. This evidence does not establish a stable head performance benefit.

## Measurement

Playwright 1.64.0; Chromium headless shell build 1248, Chrome 156.0.8078.4. Viewport 1440×900; locale en-US; service workers blocked; no explicit network or CPU throttling. The host exposed four CPUs with affinity `[11, 25, 48, 95]`.

For each page, eight cold pairs and four walk pairs alternate base/head. `(pair_index + page_index) % 2` decides which goes first; each variant is first exactly half the time. One browser process is shared, but every sample gets a fresh browser context.

- Cold: fresh HTTP cache, reusing login storage. This is not a first-ever visit with empty persistent storage.
- Walk: load the logged-in home, wait for networkidle plus 300 ms, then push the route with the real Vue router. Warm-up HTTP failures and script errors are stored separately.
- `ready_ms`: unchanged content selector/text/skeleton criterion, stamped on an animation frame.
- `long_task_ms`: original accumulated >50 ms main-thread task definition, read just after ready. Raw task entries and collection time are retained to inspect the boundary. The old live observer is disconnected before a walk reset so tasks cannot be counted twice.
- `api_serial_ms`: sum of nonoverlapping API bursts after the first. Includes `/api/` calls and legacy XMLHttpRequest calls, including responses with HTTP failures. Later bursts may be intentional background reads; the metric is not proven removable latency. The run recorded avatar 404s, warmup 500s, and two measured-phase `work-summary` 500s separately in `summary.json`.
- `last_response_ms` includes all completed resource entries, not just API responses. It cannot by itself establish API causality.

The local services share PostgreSQL 17.11.0 and fakeredis 2.39.0. Fakeredis is not CI's Valkey. The proxy serves base on 3000 and head on 3001, forwards both API URL forms to port 8081 and `/collab` WebSockets to 8902. Its static responses use `Cache-Control: no-cache` for both variants.

Fonts were extracted locally (DejaVu and Liberation). English text renders; Chinese demo text appears as missing-glyph boxes in screenshots. Both variants use the same fonts. These are local comparative measurements, not production-device benchmarks.

## Files

- `paired.py`: driver, validated source adaptations, metadata and per-sample atomic writes. Authentication storage stays in memory and is never exported.
- `serve.py`: local artifact/API/WebSocket proxy.
- `artifact_manifest.py`: BUILD_REF validation and artifact hashing; run with `python -I`.
- `backup_watch.py`: immutable checkpoints and one backup attempt per batch. Stops if backup fails; no automatic failure-retry loop.
- `results.json`: all formal samples in execution order. `smoke.json` is a separate preflight and is not included in formal results.
- `checkpoint-*.json`: recoverable interim snapshots, not additional observations. Failure lists remain live after the timing window, through screenshot/context close, and a later write can update an earlier sample. In checkpoint 230, sample index 229 has no HTTP failure yet; the final sample includes two late avatar 404s. Timing values are unchanged. Keep both snapshots as recorded; their failure lists are not guaranteed identical.
- `results-*.png`: representative screenshots taken after, not inside, each timing window.
- `summarize.py`: validates complete balanced pairs, then writes per-page base/head medians and paired deltas to `summary.json` and `summary.md`.

## Rerun commands

Keep downloaded artifacts in separate empty directories. Install the pinned browser and its local runtime dependencies, migrate an isolated database, run `seed_demo.py`, and save the new parameters. Start the common backend/collaboration services with matching disposable local secrets.

```sh
uv run --no-project --with aiohttp python -I serve.py --base /path/to/base --head /path/to/head
# Set PLAYWRIGHT_BROWSERS_PATH, LD_LIBRARY_PATH and FONTCONFIG_FILE for the isolated browser.
uv run --no-project --with playwright==1.64.0 python -I paired.py \
  --harness ./harness --params ./params.json --out ./new-results.json \
  --cold-pairs 8 --walk-pairs 4
python3 -I summarize.py ./new-results.json --out ./new-summary.json
```

The driver refuses to overwrite existing results. For a new run, save new params and a new output filename. `summary.json` distinguishes differences of page medians from medians of actual within-pair differences. No outlier removal or exclusion of the first pair is performed. First-pair setup activity and host drift may add noise; pair balance does not eliminate it.
