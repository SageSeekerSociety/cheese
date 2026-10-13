// `:::walk` in the browser: a numbered list beside one panel, where the panel
// shows the screen after the highlighted step. The steps are the only control
// and the only progress indicator. The HTML build.mjs wrote shows the screen
// after the last step, with no step highlighted, and that is what stays without
// a script or with reduced motion (there the steps still switch the frame, with
// nothing moving). The first time it is mostly in view it walks through once —
// the screen before step 1, then each step — and stops on the last; clicking or
// tapping a step shows the screen after that step.
import { panelStage } from './panel-window.mjs'

const $$ = (s, r) => [...r.querySelectorAll(s)]
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

// How long a step stays highlighted: long enough to read it beside its frame.
const BEFORE = 900
const HOLD = 1500
const PER_CHAR = 30
const MAX_HOLD = 3600

export function mountWalk(el, { reduced = false } = {}) {
  const steps = $$('[data-walk-step]', el)
  const fig = el.querySelector('[data-walk-panel]')
  if (!steps.length || !fig) return
  const stage = panelStage(fig)
  if (!stage.win) return
  el.classList.add('walk-live')
  let run = 0

  const mark = (n) => steps.forEach((s, i) => {
    s.classList.toggle('on', i + 1 === n)
    s.setAttribute('aria-current', i + 1 === n ? 'step' : 'false')
  })

  steps.forEach((s, i) => {
    s.tabIndex = 0
    s.setAttribute('role', 'button')
    const pick = async () => {
      const me = ++run
      mark(i + 1)
      if (reduced) return stage.jump(i + 1)
      // From the screen before this step, so the press that the step describes is seen.
      stage.jump(i, { animate: true })
      await stage.go(i + 1)
      if (run !== me) return
    }
    s.addEventListener('click', pick)
    s.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return
      e.preventDefault()
      pick()
    })
  })

  stage.jump(stage.beats)
  if (reduced) return

  async function walk() {
    const me = ++run
    mark(0)
    stage.jump(0, { animate: true })
    await wait(BEFORE)
    for (let n = 1; n <= steps.length; n++) {
      if (run !== me) return
      mark(n)
      if (!(await stage.go(n))) return
      await wait(Math.min(MAX_HOLD, HOLD + steps[n - 1].textContent.length * PER_CHAR))
    }
  }

  if (typeof IntersectionObserver === 'function') {
    const io = new IntersectionObserver((es) => {
      if (!es.some((e) => e.isIntersecting)) return
      io.disconnect()
      walk()
    }, { threshold: 0.5 })
    io.observe(el)
  }
}
