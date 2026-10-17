<script setup lang="ts">
// A coordinate plane: the app gives the ranges, and puts points, curves, lines
// and areas inside it in the plane's own numbers; the plane does the arithmetic
// from numbers to the screen, draws the axes in the document's look, and lets
// the reader drag a point (by pointer or arrow keys), handing the app the new
// position in those same numbers. Whatever moves for any other reason slides to
// its new place.
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, useHost, watch } from 'vue'

import { announce } from './emit'
import { duration, ease, still } from './motion'
import { PLANE_ITEM_CHANGED } from './planeItem'

const props = withDefaults(
  defineProps<{
    x?: number[]
    y?: number[]
    height?: number
    xLabel?: string
    yLabel?: string
    grid?: boolean
    equal?: boolean
  }>(),
  { x: () => [0, 10], y: () => [0, 10], height: 240, xLabel: '', yLabel: '', grid: true, equal: false }
)
type Fn = (x: number) => number
type Held = HTMLElement & Record<string, unknown>
type Item = { el: Held; kind: 'point' | 'curve' | 'line' | 'area'; key: string }

const host = useHost() as HTMLElement
const box = ref<HTMLElement | null>(null)
const width = ref(0)
const version = ref(0)
const bump = () => version.value++

const num = (v: unknown, fallback: number) =>
  Number.isFinite(Number(v)) && v !== '' && v != null ? Number(v) : fallback
const pair = (v: unknown): [number, number] | null =>
  Array.isArray(v) && v.length >= 2 && v.every((n) => Number.isFinite(Number(n))) ? [Number(v[0]), Number(v[1])] : null
const flag = (v: unknown) => v === '' || v === true || v === 'true'
const fn = (v: unknown): Fn | null => (typeof v === 'function' ? (v as Fn) : null)

const H = computed(() => Math.max(120, num(props.height, 240)))
const pad = computed(() => ({ l: 44, r: 14, t: props.yLabel ? 26 : 12, b: props.xLabel ? 44 : 26 }))
/** The ranges drawn. With `equal`, a unit is as long across as up, so a square
 *  stays square: the range with room to spare widens about its middle to fill
 *  the box, and both ranges asked for stay in view. */
const ranges = computed(() => {
  const x = pair(props.x) ?? [0, 10]
  const y = pair(props.y) ?? [0, 10]
  const w = width.value - pad.value.l - pad.value.r
  const h = H.value - pad.value.t - pad.value.b
  if (!flag(props.equal) || w <= 0 || h <= 0) return { x, y }
  const unit = Math.min(w / (x[1] - x[0]), h / (y[1] - y[0]))
  const around = ([lo, hi]: [number, number], span: number): [number, number] => [
    (lo + hi) / 2 - span / 2,
    (lo + hi) / 2 + span / 2,
  ]
  return { x: around(x, w / unit), y: around(y, h / unit) }
})
const xr = computed(() => ranges.value.x)
const yr = computed(() => ranges.value.y)
const sx = (x: number) =>
  pad.value.l + ((x - xr.value[0]) / (xr.value[1] - xr.value[0])) * (width.value - pad.value.l - pad.value.r)
const sy = (y: number) =>
  H.value - pad.value.b - ((y - yr.value[0]) / (yr.value[1] - yr.value[0])) * (H.value - pad.value.t - pad.value.b)
const ix = (px: number) =>
  xr.value[0] + ((px - pad.value.l) / (width.value - pad.value.l - pad.value.r)) * (xr.value[1] - xr.value[0])
const iy = (py: number) =>
  yr.value[0] + ((H.value - pad.value.b - py) / (H.value - pad.value.t - pad.value.b)) * (yr.value[1] - yr.value[0])

/** Round numbers for the axes: about five steps of 1, 2 or 5 times a power of ten. */
function ticks([lo, hi]: [number, number]) {
  const raw = (hi - lo) / 5
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)))
  return out
}
const label = (v: number) => String(Number(v.toPrecision(6)))
const xt = computed(() => ticks(xr.value))
const yt = computed(() => ticks(yr.value))

const items = computed<Item[]>(() => {
  void version.value
  const seen: Record<string, number> = {}
  return [...host.children].flatMap((el) => {
    const kind = el.tagName.toLowerCase().replace('c-', '') as Item['kind']
    if (!['point', 'curve', 'line', 'area'].includes(kind)) return []
    seen[kind] = (seen[kind] ?? 0) + 1
    return [{ el: el as Held, kind, key: `${kind}${seen[kind]}` }]
  })
})

const tones = new Set(['ok', 'warn', 'danger', 'muted', 'accent', 'ink', 'text'])
function color(tone: unknown, fallback: string) {
  const t = String(tone ?? '')
  if (/^[1-8]$/.test(t)) return `var(--chart-${t})`
  return tones.has(t) ? `var(--${t})` : fallback
}

const SAMPLES = 160
function samples(f: Fn, from: number, to: number): number[] {
  const out: number[] = []
  for (let i = 0; i <= SAMPLES; i++) {
    const x = from + ((to - from) * i) / SAMPLES
    const y = f(x)
    out.push(sx(x), Number.isFinite(y) ? sy(y) : NaN)
  }
  return out
}

/** What each item looks like on screen: its points as one list of numbers, so a
 *  change of value can slide from the old list to the new. */
type Shape = {
  key: string
  kind: Item['kind']
  nums: number[]
  stroke: string
  fill: string
  dashed: boolean
  label: string
  item: Item
}
const target = computed<Shape[]>(() => {
  if (!width.value) return []
  void version.value
  return items.value.flatMap((item): Shape[] => {
    const e = item.el
    const label = e.label == null ? '' : String(e.label)
    const base = { key: item.key, kind: item.kind, label, item, dashed: flag(e.dashed) }
    if (item.kind === 'point') {
      const p = pair(e.value)
      if (!p) return []
      const from = pair(e.from)
      const c = color(e.tone, 'var(--chart-1)')
      return [{ ...base, nums: [sx(p[0]), sy(p[1]), ...(from ? [sx(from[0]), sy(from[1])] : [])], stroke: c, fill: c }]
    }
    if (item.kind === 'curve') {
      const f = fn(e.f)
      if (!f) return []
      const c = color(e.tone, 'var(--chart-1)')
      return [{ ...base, nums: samples(f, num(e.from, xr.value[0]), num(e.to, xr.value[1])), stroke: c, fill: 'none' }]
    }
    if (item.kind === 'line') {
      const c = color(e.tone, 'var(--muted)')
      let nums: number[] | null = null
      if (e.x != null && e.x !== '') nums = [sx(num(e.x, 0)), sy(yr.value[0]), sx(num(e.x, 0)), sy(yr.value[1])]
      else if (e.y != null && e.y !== '') nums = [sx(xr.value[0]), sy(num(e.y, 0)), sx(xr.value[1]), sy(num(e.y, 0))]
      else {
        const a = pair(e.from)
        const b = pair(e.to)
        if (a && b) nums = [sx(a[0]), sy(a[1]), sx(b[0]), sy(b[1])]
      }
      return nums ? [{ ...base, nums, stroke: c, fill: 'none' }] : []
    }
    const c = color(e.tone, 'var(--chart-1)')
    const f = fn(e.f)
    let nums: number[] = []
    if (f) {
      const from = num(e.from, xr.value[0])
      const to = num(e.to, xr.value[1])
      const floor = num(e.base, 0)
      nums = [...samples(f, from, to), sx(to), sy(floor), sx(from), sy(floor)]
    } else if (Array.isArray(e.points)) {
      nums = (e.points as unknown[]).flatMap((p) => {
        const q = pair(p)
        return q ? [sx(q[0]), sy(q[1])] : []
      })
    }
    return nums.length >= 6 ? [{ ...base, nums, stroke: c, fill: c }] : []
  })
})

// What is on screen: the target, reached at once while the reader drags (the
// picture follows the hand) and slid to otherwise.
const shown = shallowRef<Shape[]>([])
const dragging = ref<string | null>(null)
let frame = 0
watch(
  target,
  (next) => {
    cancelAnimationFrame(frame)
    const before = new Map(shown.value.map((s) => [s.key, s.nums]))
    if (dragging.value || still() || !shown.value.length) {
      shown.value = next
      return
    }
    const start = performance.now()
    const span = duration()
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / span)
      const k = ease(t)
      shown.value = next.map((s) => {
        const from = before.get(s.key)
        if (!from || from.length !== s.nums.length) return s
        return {
          ...s,
          nums: s.nums.map((v, i) =>
            Number.isFinite(from[i]) && Number.isFinite(v) ? from[i] + (v - from[i]) * k : v
          ),
        }
      })
      if (t < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
  },
  { immediate: true }
)

function path(nums: number[], close = false) {
  let d = ''
  let pen = false
  for (let i = 0; i < nums.length; i += 2) {
    const [x, y] = [nums[i], nums[i + 1]]
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      pen = false
      continue
    }
    d += `${pen ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`
    pen = true
  }
  return close ? `${d}Z` : d
}

/** Where a dragged point may go: along its axes, onto its curve, inside the
 *  plane, on its grid. */
function settle(el: Held, x: number, y: number, was: [number, number]): [number, number] {
  const drag = String(el.drag ?? '')
  const snap = num(el.snap, 0)
  const on = fn(el.on)
  if (!drag.includes('x')) x = was[0]
  if (!drag.includes('y') && !on) y = was[1]
  x = Math.min(Math.max(x, xr.value[0]), xr.value[1])
  y = Math.min(Math.max(y, yr.value[0]), yr.value[1])
  // Without a grid, to a thousandth of the axis: no 3.0000000000000004 on screen.
  const round = (v: number, range: [number, number]) => {
    if (snap > 0) return Number((Math.round(v / snap) * snap).toPrecision(12))
    const places = Math.max(0, -Math.floor(Math.log10((range[1] - range[0]) / 1000)))
    return Number(v.toFixed(places))
  }
  x = round(x, xr.value)
  if (on) {
    const fy = on(x)
    if (Number.isFinite(fy)) y = Number(fy.toPrecision(10))
  } else y = round(y, yr.value)
  return [x, y]
}
const draggable = (s: Shape) => s.kind === 'point' && (String(s.item.el.drag ?? '') !== '' || fn(s.item.el.on) != null)

function grab(s: Shape, e: PointerEvent) {
  if (!draggable(s) || e.button !== 0) return
  e.preventDefault()
  const svg = (e.currentTarget as SVGElement).ownerSVGElement as SVGSVGElement
  svg.setPointerCapture(e.pointerId)
  dragging.value = s.key
  ;(e.currentTarget as SVGGElement).focus()
  const move = (ev: PointerEvent) => {
    const r = svg.getBoundingClientRect()
    const was = pair(s.item.el.value) ?? [0, 0]
    const next = settle(s.item.el, ix(ev.clientX - r.left), iy(ev.clientY - r.top), was)
    if (next[0] !== was[0] || next[1] !== was[1]) announce(s.item.el, next)
  }
  const done = () => {
    dragging.value = null
    svg.removeEventListener('pointermove', move)
    svg.removeEventListener('pointerup', done)
    svg.removeEventListener('pointercancel', done)
  }
  svg.addEventListener('pointermove', move)
  svg.addEventListener('pointerup', done)
  svg.addEventListener('pointercancel', done)
  move(e)
}
function nudge(s: Shape, e: KeyboardEvent) {
  if (!draggable(s)) return
  const was = pair(s.item.el.value) ?? [0, 0]
  const snap = num(s.item.el.snap, 0)
  const dx = snap || (xr.value[1] - xr.value[0]) / 50
  const dy = snap || (yr.value[1] - yr.value[0]) / 50
  const by: Record<string, [number, number]> = {
    ArrowRight: [dx, 0],
    ArrowLeft: [-dx, 0],
    ArrowUp: [0, dy],
    ArrowDown: [0, -dy],
  }
  const d = by[e.key]
  if (!d) return
  e.preventDefault()
  const next = settle(s.item.el, was[0] + d[0], was[1] + d[1], was)
  if (next[0] !== was[0] || next[1] !== was[1]) announce(s.item.el, next)
}
/** The arrowhead at a vector's tip, pointing away from where it starts. */
function head([x, y, x0, y0]: number[]) {
  const len = Math.hypot(x - x0, y - y0) || 1
  const [ux, uy] = [(x - x0) / len, (y - y0) / len]
  const [bx, by] = [x - ux * 11, y - uy * 11]
  return `M${x} ${y}L${bx - uy * 5} ${by + ux * 5}L${bx + uy * 5} ${by - ux * 5}Z`
}
const valueText = (s: Shape) => {
  const p = pair(s.item.el.value)
  return p ? `${label(p[0])}, ${label(p[1])}` : ''
}

let sizer: ResizeObserver | null = null
let watcher: MutationObserver | null = null
onMounted(() => {
  // Read on the next frame: setting the width draws the picture, which changes
  // the box's height, which would report again within the same frame.
  sizer = new ResizeObserver(() =>
    requestAnimationFrame(() => {
      const w = box.value?.clientWidth ?? 0
      if (w !== width.value) width.value = w
    })
  )
  if (box.value) sizer.observe(box.value)
  watcher = new MutationObserver(bump)
  watcher.observe(host, { childList: true })
  host.addEventListener(PLANE_ITEM_CHANGED, bump)
  bump()
})
onBeforeUnmount(() => {
  sizer?.disconnect()
  watcher?.disconnect()
  host.removeEventListener(PLANE_ITEM_CHANGED, bump)
  cancelAnimationFrame(frame)
})
const clip = `plane-${Math.random().toString(36).slice(2, 8)}`
</script>
<template>
  <div ref="box" class="plane">
    <svg v-if="width" :viewBox="`0 0 ${width} ${H}`" :width="width" :height="H" role="img">
      <defs>
        <clipPath :id="clip">
          <rect :x="pad.l" :y="pad.t" :width="width - pad.l - pad.r" :height="H - pad.t - pad.b" />
        </clipPath>
      </defs>
      <g class="grid">
        <template v-if="grid">
          <line v-for="t in xt" :key="`gx${t}`" :x1="sx(t)" :x2="sx(t)" :y1="pad.t" :y2="H - pad.b" />
          <line v-for="t in yt" :key="`gy${t}`" :y1="sy(t)" :y2="sy(t)" :x1="pad.l" :x2="width - pad.r" />
        </template>
      </g>
      <g class="axes">
        <line :x1="pad.l" :x2="width - pad.r" :y1="H - pad.b" :y2="H - pad.b" />
        <line :x1="pad.l" :x2="pad.l" :y1="pad.t" :y2="H - pad.b" />
        <text v-for="t in xt" :key="`tx${t}`" :x="sx(t)" :y="H - pad.b + 17" text-anchor="middle">{{ label(t) }}</text>
        <text v-for="t in yt" :key="`ty${t}`" :x="pad.l - 6" :y="sy(t) + 4" text-anchor="end">{{ label(t) }}</text>
        <text v-if="xLabel" :x="(pad.l + width - pad.r) / 2" :y="H - 6" text-anchor="middle">{{ xLabel }}</text>
        <text v-if="yLabel" :x="pad.l" :y="14" text-anchor="start">{{ yLabel }}</text>
      </g>
      <g :clip-path="`url(#${clip})`">
        <template v-for="s in shown" :key="s.key">
          <path v-if="s.kind === 'area'" class="area" :d="path(s.nums, true)" :fill="s.fill" />
          <path
            v-else-if="s.kind === 'curve'"
            class="curve"
            :d="path(s.nums)"
            :stroke="s.stroke"
            :class="{ dashed: s.dashed }"
          />
          <line
            v-else-if="s.kind === 'line'"
            class="line"
            :x1="s.nums[0]"
            :y1="s.nums[1]"
            :x2="s.nums[2]"
            :y2="s.nums[3]"
            :stroke="s.stroke"
            :class="{ dashed: s.dashed }"
          />
        </template>
      </g>
      <template v-for="s in shown" :key="`l${s.key}`">
        <text
          v-if="s.label && s.kind === 'line'"
          class="tag"
          :x="Math.max(s.nums[0], s.nums[2]) - 4"
          :y="Math.min(s.nums[1], s.nums[3]) - 6"
          text-anchor="end"
        >
          {{ s.label }}
        </text>
        <text
          v-else-if="s.label && s.kind === 'curve'"
          class="tag"
          :x="s.nums[s.nums.length - 2] - 4"
          :y="s.nums[s.nums.length - 1] - 8"
          text-anchor="end"
        >
          {{ s.label }}
        </text>
        <text v-else-if="s.label && s.kind === 'area'" class="tag" :x="s.nums[0] + 6" :y="s.nums[1] + 18">
          {{ s.label }}
        </text>
      </template>
      <g
        v-for="s in shown.filter((one) => one.kind === 'point')"
        :key="`p${s.key}`"
        class="point"
        :class="{ movable: draggable(s), held: dragging === s.key }"
        :tabindex="draggable(s) ? 0 : undefined"
        :role="draggable(s) ? 'slider' : undefined"
        :aria-label="s.label || undefined"
        :aria-valuetext="valueText(s)"
        @pointerdown="grab(s, $event)"
        @keydown="nudge(s, $event)"
      >
        <template v-if="s.nums.length >= 4">
          <line class="vector" :x1="s.nums[2]" :y1="s.nums[3]" :x2="s.nums[0]" :y2="s.nums[1]" :stroke="s.stroke" />
          <path :d="head(s.nums)" :fill="s.fill" />
        </template>
        <circle v-if="draggable(s)" class="hit" :cx="s.nums[0]" :cy="s.nums[1]" r="18" />
        <circle v-if="draggable(s)" class="ring" :cx="s.nums[0]" :cy="s.nums[1]" r="11" />
        <circle
          v-if="s.nums.length < 4 || draggable(s)"
          class="dot"
          :cx="s.nums[0]"
          :cy="s.nums[1]"
          :r="draggable(s) ? 7 : 5"
          :fill="s.fill"
        />
        <text v-if="s.label" class="tag" :x="s.nums[0] + 10" :y="s.nums[1] - 10">{{ s.label }}</text>
      </g>
    </svg>
  </div>
</template>
<style>
*,
*::before,
*::after {
  box-sizing: border-box;
}
:host {
  display: block;
  margin: 8px 0;
}
.plane {
  width: 100%;
  min-width: 0;
}
svg {
  display: block;
  overflow: visible;
  font-family: var(--font-sans);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.grid line {
  stroke: var(--line);
  stroke-width: 1;
}
.axes line {
  stroke: var(--line-2);
  stroke-width: 1;
}
.axes text {
  fill: var(--muted);
}
.curve,
.line {
  fill: none;
  stroke-width: 2;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.line {
  stroke-width: 1.5;
}
.dashed {
  stroke-dasharray: 5 4;
}
.area {
  fill-opacity: 0.18;
  stroke: none;
}
.tag {
  fill: var(--text);
  paint-order: stroke;
  stroke: var(--surface);
  stroke-width: 4;
  stroke-linejoin: round;
}
.vector {
  stroke-width: 2;
}
/* Only the dot (and the larger ring of air around a movable one) takes the pointer: a vector's
   shaft or a label crossing another point must not grab it. */
.point {
  pointer-events: none;
}
.point .hit,
.point .dot {
  pointer-events: auto;
}
.point .hit {
  fill: transparent;
}
.point .ring {
  fill: none;
  stroke: transparent;
  stroke-width: 2;
  transition: stroke var(--dur-quick) var(--ease-standard);
}
.point.movable {
  cursor: grab;
  touch-action: none;
  outline: none;
}
.point.movable .dot {
  stroke: var(--surface);
  stroke-width: 2;
}
.point.movable:hover .ring,
.point.movable:focus-visible .ring {
  stroke: var(--line-2);
}
.point.held {
  cursor: grabbing;
}
.point.held .ring,
.point.movable:focus-visible .ring {
  stroke: var(--accent);
}
</style>
