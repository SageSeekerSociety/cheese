// `@platform/sim`: a process that moves in steps of model time — an epidemic,
// a queue, an orbit — played, paused and reset by the reader. The app says what
// the start is and what one step does; how many steps a frame takes, so the
// pace is the same on every screen, and stopping when the app goes, are here.
// It is shell 1's contract: it may grow, never change.
import { getCurrentScope, onScopeDispose, reactive, shallowRef, triggerRef } from 'vue'

export type Simulation<S> = {
  /** Where the process is now. */
  state: S
  /** Model time: steps taken × dt. */
  t: number
  steps: number
  running: boolean
  play(): void
  pause(): void
  /** Back to the start (with its `start` steps taken), paused. */
  reset(): void
  /** Take `n` steps at once (default 1). */
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
  /** Steps already taken when the app opens, so its first picture shows something. */
  start?: number
}): Simulation<S> {
  const dt = options.dt ?? 1
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
    play() {
      if (sim.running) return
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
      sim.advance(options.start ?? 0)
    },
    advance(n = 1) {
      // The steps run on the plain state, and the page hears of it once.
      let s = state.value
      let { t, steps } = sim
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
    frame = requestAnimationFrame(tick)
  }

  sim.advance(options.start ?? 0)
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
