// The docs built for a host of their own (DOCS_BASE=, src/where.mjs), checked
// the way a reader meets them: where the links go, where the demo stages come
// from, who the page will talk to over postMessage, and what theme a stage is
// told to paint.
//
//     node docs/site/test/docs-host.test.mjs                  (builds into a temp dir)
//     OUT=/tmp/docs-host node docs/site/test/docs-host.test.mjs   (uses a DOCS_BASE= build there
//                                                              made with DOCS_PLATFORM=https://app.example.test)
//
// happy-dom comes from `frontend/node_modules`, as for demo-arch.test.mjs.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.dirname(HERE)
const REPO = path.dirname(path.dirname(SITE))
const PLATFORM = 'https://app.example.test'
const DOCS = 'https://docs.example.test'

const failures = []
let checks = 0
const ok = (yes, what) => {
  checks++
  if (!yes) failures.push(what)
}
const eq = (got, want, what) => ok(String(got) === String(want), `${what}: got «${got}», wanted «${want}»`)

const require = createRequire(path.join(REPO, 'frontend/package.json'))
const { Window } = require('happy-dom')

const out = process.env.OUT || fs.mkdtempSync(path.join(os.tmpdir(), 'docs-host-'))
if (!process.env.OUT) {
  execFileSync(process.execPath, ['build.mjs'], {
    cwd: SITE,
    env: { ...process.env, OUT: out, DOCS_BASE: '', DOCS_PLATFORM: PLATFORM },
    stdio: ['ignore', 'ignore', 'inherit'],
  })
}
const read = (f) => fs.readFileSync(path.join(out, f), 'utf8')

// ---------- links: every page of the site at the root, the platform by its origin ----------
for (const file of ['index.html', 'quickstart.html', 'dev/turn.html', 'changelog.html', 'dev-gate.html']) {
  const html = read(file)
  const stale = [...html.matchAll(/(?:href|src)="(\/docs\/[^"]*)"/g)].map((m) => m[1])
  eq(stale.length, 0, `${file} links into /docs/ (${stale.slice(0, 3).join(', ')})`)
}
ok(read('llms.txt').includes('](https://docs.okcheese.com/quickstart.md)'), 'llms.txt points at the docs host')
const index = JSON.parse(read('ask-index.json'))
ok(index.length > 0 && index.every((r) => r.url.startsWith('/') && !r.url.startsWith('/docs/')), 'the ask index holds paths within the site')

// ---------- the demo stages: the platform's pages ----------
const settings = {
  disableJavaScriptEvaluation: true,
  disableJavaScriptFileLoading: true,
  disableCSSFileLoading: true,
  disableIframePageLoading: true,
  disableComputedStyleRendering: true,
}
const window = new Window({ url: `${DOCS}/dev/turn`, settings })
window.document.write(read('dev/turn.html'))
const stages = [...window.document.querySelectorAll('iframe[data-dm-embed]')]
ok(stages.length > 0, 'dev/turn has a demo stage')
for (const f of stages) ok(f.getAttribute('src').startsWith(`${PLATFORM}/demo/`), `stage comes from the platform: ${f.getAttribute('src')}`)

const g = globalThis
for (const k of ['window', 'document', 'location', 'HTMLElement', 'Element', 'Event', 'CustomEvent', 'Node', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'IntersectionObserver']) {
  try { if (k in window) g[k] = window[k] } catch {}
}
g.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
// What the page sends to the stage, and to which origin.
const stage = stages[0]
const sent = []
const frame = { postMessage: (data, origin) => sent.push({ data, origin }) }
Object.defineProperty(stage, 'contentWindow', { get: () => frame })
const { mountDemos, themeStages } = await import(`${path.join(SITE, 'src/demo-dom.mjs')}?v=${Math.random()}`)
mountDemos(window.document)

const from = (origin, source = frame) =>
  window.dispatchEvent(new window.MessageEvent('message', { data: { cheeseDemo: 'ready' }, origin, source }))
from('https://elsewhere.example')
from(DOCS)
eq(sent.length, 0, 'a "ready" from any origin but the platform is ignored')
from(PLATFORM, {})
eq(sent.length, 0, 'and so is one from the platform that is not the stage')
from(PLATFORM)
eq(sent.length, 1, 'the stage saying it is ready gets its step')
eq(sent[0]?.origin, PLATFORM, 'sent to the platform origin and no other')
eq(sent[0]?.data?.cheeseDemo, 'go', 'as a go')

// ---------- theme: told in the address ----------
themeStages(true, window.document)
eq(new URL(stage.getAttribute('src')).searchParams.get('theme'), 'dark', 'a dark page tells the stage dark')
eq(new URL(stage.getAttribute('src')).searchParams.get('embed'), '1', 'and keeps it embedded')
themeStages(false, window.document)
eq(new URL(stage.getAttribute('src')).searchParams.get('theme'), 'light', 'switching tells it light')

if (failures.length) {
  console.error(`docs-host: ${failures.length} of ${checks} check(s) failed`)
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`docs-host: ${checks} checks passed (${out})`)
