// The `demo-panel` demos, the way a reader meets them.
//
//     node docs/site/test/demo-panel.test.mjs          (builds into a temp dir)
//     OUT=/tmp/docs-y node docs/site/test/demo-panel.test.mjs   (uses what is there)
//
// What a reader must be able to count on: a panel nobody plays — no script,
// reduced motion, never scrolled to — already shows the result; when it plays
// it starts from before the action and stops on that same result; it can be
// played again; and the build refuses a panel that quotes a label the product
// does not show, or tries to show more than one small action.
//
// happy-dom comes from `frontend/node_modules`, as in demo-arch.test.mjs.
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
const ok = (yes, what) => { checks++; if (!yes) failures.push(what) }
const eq = (got, want, what) => ok(String(got) === String(want), `${what}: got «${got}», wanted «${want}»`)

const require = createRequire(path.join(REPO, 'frontend/package.json'))
let Window
try {
  ({ Window } = require('happy-dom'))
} catch (e) {
  console.error(`demo-panel: happy-dom is not installed under frontend/node_modules (${e.message}).`)
  process.exit(2)
}

const out = process.env.OUT || fs.mkdtempSync(path.join(os.tmpdir(), 'docs-panel-'))
if (!process.env.OUT) {
  execFileSync(process.execPath, ['build.mjs'], { cwd: SITE, env: { ...process.env, OUT: out }, stdio: ['ignore', 'ignore', 'inherit'] })
}
const PAGE = 'working-with-cheese.html'
const settings = { disableJavaScriptEvaluation: true, disableJavaScriptFileLoading: true, disableCSSFileLoading: true, disableIframePageLoading: true, disableComputedStyleRendering: true }

function page() {
  const window = new Window({ url: 'https://cheese.local/docs/working-with-cheese', settings })
  window.document.write(fs.readFileSync(path.join(out, PAGE), 'utf8'))
  return window
}

async function mount(window, { reduced = false, observer = true } = {}) {
  const g = globalThis
  for (const k of ['window', 'document', 'location', 'HTMLElement', 'Element', 'Event', 'CustomEvent', 'Node', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
    try { if (k in window) g[k] = window[k] } catch {}
  }
  g.matchMedia = (q) => ({ matches: reduced && q.includes('reduce'), addEventListener() {}, removeEventListener() {} })
  // The panel plays the first time it is on screen; here every panel is.
  if (observer) {
    g.IntersectionObserver = class { constructor(cb) { this.cb = cb } observe(el) { setTimeout(() => this.cb([{ isIntersecting: true, target: el }]), 0) } disconnect() {} }
  } else {
    delete g.IntersectionObserver
  }
  const mod = await import(`${path.join(SITE, 'src/demo-dom.mjs')}?v=${Math.random()}`)
  mod.mountDemos(window.document)
}

// What a reader sees in a panel: the text of every part that is not hidden.
const seen = (fig) => [...fig.querySelectorAll('.dp-win *')].filter((n) => !n.closest('[hidden]') && !n.children.length).map((n) => n.textContent.trim()).filter(Boolean).join(' | ')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function until(cond, ms) {
  const t0 = Date.now()
  while (!cond()) { if (Date.now() - t0 > ms) return false; await sleep(50) }
  return true
}

// ---------- at rest: the result is what is there ----------
const still = page()
const figs = [...still.document.querySelectorAll('figure.demo-panel:not([data-walk-panel])')]
ok(figs.length >= 1, `the template page has its standalone demo (found ${figs.length})`)
const rest = figs.map(seen)
for (const [i, fig] of figs.entries()) {
  ok(!!fig.querySelector('figcaption.dp-cap')?.textContent.trim(), `demo ${i + 1} has its one caption line`)
  ok(!fig.querySelector('input[type=range], progress, [data-dm-range], [data-dm-jump]'), `demo ${i + 1} carries no progress controls`)
}
ok(rest[0].includes('1 条回复') && !rest[0].includes('正在回复'), `demo 1 rests on the reply, not on 芝士 still replying: ${rest[0]}`)

// ---------- reduced motion: nothing moves, the result stays ----------
{
  const w = page()
  await mount(w, { reduced: true })
  await sleep(200)
  const f = [...w.document.querySelectorAll('figure.demo-panel:not([data-walk-panel])')]
  f.forEach((fig, i) => eq(seen(fig), rest[i], `demo ${i + 1} under reduced motion`))
}

// ---------- playing: from before the action to the result, once ----------
{
  const w = page()
  await mount(w)
  await sleep(150)
  const f = [...w.document.querySelectorAll('figure.demo-panel:not([data-walk-panel])')]
  f.forEach((fig, i) => ok(seen(fig) !== rest[i], `demo ${i + 1} rewinds to before the action when it starts playing`))
  ok(!seen(f[0]).includes('1 条回复'), 'demo 1 starts before the message is sent')
  const done = await until(() => f.every((fig) => fig.classList.contains('dp-done')), 20000)
  ok(done, 'every demo finishes within 20 s')
  f.forEach((fig, i) => eq(seen(fig), rest[i], `demo ${i + 1} stops on the result`))
  f.forEach((fig, i) => ok(!fig.querySelector('[data-dp-replay]').hidden, `demo ${i + 1} offers 重播 once it has played`))

  // 重播 runs it again, from the start.
  const replay = f[0].querySelector('[data-dp-replay]')
  replay.dispatchEvent(new w.MouseEvent('click', { bubbles: true }))
  await sleep(150)
  ok(!seen(f[0]).includes('1 条回复'), '重播 starts demo 1 over')
  ok(await until(() => f[0].classList.contains('dp-done'), 20000), '重播 plays to the end again')
  eq(seen(f[0]), rest[0], 'and stops on the same result')
}

// ---------- step lists that drive one panel ----------
// The steps are the controller: at rest the panel shows the screen after the
// last step; walking through highlights each step with the screen after it;
// clicking a step shows that step's screen.
const onStep = (w) => w.querySelector('[data-walk-step].on')?.dataset.walkStep || ''
{
  const restW = page()
  const walks = [...restW.document.querySelectorAll('[data-walk]')]
  ok(walks.length >= 2, `the template page has its two step-driven panels (found ${walks.length})`)
  const restWalk = walks.map((w) => seen(w.querySelector('[data-walk-panel]')))
  walks.forEach((w, i) => {
    eq(w.querySelectorAll('[data-walk-step]').length, Number(w.querySelector('[data-walk-panel]').dataset.beats), `walk ${i + 1}: one frame per step`)
    ok(!w.querySelector('figcaption, [data-dp-replay], input[type=range], progress'), `walk ${i + 1}: no caption, no replay, no progress bar — the steps are all of it`)
  })
  ok(restWalk[0].includes('已开始') && restWalk[0].includes('芝士这一轮的清单'), `walk 1 rests on the started task with its checklist: ${restWalk[0]}`)
  ok(restWalk[1].includes('已采纳') && restWalk[1].includes('任务已关闭'), `walk 2 rests on 已采纳 and 任务已关闭: ${restWalk[1]}`)

  // Reduced motion: nothing walks by itself, the last screen stays; a click switches.
  {
    const w = page()
    await mount(w, { reduced: true })
    await sleep(200)
    const ws = [...w.document.querySelectorAll('[data-walk]')]
    ws.forEach((x, i) => eq(seen(x.querySelector('[data-walk-panel]')), restWalk[i], `walk ${i + 1} under reduced motion`))
    ws[0].querySelector('[data-walk-step="1"]').dispatchEvent(new w.MouseEvent('click', { bubbles: true }))
    eq(onStep(ws[0]), '1', 'reduced motion: clicking step 1 highlights it')
    const f1 = seen(ws[0].querySelector('[data-walk-panel]'))
    ok(f1.includes('讨论中') && f1.includes('目标'), `reduced motion: and shows the task page with its document, not yet started: ${f1}`)
  }

  // Walking through once, then a click.
  {
    const w = page()
    await mount(w)
    await sleep(150)
    const ws = [...w.document.querySelectorAll('[data-walk]')]
    const p0 = ws[0].querySelector('[data-walk-panel]')
    ok(seen(p0).includes('1 条回复') && !seen(p0).includes('已开始'), `walk 1 starts on the screen before step 1: ${seen(p0)}`)
    eq(onStep(ws[0]), '', 'no step is highlighted before step 1 starts')
    ok(await until(() => onStep(ws[0]) === '1', 3000), 'walk 1 highlights step 1')
    ok(await until(() => onStep(ws[0]) === '3' && seen(p0) === restWalk[0], 40000), 'walk 1 ends on step 3 with the last screen')
    await sleep(4000)
    eq(onStep(ws[0]), '3', 'and stops there')
    const p1 = ws[1].querySelector('[data-walk-panel]')
    ws[1].querySelector('[data-walk-step="1"]').dispatchEvent(new w.MouseEvent('click', { bubbles: true }))
    eq(onStep(ws[1]), '1', 'clicking a step highlights it')
    ok(await until(() => seen(p1).includes('审阅重点') && seen(p1).includes('改动') && !seen(p1).includes('已采纳'), 5000), `clicking step 1 of walk 2 shows this delivery in 改动: ${seen(p1)}`)
    // The window is as tall as the frame on screen: nothing holds it at the
    // tallest frame, and once a step has settled no fixed height is left on it.
    await sleep(1200)
    for (const [i, x] of ws.entries()) {
      const win = x.querySelector('.dp-win')
      eq(win.style.minHeight, '', `walk ${i + 1}: no min-height holds the window at its tallest frame`)
      eq(win.style.height, '', `walk ${i + 1}: after a step settles the window has no fixed height`)
    }
  }
}

// ---------- what the build refuses ----------
// Straight through the renderer build.mjs uses, with the product's strings
// registered the way build.mjs registers them.
{
  const { renderDemo, registerUiStrings } = await import(path.join(SITE, 'src/demos.mjs'))
  const dir = path.join(REPO, 'frontend/src/i18n/messages/zh-CN')
  const leaves = (v) => (typeof v === 'string' ? [v] : v && typeof v === 'object' ? Object.values(v).flatMap(leaves) : [])
  registerUiStrings(fs.readdirSync(dir).filter((f) => f.endsWith('.json')).flatMap((f) => leaves(JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')))))
  const run = (fence) => { try { renderDemo('demo-panel', fence, 'test'); return '' } catch (e) { return e.message } }
  eq(run('title: 好的\ncaption: 一行\nparts:\n  - kind: composer\n    placeholder: 输入消息，@芝士 交给它处理\n    button: 交给芝士\n    type: 你好\n    at: 1\n    press: 2'), '', 'a panel quoting real labels builds')
  const made = run('title: 假按钮\ncaption: 一行\nparts:\n  - kind: composer\n    placeholder: 输入消息，@芝士 交给它处理\n    button: 立即派活\n    type: 你好\n    at: 1\n    press: 2')
  ok(/立即派活/.test(made) && /not a string the product shows/.test(made), `a label the product does not show fails the build: «${made}»`)
  const long = run('title: 太长\ncaption: 一行\nparts:\n  - kind: msg\n    who: 你\n    say: 一\n    at: 5')
  ok(/at most 4/.test(long), `a demo of more than four beats fails the build: «${long}»`)
  eq(run('title: 对话框\ncaption: 一行\nparts:\n  - kind: field\n    label: 项目名称\n    value: 组会资料\n  - kind: buttons\n    actions: 取消 | 下一步\n    pressing: 下一步\n    press: 1'), '', 'a dialog quoting real field and button labels builds')
  const field = run('title: 假字段\ncaption: 一行\nparts:\n  - kind: field\n    label: 项目代号\n  - kind: buttons\n    actions: 取消 | 下一步\n    pressing: 下一步\n    press: 1')
  ok(/项目代号/.test(field) && /not a string the product shows/.test(field), `a field label the product does not show fails the build: «${field}»`)
  eq(run('title: 一行\ncaption: 一行\nparts:\n  - kind: row\n    title: 我的 MacBook\n    sub: 刚刚\n    status: 在线\n    button: 发布\n    press: 1'), '', 'a row with its own name and a real status and button builds')
  const row = run('title: 假状态\ncaption: 一行\nparts:\n  - kind: row\n    title: 我的 MacBook\n    status: 飞速运转\n    button: 发布\n    press: 1')
  ok(/飞速运转/.test(row) && /not a string the product shows/.test(row), `a row status the product does not show fails the build: «${row}»`)
  const still = run('title: 不动\ncaption: 一行\nparts:\n  - kind: msg\n    who: 你\n    say: 一')
  ok(/picture/.test(still), `a panel where nothing changes fails the build: «${still}»`)
}

if (failures.length) {
  console.error(`\ndemo-panel: ${failures.length} of ${checks} checks failed\n`)
  for (const f of failures) console.error(`  - ${f}`)
  process.exit(1)
}
console.log(`demo-panel: ${checks} checks passed (${out})`)
