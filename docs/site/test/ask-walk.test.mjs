// The lines 问芝士 shows while it looks for the answer.
//
//     node docs/site/test/ask-walk.test.mjs                     (builds into a temp dir)
//     OUT=/tmp/docs-y node docs/site/test/ask-walk.test.mjs    (uses what is there)
//
// The backend streams a `tool` event for every search and every page the model
// reads; src/walk.mjs turns those into what the reader sees. What is worth
// checking is exactly that: a line per call while it works, one folded line
// once it answers, and nothing a page title can inject. The panel itself needs
// a browser, a signed-in session and a live model, so this drives the part that
// decides the markup — and then checks the shipping build carries it.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.dirname(HERE)
const REPO = path.dirname(path.dirname(SITE))

const failures = []
let checks = 0
const ok = (yes, what) => {
  checks++
  if (!yes) failures.push(what)
}
const eq = (got, want, what) => ok(String(got) === String(want), `${what}: got «${got}», wanted «${want}»`)
const has = (hay, needle, what) => ok(String(hay).includes(needle), `${what}: «${needle}» is not in it`)
const hasNot = (hay, needle, what) => ok(!String(hay).includes(needle), `${what}: «${needle}» is in it`)

const require = createRequire(path.join(REPO, 'frontend/package.json'))
let Window
try {
  ({ Window } = require('happy-dom'))
} catch (e) {
  console.error(`ask-walk: happy-dom is not installed under frontend/node_modules (${e.message}).`)
  console.error('It is a frontend devDependency; the docs site does not carry one of its own.')
  process.exit(2)
}

const out = process.env.OUT || fs.mkdtempSync(path.join(os.tmpdir(), 'docs-walk-'))
if (!process.env.OUT) {
  execFileSync(process.execPath, ['build.mjs'], { cwd: SITE, env: { ...process.env, OUT: out }, stdio: ['ignore', 'ignore', 'inherit'] })
} else if (!fs.existsSync(path.join(out, 'sections.json'))) {
  console.error(`ask-walk: no build in ${out} — run the build first or leave OUT unset`)
  process.exit(2)
}

const { stepOf, walkHtml } = await import(`${path.join(SITE, 'src/walk.mjs')}?v=${Math.random()}`)
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

const window = new Window({ url: 'https://cheese.local/docs/' })
const render = (html) => {
  window.document.body.innerHTML = `<div class="body">${html}</div>`
  return window.document.querySelector('.body')
}

// ---------- one tool event, as a line ----------
{
  eq(stepOf({ kind: 'search', query: '邀请成员' }).text, '正在搜：邀请成员', 'a search says what it is searching for')
  eq(stepOf({ kind: 'search', query: '邀请成员' }).icon, 'search', 'and is drawn with the search icon')
  eq(stepOf({ kind: 'fetch', title: '团队', url: '/docs/teams' }).text, '正在读：团队', 'a read names the page the reader knows')
  eq(stepOf({ kind: 'fetch', title: '团队' }).icon, 'doc', 'and is drawn as a page')
  has(stepOf({ kind: 'list' }).text, '有哪些页', 'listing the pages says so')
  has(stepOf({ kind: 'whatever' }).text, '有哪些页', 'an event from a newer backend is not left undefined')
}

// ---------- the whole walk ----------
{
  const steps = [stepOf({ kind: 'search', query: '邀请' }), stepOf({ kind: 'fetch', title: '团队' })]
  const live = render(walkHtml(steps, true, esc))
  const details = live.querySelector('details.walk')
  ok(details && details.hasAttribute('open'), 'while it works the lines are open')
  eq(live.querySelectorAll('.walk-item').length, 2, 'one line per tool call')
  has(live.textContent, '正在搜：邀请', 'the search is on screen')
  has(live.textContent, '正在读：团队', 'so is the page being read')
  ok(live.querySelectorAll('.walk-item')[1].classList.contains('live'), 'the last line is the one happening now')
  eq(live.querySelectorAll('svg.ico').length, 2, 'each line carries its own icon')

  const folded = render(walkHtml(steps, false, esc))
  ok(!folded.querySelector('details.walk').hasAttribute('open'), 'once the answer starts the lines fold away')
  has(folded.querySelector('summary').textContent, '查了 2 步', 'and say how much was done')
  eq(folded.querySelectorAll('.walk-item.live').length, 0, 'nothing is still running')

  eq(walkHtml([], true, esc), '', 'a question answered from one search shows no walk at all')
}

// ---------- what the model hands back is data, not markup ----------
{
  const hostile = [stepOf({ kind: 'search', query: '<img src=x onerror=alert(1)>' }), stepOf({ kind: 'fetch', title: '<script>alert(2)</script>' })]
  const html = walkHtml(hostile, true, esc)
  hasNot(html, '<img', 'a search term I typed cannot open a tag')
  hasNot(html, '<script', 'nor can a page title someone else wrote')
  const live = render(html)
  has(live.textContent, '<img src=x onerror=alert(1)>', 'both are still readable as text')
  has(live.textContent, '<script>alert(2)</script>', 'and both are still there')
}

// ---------- and the shipping build carries it ----------
{
  const js = fs.readFileSync(path.join(out, 'assets', fs.readdirSync(path.join(out, 'assets')).find((f) => /^app-.*\.js$/.test(f))), 'utf8')
  // The bundle is minified, and the minifier writes every non-ASCII character
  // as a \uXXXX escape: read it back as text before looking for Chinese.
  const text = js.replace(/\\u([0-9a-f]{4})/gi, (_, hex) => String.fromCharCode(parseInt(hex, 16)))
  has(js, '"tool"', 'the built panel listens for tool events')
  has(text, '正在搜：', 'and shows what it is searching for')
  has(text, '正在读：', 'and which page it is reading')
  has(js, 'walk-item', 'and renders the lines the same way this test just read')
  const css = fs.readFileSync(path.join(out, 'assets', fs.readdirSync(path.join(out, 'assets')).find((f) => /^app-.*\.css$/.test(f))), 'utf8')
  has(css, '.walk-item.live', 'the style for the line in progress is shipped')
  has(css, '.walk>summary', 'and the folded line has one')
  has(css, '.typing i', 'the dots a question starts with are still there')
}

if (failures.length) {
  console.error(`\nask-walk: ${failures.length} of ${checks} checks failed\n`)
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`ask-walk: ${checks} checks passed (${out})`)
