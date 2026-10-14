// What a <c-space> does with the elements inside it: each one becomes a
// three.js object in the app's own coordinates (z up, right-handed), slides to
// its new shape when its numbers change, and is drawn only when something did.
// The reader turns the view; a point the app lets them drag follows the
// pointer under the constraint it was given; a click on anything the app
// listens on reaches that element.
//
// Two layers. Bodies (surfaces, solids, meshes, lines, curves, point clouds,
// fields) live in a tree under `root`, which maps the app's ranges into a cube
// about the origin, and under the three.js group of every <c-group> they sit
// in. Marks (the dots of points, arrowheads, drop lines) keep the same size on
// screen whatever the ranges or the zoom, so they are placed in world space each
// frame, from where their element sits in that tree.
import { computed, type ComputedRef, type EffectScope, effectScope, type Ref, ref, watch } from 'vue'
import * as THREE from 'three'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js'
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js'

import { onTheme, token } from '../../theme'
import { announce } from '../emit'
import { duration, ease, still } from '../motion'
import { PLANE_ITEM_CHANGED } from '../planeItem'

import {
  contours,
  graphGrid,
  type Grid,
  gridIndex,
  gridLines,
  implicit,
  paramGrid,
  ramp,
  type Range,
  ticks,
  type Vec3,
} from './geometry'

type Held = HTMLElement & Record<string, unknown> & { clickable?: boolean }
type Fn = (...a: number[]) => unknown

export type Box = { x: Range; y: Range; z: Range; equal: boolean; axes: boolean; labels: [string, string, string] }

const ITEMS = new Set([
  'c-point',
  'c-curve',
  'c-line',
  'c-area',
  'c-surface',
  'c-box',
  'c-sphere',
  'c-cylinder',
  'c-cone',
  'c-mesh',
  'c-points',
  'c-field',
  'c-text',
  'c-group',
])

// ——— reading what an element holds ———

const isNum = (v: unknown) => v !== '' && v != null && Number.isFinite(Number(v))
const num = (v: unknown, fallback: number) => (isNum(v) ? Number(v) : fallback)
const flag = (v: unknown) => v === '' || v === true || v === 'true'
const fnOf = (v: unknown): Fn | null => (typeof v === 'function' ? (v as Fn) : null)
function list(v: unknown): unknown {
  if (typeof v !== 'string') return v
  try {
    return JSON.parse(v)
  } catch {
    return v
  }
}
function vec(v: unknown): Vec3 | null {
  const a = list(v)
  return Array.isArray(a) && a.length === 3 && a.every(isNum) ? (a.map(Number) as Vec3) : null
}
function range(v: unknown, fallback: Range): Range {
  const a = list(v)
  return Array.isArray(a) && a.length === 2 && a.every(isNum) && Number(a[0]) < Number(a[1])
    ? [Number(a[0]), Number(a[1])]
    : fallback
}
const show = (v: unknown) => {
  try {
    const s = typeof v === 'function' ? 'function' : JSON.stringify(v)
    return s && s.length > 60 ? `${s.slice(0, 60)}…` : String(s)
  } catch {
    return String(v)
  }
}

/** A mistake in how an element is written: the app is wrong, so the check
 *  refuses it, and the message says which element and what to write. */
export class Miswritten extends Error {}

// ——— colour ———

const paint = document.createElement('canvas').getContext('2d')
function cssColor(value: string): THREE.Color {
  if (paint) {
    paint.fillStyle = '#000'
    paint.fillStyle = value
    return new THREE.Color().setStyle(String(paint.fillStyle))
  }
  return new THREE.Color().setStyle(value)
}
const TONES = new Set(['ok', 'warn', 'danger', 'muted', 'accent', 'ink', 'text'])
function tone(v: unknown, fallback: string): THREE.Color {
  const t = String(v ?? '')
  if (/^[1-8]$/.test(t)) return cssColor(token(`chart-${t}`))
  return cssColor(token(TONES.has(t) ? t : fallback))
}

// ——— what each kind of element is, as numbers ———

/** An element as numbers that can slide (`nums`), what else it needs to be
 *  drawn (`style`), and `shape`: two specs with the same shape can slide from
 *  one to the other, number by number. */
type Spec = {
  nums: number[]
  shape: string
  style: Record<string, unknown>
  /** For an element drawn from thousands of samples: the sampling, run when
   *  the spec is used rather than while it is worked out. */
  make?: (coarse: boolean) => { nums: number[]; shape?: string; style?: Record<string, unknown> }
}

type Ctx = { box: Box }

/** Call a function at a few points while the spec is worked out, so the
 *  space learns which of the app's values it reads; the full sampling runs
 *  later, outside that bookkeeping (`make`), which would otherwise cost more
 *  than the function itself on every one of thousands of points. */
function probe<T>(points: number[][], f: (...a: number[]) => T): T[] {
  return points.map((p) => f(...p))
}
const across = (r: Range) => [r[0], (r[0] + r[1]) / 2, r[1]]

function surfaceSpec(el: Held, { box }: Ctx): Spec | null {
  const f = fnOf(el.f)
  if (!f)
    throw new Miswritten(
      '<c-surface> 需要 :f：两个参数的函数 (x, y) => z、参数方程 (u, v) => [x, y, z]，或三个参数的函数 (x, y, z) => 值（画出值为 0 的曲面）。'
    )
  const colorFn = fnOf(el.color)
  const style = { tone: el.tone, opacity: num(el.opacity, 1), wire: flag(el.wire), contours: el.contours }
  const tint = (pts: number[][]) => {
    if (colorFn)
      probe(
        pts.filter((q) => q.every(Number.isFinite)),
        colorFn
      )
  }
  if (f.length >= 3) {
    const xr = range(el.x, box.x)
    const yr = range(el.y, box.y)
    const zr = range(el.z, box.z)
    const level = num(el.level, 0)
    const pts = across(xr).flatMap((x) => across(yr).flatMap((y) => across(zr).map((z) => [x, y, z])))
    probe(pts, f)
    tint(pts)
    return {
      nums: [],
      shape: 'implicit',
      style: { ...style, form: 'implicit' },
      make: (coarse) => {
        const made = implicit(f as (x: number, y: number, z: number) => number, [xr, yr, zr], coarse ? 14 : 36, level)
        const nums = Array.from(made.positions)
        return {
          nums,
          shape: `implicit${nums.length}`,
          style: { normals: made.normals, colors: colorsOf(colorFn, nums) },
        }
      },
    }
  }
  const ur = el.u != null ? range(el.u, [0, 1]) : null
  const vr = el.v != null ? range(el.v, [0, 1]) : null
  const xr = range(el.x, box.x)
  const yr = range(el.y, box.y)
  const grid = ur || vr ? [across(ur ?? [0, 1]), across(vr ?? [0, 1])] : [across(xr), across(yr)]
  const seen = probe(
    grid[0].flatMap((a) => grid[1].map((b) => [a, b])),
    f
  )
  const parametric = seen.some(Array.isArray) || ur != null || vr != null
  if (parametric && seen.some((v) => v != null && !Array.isArray(v)))
    throw new Miswritten(
      `<c-surface> 写了 :u 或 :v，是参数方程，:f 应返回 [x, y, z]；现在返回 ${show(seen.find((v) => !Array.isArray(v)))}。`
    )
  tint(
    parametric
      ? (seen.filter(Array.isArray) as number[][])
      : grid[0].flatMap((x, i) => grid[1].map((y, j) => [x, y, Number(seen[i * 3 + j])]))
  )
  return {
    nums: [],
    shape: 'grid',
    style: { ...style, form: parametric ? 'param' : 'graph' },
    make: (coarse) => {
      const n = coarse ? 24 : 48
      const g = parametric
        ? paramGrid(f, ur ?? [0, 1], vr ?? [0, 1], n)
        : graphGrid(f as (x: number, y: number) => number, xr, yr, n)
      const nums = Array.from(g.positions)
      if (!nums.some(Number.isFinite)) throw new Miswritten('<c-surface> 的 :f 在整个范围内都没有返回有效的数。')
      return { nums, shape: `grid${n}`, style: { n, colors: colorsOf(colorFn, nums) } }
    },
  }
}

function specOf(el: Held, kind: string, ctx: Ctx): Spec | null {
  const base = { tone: el.tone, label: el.label == null ? '' : String(el.label), opacity: num(el.opacity, 1) }
  switch (kind) {
    case 'c-point': {
      const p = vec(el.value)
      if (!p) throw new Miswritten(`<c-point> 在 <c-space> 中的值应为 [x, y, z] 三个数，现在是 ${show(el.value)}。`)
      const from = el.from == null || el.from === '' ? null : vec(el.from)
      if (el.from != null && el.from !== '' && !from)
        throw new Miswritten(`<c-point> 的 :from 应为 [x, y, z] 三个数，现在是 ${show(el.from)}。`)
      const drag = String(el.drag ?? '')
      if (drag && !['x', 'y', 'z', 'xy', 'xz', 'yz'].includes(drag))
        throw new Miswritten(
          `<c-point> 的 drag 只能是 x、y、z、xy、xz 或 yz（最多沿两个方向拖动），现在是「${drag}」。`
        )
      return {
        nums: from ? [...p, ...from] : p,
        shape: from ? 'vector' : 'point',
        style: { ...base, drag, on: fnOf(el.on), drop: flag(el.drop), clickable: el.clickable },
      }
    }
    case 'c-line': {
      const a = vec(el.from)
      const b = vec(el.to)
      if (!a || !b)
        throw new Miswritten(
          `<c-line> 在 <c-space> 中要写 :from 和 :to，各为 [x, y, z]；现在是 ${show(el.from)} 和 ${show(el.to)}。`
        )
      return { nums: [...a, ...b], shape: 'line', style: { ...base, dashed: flag(el.dashed) } }
    }
    case 'c-curve': {
      const nums: number[] = []
      if (el.points != null) {
        const pts = list(el.points)
        if (!Array.isArray(pts))
          throw new Miswritten(`<c-curve> 的 :points 应为由 [x, y, z] 组成的数组，现在是 ${show(el.points)}。`)
        for (const p of pts) {
          const v = vec(p)
          if (!v) throw new Miswritten(`<c-curve> 的 :points 中每一项应为 [x, y, z]，其中有 ${show(p)}。`)
          nums.push(...v)
        }
      } else {
        const f = fnOf(el.f)
        if (!f) throw new Miswritten('<c-curve> 在 <c-space> 中要写 :f="t => [x, y, z]"（参数方程）或 :points。')
        const from = num(el.from, 0)
        const to = num(el.to, 1)
        const [first] = probe([[from], [(from + to) / 2], [to]], f)
        if (!vec(first))
          throw new Miswritten(`<c-curve> 在 <c-space> 中的 :f 应返回 [x, y, z]；f(${from}) 返回了 ${show(first)}。`)
        const N = 240
        return {
          nums: [],
          shape: `curve${(N + 1) * 3}`,
          style: { ...base, dashed: flag(el.dashed) },
          make: () => {
            const out: number[] = []
            for (let i = 0; i <= N; i++) out.push(...(vec(f(from + ((to - from) * i) / N)) ?? [NaN, NaN, NaN]))
            return { nums: out }
          },
        }
      }
      return { nums, shape: `curve${nums.length}`, style: { ...base, dashed: flag(el.dashed) } }
    }
    case 'c-area': {
      const pts = list(el.points)
      const vs = Array.isArray(pts) ? pts.map(vec) : []
      if (vs.length < 3 || vs.some((v) => !v))
        throw new Miswritten(
          `<c-area> 在 <c-space> 中要写 :points：至少三个 [x, y, z]，依次是多边形的顶点；现在是 ${show(el.points)}。`
        )
      return {
        nums: (vs as Vec3[]).flat(),
        shape: `area${vs.length}`,
        style: { ...base, opacity: num(el.opacity, 0.35) },
      }
    }
    case 'c-surface':
      return surfaceSpec(el, ctx)
    case 'c-sphere': {
      const c = vec(el.center) ?? [0, 0, 0]
      if (el.center != null && !vec(el.center))
        throw new Miswritten(`<c-sphere> 的 :center 应为 [x, y, z]，现在是 ${show(el.center)}。`)
      return {
        nums: [...c, num(el.radius, 1)],
        shape: 'sphere',
        style: { ...base, wire: flag(el.wire), clickable: el.clickable },
      }
    }
    case 'c-box':
    case 'c-cylinder':
    case 'c-cone': {
      const a = vec(el.from)
      const b = vec(el.to)
      if (!a || !b)
        throw new Miswritten(
          `<${kind}> 要写 :from 和 :to，各为 [x, y, z]${kind === 'c-box' ? '（相对的两个角）' : '（两端的中心）'}；现在是 ${show(el.from)} 和 ${show(el.to)}。`
        )
      return {
        nums: kind === 'c-box' ? [...a, ...b] : [...a, ...b, num(el.radius, 0.1)],
        shape: kind,
        style: { ...base, wire: flag(el.wire), clickable: el.clickable },
      }
    }
    case 'c-mesh': {
      const vs = list(el.vertices)
      const fs = list(el.faces)
      if (!Array.isArray(vs) || vs.some((v) => !vec(v)))
        throw new Miswritten(`<c-mesh> 的 :vertices 应为由 [x, y, z] 组成的数组，现在是 ${show(el.vertices)}。`)
      if (
        !Array.isArray(fs) ||
        fs.some(
          (f) => !Array.isArray(f) || f.length < 3 || f.some((i) => !Number.isInteger(i) || i < 0 || i >= vs.length)
        )
      )
        throw new Miswritten(
          `<c-mesh> 的 :faces 应为面的列表，每个面是至少三个顶点序号（从 0 开始，小于顶点数 ${vs.length}）；现在是 ${show(el.faces)}。`
        )
      return {
        nums: (vs as unknown[]).flatMap((v) => vec(v) as Vec3),
        shape: `mesh${vs.length}:${JSON.stringify(fs)}`,
        style: { ...base, faces: fs, wire: flag(el.wire), color: fnOf(el.color), clickable: el.clickable },
      }
    }
    case 'c-points': {
      const pts = list(el.points)
      if (!Array.isArray(pts))
        throw new Miswritten(`<c-points> 的 :points 应为由 [x, y, z] 组成的数组，现在是 ${show(el.points)}。`)
      const nums: number[] = []
      for (const p of pts) {
        const v = vec(p)
        if (!v) throw new Miswritten(`<c-points> 的 :points 中每一项应为 [x, y, z]，其中有 ${show(p)}。`)
        nums.push(...v)
      }
      const colorFn = fnOf(el.color)
      if (colorFn && nums.length) probe([nums.slice(0, 3)], colorFn)
      return {
        nums,
        shape: `points${nums.length}`,
        style: { ...base, size: num(el.size, 3) },
        make: () => ({ nums, style: { colors: colorsOf(colorFn, nums) } }),
      }
    }
    case 'c-field': {
      const f = fnOf(el.f)
      if (!f) throw new Miswritten('<c-field> 需要 :f="(x, y, z) => [vx, vy, vz]"。')
      const box = ctx.box
      const nRaw = list(el.n)
      const n = (Array.isArray(nRaw) ? nRaw : [nRaw, nRaw, nRaw]).map((v) =>
        Math.max(1, Math.min(16, Math.round(num(v, 6))))
      )
      const rs = [range(el.x, box.x), range(el.y, box.y), range(el.z, box.z)]
      const at = (r: Range, i: number, count: number) =>
        count === 1 ? (r[0] + r[1]) / 2 : r[0] + ((r[1] - r[0]) * (i + 0.5)) / count
      const corner: Vec3 = [at(rs[0], 0, n[0]), at(rs[1], 0, n[1]), at(rs[2], 0, n[2])]
      const [first] = probe([corner, ...rs[0].flatMap((x) => rs[1].flatMap((y) => rs[2].map((z) => [x, y, z])))], f)
      if (!vec(first))
        throw new Miswritten(
          `<c-field> 的 :f 应返回 [vx, vy, vz]；f(${corner.map((c) => +c.toFixed(3)).join(', ')}) 返回了 ${show(first)}。`
        )
      const make = () => {
        const nums: number[] = []
        for (let i = 0; i < n[0]; i++)
          for (let j = 0; j < n[1]; j++)
            for (let k = 0; k < n[2]; k++) {
              const p: Vec3 = [at(rs[0], i, n[0]), at(rs[1], j, n[1]), at(rs[2], k, n[2])]
              nums.push(...p, ...(vec(f(...p)) ?? [0, 0, 0]))
            }
        return { nums }
      }
      const spacing = Math.min(...rs.map((r, a) => (n[a] > 1 ? (r[1] - r[0]) / n[a] : Infinity)))
      return {
        nums: [],
        shape: `field${n.join('x')}`,
        style: { ...base, spacing: Number.isFinite(spacing) ? spacing : 1 },
        make,
      }
    }
    case 'c-text': {
      const p = vec(el.at)
      if (!p) throw new Miswritten(`<c-text> 要写 :at="[x, y, z]"，文字放在这一点；现在是 ${show(el.at)}。`)
      return { nums: p, shape: 'text', style: base }
    }
    case 'c-group': {
      const pos = el.position == null ? [0, 0, 0] : vec(el.position)
      if (!pos) throw new Miswritten(`<c-group> 的 :position 应为 [x, y, z]，现在是 ${show(el.position)}。`)
      const q = new THREE.Quaternion()
      if (el.rotate != null && el.rotate !== '') {
        const r = list(el.rotate)
        const axis = Array.isArray(r) ? vec(r[0]) : null
        if (!Array.isArray(r) || r.length !== 2 || !axis || !isNum(r[1]) || Math.hypot(...axis) === 0)
          throw new Miswritten(
            `<c-group> 的 :rotate 应为 [转轴, 角度]，例如 [[0, 0, 1], 0.5] 表示绕 z 轴转 0.5 弧度；现在是 ${show(el.rotate)}。`
          )
        q.setFromAxisAngle(new THREE.Vector3(...axis).normalize(), Number(r[1]))
      }
      const sRaw = list(el.scale)
      const s = isNum(sRaw) ? [Number(sRaw), Number(sRaw), Number(sRaw)] : vec(sRaw) ?? [1, 1, 1]
      return { nums: [...pos, q.x, q.y, q.z, q.w, ...s], shape: 'group', style: {} }
    }
  }
  return null
}

/** The ramp positions an app's `:color` gives each point, 0..1; read while
 *  the spec is worked out, so the colours follow whatever the function reads. */
function colorsOf(f: unknown, nums: number[]): number[] | null {
  if (typeof f !== 'function') return null
  const out: number[] = []
  for (let i = 0; i < nums.length; i += 3) {
    const t = Number((f as Fn)(nums[i], nums[i + 1], nums[i + 2]))
    out.push(Number.isFinite(t) ? t : 0)
  }
  return out
}

function rampColors(ts: number[]): number[] {
  const out: number[] = []
  const c = new THREE.Color()
  for (const t of ts) {
    const [r, g, b] = ramp(t)
    out.push(...c.setRGB(r, g, b, THREE.SRGBColorSpace).toArray())
  }
  return out
}

/** Whether two specs draw the same picture. An app draws again on every
 *  change anywhere in it, handing most elements exactly what they had. */
function same(a: Spec, b: Spec) {
  if (a.shape !== b.shape || a.nums.length !== b.nums.length) return false
  for (let i = 0; i < a.nums.length; i++)
    if (a.nums[i] !== b.nums[i] && !(Number.isNaN(a.nums[i]) && Number.isNaN(b.nums[i]))) return false
  const ca = a.style.colors as number[] | null | undefined
  const cb = b.style.colors as number[] | null | undefined
  if (Boolean(ca) !== Boolean(cb) || (ca && cb && (ca.length !== cb.length || ca.some((v, i) => v !== cb[i]))))
    return false
  const plain = (s: Record<string, unknown>) =>
    JSON.stringify(s, (k, v) => (typeof v === 'function' || k === 'colors' || k === 'normals' ? undefined : v))
  return plain(a.style) === plain(b.style)
}

/** Between two numeric shapes; a group's rotation turns along the shorter arc. */
function between(kind: string, a: number[], b: number[], k: number): number[] {
  if (kind === 'c-group') {
    const q = new THREE.Quaternion(a[3], a[4], a[5], a[6]).slerp(new THREE.Quaternion(b[3], b[4], b[5], b[6]), k)
    const lerp = (i: number) => a[i] + (b[i] - a[i]) * k
    return [lerp(0), lerp(1), lerp(2), q.x, q.y, q.z, q.w, lerp(7), lerp(8), lerp(9)]
  }
  return b.map((v, i) => (Number.isFinite(a[i]) && Number.isFinite(v) ? a[i] + (v - a[i]) * k : v))
}

// ——— the space ———

type Rec = {
  el: Held
  kind: string
  parent: Rec | null
  scope: EffectScope
  version: Ref<number>
  spec: ComputedRef<Spec | null>
  /** What is drawn now, and what it is sliding towards. */
  shown: Spec | null
  slide: { from: number[]; to: Spec; start: number; span: number } | null
  last: number
  /** When the element last asked to be drawn anew, how long after the time
   *  before, and what it asked for, if it is not sampled yet. */
  asked: number
  gap: number
  pending: Spec | null
  refine: number
  body: THREE.Group
  marks: THREE.Group
  label: HTMLElement | null
}

/** What may run past the box and is cut at its faces. */
const CLIPPED = new Set(['c-surface', 'c-curve', 'c-line', 'c-area', 'c-mesh'])

const PX = { dot: 5, handle: 7, head: 9, line: 2, thin: 1.25 }
const FOV = 28

export class Space {
  /** The drawing surface; none while the space is out of sight and has given
   *  its WebGL context back (`sleep`), or after the browser took it. */
  renderer: THREE.WebGLRenderer | null = null
  readonly scene = new THREE.Scene()
  readonly camera = new THREE.PerspectiveCamera(FOV, 1, 0.01, 100)
  readonly root = new THREE.Group()
  private axesGroup = new THREE.Group()
  private marksLayer = new THREE.Group()
  private recs = new Map<Element, Rec>()
  private lineMaterials = new Set<LineMaterial>()
  private width = 1
  private height = 1
  private box: Box
  private boxRef: Ref<Box>
  private frame = 0
  private disposed = false
  private offTheme: () => void
  private watcher: MutationObserver
  /** The camera: around the z axis (az), above the floor (el), how far. */
  view = { az: -1.0, el: 0.45, zoom: 1 }
  private viewAnim: { from: Framing; to: Framing; start: number } | null = null
  private spin = { az: 0, el: 0 }
  /** The point being dragged, and the guide that shows its constraint. */
  private held: { rec: Rec; guide: THREE.Object3D | null; offset: { x: number; y: number } } | null = null
  onViewChanged: () => void = () => {}
  onError: (message: string) => void = () => {}

  constructor(
    readonly host: HTMLElement,
    readonly stage: HTMLElement,
    readonly overlay: HTMLElement,
    box: Box
  ) {
    this.box = box
    this.boxRef = ref(box) as Ref<Box>
    this.wake()
    this.camera.up.set(0, 0, 1)
    this.scene.add(this.root, this.marksLayer, this.camera)
    this.root.add(this.axesGroup)
    // Light that comes with the reader: a soft fill everywhere and a lamp over
    // the reader's left shoulder, so the side they look at is always lit.
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.75))
    const lamp = new THREE.DirectionalLight(0xffffff, 2.3)
    lamp.position.set(-1, 1.5, 1)
    this.camera.add(lamp, lamp.target)
    lamp.target.position.set(0, 0, -1)
    this.offTheme = onTheme(() => this.restyle())
    this.watcher = new MutationObserver(() => this.discover())
    this.watcher.observe(host, { childList: true, subtree: true })
    host.addEventListener(PLANE_ITEM_CHANGED, this.changed)
    this.sight.observe(host)
    document.addEventListener('visibilitychange', this.pageShown)
    this.setBox(box)
    this.discover()
  }

  // ——— the drawing surface ———
  //
  // A browser keeps only so many WebGL contexts at once (16 in Chrome) and
  // takes the oldest when another is made; a phone may take one back when
  // memory runs short. So a space out of sight for a while gives its context
  // back, and one whose context was taken makes a new one when it is next in
  // sight. Everything it draws is kept apart from the context and uploaded
  // again to the new one.

  private visible = true
  private dozing = 0
  private sight = new IntersectionObserver((entries) => {
    const now = entries[entries.length - 1]?.isIntersecting ?? true
    if (now === this.visible) return
    this.visible = now
    clearTimeout(this.dozing)
    // Back in sight: draw what changed while it was not, with the context it
    // kept or a new one.
    if (now) {
      this.wake()
      this.kick()
    } else this.dozing = window.setTimeout(() => this.sleep(), 20_000)
  })

  /** A page that comes back to the front may have dropped what was drawn. */
  private pageShown = () => {
    if (document.visibilityState === 'visible') this.kick()
  }

  /** Make a context to draw with, if there is none. */
  wake() {
    if (this.renderer || this.disposed) return
    const canvas = document.createElement('canvas')
    this.stage.prepend(canvas)
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1))
    renderer.localClippingEnabled = true
    renderer.setSize(this.width, this.height, false)
    // A context the browser hands back has nothing on it.
    canvas.addEventListener('webglcontextrestored', () => this.kick())
    canvas.addEventListener('webglcontextlost', (e) => {
      e.preventDefault()
      if (this.renderer === renderer) this.drop()
      if (this.visible) requestAnimationFrame(() => this.wake())
    })
    this.renderer = renderer
    this.kick()
  }

  /** Give the context back. */
  sleep() {
    if (!this.renderer || this.visible) return
    const renderer = this.renderer
    this.drop()
    renderer.forceContextLoss()
  }

  private drop() {
    const renderer = this.renderer
    if (!renderer) return
    this.renderer = null
    renderer.dispose()
    renderer.domElement.remove()
  }

  // ——— the box ———

  setBox(box: Box) {
    this.box = box
    this.boxRef.value = box
    const span = [box.x, box.y, box.z].map((r) => r[1] - r[0])
    const s = box.equal ? span.map(() => 2 / Math.max(...span)) : span.map((d) => 2 / d)
    const c = [box.x, box.y, box.z].map((r) => (r[0] + r[1]) / 2)
    this.root.scale.set(s[0], s[1], s[2])
    this.root.position.set(-c[0] * s[0], -c[1] * s[1], -c[2] * s[2])
    this.root.updateMatrixWorld(true)
    const [hx, hy, hz] = this.half()
    const e = 1e-4
    this.clip[0].set(new THREE.Vector3(1, 0, 0), hx + e)
    this.clip[1].set(new THREE.Vector3(-1, 0, 0), hx + e)
    this.clip[2].set(new THREE.Vector3(0, 1, 0), hy + e)
    this.clip[3].set(new THREE.Vector3(0, -1, 0), hy + e)
    this.clip[4].set(new THREE.Vector3(0, 0, 1), hz + e)
    this.clip[5].set(new THREE.Vector3(0, 0, -1), hz + e)
    // Dashes in the app's coordinates are scaled to the box: a new box, new ones.
    for (const [key, m] of this.materials)
      if (key.startsWith('line|') && /\|true\|(clip)?$/.test(key)) {
        this.lineMaterials.delete(m as LineMaterial)
        m.dispose()
        this.materials.delete(key)
      }
    this.buildAxes()
    for (const rec of this.recs.values()) this.draw(rec)
    this.fit()
    this.kick()
  }

  /** The box's six faces: a surface, curve or line that runs past the ranges
   *  is cut there, as a curve is at the edge of a plane. */
  private clip = Array.from({ length: 6 }, () => new THREE.Plane())

  /** The world-space half sizes of the box. */
  private half(): Vec3 {
    const b = this.box
    return [b.x, b.y, b.z].map((r, i) => ((r[1] - r[0]) * this.root.scale.getComponent(i)) / 2) as Vec3
  }

  private buildAxes() {
    for (const child of [...this.axesGroup.children]) this.dropObject(child)
    this.axesGroup.clear()
    if (!this.box.axes) return
    const { x, y, z } = this.box
    const grid: number[] = []
    for (const t of ticks(x)) grid.push(t, y[0], z[0], t, y[1], z[0])
    for (const t of ticks(y)) grid.push(x[0], t, z[0], x[1], t, z[0])
    this.axesGroup.add(this.segments(grid, 'line', 1, { name: 'grid' }))
    const rim = [x[0], y[0], x[1], y[0], x[1], y[1], x[0], y[1]]
    const floor: number[] = []
    for (let i = 0; i < 4; i++) {
      const j = (i + 1) % 4
      floor.push(rim[i * 2], rim[i * 2 + 1], z[0], rim[j * 2], rim[j * 2 + 1], z[0])
    }
    this.axesGroup.add(this.segments(floor, 'line-2', 1.25, { name: 'rim' }))
    // One upright edge at each corner of the floor; the one at the back is shown.
    for (let i = 0; i < 4; i++) {
      const edge = this.segments([rim[i * 2], rim[i * 2 + 1], z[0], rim[i * 2], rim[i * 2 + 1], z[1]], 'line-2', 1.25, {
        name: `post${i}`,
      })
      edge.visible = false
      this.axesGroup.add(edge)
    }
  }

  private segments(nums: number[], color: string, px: number, opts: { name?: string; opacity?: number } = {}) {
    const geometry = new LineSegmentsGeometry().setPositions(nums)
    const material = this.lineMaterial({ color: cssColor(token(color)), px, opacity: opts.opacity ?? 1 })
    const seg = new LineSegments2(geometry, material)
    if (opts.name) seg.name = opts.name
    return seg
  }

  /** Dashes are measured in the coordinates of the line they are on; those
   *  under `root` are the app's, so they are scaled to stay the same length on
   *  screen whatever the ranges. */
  private dashScale(local: boolean) {
    if (!local) return 1
    const s = this.root.scale
    return (s.x + s.y + s.z) / 3
  }

  /** Materials are shared and kept for the life of the space: three.js
   *  compiles a shader for each kind of material and frees it with the last
   *  material that used it, so making and dropping materials as a picture
   *  changes would compile shaders on every frame. */
  private materials = new Map<string, THREE.Material>()
  private material<M extends THREE.Material>(key: string, make: () => M): M {
    const full = `${key}|${this.clipping ? 'clip' : ''}`
    let m = this.materials.get(full) as M | undefined
    if (!m) {
      m = make()
      if (this.clipping) m.clippingPlanes = this.clip
      this.materials.set(full, m)
    }
    return m
  }
  /** Whether what is being drawn now is cut at the box's faces. */
  private clipping = false

  private lineMaterial({
    color,
    px,
    dashed = false,
    opacity = 1,
    ghost = false,
    local = true,
  }: {
    color: THREE.Color
    px: number
    dashed?: boolean
    opacity?: number
    ghost?: boolean
    local?: boolean
  }) {
    return this.material(`line|${color.getHex()}|${px}|${dashed}|${opacity}|${ghost}|${local}`, () =>
      this.newLineMaterial({ color, px, dashed, opacity, ghost, local })
    )
  }

  private newLineMaterial({
    color,
    px,
    dashed,
    opacity,
    ghost,
    local,
  }: {
    color: THREE.Color
    px: number
    dashed: boolean
    opacity: number
    ghost: boolean
    local: boolean
  }) {
    const m = new LineMaterial({
      color: color.getHex(),
      linewidth: px,
      dashed,
      dashSize: 0.06,
      gapSize: 0.045,
      transparent: ghost || opacity < 1,
      opacity: ghost ? 0.22 * opacity : opacity,
      depthTest: !ghost,
      depthWrite: !ghost && opacity >= 1,
    })
    m.dashScale = this.dashScale(local)
    m.resolution.set(this.width, this.height)
    this.lineMaterials.add(m)
    return m
  }

  // ——— size and camera ———

  resize(width: number, height: number) {
    if (width < 1 || height < 1) return
    this.width = width
    this.height = height
    this.renderer?.setSize(width, height, false)
    this.camera.aspect = width / height
    for (const m of this.lineMaterials) m.resolution.set(width, height)
    this.fit()
    this.kick()
  }

  /** How far the camera stands, and how far the picture is shifted so the
   *  box sits in the middle of the frame (in the frame's own -1..1 units). */
  private framed: Framing = { az: -1, el: 0.45, d: 6, sx: 0, sy: 0 }

  /** The framing for a view: the box, with room for the numbers on its edges,
   *  as large as fits, and centred. Taken for the view the app asks for and
   *  kept while the reader turns, so the picture does not swell and shrink. */
  private framing(az: number, el: number): Framing {
    // The box, and any solid that stands out of it (an atom on a corner of
    // its cell); with axes, room for the numbers on the edges.
    const room = this.box.axes ? 1.1 : 1.02
    const [hx, hy, hz] = this.half().map((h) => h * room)
    const bounds = new THREE.Box3(new THREE.Vector3(-hx, -hy, -hz), new THREE.Vector3(hx, hy, hz))
    this.root.updateMatrixWorld(true)
    for (const rec of this.recs.values())
      if (!CLIPPED.has(rec.kind) && rec.kind !== 'c-group' && rec.kind !== 'c-text') bounds.expandByObject(rec.body)
    const w = new THREE.Vector3(Math.cos(el) * Math.cos(az), Math.cos(el) * Math.sin(az), Math.sin(el))
    const right = new THREE.Vector3(0, 0, 1).cross(w).normalize()
    const up = right.clone().cross(w).negate()
    const tanV = Math.tan(THREE.MathUtils.degToRad(FOV) / 2)
    const tanH = tanV * (this.width / this.height)
    const corners: THREE.Vector3[] = []
    const [p, q] = [bounds.min, bounds.max]
    for (const a of [p.x, q.x])
      for (const b of [p.y, q.y]) for (const c of [p.z, q.z]) corners.push(new THREE.Vector3(a, b, c))
    const spread = (d: number) => {
      let [x0, x1, y0, y1] = [Infinity, -Infinity, Infinity, -Infinity]
      for (const c of corners) {
        const depth = d - c.dot(w)
        const x = c.dot(right) / (depth * tanH)
        const y = c.dot(up) / (depth * tanV)
        ;[x0, x1, y0, y1] = [Math.min(x0, x), Math.max(x1, x), Math.min(y0, y), Math.max(y1, y)]
      }
      return { x0, x1, y0, y1 }
    }
    const R = Math.max(...corners.map((c) => c.length()))
    let [lo, hi] = [R * 1.01, R * 60]
    for (let i = 0; i < 40; i++) {
      const mid = (lo + hi) / 2
      const s = spread(mid)
      if (Math.max(s.x1 - s.x0, s.y1 - s.y0) / 2 > 0.96) lo = mid
      else hi = mid
    }
    const s = spread(hi)
    return { az, el, d: hi, sx: (s.x0 + s.x1) / 2, sy: (s.y0 + s.y1) / 2 }
  }

  private fit() {
    this.framed = this.framing(this.framed.az, this.framed.el)
    this.placeCamera()
  }

  private placeCamera() {
    const { az, el, zoom } = this.view
    const d = this.framed.d * zoom
    this.camera.position.set(d * Math.cos(el) * Math.cos(az), d * Math.cos(el) * Math.sin(az), d * Math.sin(el))
    this.camera.lookAt(0, 0, 0)
    // Shift the picture rather than the camera: the view still turns about
    // the middle of the box.
    const [W, H] = [this.width, this.height]
    this.camera.setViewOffset(W, H, (this.framed.sx * W) / 2, (-this.framed.sy * H) / 2, W, H)
    this.camera.updateMatrixWorld()
  }

  /** Turn to a view the app asked for: at once, or along the sphere. */
  setView(az: number, el: number, animate: boolean) {
    this.spin = { az: 0, el: 0 }
    // The shorter way round.
    let target = az
    while (target - this.view.az > Math.PI) target -= 2 * Math.PI
    while (target - this.view.az < -Math.PI) target += 2 * Math.PI
    const to = this.framing(target, clampEl(el))
    if (!animate || still()) {
      this.viewAnim = null
      this.framed = to
      this.view = { az: to.az, el: to.el, zoom: 1 }
      this.placeCamera()
      this.kick()
      return
    }
    this.viewAnim = {
      from: { ...this.framed, az: this.view.az, el: this.view.el, d: this.framed.d * this.view.zoom },
      to,
      start: performance.now(),
    }
    this.view.zoom = 1
    this.kick()
  }

  turn(daz: number, del: number, fling = false) {
    this.viewAnim = null
    this.view.az -= daz
    this.view.el = clampEl(this.view.el + del)
    if (fling) this.spin = { az: daz, el: del }
    this.placeCamera()
    this.onViewChanged()
    this.kick()
  }

  stopSpin() {
    this.spin = { az: 0, el: 0 }
  }

  zoomBy(k: number) {
    this.view.zoom = Math.min(2.5, Math.max(0.35, this.view.zoom * k))
    this.placeCamera()
    this.onViewChanged()
    this.kick()
  }

  // ——— the elements ———

  private changed = (e: Event) => {
    const rec = this.recs.get(e.target as Element)
    if (rec) rec.version.value++
    else this.discover()
  }

  /** Match the records to the elements now in the space: new ones get a
   *  record, gone ones lose theirs, and each is hung under its group. */
  discover() {
    const seen = new Set<Element>()
    const walk = (parent: Element, owner: Rec | null) => {
      for (const el of parent.children) {
        const kind = el.tagName.toLowerCase()
        if (!ITEMS.has(kind)) continue
        seen.add(el)
        let rec = this.recs.get(el)
        if (rec && rec.parent !== owner) {
          this.forget(rec)
          rec = undefined
        }
        if (!rec) rec = this.record(el as Held, kind, owner)
        if (kind === 'c-group') walk(el, rec)
      }
    }
    walk(this.host, null)
    for (const [el, rec] of this.recs) if (!seen.has(el)) this.forget(rec)
    this.kick()
  }

  private record(el: Held, kind: string, parent: Rec | null): Rec {
    const scope = effectScope(true)
    const version = ref(0)
    const body = new THREE.Group()
    const marks = new THREE.Group()
    ;(parent ? parent.body : this.root).add(body)
    this.marksLayer.add(marks)
    const rec = {
      el,
      kind,
      parent,
      scope,
      version,
      body,
      marks,
      shown: null,
      slide: null,
      last: 0,
      asked: 0,
      gap: Infinity,
      pending: null,
      label: null,
    } as unknown as Rec
    const ctx = { box: this.boxRef.value }
    rec.spec = scope.run(() =>
      computed(() => {
        void version.value
        ctx.box = this.boxRef.value
        try {
          return specOf(el, kind, ctx)
        } catch (e) {
          this.onError(
            e instanceof Miswritten ? e.message : `<${kind}> 中的函数运行出错：${(e as Error)?.message ?? e}`
          )
          return null
        }
      })
    )!
    scope.run(() =>
      watch(
        rec.spec,
        (raw) => {
          const now = performance.now()
          rec.gap = now - rec.asked
          rec.asked = now
          clearTimeout(rec.refine)
          // Sampled once per frame at most, from the latest request: a slider
          // can ask several times between two pictures.
          if (raw?.make && rec.shown) {
            rec.pending = raw
            this.kick()
          } else this.retarget(rec, this.made(rec, raw, false))
        },
        { immediate: true }
      )
    )
    this.recs.set(el, rec)
    return rec
  }

  private made(rec: Rec, raw: Spec | null, coarse: boolean): Spec | null {
    if (!raw?.make) return raw
    try {
      const made = raw.make(coarse)
      return { nums: made.nums, shape: made.shape ?? raw.shape, style: { ...raw.style, ...made.style } }
    } catch (e) {
      this.onError(
        e instanceof Miswritten ? e.message : `<${rec.kind}> 中的函数运行出错：${(e as Error)?.message ?? e}`
      )
      return null
    }
  }

  private forget(rec: Rec) {
    clearTimeout(rec.refine)
    rec.scope.stop()
    this.recs.delete(rec.el)
    for (const [, other] of this.recs) if (other.parent === rec) this.forget(other)
    this.clear(rec)
    rec.body.removeFromParent()
    rec.marks.removeFromParent()
    rec.label?.remove()
    if (this.held?.rec === rec) this.held = null
  }

  /** A new target: slide to it when it is a jump, follow it when it keeps
   *  coming (a drag, a simulation, a slider held down), else just take it. */
  private retarget(rec: Rec, spec: Spec | null) {
    const now = performance.now()
    const was = rec.shown
    const quick = now - rec.last < 120
    rec.last = now
    if (!spec) {
      rec.shown = null
      rec.slide = null
      this.draw(rec)
      return
    }
    if (was && !rec.slide && same(was, spec)) {
      rec.shown = spec
      return
    }
    const from = rec.slide ? this.sliding(rec, now) : was?.nums
    if (!was || !from || was.shape !== spec.shape || quick || this.held?.rec === rec || still()) {
      rec.slide = null
      rec.shown = spec
      this.draw(rec)
      return
    }
    rec.slide = { from, to: spec, start: now, span: duration() }
    this.kick()
  }

  private sliding(rec: Rec, now: number): number[] {
    const s = rec.slide!
    const k = ease(Math.min(1, (now - s.start) / s.span))
    return between(rec.kind, s.from, s.to.nums, k)
  }

  private restyle() {
    this.buildAxes()
    for (const rec of this.recs.values()) this.draw(rec)
    this.kick()
  }

  // ——— drawing one element ———

  private clear(rec: Rec) {
    for (const g of [rec.body, rec.marks])
      for (const child of [...g.children]) {
        if (rec.kind === 'c-group' && g === rec.body && this.isRecBody(child)) continue
        this.dropObject(child)
        child.removeFromParent()
      }
  }

  private isRecBody(o: THREE.Object3D) {
    for (const r of this.recs.values()) if (r.body === o) return true
    return false
  }

  /** Free what an object holds on the GPU; its materials are shared and stay. */
  private dropObject(o: THREE.Object3D) {
    o.traverse((x) => (x as THREE.Mesh).geometry?.dispose?.())
  }

  private surfaceMaterial(style: Record<string, unknown>, fallback = '1', vertexColors = false) {
    const opacity = Math.min(1, Math.max(0.05, num(style.opacity, 1)))
    const color = vertexColors ? new THREE.Color(0xffffff) : tone(style.tone, `chart-${fallback}`)
    return this.material(`surface|${color.getHex()}|${opacity}|${vertexColors}|${Boolean(style.wire)}`, () =>
      this.newSurfaceMaterial(color, opacity, vertexColors, Boolean(style.wire))
    )
  }

  private newSurfaceMaterial(color: THREE.Color, opacity: number, vertexColors: boolean, wireframe: boolean) {
    return new THREE.MeshPhongMaterial({
      color,
      wireframe,
      vertexColors,
      shininess: 24,
      specular: 0x1a1a1a,
      side: THREE.DoubleSide,
      transparent: opacity < 1,
      opacity,
      depthWrite: opacity >= 0.99,
      polygonOffset: true,
      polygonOffsetFactor: 1,
      polygonOffsetUnits: 1,
    })
  }

  /** A line in the element's own coordinates, with a faint copy that shows
   *  through whatever stands in front of it. */
  private line(nums: number[], color: THREE.Color, px: number, dashed: boolean, opacity = 1, closed = false) {
    const pts = closed ? [...nums, nums[0], nums[1], nums[2]] : nums
    // A run of points broken by NaN (where a function has no value) is drawn as segments.
    const segs: number[] = []
    for (let i = 0; i + 5 < pts.length; i += 3) {
      const a = pts.slice(i, i + 6)
      if (a.every(Number.isFinite)) segs.push(...a)
    }
    const out = new THREE.Group()
    if (!segs.length) return out
    for (const ghost of [false, true]) {
      const material = this.lineMaterial({ color, px, dashed, opacity, ghost })
      const seg = new LineSegments2(new LineSegmentsGeometry().setPositions(segs), material)
      if (dashed) seg.computeLineDistances()
      seg.renderOrder = ghost ? 10 : 0
      out.add(seg)
    }
    return out
  }

  private draw(rec: Rec) {
    this.clear(rec)
    rec.label?.remove()
    rec.label = null
    const spec = rec.shown
    if (!spec) return this.kick()
    const nums = rec.slide ? this.sliding(rec, performance.now()) : spec.nums
    const st = spec.style
    const label = String(st.label ?? '')
    this.clipping = CLIPPED.has(rec.kind)
    switch (rec.kind) {
      case 'c-group': {
        rec.body.position.set(nums[0], nums[1], nums[2])
        rec.body.quaternion.set(nums[3], nums[4], nums[5], nums[6])
        rec.body.scale.set(nums[7], nums[8], nums[9])
        break
      }
      case 'c-point': {
        const color = tone(st.tone, 'chart-1')
        const movable = st.drag !== '' || st.on != null
        for (const ghost of [false, true]) {
          const dot = new THREE.Mesh(
            new THREE.SphereGeometry(1, 20, 12),
            this.material(
              `dot|${color.getHex()}|${ghost}`,
              () =>
                new THREE.MeshPhongMaterial({
                  color,
                  shininess: 40,
                  transparent: ghost,
                  opacity: ghost ? 0.3 : 1,
                  depthTest: !ghost,
                  depthWrite: !ghost,
                })
            )
          )
          dot.userData = { px: movable ? PX.handle : PX.dot, at: 0 }
          dot.renderOrder = ghost ? 11 : 1
          if (spec.shape === 'point' || movable) rec.marks.add(dot)
          else dot.geometry.dispose()
        }
        if (spec.shape === 'vector') {
          rec.body.add(this.line([nums[3], nums[4], nums[5], nums[0], nums[1], nums[2]], color, PX.line, false))
          const head = new THREE.Mesh(
            new THREE.ConeGeometry(0.42, 1, 18),
            this.material(`head|${color.getHex()}`, () => new THREE.MeshPhongMaterial({ color, shininess: 30 }))
          )
          head.geometry.translate(0, -0.5, 0)
          head.userData = { px: PX.head * 1.6, at: 0, head: true }
          rec.marks.add(head)
        }
        if (st.drop) {
          const drop = new THREE.Group()
          drop.userData = { drop: true, color }
          rec.marks.add(drop)
        }
        if (label) rec.label = this.tag(label, 'point')
        break
      }
      case 'c-line':
        rec.body.add(this.line(nums, tone(st.tone, 'muted'), 1.5, Boolean(st.dashed), num(st.opacity, 1)))
        if (label) rec.label = this.tag(label, 'end')
        break
      case 'c-curve':
        rec.body.add(this.line(nums, tone(st.tone, 'chart-1'), PX.line + 0.5, Boolean(st.dashed), num(st.opacity, 1)))
        if (label) rec.label = this.tag(label, 'end')
        break
      case 'c-area': {
        const color = tone(st.tone, 'chart-1')
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.Float32BufferAttribute(nums, 3))
        const idx: number[] = []
        for (let i = 1; i + 1 < nums.length / 3; i++) idx.push(0, i, i + 1)
        geo.setIndex(idx)
        geo.computeVertexNormals()
        rec.body.add(new THREE.Mesh(geo, this.surfaceMaterial({ ...st, wire: false, opacity: num(st.opacity, 0.35) })))
        rec.body.add(this.line(nums, color, 1.25, false, 0.9, true))
        if (label) rec.label = this.tag(label, 'center')
        break
      }
      case 'c-surface':
        this.drawSurface(rec, nums, st)
        if (label) rec.label = this.tag(label, 'center')
        break
      case 'c-sphere': {
        const m = new THREE.Mesh(new THREE.SphereGeometry(1, 40, 24), this.surfaceMaterial(st))
        m.position.set(nums[0], nums[1], nums[2])
        m.scale.setScalar(Math.max(1e-6, nums[3]))
        m.userData.rec = rec
        rec.body.add(m)
        if (label) rec.label = this.tag(label, 'top')
        break
      }
      case 'c-box': {
        const [a, b] = [nums.slice(0, 3), nums.slice(3, 6)]
        const size = [0, 1, 2].map((i) => Math.max(1e-6, Math.abs(b[i] - a[i])))
        const geo = new THREE.BoxGeometry(1, 1, 1)
        const m = new THREE.Mesh(geo, this.surfaceMaterial({ ...st, wire: false }))
        m.position.set((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
        m.scale.set(size[0], size[1], size[2])
        m.userData.rec = rec
        if (!st.wire) rec.body.add(m)
        const edges = new THREE.EdgesGeometry(geo)
        const pos = Array.from(edges.attributes.position.array as Float32Array)
        for (let i = 0; i < pos.length; i += 3)
          for (let c = 0; c < 3; c++) pos[i + c] = pos[i + c] * size[c] + (a[c] + b[c]) / 2
        edges.dispose()
        // Its edges, as a mesh's: boxes side by side (the columns of a sum,
        // a stack of blocks) read as separate only by their edges.
        const color = tone(st.tone, 'chart-1')
        rec.body.add(
          this.segmentsIn(pos, st.wire ? color : color.clone().lerp(cssColor(token('ink')), 0.35), st.wire ? 1.5 : 1.25)
        )
        if (label) rec.label = this.tag(label, 'top')
        break
      }
      case 'c-cylinder':
      case 'c-cone': {
        const a = new THREE.Vector3(nums[0], nums[1], nums[2])
        const b = new THREE.Vector3(nums[3], nums[4], nums[5])
        const len = Math.max(1e-6, a.distanceTo(b))
        const r = Math.max(1e-6, nums[6])
        const geo =
          rec.kind === 'c-cone' ? new THREE.ConeGeometry(1, 1, 36, 1) : new THREE.CylinderGeometry(1, 1, 1, 36, 1)
        const m = new THREE.Mesh(geo, this.surfaceMaterial(st))
        m.position.copy(a).add(b).multiplyScalar(0.5)
        m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize())
        m.scale.set(r, len, r)
        m.userData.rec = rec
        rec.body.add(m)
        if (label) rec.label = this.tag(label, 'end')
        break
      }
      case 'c-mesh':
        this.drawMesh(rec, nums, st)
        if (label) rec.label = this.tag(label, 'center')
        break
      case 'c-points': {
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.Float32BufferAttribute(nums, 3))
        const colors = st.colors as number[] | null
        const colored = Boolean(colors && colors.length * 3 === nums.length)
        if (colored) geo.setAttribute('color', new THREE.Float32BufferAttribute(rampColors(colors!), 3))
        const color = colored ? new THREE.Color(0xffffff) : tone(st.tone, 'chart-1')
        const size = num(st.size, 3) * Math.min(2, window.devicePixelRatio || 1)
        const opacity = num(st.opacity, 1)
        const material = this.material(
          `points|${color.getHex()}|${colored}|${size}|${opacity}`,
          () =>
            new THREE.PointsMaterial({
              color,
              vertexColors: colored,
              size,
              sizeAttenuation: false,
              map: dotTexture(),
              alphaTest: 0.4,
              transparent: opacity < 1,
              opacity,
            })
        )
        rec.body.add(new THREE.Points(geo, material))
        break
      }
      case 'c-field':
        this.drawField(rec, nums, st)
        break
      case 'c-text':
        break
    }
    this.clipping = false
    this.kick()
  }

  private segmentsIn(
    nums: number[],
    color: THREE.Color,
    px: number,
    opts: { opacity?: number; dashed?: boolean } = {}
  ) {
    const seg = new LineSegments2(
      new LineSegmentsGeometry().setPositions(nums),
      this.lineMaterial({ color, px, ...opts })
    )
    if (opts.dashed) seg.computeLineDistances()
    return seg
  }

  private drawSurface(rec: Rec, nums: number[], st: Record<string, unknown>) {
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(nums, 3))
    let grid: Grid | null = null
    if (st.form === 'implicit') geo.setAttribute('normal', new THREE.BufferAttribute(st.normals as Float32Array, 3))
    else {
      grid = { positions: new Float32Array(nums), nu: Number(st.n), nv: Number(st.n) }
      // A NaN corner leaves its triangles out, but three's normals still read it.
      const safe = new Float32Array(nums.map((v) => (Number.isFinite(v) ? v : 0)))
      geo.setAttribute('position', new THREE.Float32BufferAttribute(safe, 3))
      geo.setIndex(gridIndex(grid))
      geo.computeVertexNormals()
    }
    const colors = st.colors as number[] | null
    if (colors && colors.length * 3 === nums.length)
      geo.setAttribute('color', new THREE.Float32BufferAttribute(rampColors(colors), 3))
    const mesh = new THREE.Mesh(geo, this.surfaceMaterial({ ...st, wire: false }, '1', Boolean(colors)))
    mesh.userData.rec = rec
    if (!st.wire || st.form === 'implicit') rec.body.add(mesh)
    else geo.dispose()
    const ink = st.wire ? tone(st.tone, 'chart-1') : cssColor(token('surface'))
    if (grid) rec.body.add(this.segmentsIn(gridLines(grid, 6), ink, st.wire ? 1.25 : 0.75))
    if (grid && st.form === 'graph' && st.contours != null && st.contours !== false && st.contours !== 'false') {
      const zs = Array.from(grid.positions).filter((_, i) => i % 3 === 2 && Number.isFinite(grid!.positions[i]))
      const lo = Math.min(...zs)
      const hi = Math.max(...zs)
      const count = isNum(st.contours) && Number(st.contours) > 0 ? Math.min(30, Number(st.contours)) : 8
      const levels = Array.from({ length: count }, (_, i) => lo + ((hi - lo) * (i + 1)) / (count + 1))
      const on = contours(grid, levels)
      if (on.length) {
        const color = tone(st.tone, 'chart-1')
        rec.body.add(this.segmentsIn(on, color.clone().lerp(cssColor(token('ink')), 0.45), 1))
        // Their shadow on the floor, where the surface stands clear of it to be
        // read there; one that lies along the floor would hide it.
        const floor = this.box.z[0]
        if (lo - floor > 0.06 * (this.box.z[1] - floor)) {
          const below = on.map((v, i) => (i % 3 === 2 ? floor : v))
          rec.body.add(this.segmentsIn(below, color, 1.25))
        }
      }
    }
  }

  private drawMesh(rec: Rec, nums: number[], st: Record<string, unknown>) {
    const faces = st.faces as number[][]
    const pos: number[] = []
    const edges: number[] = []
    const p = (i: number) => [nums[i * 3], nums[i * 3 + 1], nums[i * 3 + 2]]
    for (const f of faces) {
      for (let i = 1; i + 1 < f.length; i++) pos.push(...p(f[0]), ...p(f[i]), ...p(f[i + 1]))
      for (let i = 0; i < f.length; i++) edges.push(...p(f[i]), ...p(f[(i + 1) % f.length]))
    }
    const color = tone(st.tone, 'chart-1')
    if (!st.wire) {
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
      geo.computeVertexNormals()
      const m = new THREE.Mesh(geo, this.surfaceMaterial({ ...st, wire: false }))
      m.userData.rec = rec
      rec.body.add(m)
    }
    rec.body.add(
      this.segmentsIn(edges, st.wire ? color : color.clone().lerp(cssColor(token('ink')), 0.35), st.wire ? 1.5 : 1.25)
    )
  }

  private drawField(rec: Rec, nums: number[], st: Record<string, unknown>) {
    const spacing = Number(st.spacing)
    const mags: number[] = []
    for (let i = 0; i < nums.length; i += 6) mags.push(Math.hypot(nums[i + 3], nums[i + 4], nums[i + 5]))
    const sorted = mags.filter(Number.isFinite).sort((a, b) => a - b)
    const typical = sorted[Math.floor(sorted.length * 0.6)] || 1
    const color = tone(st.tone, 'muted')
    const shafts: number[] = []
    const heads: THREE.Matrix4[] = []
    for (let i = 0, a = 0; i < nums.length; i += 6, a++) {
      const m = mags[a]
      if (!(m > 0)) continue
      const len = spacing * 0.8 * Math.min(1, m / typical)
      const dir = new THREE.Vector3(nums[i + 3], nums[i + 4], nums[i + 5]).divideScalar(m)
      const base = new THREE.Vector3(nums[i], nums[i + 1], nums[i + 2]).addScaledVector(dir, -len / 2)
      const tip = base.clone().addScaledVector(dir, len)
      const headLen = Math.min(len * 0.45, spacing * 0.25)
      shafts.push(
        base.x,
        base.y,
        base.z,
        ...tip
          .clone()
          .addScaledVector(dir, -headLen * 0.6)
          .toArray()
      )
      const mtx = new THREE.Matrix4().compose(
        tip,
        new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir),
        new THREE.Vector3(headLen * 0.4, headLen, headLen * 0.4)
      )
      heads.push(mtx)
    }
    if (shafts.length) rec.body.add(this.segmentsIn(shafts, color, 1.5))
    const cone = new THREE.ConeGeometry(1, 1, 10)
    cone.translate(0, -0.5, 0)
    const inst = new THREE.InstancedMesh(
      cone,
      this.material(`arrow|${color.getHex()}`, () => new THREE.MeshPhongMaterial({ color })),
      heads.length
    )
    heads.forEach((m, i) => inst.setMatrixAt(i, m))
    rec.body.add(inst)
  }

  // ——— labels ———

  private tag(text: string, anchor: string) {
    const el = document.createElement('div')
    el.className = 'tag'
    el.dataset.anchor = anchor
    el.textContent = text
    this.overlay.append(el)
    return el
  }

  // ——— each frame ———

  kick() {
    if (this.frame || this.disposed) return
    this.frame = requestAnimationFrame(() => {
      this.frame = 0
      this.render()
    })
  }

  /** Sample what asked to be since the last frame. While the reader keeps
   *  changing it (a slider held, a drag), a surface is sampled on a coarser
   *  grid, and on the full one once they stop. */
  private sample() {
    for (const rec of this.recs.values()) {
      const raw = rec.pending
      if (!raw) continue
      rec.pending = null
      const coarse = rec.gap < 300 && !still()
      this.retarget(rec, this.made(rec, raw, coarse))
      if (coarse)
        rec.refine = window.setTimeout(() => {
          if (rec.spec.value !== raw) return
          rec.gap = Infinity
          rec.pending = raw
          this.kick()
        }, 200)
    }
  }

  private render() {
    // Out of sight, or without a context: drawn when seen again.
    if (!this.visible || !this.renderer) return
    this.sample()
    const now = performance.now()
    let moving = false
    for (const rec of this.recs.values()) {
      if (!rec.slide) continue
      if (now - rec.slide.start >= rec.slide.span) {
        rec.shown = rec.slide.to
        rec.slide = null
      } else moving = true
      this.draw(rec)
    }
    if (this.viewAnim) {
      const k = ease(Math.min(1, (now - this.viewAnim.start) / duration()))
      const { from, to } = this.viewAnim
      const at = (key: keyof Framing) => from[key] + (to[key] - from[key]) * k
      this.framed = { az: at('az'), el: at('el'), d: at('d'), sx: at('sx'), sy: at('sy') }
      this.view.az = this.framed.az
      this.view.el = this.framed.el
      if (k >= 1) this.viewAnim = null
      else moving = true
      this.placeCamera()
    }
    if (Math.abs(this.spin.az) + Math.abs(this.spin.el) > 1e-4 && !this.held) {
      this.view.az -= this.spin.az
      this.view.el = clampEl(this.view.el + this.spin.el)
      this.spin.az *= 0.88
      this.spin.el *= 0.88
      this.placeCamera()
      moving = true
    }
    this.scene.updateMatrixWorld()
    this.placeMarks()
    this.placeAxes()
    this.renderer.render(this.scene, this.camera)

    this.placeLabels()
    if (moving) this.kick()
  }

  /** World units per screen pixel at a point. */
  private perPixel(world: THREE.Vector3) {
    const d = world.clone().sub(this.camera.position).dot(this.camera.getWorldDirection(new THREE.Vector3()))
    return (2 * Math.max(d, 0.01) * Math.tan(THREE.MathUtils.degToRad(FOV) / 2)) / this.height
  }

  /** Where a point in an element's coordinates is in the world. */
  worldOf(rec: Rec, p: number[]) {
    return new THREE.Vector3(p[0], p[1], p[2]).applyMatrix4((rec.parent ? rec.parent.body : this.root).matrixWorld)
  }

  private floorWorldZ() {
    return new THREE.Vector3(0, 0, this.box.z[0]).applyMatrix4(this.root.matrixWorld).z
  }

  private placeMarks() {
    for (const rec of this.recs.values()) {
      if (rec.kind !== 'c-point' || !rec.shown) continue
      const nums = rec.slide ? this.sliding(rec, performance.now()) : rec.shown.nums
      const p = this.worldOf(rec, nums)
      const from = rec.shown.shape === 'vector' ? this.worldOf(rec, nums.slice(3)) : null
      for (const m of [...rec.marks.children]) {
        if (m.userData.drop) {
          for (const c of [...m.children]) {
            this.dropObject(c)
            c.removeFromParent()
          }
          const foot = new THREE.Vector3(p.x, p.y, this.floorWorldZ())
          if (Math.abs(foot.z - p.z) > 1e-6) {
            const seg = new LineSegments2(
              new LineSegmentsGeometry().setPositions([...p.toArray(), ...foot.toArray()]),
              this.lineMaterial({ color: m.userData.color, px: 1, dashed: true, opacity: 0.8, local: false })
            )
            seg.computeLineDistances()
            m.add(seg)
          }
          continue
        }
        const px = m.userData.px as number
        m.position.copy(p)
        m.scale.setScalar(px * this.perPixel(p))
        if (m.userData.head && from) {
          const dir = p.clone().sub(from)
          if (dir.lengthSq() > 0) m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize())
          m.visible = dir.lengthSq() > 0
        }
      }
    }
  }

  private project(world: THREE.Vector3) {
    const v = world.clone().project(this.camera)
    return { x: ((v.x + 1) / 2) * this.width, y: ((1 - v.y) / 2) * this.height, behind: v.z > 1 }
  }

  private axisTags: HTMLElement[] = []
  private placeAxes() {
    let used = 0
    const done = () => {
      for (const t of this.axisTags.splice(used)) t.remove()
    }
    if (!this.box.axes) return done()
    const { x, y, z } = this.box
    const eye = this.camera.position.clone().applyMatrix4(this.root.matrixWorld.clone().invert())
    // x and y are numbered along the floor's two edges nearest the reader.
    const corners: [number, number][] = [
      [x[0], y[0]],
      [x[1], y[0]],
      [x[1], y[1]],
      [x[0], y[1]],
    ]
    // The upright edge at the floor corner furthest left on screen carries z,
    // outside the drawing rather than through it.
    let far = 0
    let best = Infinity
    corners.forEach(([cx, cy], i) => {
      const s = this.project(new THREE.Vector3(cx, cy, z[0]).applyMatrix4(this.root.matrixWorld))
      if (s.x < best) {
        best = s.x
        far = i
      }
    })
    for (let i = 0; i < 4; i++) {
      const post = this.axesGroup.getObjectByName(`post${i}`)
      if (post) post.visible = i === far
    }
    const nearY = Math.abs(eye.y - y[0]) < Math.abs(eye.y - y[1]) ? y[0] : y[1]
    const nearX = Math.abs(eye.x - x[0]) < Math.abs(eye.x - x[1]) ? x[0] : x[1]
    const toWorld = (a: number, b: number, c: number) => new THREE.Vector3(a, b, c).applyMatrix4(this.root.matrixWorld)
    const out = (p: THREE.Vector3, d: THREE.Vector3) => p.clone().addScaledVector(d, 0.12)
    const yOut = new THREE.Vector3(0, Math.sign(nearY - (y[0] + y[1]) / 2), 0)
    const xOut = new THREE.Vector3(Math.sign(nearX - (x[0] + x[1]) / 2), 0, 0)
    const put = (text: string, world: THREE.Vector3, cls = 'tick') => {
      const s = this.project(world)
      if (s.behind) return
      let el = this.axisTags[used]
      if (!el) {
        el = document.createElement('div')
        this.overlay.prepend(el)
        this.axisTags.push(el)
      }
      used++
      el.className = cls
      if (el.textContent !== text) el.textContent = text
      el.style.transform = `translate(${s.x.toFixed(1)}px, ${s.y.toFixed(1)}px) translate(-50%, -50%)`
    }
    const fmt = (v: number) => String(Number(v.toPrecision(6)))
    for (const t of ticks(x, 4)) put(fmt(t), out(toWorld(t, nearY, z[0]), yOut))
    for (const t of ticks(y, 4)) put(fmt(t), out(toWorld(nearX, t, z[0]), xOut))
    const [fx, fy] = corners[far]
    const zOut = new THREE.Vector3(Math.sign(fx - (x[0] + x[1]) / 2), Math.sign(fy - (y[0] + y[1]) / 2), 0).normalize()
    // The floor's corner already has the x and y numbers.
    for (const t of ticks(z, 4)) if (t > z[0] + (z[1] - z[0]) * 0.02) put(fmt(t), out(toWorld(fx, fy, t), zOut))
    const [lx, ly, lz] = this.box.labels
    put(lx, out(toWorld((x[0] + x[1]) / 2, nearY, z[0]), yOut).addScaledVector(yOut, 0.18), 'name')
    put(ly, out(toWorld(nearX, (y[0] + y[1]) / 2, z[0]), xOut).addScaledVector(xOut, 0.18), 'name')
    put(lz, out(toWorld(fx, fy, z[1]), zOut).add(new THREE.Vector3(0, 0, 0.12)), 'name')
    done()
  }

  private placeLabels() {
    const now = performance.now()
    const placed: { x: number; y: number; w: number; h: number }[] = []
    for (const rec of this.recs.values()) {
      const spec = rec.shown
      if (!spec) continue
      const nums = rec.slide ? this.sliding(rec, now) : spec.nums
      const el = rec.kind === 'c-text' ? rec.el : rec.label
      if (!el) continue
      let at: number[]
      let dx = 0
      let dy = 0
      if (rec.kind === 'c-point') [at, dx, dy] = [nums.slice(0, 3), 10, -12]
      else if (rec.kind === 'c-sphere') [at, dy] = [[nums[0], nums[1], nums[2] + nums[3]], -12]
      else if (rec.kind === 'c-text') at = nums
      else if (
        spec.shape.startsWith('curve') ||
        rec.kind === 'c-line' ||
        rec.kind === 'c-cylinder' ||
        rec.kind === 'c-cone'
      ) {
        const end = rec.kind === 'c-line' || rec.kind === 'c-cylinder' || rec.kind === 'c-cone' ? 3 : nums.length - 3
        at = nums.slice(end, end + 3)
        ;[dx, dy] = [6, -10]
      } else {
        const c = [0, 0, 0]
        let n = 0
        for (let i = 0; i + 2 < nums.length; i += 3)
          if ([0, 1, 2].every((k) => Number.isFinite(nums[i + k]))) {
            for (let k = 0; k < 3; k++) c[k] += nums[i + k]
            n++
          }
        at = c.map((v) => v / Math.max(1, n))
      }
      const s = this.project(this.worldOf(rec, at))
      el.style.visibility = s.behind ? 'hidden' : ''
      if (rec.kind === 'c-text') {
        el.style.transform = `translate(${s.x.toFixed(1)}px, ${s.y.toFixed(1)}px) translate(-50%, -50%)`
        continue
      }
      // A label that would cover one already placed moves a line up or down.
      // Measured once, the first time the label has a size.
      if (!Number(el.dataset.w)) [el.dataset.w, el.dataset.h] = [String(el.offsetWidth), String(el.offsetHeight)]
      const [w, h] = [Number(el.dataset.w), Number(el.dataset.h)]
      const left = s.x + dx - (dx < 0 ? w : dx === 0 ? w / 2 : 0)
      const top = s.y + dy - h / 2
      const clear = (t: number) =>
        !placed.some((b) => left < b.x + b.w && b.x < left + w && t < b.y + b.h && b.y < t + h)
      const y = [0, -h, h, -2 * h, 2 * h].map((d) => top + d).find(clear) ?? top
      if (!s.behind) placed.push({ x: left, y, w, h })
      el.style.transform = `translate(${left.toFixed(1)}px, ${y.toFixed(1)}px)`
    }
  }

  // ——— the pointer ———

  /** Points a reader may drag, or click, under a screen position. */
  pointAt(x: number, y: number, want: (rec: Rec) => boolean): Rec | null {
    let best: Rec | null = null
    let bestD = 22
    for (const rec of this.recs.values()) {
      if (rec.kind !== 'c-point' || !rec.shown || !want(rec)) continue
      const s = this.project(this.worldOf(rec, rec.shown.nums))
      const d = Math.hypot(s.x - x, s.y - y)
      if (d < bestD) {
        bestD = d
        best = rec
      }
    }
    return best
  }

  movable(rec: Rec) {
    const st = rec.shown?.style
    return Boolean(st && (st.drag !== '' || st.on != null))
  }

  /** The element a click at a screen position lands on, if the app listens for it. */
  clickAt(x: number, y: number): Held | null {
    const point = this.pointAt(x, y, (r) => Boolean(r.el.clickable))
    if (point) return point.el
    const ray = new THREE.Raycaster()
    ray.setFromCamera(new THREE.Vector2((x / this.width) * 2 - 1, 1 - (y / this.height) * 2), this.camera)
    const hits = ray.intersectObjects(this.root.children, true)
    for (const h of hits) {
      const rec = h.object.userData.rec as Rec | undefined
      if (rec?.el.clickable) return rec.el
    }
    return null
  }

  /** Take hold of a point, `x, y` being where the pointer took it. */
  grab(rec: Rec, px: number, py: number) {
    const at = this.project(this.worldOf(rec, rec.shown!.nums))
    this.held = { rec, guide: null, offset: { x: px - at.x, y: py - at.y } }
    this.stopSpin()
    const st = rec.shown!.style
    const axes = st.on ? 'xy' : String(st.drag)
    const p = rec.shown!.nums
    const { x, y, z } = this.box
    const guide = new THREE.Group()
    const color = cssColor(token('accent'))
    if (axes.length === 2) {
      // A patch of the plane the point moves in, ruled like the floor.
      const lines: number[] = []
      const ax = ['x', 'y', 'z'].indexOf(axes[0])
      const ay = ['x', 'y', 'z'].indexOf(axes[1])
      const rs = [x, y, z]
      const fixed = [0, 1, 2].find((i) => i !== ax && i !== ay)!
      const at = (u: number, v: number) => {
        const q = [0, 0, 0]
        q[ax] = u
        q[ay] = v
        q[fixed] = st.on ? rs[2][0] : p[fixed]
        return q
      }
      for (const u of ticks(rs[ax], 8)) lines.push(...at(u, rs[ay][0]), ...at(u, rs[ay][1]))
      for (const v of ticks(rs[ay], 8)) lines.push(...at(rs[ax][0], v), ...at(rs[ax][1], v))
      guide.add(this.segmentsIn(lines, color, 1, { opacity: 0.4 }))
    } else {
      const a = ['x', 'y', 'z'].indexOf(axes)
      const rs = [x, y, z]
      const from = [...p.slice(0, 3)]
      const to = [...p.slice(0, 3)]
      from[a] = rs[a][0]
      to[a] = rs[a][1]
      guide.add(this.segmentsIn([...from, ...to], color, 1.25, { dashed: true }))
    }
    ;(rec.parent ? rec.parent.body : this.root).add(guide)
    this.held.guide = guide
    this.kick()
  }

  /** Move the held point to where the pointer is, under its constraint: on
   *  its surface, where the ray from the eye through the pointer meets it; in
   *  its plane, where the ray crosses that plane; along its axis, at the point
   *  of the axis nearest the ray. Measured from where the point was taken
   *  hold of, so it does not jump to the pointer's tip. */
  drag(x: number, y: number) {
    const held = this.held
    const shown = held?.rec.shown
    if (!held || !shown) return
    const rec = held.rec
    const st = shown.style
    const on = st.on as Fn | null
    const cur = shown.nums.slice(0, 3)
    const rs = [this.box.x, this.box.y, this.box.z]
    const ray = this.rayAt(x - held.offset.x, y - held.offset.y, rec)
    const inside = (v: number[]) => v.map((c, i) => Math.min(rs[i][1], Math.max(rs[i][0], c)))
    let next: number[] | null = null
    if (on) next = this.ontoSurface(ray, on, cur) ?? this.ontoPlane(ray, cur, 2, 0.05)
    else {
      const axes = String(st.drag)
        .split('')
        .map((a) => ['x', 'y', 'z'].indexOf(a))
      if (axes.length === 2) {
        const fixed = [0, 1, 2].find((i) => !axes.includes(i))!
        next = this.ontoPlane(ray, cur, fixed, 0.12)
        // Seen nearly edge-on, a plane gives no steady crossing: move along
        // whichever of its two axes the reader sees better.
        if (!next) next = this.ontoAxis(ray, cur, this.clearerAxis(rec, cur, axes))
      } else next = this.ontoAxis(ray, cur, axes[0])
    }
    if (!next) return
    next = inside(next)
    const snap = num(rec.el.snap, 0)
    const free = on
      ? [0, 1]
      : String(st.drag)
          .split('')
          .map((a) => ['x', 'y', 'z'].indexOf(a))
    for (let i = 0; i < 3; i++) {
      if (!free.includes(i)) {
        next[i] = cur[i]
        continue
      }
      if (snap > 0) next[i] = Number((Math.round(next[i] / snap) * snap).toPrecision(12))
      else {
        // To a thousandth of the axis: no 3.0000000000000004 on screen.
        const places = Math.max(0, -Math.floor(Math.log10((rs[i][1] - rs[i][0]) / 1000)))
        next[i] = Number(next[i].toFixed(places))
      }
    }
    if (on) next[2] = Number(Number(on(next[0], next[1])).toPrecision(10))
    if (next.some((v, i) => v !== cur[i])) announce(rec.el, next)
  }

  /** The ray from the eye through a screen position, in an element's coordinates. */
  private rayAt(x: number, y: number, rec: Rec) {
    const caster = new THREE.Raycaster()
    caster.setFromCamera(new THREE.Vector2((x / this.width) * 2 - 1, 1 - (y / this.height) * 2), this.camera)
    const inverse = (rec.parent ? rec.parent.body : this.root).matrixWorld.clone().invert()
    return caster.ray.clone().applyMatrix4(inverse)
  }

  /** Where a ray meets z = on(x, y) inside the box. A ray can cross a
   *  folded surface more than once; the crossing nearest the point's last
   *  place is taken, so a drag stays on the sheet it started on. */
  private ontoSurface(ray: THREE.Ray, on: Fn, near: number[]): number[] | null {
    const { x, y, z } = this.box
    const box = new THREE.Box3(new THREE.Vector3(x[0], y[0], z[0]), new THREE.Vector3(x[1], y[1], z[1]))
    const enter = new THREE.Vector3()
    if (!ray.intersectBox(box, enter)) return null
    const t0 = ray.origin.distanceTo(enter)
    const exit = new THREE.Ray(ray.at(t0 + 1e6, new THREE.Vector3()), ray.direction.clone().negate())
    const back = new THREE.Vector3()
    if (!exit.intersectBox(box, back)) return null
    const t1 = ray.origin.distanceTo(back)
    const gap = (t: number) => {
      const p = ray.at(t, new THREE.Vector3())
      return p.z - Number(on(p.x, p.y))
    }
    const scale = [x, y, z].map((r) => r[1] - r[0])
    const far = (p: THREE.Vector3) => Math.hypot(...[p.x, p.y, p.z].map((c, i) => (c - near[i]) / scale[i]))
    let best: THREE.Vector3 | null = null
    const N = 200
    let [a, ga] = [t0, gap(t0)]
    for (let i = 1; i <= N; i++) {
      const b = t0 + ((t1 - t0) * i) / N
      const gb = gap(b)
      if (Number.isFinite(ga) && Number.isFinite(gb) && ga * gb <= 0) {
        let [lo, hi, glo] = [a, b, ga]
        for (let k = 0; k < 30; k++) {
          const mid = (lo + hi) / 2
          const gm = gap(mid)
          if (glo * gm <= 0) hi = mid
          else [lo, glo] = [mid, gm]
        }
        const hit = ray.at((lo + hi) / 2, new THREE.Vector3())
        if (!best || far(hit) < far(best)) best = hit
      }
      ;[a, ga] = [b, gb]
    }
    return best ? best.toArray() : null
  }

  /** Where a ray crosses the plane through `p` across axis `fixed`; null when
   *  the plane is seen too nearly edge-on to give a steady answer. */
  private ontoPlane(ray: THREE.Ray, p: number[], fixed: number, steep: number): number[] | null {
    const normal = new THREE.Vector3()
    normal.setComponent(fixed, 1)
    if (Math.abs(ray.direction.dot(normal)) < steep) return null
    const plane = new THREE.Plane(normal, -p[fixed])
    const hit = ray.intersectPlane(plane, new THREE.Vector3())
    return hit ? hit.toArray() : null
  }

  /** The point on the line through `p` along an axis nearest a ray. */
  private ontoAxis(ray: THREE.Ray, p: number[], axis: number): number[] {
    const d = new THREE.Vector3()
    d.setComponent(axis, 1)
    const o = new THREE.Vector3(p[0], p[1], p[2])
    const w0 = o.clone().sub(ray.origin)
    const b = d.dot(ray.direction)
    const denom = 1 - b * b
    if (denom < 1e-9) return [...p]
    const s = (b * w0.dot(ray.direction) - w0.dot(d)) / denom
    return o.addScaledVector(d, s).toArray()
  }

  /** Of two axes, the one along which a step moves the point further on screen. */
  private clearerAxis(rec: Rec, p: number[], axes: number[]) {
    const at = this.project(this.worldOf(rec, p))
    const reach = axes.map((a) => {
      const q = [...p]
      q[a] += (this.rangeOf(a)[1] - this.rangeOf(a)[0]) * 0.01
      const s = this.project(this.worldOf(rec, q))
      return Math.hypot(s.x - at.x, s.y - at.y)
    })
    return reach[0] >= reach[1] ? axes[0] : axes[1]
  }

  private rangeOf(axis: number) {
    return [this.box.x, this.box.y, this.box.z][axis]
  }

  /** The held point's value, for the readout beside it. */
  heldAt(): { text: string; x: number; y: number } | null {
    const rec = this.held?.rec
    if (!rec?.shown) return null
    const p = rec.shown.nums.slice(0, 3)
    const s = this.project(this.worldOf(rec, p))
    const fmt = (v: number) => String(Number(v.toPrecision(4)))
    return { text: `(${p.map(fmt).join(', ')})`, x: s.x, y: s.y }
  }

  release() {
    if (this.held?.guide) {
      this.dropObject(this.held.guide)
      this.held.guide.removeFromParent()
    }
    this.held = null
    this.kick()
  }

  get holding() {
    return this.held != null
  }

  dispose() {
    this.disposed = true
    cancelAnimationFrame(this.frame)
    this.watcher.disconnect()
    this.host.removeEventListener(PLANE_ITEM_CHANGED, this.changed)
    this.offTheme()
    for (const rec of [...this.recs.values()]) this.forget(rec)
    this.sight.disconnect()
    document.removeEventListener('visibilitychange', this.pageShown)
    clearTimeout(this.dozing)
    this.drop()
  }
}

type Framing = { az: number; el: number; d: number; sx: number; sy: number }

const clampEl = (el: number) => Math.max(-1.45, Math.min(1.45, el))

let dot: THREE.Texture | null = null
/** A round dot for point clouds, which WebGL draws as squares. */
function dotTexture() {
  if (dot) return dot
  const c = document.createElement('canvas')
  c.width = c.height = 32
  const g = c.getContext('2d')!
  g.beginPath()
  g.arc(16, 16, 15, 0, Math.PI * 2)
  g.fillStyle = '#fff'
  g.fill()
  dot = new THREE.CanvasTexture(c)
  return dot
}
