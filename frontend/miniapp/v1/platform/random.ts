// `@platform/random`: random numbers an app can repeat. Given the same seed,
// the same numbers come out, in every browser — so the picture the platform's
// check takes is the picture the reader sees, and an app can assert what its
// simulation produces. It is shell 1's contract: it may grow, never change.

export type Random = {
  /** A number in [0, 1). */
  next(): number
  /** A number in [a, b). */
  uniform(a: number, b: number): number
  /** A whole number from a to b, both included. */
  int(a: number, b: number): number
  normal(mean?: number, sd?: number): number
  /** The wait until the next event, when events come `rate` times per unit of time. */
  exponential(rate: number): number
  /** How many events come in one unit of time, when `mean` come on average. */
  poisson(mean: number): number
  pick<T>(list: readonly T[]): T
  /** A shuffled copy; the list itself is left as it was. */
  shuffle<T>(list: readonly T[]): T[]
}

export function random(seed = 1): Random {
  // mulberry32: a 32-bit state, integer arithmetic only, so every engine
  // produces the same sequence.
  let a = Math.trunc(Number(seed) || 0) >>> 0
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  let spare: number | null = null
  const r: Random = {
    next,
    uniform: (lo, hi) => lo + (hi - lo) * next(),
    int: (lo, hi) => Math.floor(lo + (Math.floor(hi) - lo + 1) * next()),
    normal(mean = 0, sd = 1) {
      if (spare !== null) {
        const z = spare
        spare = null
        return mean + sd * z
      }
      let u = 0
      while (u === 0) u = next()
      const v = next()
      const m = Math.sqrt(-2 * Math.log(u))
      spare = m * Math.sin(2 * Math.PI * v)
      return mean + sd * m * Math.cos(2 * Math.PI * v)
    },
    exponential: (rate) => -Math.log(1 - next()) / rate,
    poisson(mean) {
      // Counting arrivals for a small mean; for a large one, the normal it nears.
      if (mean > 30) return Math.max(0, Math.round(r.normal(mean, Math.sqrt(mean))))
      const limit = Math.exp(-mean)
      let k = 0
      for (let p = next(); p > limit; p *= next()) k++
      return k
    },
    pick: (list) => list[Math.floor(next() * list.length)],
    shuffle(list) {
      const out = list.slice()
      for (let i = out.length - 1; i > 0; i--) {
        const j = Math.floor(next() * (i + 1))
        ;[out[i], out[j]] = [out[j], out[i]]
      }
      return out
    },
  }
  return r
}
