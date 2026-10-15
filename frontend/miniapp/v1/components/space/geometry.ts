// The arithmetic behind what a <c-space> draws, apart from three.js: the
// numbers on the axes, a surface sampled from the function an app gives it,
// the level lines of a height, an implicit surface's triangles, and the colour
// ramp a value is painted with. Everything is in the app's own coordinates.

export type Vec3 = [number, number, number]
export type Range = [number, number]

/** Round numbers for an axis: about `count` steps of 1, 2 or 5 times a power of ten. */
export function ticks([lo, hi]: Range, count = 5): number[] {
  const raw = (hi - lo) / count
  if (!(raw > 0)) return [lo]
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)))
  return out
}

/** A grid of points on a surface, row by row, with NaN where the function has no value. */
export type Grid = { positions: Float32Array; nu: number; nv: number }

/** `z = f(x, y)` over a rectangle. */
export function graphGrid(f: (x: number, y: number) => number, xr: Range, yr: Range, n: number): Grid {
  const positions = new Float32Array((n + 1) * (n + 1) * 3)
  let k = 0
  for (let j = 0; j <= n; j++)
    for (let i = 0; i <= n; i++) {
      const x = xr[0] + ((xr[1] - xr[0]) * i) / n
      const y = yr[0] + ((yr[1] - yr[0]) * j) / n
      const z = Number(f(x, y))
      positions[k++] = x
      positions[k++] = y
      positions[k++] = Number.isFinite(z) ? z : NaN
    }
  return { positions, nu: n, nv: n }
}

/** `(u, v) → [x, y, z]` over a rectangle of parameters. */
export function paramGrid(f: (u: number, v: number) => unknown, ur: Range, vr: Range, n: number): Grid {
  const positions = new Float32Array((n + 1) * (n + 1) * 3)
  let k = 0
  for (let j = 0; j <= n; j++)
    for (let i = 0; i <= n; i++) {
      const p = f(ur[0] + ((ur[1] - ur[0]) * i) / n, vr[0] + ((vr[1] - vr[0]) * j) / n)
      const ok = Array.isArray(p) && p.length >= 3 && p.slice(0, 3).every((c) => Number.isFinite(Number(c)))
      for (let c = 0; c < 3; c++) positions[k++] = ok ? Number((p as number[])[c]) : NaN
    }
  return { positions, nu: n, nv: n }
}

/** The triangles of a grid, leaving out any that touch a point with no value. */
export function gridIndex({ positions, nu }: Grid): number[] {
  const index: number[] = []
  const at = (i: number, j: number) => j * (nu + 1) + i
  const ok = (p: number) => Number.isFinite(positions[p * 3 + 2]) && Number.isFinite(positions[p * 3])
  for (let j = 0; j < nu; j++)
    for (let i = 0; i < nu; i++) {
      const a = at(i, j)
      const b = at(i + 1, j)
      const c = at(i + 1, j + 1)
      const d = at(i, j + 1)
      if (ok(a) && ok(b) && ok(c)) index.push(a, b, c)
      if (ok(a) && ok(c) && ok(d)) index.push(a, c, d)
    }
  return index
}

/** Every `every`-th line of the grid in each direction, as segments: the
 *  surface's own lines of constant u and constant v. */
export function gridLines({ positions, nu, nv }: Grid, every: number): number[] {
  const out: number[] = []
  const p = (i: number, j: number) => {
    const k = (j * (nu + 1) + i) * 3
    return [positions[k], positions[k + 1], positions[k + 2]]
  }
  const seg = (a: number[], b: number[]) => {
    if ([...a, ...b].every(Number.isFinite)) out.push(...a, ...b)
  }
  for (let j = 0; j <= nv; j += every) for (let i = 0; i < nu; i++) seg(p(i, j), p(i + 1, j))
  for (let i = 0; i <= nu; i += every) for (let j = 0; j < nv; j++) seg(p(i, j), p(i, j + 1))
  return out
}

/** The level lines of a height grid at each of `levels`, as segments
 *  (marching squares, one segment pair per saddle cell). */
export function contours({ positions, nu }: Grid, levels: number[]): number[] {
  const out: number[] = []
  const row = nu + 1
  const cx = [0, 0, 0, 0]
  const cy = [0, 0, 0, 0]
  const cz = [0, 0, 0, 0]
  const hit: number[] = []
  for (let j = 0; j < nu; j++)
    for (let i = 0; i < nu; i++) {
      const k = [j * row + i, j * row + i + 1, (j + 1) * row + i + 1, (j + 1) * row + i]
      let ok = true
      let lo = Infinity
      let hi = -Infinity
      for (let c = 0; c < 4; c++) {
        const p = k[c] * 3
        cx[c] = positions[p]
        cy[c] = positions[p + 1]
        cz[c] = positions[p + 2]
        if (!Number.isFinite(cz[c])) ok = false
        lo = Math.min(lo, cz[c])
        hi = Math.max(hi, cz[c])
      }
      if (!ok) continue
      for (const h of levels) {
        if (h < lo || h > hi) continue
        hit.length = 0
        for (let e = 0; e < 4; e++) {
          const f = (e + 1) % 4
          if (cz[e] < h !== cz[f] < h) {
            const t = (h - cz[e]) / (cz[f] - cz[e])
            hit.push(cx[e] + t * (cx[f] - cx[e]), cy[e] + t * (cy[f] - cy[e]), h)
          }
        }
        for (let q = 0; q + 5 < hit.length; q += 6)
          out.push(hit[q], hit[q + 1], hit[q + 2], hit[q + 3], hit[q + 4], hit[q + 5])
      }
    }
  return out
}

// A cube split into six tetrahedra that share its main diagonal (corner 0 to 6).
const CORNERS: Vec3[] = [
  [0, 0, 0],
  [1, 0, 0],
  [1, 1, 0],
  [0, 1, 0],
  [0, 0, 1],
  [1, 0, 1],
  [1, 1, 1],
  [0, 1, 1],
]
const CX = CORNERS.map((c) => c[0])
const CY = CORNERS.map((c) => c[1])
const CZ = CORNERS.map((c) => c[2])
const TETRA = [
  [0, 6, 1, 2],
  [0, 6, 2, 3],
  [0, 6, 3, 7],
  [0, 6, 7, 4],
  [0, 6, 4, 5],
  [0, 6, 5, 1],
]

/** The surface F(x, y, z) = level inside a box, as triangles with a normal at
 *  each corner (marching tetrahedra: no lookup tables, no ambiguous cases).
 *  The normals come from F's gradient, so the surface is shaded smoothly, and
 *  every triangle faces the way the gradient points, so its two sides are lit
 *  consistently. */
export function implicit(
  F: (x: number, y: number, z: number) => number,
  [xr, yr, zr]: [Range, Range, Range],
  n: number,
  level = 0
): { positions: Float32Array; normals: Float32Array } {
  const step = [(xr[1] - xr[0]) / n, (yr[1] - yr[0]) / n, (zr[1] - zr[0]) / n]
  const N = n + 1
  const at = (i: number, j: number, k: number) => (k * N + j) * N + i
  const values = new Float32Array(N * N * N)
  for (let k = 0; k < N; k++)
    for (let j = 0; j < N; j++)
      for (let i = 0; i < N; i++) {
        const v = Number(F(xr[0] + i * step[0], yr[0] + j * step[1], zr[0] + k * step[2])) - level
        values[at(i, j, k)] = Number.isFinite(v) ? v : NaN
      }
  // The gradient at each grid point, by differences with its neighbours.
  const grad = new Float32Array(N * N * N * 3)
  for (let k = 0; k < N; k++)
    for (let j = 0; j < N; j++)
      for (let i = 0; i < N; i++) {
        const o = at(i, j, k) * 3
        const d = (a: number, b: number, h: number) => (values[b] - values[a]) / h
        grad[o] = d(at(Math.max(i - 1, 0), j, k), at(Math.min(i + 1, n), j, k), step[0] * (i > 0 && i < n ? 2 : 1))
        grad[o + 1] = d(at(i, Math.max(j - 1, 0), k), at(i, Math.min(j + 1, n), k), step[1] * (j > 0 && j < n ? 2 : 1))
        grad[o + 2] = d(at(i, j, Math.max(k - 1, 0)), at(i, j, Math.min(k + 1, n)), step[2] * (k > 0 && k < n ? 2 : 1))
      }
  const pos: number[] = []
  const nor: number[] = []
  const id = new Int32Array(8)
  const val = new Float64Array(8)
  // One corner of a triangle: where the edge between grid points a and b
  // crosses zero, with the gradient there.
  const tri = new Float64Array(18)
  let [ci, cj, ck] = [0, 0, 0]
  const [x0, y0, z0] = [xr[0], yr[0], zr[0]]
  const [sx, sy, sz] = step
  /** Corner `slot` of the triangle: where the cell's edge from corner p to
   *  corner q crosses zero, with the gradient there. */
  const edge = (slot: number, p: number, q: number) => {
    const t = val[p] / (val[p] - val[q])
    tri[slot] = x0 + (ci + CX[p] + t * (CX[q] - CX[p])) * sx
    tri[slot + 1] = y0 + (cj + CY[p] + t * (CY[q] - CY[p])) * sy
    tri[slot + 2] = z0 + (ck + CZ[p] + t * (CZ[q] - CZ[p])) * sz
    const a = id[p] * 3
    const b = id[q] * 3
    tri[slot + 9] = grad[a] + t * (grad[b] - grad[a])
    tri[slot + 10] = grad[a + 1] + t * (grad[b + 1] - grad[a + 1])
    tri[slot + 11] = grad[a + 2] + t * (grad[b + 2] - grad[a + 2])
  }
  const emit = () => {
    // Face the way the gradient points.
    const [ax, ay, az, bx, by, bz, cx, cy, cz] = tri
    const nx = (by - ay) * (cz - az) - (bz - az) * (cy - ay)
    const ny = (bz - az) * (cx - ax) - (bx - ax) * (cz - az)
    const nz = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    const gx = tri[9] + tri[12] + tri[15]
    const gy = tri[10] + tri[13] + tri[16]
    const gz = tri[11] + tri[14] + tri[17]
    const flip = nx * gx + ny * gy + nz * gz < 0
    for (let k = 0; k < 3; k++) {
      const v = flip && k > 0 ? 3 - k : k
      pos.push(tri[v * 3], tri[v * 3 + 1], tri[v * 3 + 2])
      const a = tri[9 + v * 3]
      const b = tri[10 + v * 3]
      const c = tri[11 + v * 3]
      const len = Math.sqrt(a * a + b * b + c * c) || 1
      nor.push(a / len, b / len, c / len)
    }
  }
  for (let k = 0; k < n; k++)
    for (let j = 0; j < n; j++)
      for (let i = 0; i < n; i++) {
        // Most cells lie wholly on one side: tell from the values alone.
        let above = 0
        let finite = true
        for (let c = 0; c < 8; c++) {
          id[c] = at(i + CX[c], j + CY[c], k + CZ[c])
          val[c] = values[id[c]]
          if (!Number.isFinite(val[c])) finite = false
          if (val[c] > 0) above++
        }
        if (!finite || above === 0 || above === 8) continue
        ;[ci, cj, ck] = [i, j, k]
        for (const t of TETRA) {
          let inCount = 0
          for (const c of t) if (val[c] <= 0) inCount++
          if (inCount === 0 || inCount === 4) continue
          const inside = t.filter((c) => val[c] <= 0)
          const outside = t.filter((c) => val[c] > 0)
          if (inCount === 1 || inCount === 3) {
            const [lone, rest] = inCount === 1 ? [inside[0], outside] : [outside[0], inside]
            edge(0, lone, rest[0])
            edge(3, lone, rest[1])
            edge(6, lone, rest[2])
            emit()
          } else {
            const [a, b] = inside
            const [c, d] = outside
            edge(0, a, c)
            edge(3, a, d)
            edge(6, b, d)
            emit()
            edge(0, a, c)
            edge(3, b, d)
            edge(6, b, c)
            emit()
          }
        }
      }
  return { positions: new Float32Array(pos), normals: new Float32Array(nor) }
}

// A perceptually even ramp from low to high (viridis, five stops): readable on
// light and dark pages alike, and in grey.
const RAMP: Vec3[] = [
  [0.267, 0.005, 0.329],
  [0.231, 0.322, 0.546],
  [0.128, 0.567, 0.551],
  [0.369, 0.789, 0.383],
  [0.993, 0.906, 0.144],
]
/** The ramp's colour at `t` in 0..1, as sRGB components. */
export function ramp(t: number): Vec3 {
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (RAMP.length - 1)
  const i = Math.min(RAMP.length - 2, Math.floor(x))
  const f = x - i
  return [0, 1, 2].map((c) => RAMP[i][c] + f * (RAMP[i + 1][c] - RAMP[i][c])) as Vec3
}
