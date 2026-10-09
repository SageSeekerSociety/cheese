// Switching state on the prerendered demo components. Every word a demo shows
// is already in the HTML (build.mjs put it there), so nothing here builds DOM
// or writes prose: it reveals steps, moves a bar, and re-runs a simulation over
// the parameters the fence declared.
import { evaluate, truthy, show, fill, parameters } from './demo-model.mjs'
import { mountContextWindow } from './context-window.mjs'
import { mountCiScope } from './ci-window.mjs'
import { mountFlow } from './flow-window.mjs'
import { mountMemory } from './memory-window.mjs'
import { mountArch } from './arch-window.mjs'
import { mountPanel } from './panel-window.mjs'
import { mountWalk } from './walk-window.mjs'

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
const $ = (s, r = document) => r.querySelector(s)
const $$ = (s, r = document) => [...r.querySelectorAll(s)]

// How long a step stays up while playing. Long enough to read a sentence.
const GAP = 1400

// ---------- demo-steps / demo-timeline ----------
function mountSteps(el) {
  const steps = $$('[data-dm-step]', el)
  const n = steps.length
  if (!n) return
  // Dimming and the controls hang off `.dm-live`, not off `html.js`: if this
  // script never loads, the demo stays a plain list you can read instead of a
  // list faded to a quarter.
  el.classList.add('dm-live')
  const range = $('[data-dm-range]', el)
  const count = $('[data-dm-count]', el)
  const fillEl = $('[data-dm-fill]', el)
  const segs = $$('[data-dm-jump]', el)
  const playBtn = $('[data-dm-play]', el)
  const playLabel = $('[data-dm-play-label]', el)
  const values = steps.map((s) => Number(s.dataset.value || 0))
  const total = values.reduce((a, b) => a + b, 0)
  const gates = steps.map((s) => !!$('[data-dm-go]', s))
  // Reduced motion gets the whole thing at once, and it never plays itself.
  let pos = reduced ? n : 1
  let playing = false
  let timer = 0

  function paint() {
    steps.forEach((s, i) => {
      s.classList.toggle('on', i === pos - 1)
      s.classList.toggle('done', i < pos - 1)
      const go = $('[data-dm-go]', s)
      if (go) go.hidden = !(i === pos - 1)
    })
    if (range) range.value = String(pos)
    if (count) count.textContent = `${pos} / ${n}`
    if (fillEl) {
      const sum = values.slice(0, pos).reduce((a, b) => a + b, 0)
      const pct = total > 0 ? (sum / total) * 100 : (pos / n) * 100
      fillEl.style.setProperty('--p', `${pct.toFixed(2)}%`)
    }
    segs.forEach((b, i) => b.classList.toggle('on', i < pos))
  }

  function stop() {
    playing = false
    clearTimeout(timer)
    timer = 0
    if (playBtn) playBtn.setAttribute('aria-pressed', 'false')
    if (playLabel) playLabel.textContent = '播放'
    paint()
  }

  // A demo with a stage (`embed:` in the fence) plays each step as a scene on
  // the product's own components, in an iframe. The list here still counts the
  // steps; the stage says when a step has finished playing, and only then does
  // the list move on — a fixed GAP would cut a long step off halfway.
  const stage = $('[data-dm-embed]', el)
  // The stage is the platform's page: the same origin under /docs/, another one
  // from the docs host.
  const stageOrigin = stage && new URL(stage.getAttribute('src'), location.href).origin
  const show = (andPlay) => stage?.contentWindow?.postMessage({ cheeseDemo: 'go', step: pos - 1, play: andPlay }, stageOrigin)
  if (stage) {
    window.addEventListener('message', (e) => {
      if (e.source !== stage.contentWindow || e.origin !== stageOrigin) return
      const kind = e.data?.cheeseDemo
      if (kind === 'ready') show(playing)
      if (kind === 'done' && playing && e.data.step === pos - 1) timer = setTimeout(tick, GAP / 3)
    })
  }

  function tick() {
    timer = 0
    if (pos >= n) return stop()
    pos++
    paint()
    if (gates[pos - 1]) return stop()   // it is the reader's turn to speak
    if (stage) show(true)
    else timer = setTimeout(tick, GAP)
  }

  function play() {
    if (playing) return stop()
    if (pos >= n) { pos = stage ? 1 : 0; paint() }
    playing = true
    if (playBtn) playBtn.setAttribute('aria-pressed', 'true')
    if (playLabel) playLabel.textContent = '暂停'
    if (stage) show(true)
    else timer = setTimeout(tick, GAP)
  }

  const goTo = (p) => { stop(); pos = Math.max(1, Math.min(n, p)); paint(); show(false) }
  const next = () => goTo(pos + 1)
  const prev = () => goTo(pos - 1)

  playBtn?.addEventListener('click', play)
  $('[data-dm-next]', el)?.addEventListener('click', next)
  $('[data-dm-prev]', el)?.addEventListener('click', prev)
  range?.addEventListener('input', () => goTo(Number(range.value)))
  segs.forEach((b) => b.addEventListener('click', () => goTo(Number(b.dataset.dmJump) + 1)))
  steps.forEach((s, i) => {
    $('[data-dm-go]', s)?.addEventListener('click', () => { goTo(i + 1); if (i + 1 < n) play() })
  })
  // Arrow keys walk the steps whenever the focus is inside the demo; the range
  // input keeps its own arrow handling, so leave it alone.
  el.addEventListener('keydown', (e) => {
    if (e.target === range) return
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    if (!el.contains(document.activeElement)) return
    e.preventDefault()
    if (e.key === 'ArrowRight') next(); else prev()
  })

  // Playing itself once, the first time it is on screen.
  if (!reduced && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((es) => {
      if (!es[0].isIntersecting) return
      io.disconnect()
      if (pos === 1) play()
    }, { threshold: 0.5 })
    io.observe(el)
  }
  paint()
}

// ---------- demo-sim ----------
function mountSim(el) {
  const inputs = $$('[data-sm-var]', el)
  if (!inputs.length) return
  const rules = JSON.parse($('[data-sm-rules]', el)?.textContent || '[]')
  const out = JSON.parse($('[data-sm-out]', el)?.textContent || '[]')
  const derived = JSON.parse($('[data-sm-derived]', el)?.textContent || '[]')
  const ruleEls = $$('[data-sm-rule]', el)
  const factEls = $$('[data-sm-fact]', el)
  const shown = new Map($$('[data-sm-show]', el).map((s) => [s.dataset.smShow, s]))

  const read = () => {
    const v = {}
    for (const i of inputs) v[i.dataset.smVar] = i.dataset.smKind === 'toggle' ? i.checked : i.dataset.smKind === 'choice' ? i.value : Number(i.value)
    return parameters({ vars: inputs.map((i) => ({ key: i.dataset.smVar, value: undefined })), derived }, v)
  }
  const say = (node, text) => { if (node) node.textContent = text }

  function paint() {
    let vals
    try { vals = read() } catch { return }
    for (const [key, node] of shown) say(node, show(vals[key]))
    let hit = -1
    rules.forEach((r, i) => {
      let on = false
      try { on = r.when === null || truthy(evaluate(r.when, vals)) } catch { on = false }
      if (on && hit < 0) hit = i
      const text = $('[data-sm-rule-text]', ruleEls[i])
      if (text) say(text, fill(r.text, vals))
      ruleEls[i]?.classList.toggle('hit', on && hit === i)
    })
    out.forEach((o, i) => {
      const node = factEls[i]
      if (!node) return
      let text
      try { text = show(evaluate(o.expr, vals)) } catch (e) { text = `（${e.message}）` }
      say(node, o.unit ? `${text} ${o.unit}` : text)
    })
  }

  el.addEventListener('input', paint)
  paint()
}

// The stages are the platform's pages. From the docs' own host they cannot read
// this site's theme preference (another origin's storage), so each is told the
// theme in its address; a changed address reloads it, and it comes back on the
// step the list is at.
export function themeStages(dark, root = document) {
  $$('iframe[data-dm-embed]', root).forEach((f) => {
    const url = new URL(f.getAttribute('src'), location.href)
    url.searchParams.set('theme', dark ? 'dark' : 'light')
    if (f.getAttribute('src') !== url.href) f.setAttribute('src', url.href)
  })
}

export function mountDemos(root = document) {
  $$('[data-walk]', root).forEach((el) => mountWalk(el, { reduced }))
  $$('[data-demo="panel"]:not([data-walk-panel])', root).forEach((el) => mountPanel(el, { reduced }))
  $$('[data-demo="steps"]', root).forEach(mountSteps)
  $$('[data-demo="sim"]', root).forEach(mountSim)
  $$('[data-demo="context"]', root).forEach(mountContextWindow)
  $$('[data-demo="ci"]', root).forEach(mountCiScope)
  $$('[data-demo="flow"]', root).forEach(mountFlow)
  $$('[data-demo="memory"]', root).forEach(mountMemory)
  $$('[data-demo="arch"]', root).forEach(mountArch)
}
