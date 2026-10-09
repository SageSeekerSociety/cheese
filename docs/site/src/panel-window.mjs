// `demo-panel` in the browser. The HTML build.mjs wrote is the last beat — the
// result — and that is what stays on screen when nothing plays: no script,
// reduced motion, or a reader who never scrolls this far. The first time the
// panel is mostly in view it rewinds to beat 0, plays each beat once and stops
// on the result. There is no progress bar and no controls; once it has played,
// a quiet 「重播」 runs it again.
//
// A part says when it is on screen (`data-at`, `data-until`); a button says
// which beat pressing it leads to (`data-press`), and is shown pressed for a
// moment before that beat. Typed text (`data-type`) is typed out. Nothing here
// writes a word the HTML did not already have.

const $$ = (s, r) => [...r.querySelectorAll(s)]

// How long a beat stays up: enough to take in what just arrived.
const HOLD = 1100
const PER_CHAR = 28
const MAX_READ = 1800
const PRESS = 420
const TYPE_CHAR = 45
const MAX_TYPE = 1600

const wait = (ms) => new Promise((r) => setTimeout(r, ms))

// The frames of one panel, and the two ways from one frame to the next: a
// button shown pressed, then the parts that arrive. A standalone panel plays
// them once (mountPanel); a panel beside a step list is driven by the steps
// (src/walk-window.mjs).
export function panelStage(el) {
  const beats = Number(el.dataset.beats || 0)
  const timed = $$('[data-at],[data-until]', el)
  const win = el.querySelector('.dp-win')
  const typed = new Map($$('[data-type]', el).map((n) => [n, n.textContent]))
  const on = (n, b) => Number(n.dataset.at || 0) <= b && (n.dataset.until === undefined || b < Number(n.dataset.until))
  let run = 0

  // Shows beat b; returns the parts that arrived with it.
  function show(b, animate) {
    const arrived = []
    for (const n of timed) {
      const vis = on(n, b)
      if (vis && n.hidden) arrived.push(n)
      n.hidden = !vis
      n.classList.toggle('dp-in', vis && animate && arrived.includes(n))
    }
    return arrived
  }

  async function type(n, me) {
    const text = typed.get(n) || ''
    const step = Math.min(TYPE_CHAR, MAX_TYPE / Math.max(1, text.length))
    for (let i = 1; i <= text.length; i++) {
      if (run !== me) return
      n.textContent = text.slice(0, i)
      await wait(step)
    }
  }

  // Hold the window at its tallest frame, so frames never move the page.
  function lock() {
    win.style.minHeight = ''
    let h = 0
    for (let b = 0; b <= beats; b++) { show(b, false); h = Math.max(h, win.offsetHeight) }
    win.style.minHeight = `${h}px`
  }

  // From whatever is up to beat b: the press that leads there, then the arrivals.
  // Resolves false if something else took over meanwhile.
  async function go(b, { press = true } = {}) {
    const me = ++run
    if (press) {
      const pressed = $$(`[data-press="${b}"]`, el).filter((n) => !n.closest('[hidden]'))
      if (pressed.length) {
        pressed.forEach((n) => n.classList.add('dp-pressing'))
        await wait(PRESS)
        pressed.forEach((n) => n.classList.remove('dp-pressing'))
        if (run !== me) return false
      }
    }
    for (const [n] of typed) n.textContent = ''
    const arrived = show(b, true)
    for (const [n, text] of typed) if (!arrived.includes(n)) n.textContent = text
    for (const n of arrived) if (typed.has(n)) await type(n, me)
    if (run !== me) return false
    const chars = arrived.filter((n) => !n.hasAttribute('data-type')).reduce((a, n) => a + n.textContent.length, 0)
    await wait(Math.min(MAX_READ, chars * PER_CHAR) / 2)
    return run === me
  }

  function jump(b) {
    run++
    for (const [n, text] of typed) n.textContent = text
    show(b, false)
  }

  return { beats, win, lock, go, jump, cancel: () => { run++ } }
}

export function mountPanel(el, { reduced = false } = {}) {
  const replay = el.querySelector('[data-dp-replay]')
  const stage = panelStage(el)
  if (!stage.beats || reduced || !stage.win) return
  el.classList.add('dp-live')
  let run = 0

  async function play() {
    const me = ++run
    el.classList.remove('dp-done')
    if (replay) replay.hidden = true
    stage.lock()
    stage.jump(0)
    for (let b = 1; b <= stage.beats; b++) {
      await wait(HOLD)
      if (run !== me) return
      if (!(await stage.go(b))) return
    }
    stage.jump(stage.beats)
    el.classList.add('dp-done')
    if (replay) replay.hidden = false
  }

  replay?.addEventListener('click', () => { play() })

  if (typeof IntersectionObserver === 'function') {
    const io = new IntersectionObserver((es) => {
      if (!es.some((e) => e.isIntersecting)) return
      io.disconnect()
      play()
    }, { threshold: 0.6 })
    io.observe(el)
  } else if (replay) {
    replay.hidden = false
  }
}
