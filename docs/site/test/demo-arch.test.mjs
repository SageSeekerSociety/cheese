// The `demo-arch` figures, driven in a DOM — the way a reader drives them.
//
//     node docs/site/test/demo-arch.test.mjs          (builds into a temp dir)
//     OUT=/tmp/docs-y node docs/site/test/demo-arch.test.mjs   (uses what is there)
//
// What this is for: the figures are prerendered by build.mjs and then taken over
// by src/arch-window.mjs in the browser — two renderings of one set of strings,
// which is exactly the pair that drifts. So the test runs the shipping build,
// mounts the page it produced with the shipped mount function, and then asserts
// the things a reader can see: where the packet stands after a click, what the
// inspector says at each stop, which station is drawn as the one that refused,
// and that the no-JavaScript fallback stays readable behind it all.
//
// happy-dom comes from `frontend/node_modules` (that is where this repo keeps
// it; the docs site has no test dependencies of its own and this adds none —
// docs/site/package.json is a build tool, not a package). Nothing else is mocked:
// the modules under test are the ones in src/, imported by path.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SITE = path.dirname(HERE)
const REPO = path.dirname(path.dirname(SITE))

// ---------- the two halves a DOM needs, and a place to report to ----------
const failures = []
let checks = 0
const ok = (yes, what) => {
  checks++
  if (!yes) failures.push(what)
}
const eq = (got, want, what) => ok(String(got) === String(want), `${what}: got «${got}», wanted «${want}»`)
const has = (hay, needle, what) => ok(String(hay).includes(needle), `${what}: «${needle}» is not in it`)

// ---------- happy-dom ----------
const require = createRequire(path.join(REPO, 'frontend/package.json'))
let Window
try {
  ({ Window } = require('happy-dom'))
} catch (e) {
  console.error(`demo-arch: happy-dom is not installed under frontend/node_modules (${e.message}).`)
  console.error('It is a frontend devDependency; the docs site does not carry one of its own.')
  process.exit(2)
}

// ---------- the page under test ----------
const out = process.env.OUT || fs.mkdtempSync(path.join(os.tmpdir(), 'docs-arch-'))
if (!process.env.OUT) {
  execFileSync(process.execPath, ['build.mjs'], { cwd: SITE, env: { ...process.env, OUT: out }, stdio: ['ignore', 'ignore', 'inherit'] })
} else if (!fs.existsSync(path.join(out, 'dev/llm.html'))) {
  console.error(`demo-arch: no build in ${out} — run the build first or leave OUT unset`)
  process.exit(2)
}

// The page's own <script> and <link> are not part of the test and must not be
// fetched: the modules under test are imported directly, and the iframes the
// step demos embed (the product's own pages) are left unloaded.
const settings = {
  disableJavaScriptEvaluation: true,
  disableJavaScriptFileLoading: true,
  disableCSSFileLoading: true,
  disableIframePageLoading: true,
  disableComputedStyleRendering: true,
}

function page(file) {
  const html = fs.readFileSync(path.join(out, file), 'utf8')
  const window = new Window({ url: 'https://cheese.local/docs/dev/', settings })
  window.document.write(html)
  return window
}

// src/arch-window.mjs and src/demo-dom.mjs read these globals at module load, so
// they are set before the dynamic import — per page, each page getting its own
// window the way a browser would.
async function mount(window) {
  const g = globalThis
  for (const k of ['window', 'document', 'location', 'HTMLElement', 'Element', 'Event', 'CustomEvent', 'Node', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame', 'IntersectionObserver']) {
    // Node 22 carries its own `navigator`, as a getter: leave whatever cannot be
    // replaced alone rather than crashing on it.
    try { if (k in window) g[k] = window[k] } catch {}
  }
  g.matchMedia = window.matchMedia ? window.matchMedia.bind(window) : () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
  const mod = await import(`${path.join(SITE, 'src/demo-dom.mjs')}?v=${Math.random()}`)
  mod.mountDemos(window.document)
}

// The figure's own state, as the page publishes it for anything watching.
const state = (fig) => ({
  kind: fig.dataset.kind,
  entry: fig.dataset.entry,
  scene: fig.dataset.scene,
  pos: Number(fig.dataset.pos),
  stops: Number(fig.dataset.stops),
  block: fig.dataset.block,
})
const side = (fig) => ({
  station: fig.querySelector('[data-now-station]').textContent,
  pos: fig.querySelector('[data-now-pos]').textContent,
  head: fig.querySelector('[data-head]').textContent,
  stop: fig.querySelector('[data-stop]')?.getAttribute('data-stop') || '',
  cards: [...fig.querySelectorAll('.ar-card')].map((c) => c.getAttribute('data-card')),
})
const stationState = (fig, key) => fig.querySelector(`[data-station="${key}"]`)?.dataset.state
const chip = (fig, key) => fig.querySelector(`[data-station="${key}"] [data-chip]`).textContent
const click = (el) => el.dispatchEvent(new (el.ownerDocument.defaultView.MouseEvent)('click', { bubbles: true }))
// Nothing but the controls moves the packet: one click per stop, no shortcut.
const stepTo = (fig, n) => {
  while (state(fig).pos < n) click(fig.querySelector('[data-next]'))
  while (state(fig).pos > n) click(fig.querySelector('[data-prev]'))
}

// ---------- llm.md: the two figures ----------
const llm = page('dev/llm.html')
await mount(llm)
const llmFigs = [...llm.document.querySelectorAll('[data-demo="arch"]')]
eq(llmFigs.length, 2, 'llm.md renders two architecture figures')

{
  const [a, b] = llmFigs
  const cfgA = JSON.parse(a.querySelector('[data-arch]').textContent)

  // --- the pickers, and what the fence said they would be ---
  eq([...a.querySelectorAll('[data-picks="entry"] .ar-pick')].map((x) => x.dataset.entry).join(','), 'cloud,codex', 'figure 1 offers the two entries')
  eq([...a.querySelectorAll('[data-picks="scene"] .ar-pick')].map((x) => x.dataset.scene).join(','), 'ok,budget', 'figure 1 offers the scenes its fence listed')
  eq(a.querySelector('[aria-pressed="true"][data-entry]').dataset.entry, 'cloud', 'figure 1 opens on its first entry')
  eq(a.querySelector('[aria-pressed="true"][data-scene]').dataset.scene, 'ok', 'figure 1 opens on its first scene')

  // --- a walk that goes through ---
  let s = state(a)
  eq(s.entry, 'cloud', 'figure 1 entry')
  eq(s.scene, 'ok', 'figure 1 scene')
  eq(s.pos, 1, 'figure 1 starts on the first stop')
  eq(s.stops, 7, 'session host × 正常放行 is seven stops')
  eq(s.block, '', 'session host × 正常放行 is not refused anywhere')
  eq(side(a).station, '会话进程', 'the inspector opens on the first station')
  has(side(a).pos, '第 1 / 7 站', 'the inspector counts the stops')
  ok(side(a).cards.includes('see'), 'the first stop shows what it saw')
  eq(stationState(a, 'session'), 'on', 'the station the packet stands on is «on»')
  eq(stationState(a, 'vendor'), 'next', 'a station ahead of the packet is «next»')
  eq(chip(a, 'vendor'), '', 'no chip for a station ahead')
  ok(a.querySelector('.ar-fallback'), 'the readable fallback is in the page')

  click(a.querySelector('[data-next]'))
  eq(state(a).pos, 2, 'the next button moves the packet one stop')
  eq(stationState(a, 'session'), 'done', 'the station left behind is «done»')
  eq(chip(a, 'session'), '', 'a station already passed has no chip')
  eq(a.querySelector('[data-wire="session|helper"]').dataset.state, 'done', 'the wire behind the packet is drawn as crossed')

  // The stop the packet is standing on and the card the inspector opens are the
  // same stop — the pair that would drift if the two renderings were separate.
  eq(side(a).head, cfgA.walks['cloud/ok'].stops[1].head, 'the inspector opens the stop the packet is on')
  const pkt = a.querySelector('[data-pkt]').getAttribute('style')
  click(a.querySelector('[data-next]'))
  ok(a.querySelector('[data-pkt]').getAttribute('style') !== pkt, 'the packet really moves')

  // --- a refusal: the walk ends on the station that refused ---
  click(a.querySelector('[data-scene="budget"]'))
  s = state(a)
  eq(s.scene, 'budget', 'the scene button switches the scene')
  eq(s.pos, 1, 'switching scenes puts the packet back on the first stop')
  eq(s.stops, 5, 'session host × 额度用完 is five stops')
  eq(s.block, 'admission', 'the walk records that 准入 refused it')
  stepTo(a, 5)
  eq(state(a).pos, 5, 'the packet reaches the last stop')
  eq(chip(a, 'admission'), '拦在这里', 'the refusing station wears the chip')
  eq(stationState(a, 'admission'), 'block', 'the packet is drawn stopped on 准入')
  eq(stationState(a, 'vendor'), 'skip', '模型厂商 is left out of the walk entirely')
  eq(chip(a, 'vendor'), '没经过', 'and the map says so')
  eq(side(a).stop, 'block', 'the inspector says the request was refused here')
  has(side(a).head, '准入', 'the last stop is the admission stop')
  has(side(a).pos, '第 5 / 5 站', 'the count agrees with the walk')
  click(a.querySelector('[data-next]'))
  eq(state(a).pos, 5, 'the packet does not walk past the last stop')
  stepTo(a, 1)
  eq(state(a).pos, 1, 'the previous button walks back to the first stop')
  click(a.querySelector('[data-prev]'))
  eq(state(a).pos, 1, 'and stops there')

  // --- another entry, and the pair it does not have ---
  click(a.querySelector('[data-entry="codex"]'))
  s = state(a)
  eq(s.entry, 'codex', 'the entry button switches the entry')
  eq(s.scene, 'budget', 'the scene is kept when the new entry has it')
  eq(s.stops, 3, 'Codex × 额度用完 is three stops')
  eq(s.block, 'gateway', 'on that path the gateway is what refuses')
  eq(stationState(a, 'meter'), 'skip', 'the meter is not in the Codex walk at all')
  stepTo(a, 3)
  eq(chip(a, 'gateway'), '拦在这里', 'the gateway wears the chip on that path')
  eq(side(a).stop, 'block', 'and the inspector says so')
  click(a.querySelector('[data-scene="ok"]'))
  eq(state(a).stops, 4, 'Codex × 正常放行 is four stops')
  eq(state(a).block, '', 'Codex × 正常放行 is not refused')

  // --- playback ---
  click(a.querySelector('[data-play]'))
  eq(a.querySelector('[data-play]').getAttribute('aria-pressed'), 'true', 'playing marks the button pressed')
  eq(a.querySelector('[data-play-label]').textContent, '暂停', 'playing relabels the button')
  click(a.querySelector('[data-play]'))
  eq(a.querySelector('[data-play]').getAttribute('aria-pressed'), 'false', 'a second click stops it')
  eq(a.querySelector('[data-play-label]').textContent, '播放', 'stopping relabels it back')

  // --- figure 2: the three answers that are not «go» ---
  const cfgB = JSON.parse(b.querySelector('[data-arch]').textContent)
  eq([...b.querySelectorAll('[data-picks="scene"] .ar-pick')].map((x) => x.dataset.scene).join(','), 'binding,failopen,subagent', 'figure 2 offers three scenes')
  // One entry has all three, and a picker with one choice is not drawn.
  eq(b.querySelectorAll('[data-picks="entry"] .ar-pick').length, 0, 'figure 2 has one entry and no entry picker')
  ok(cfgB.walks['cloud/binding'], 'figure 2 walks the session host')
  eq(state(b).block, 'admission', '绑定解析不出 walks into a refusal')
  stepTo(b, 5)
  eq(side(b).stop, 'block', 'and the inspector says so')

  click(b.querySelector('[data-scene="failopen"]'))
  s = state(b)
  eq(s.stops, 7, '问不到主 API is seven stops')
  eq(s.block, '', 'nothing refuses on the fail-open walk')
  stepTo(b, 5)
  eq(side(b).stop, 'soft', '准入 is drawn as the soft stop it is')
  eq(stationState(b, 'admission'), 'soft', 'the station is not drawn as refusing')
  eq(chip(b, 'admission'), '软放行', 'and it says which kind of soft')
  eq(stationState(b, 'subscription'), 'next', '订阅路 is where it goes next')
  stepTo(b, 7)
  eq(side(b).station, '模型厂商', 'the fail-open walk does reach the vendor')
  eq(cfgB.walks['cloud/failopen'].stops.length, 7, 'the figure JSON walks the same seven stops')

  click(b.querySelector('[data-scene="subagent"]'))
  eq(state(b).stops, 7, '分身指定模型 is seven stops')
  eq(state(b).block, '', 'a named subagent model is not a refusal')
  stepTo(b, 5)
  has(side(b).head, '准入', 'the fifth stop is 准入')
  has(JSON.stringify(cfgB.walks['cloud/subagent'].stops[4].say), 'claude-sonnet-5', '准入 is where the name is translated to the wire name')

  // --- the play loop really walks, and stops when told ---
  // Timings are the figure's own (first tick 320 ms out, then one stop every
  // 2.6 s). The waits are loose on purpose: a slow box may land on a later
  // stop, which is fine — what has to hold is that the packet moves by itself
  // and that a second click freezes it.
  const delay = (ms) => new Promise((r) => setTimeout(r, ms))
  const from = state(a).pos
  click(a.querySelector('[data-play]'))
  await delay(1200)
  click(a.querySelector('[data-play]'))
  const walked = state(a).pos
  ok(walked > from, `playing walks on its own (${from} → ${walked})`)
  await delay(3000)
  eq(state(a).pos, walked, 'and a second click stops the walking')

  // --- the fallback list: every walk, readable with no script ---
  for (const [fig, name] of [[a, 'figure 1'], [b, 'figure 2']]) {
    const cfg = JSON.parse(fig.querySelector('[data-arch]').textContent)
    const items = [...fig.querySelectorAll('.ar-fallback li')]
    eq(items.length, Object.keys(cfg.walks).length, `${name} lists every walk it offers`)
    const refused = items.filter((li) => li.classList.contains('block'))
    eq(refused.length, Object.values(cfg.walks).filter((w) => w.stops.some((x) => x.block)).length, `${name} marks each refused walk`)
    for (const li of refused) has(li.textContent, '停在第', `${name} says where a refused walk stopped`)
    has(fig.querySelector('.ar-fallback').textContent, '拦在这里', `${name} names the station that refused`)
  }
}

// ---------- machines.md: one entry, four scenes ----------
const mc = page('dev/machines.html')
await mount(mc)
const mcFigs = [...mc.document.querySelectorAll('[data-demo="arch"]')]
eq(mcFigs.length, 1, 'machines.md renders one architecture figure')
{
  const fig = mcFigs[0]
  const cfg = JSON.parse(fig.querySelector('[data-arch]').textContent)
  eq(state(fig).kind, 'machines', 'the figure is drawn from the machines map')
  eq([...fig.querySelectorAll('[data-picks="scene"] .ar-pick')].map((x) => x.dataset.scene).join(','), 'ok,probe,quarantine,unknown', 'the scenes a tool call can end in')
  eq(fig.querySelectorAll('[data-picks="entry"] .ar-pick').length, 0, 'one entry is not offered as a choice')
  eq(state(fig).stops, 11, '正常一轮 is eleven stops')
  eq(state(fig).block, '', '正常一轮 is not refused')
  has(fig.querySelector('.ar-fallback').textContent, '机器连接服务', 'the fallback names the long connection')

  click(fig.querySelector('[data-scene="probe"]'))
  eq(state(fig).stops, 3, '开跑前没应答 is three stops')
  eq(state(fig).block, 'hub', 'it stops at the hub')
  stepTo(fig, 3)
  eq(side(fig).stop, 'block', 'and the inspector says so')
  has(side(fig).head, '没有答复', 'the last stop is the one that timed out')

  click(fig.querySelector('[data-scene="quarantine"]'))
  eq(state(fig).stops, 8, '连续失败被隔离 is eight stops')
  eq(state(fig).block, 'api', 'the room is told where it stopped')
  click(fig.querySelector('[data-scene="unknown"]'))
  eq(state(fig).stops, 8, '结果未知 is eight stops')
  eq(state(fig).block, 'api', 'a result nobody can settle ends at the platform too')
  stepTo(fig, 8)
  has(side(fig).head, '交人确认', 'the walk ends asking a person')
  eq(stationState(fig, 'hub'), 'done', 'the connection is behind the packet by then')

  const items = [...fig.querySelectorAll('.ar-fallback li')]
  eq(items.length, 4, 'the fallback lists all four walks')
  eq(items.filter((li) => li.classList.contains('block')).length, 3, 'three of the four are stops, not completions')
  eq(cfg.walks['tool/ok'].stops.length, 11, 'the figure JSON has the same walk')
}

// ---------- the two readers: the same strings, once in HTML once in prose ----------
{
  const llmMd = fs.readFileSync(path.join(out, 'dev/llm.md'), 'utf8')
  const mcMd = fs.readFileSync(path.join(out, 'dev/machines.md'), 'utf8')
  for (const [md, title] of [[llmMd, '换入口：包从哪进、在哪拦'], [llmMd, '另外三种情形：绑错、问不到、分身指定'], [mcMd, '一次工具调用走哪几站，出错时停在哪']]) {
    has(md, title, 'the page carries the figure’s text version')
    has(md, '网页上是一张可以点开每一站的路径图', 'and says what the map is')
  }
  // The prose and the picture are built from the same walk, so a stop's headline
  // has to show up in both.
  const cfg = JSON.parse(llmFigs[0].querySelector('[data-arch]').textContent)
  has(llmMd, cfg.walks['cloud/ok'].stops[0].head, 'the prose twin quotes the walk the figure draws')
  has(llmMd, cfg.walks['cloud/budget'].stops[4].head, 'the refused walk is in the prose too')
  // The indexes agents search are built from that same prose, so a figure whose
  // text version never reached them is a figure nobody can find.
  for (const [file, name] of [['dev/search.json', 'the search index'], ['dev/sections.json', 'the 问芝士 index']]) {
    has(fs.readFileSync(path.join(out, file), 'utf8'), '换入口：包从哪进、在哪拦', `${name} gets the text version as well`)
  }
}

// ---------- what no script, and no wide screen, gets ----------
// happy-dom does not lay anything out, so the stylesheet is read as text: the
// fallback must be hidden *only* by the class the script adds, and the narrow
// screen must show it whatever happens.
{
  const css = fs.readFileSync(path.join(out, 'assets', fs.readdirSync(path.join(out, 'assets')).find((f) => /^app-.*\.css$/.test(f))), 'utf8')
  has(css, '.demo-arch.ar-live .ar-fallback{display:none}', 'the readable list is hidden only once the script is up')
  ok(!/\.demo-arch \.ar-fallback\{display:none/.test(css), 'and it is never hidden before that')
  has(css, '.demo-arch.ar-live .ar-ctl-wrap{display:block}', 'the controls are reserved for the live version')
  // The minifier may or may not keep the space after the colon.
  const narrow = css.slice(Math.max(css.lastIndexOf('@media (max-width:760px)'), css.lastIndexOf('@media (max-width: 760px)')))
  has(narrow, '.demo-arch .ar-fallback', 'a narrow screen gets the list back')
  has(narrow, '.demo-arch .ar-board', 'and drops the map it cannot draw')
}

// ---------- report ----------
if (failures.length) {
  console.error(`\ndemo-arch: ${failures.length} of ${checks} checks failed\n`)
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`demo-arch: ${checks} checks passed (${out})`)
