// `@platform/sim`: a process that moves in steps of model time — an epidemic,
// a queue, an orbit — played, paused and reset by the reader. The app says what
// the start is, what one step does and, for a process that finishes, where it
// ends; how many steps a frame takes, so the pace is the same on every screen,
// stopping at the end, playing again from the start, and stopping when the app
// goes, are here.
// It is shell 1's contract: it may grow, never change.
import { getCurrentScope, onScopeDispose, reactive, shallowRef, triggerRef } from 'vue'

export type Simulation<S> = {
  /** Where the process is now. */
  state: S
  /** Model time: steps taken × dt. */
  t: number
  steps: number
  running: boolean
  /** The process has reached its `end`. */
  done: boolean
  /** From where it is; at the end, from the start again. */
  play(): void
  pause(): void
  /** Back to the start, paused. */
  reset(): void
  /** Take `n` steps at once (default 1), never past the end. */
  advance(n?: number): void
}

/** More steps than this in one frame and the frame is given up on: the
 *  process is behind, not the screen. */
const MOST_PER_FRAME = 2000

export function simulate<S>(options: {
  init: () => S
  /** The next state from this one; it may change `state` and return it. */
  step: (state: S, dt: number, t: number) => S
  /** Model time one step covers (default 1). */
  dt?: number
  /** Steps per second while playing (default 10). */
  speed?: number
  /** Steps the process takes in all, for one that finishes: playing stops
   *  there. Without it the process runs until paused. */
  end?: number
}): Simulation<S> {
  const dt = options.dt ?? 1
  const end = options.end ?? Infinity
  const speed = Math.max(0, options.speed ?? 10)
  let frame = 0
  let last = 0
  let owed = 0
  // The state is the app's own plain object: a step works on it at full
  // speed, and the page is told once per frame, not once per cell changed.
  const state = shallowRef(options.init())

  const sim = reactive({
    state,
    t: 0,
    steps: 0,
    running: false,
    get done() {
      return sim.steps >= end
    },
    play() {
      if (sim.running) return
      if (sim.done) sim.reset()
      sim.running = true
      last = performance.now()
      owed = 0
      frame = requestAnimationFrame(tick)
    },
    pause() {
      sim.running = false
      cancelAnimationFrame(frame)
    },
    reset() {
      sim.pause()
      state.value = options.init()
      sim.t = 0
      sim.steps = 0
    },
    advance(n = 1) {
      // The steps run on the plain state, and the page hears of it once.
      let s = state.value
      let { t, steps } = sim
      n = Math.min(n, end - steps)
      if (n <= 0) return
      for (let i = 0; i < n; i++) {
        s = options.step(s, dt, t)
        steps += 1
        t = steps * dt
      }
      if (s === state.value) triggerRef(state)
      else state.value = s
      sim.t = t
      sim.steps = steps
    },
  }) as unknown as Simulation<S>

  function tick(now: number) {
    if (!sim.running) return
    owed += ((now - last) / 1000) * speed
    last = now
    const n = Math.min(Math.floor(owed), MOST_PER_FRAME)
    owed -= Math.floor(owed)
    if (n > 0) sim.advance(n)
    if (sim.done) sim.running = false
    else frame = requestAnimationFrame(tick)
  }

  if (getCurrentScope()) onScopeDispose(() => cancelAnimationFrame(frame))
  return sim
}

/** One fourth-order Runge–Kutta step of dy/dt = f(t, y), y a list of numbers. */
export function rk4(f: (t: number, y: number[]) => number[], y: number[], t: number, dt: number): number[] {
  const add = (a: number[], b: number[], k: number) => a.map((v, i) => v + b[i] * k)
  const k1 = f(t, y)
  const k2 = f(t + dt / 2, add(y, k1, dt / 2))
  const k3 = f(t + dt / 2, add(y, k2, dt / 2))
  const k4 = f(t + dt, add(y, k3, dt))
  return y.map((v, i) => v + (dt / 6) * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i]))
}
