<script setup lang="ts">
// A space with three axes: the app gives the ranges and puts points, curves,
// surfaces, solids and groups inside it in its own numbers (z up); the space
// draws them with depth and light in the document's colours, lets the reader
// turn it and drag the points the app allows, and slides whatever changes to
// its new place. The drawing is space/space.ts; this is the element around it:
// its size, the reader's hands, and the button that puts the view back.
import { computed, onBeforeUnmount, onMounted, ref, useHost, watch } from 'vue'

import { fail } from '../runtime'

import { type Box, Space } from './space/space'

const props = withDefaults(
  defineProps<{
    x?: number[]
    y?: number[]
    z?: number[]
    height?: number
    view?: number[]
    equal?: boolean
    axes?: string
    xLabel?: string
    yLabel?: string
    zLabel?: string
  }>(),
  {
    x: () => [-1, 1],
    y: () => [-1, 1],
    z: () => [-1, 1],
    height: 320,
    view: () => [-1.0, 0.45],
    equal: false,
    axes: '',
    xLabel: 'x',
    yLabel: 'y',
    zLabel: 'z',
  }
)

const host = useHost() as HTMLElement
const box = ref<HTMLElement | null>(null)
const stage = ref<HTMLElement | null>(null)
const overlay = ref<HTMLElement | null>(null)
const turned = ref(false)
const readout = ref<{ text: string; x: number; y: number } | null>(null)
const full = ref(document.documentElement.dataset.mode === 'full')
let space: Space | null = null

const num = (v: unknown, fallback: number) =>
  v !== '' && v != null && Number.isFinite(Number(v)) ? Number(v) : fallback
const flag = (v: unknown) => v === '' || v === true || v === 'true'
function pair(v: unknown, fallback: [number, number]): [number, number] {
  return Array.isArray(v) && v.length >= 2 && v.every((n) => Number.isFinite(Number(n))) && Number(v[0]) < Number(v[1])
    ? [Number(v[0]), Number(v[1])]
    : fallback
}
const H = computed(() => Math.max(160, num(props.height, 320)))
const spec = computed<Box>(() => ({
  x: pair(props.x, [-1, 1]),
  y: pair(props.y, [-1, 1]),
  z: pair(props.z, [-1, 1]),
  equal: flag(props.equal),
  axes: props.axes !== 'none',
  labels: [props.xLabel || 'x', props.yLabel || 'y', props.zLabel || 'z'],
}))
const asked = computed(() => {
  const v = Array.isArray(props.view) ? props.view : []
  return { az: num(v[0], -1.0), el: num(v[1], 0.45) }
})

const told = new Set<string>()
function report(message: string) {
  if (told.has(message)) return
  told.add(message)
  fail(message)
}

// ——— the reader's hands ———
//
// A mouse turns the view freely. In the document a finger turns it only
// sideways, around the upright axis: a finger moving up or down is scrolling
// the page, and the page keeps it (touch-action: pan-y). Full screen, the
// space has the screen to itself: a finger turns it every way and two pinch.
const TURN = 0.008
const touches = new Map<number, { x: number; y: number }>()
let gesture: {
  kind: 'turn' | 'drag' | 'pinch'
  x: number
  y: number
  moved: number
  at: number
  dist?: number
} | null = null
let lastMove = { dx: 0, dy: 0, t: 0 }

function local(e: PointerEvent) {
  const r = stage.value!.getBoundingClientRect()
  return { x: e.clientX - r.left, y: e.clientY - r.top }
}

function down(e: PointerEvent) {
  if (!space || (e.pointerType === 'mouse' && e.button !== 0)) return
  const p = local(e)
  touches.set(e.pointerId, p)
  stage.value!.setPointerCapture(e.pointerId)
  space.stopSpin()
  if (touches.size === 2 && full.value) {
    const [a, b] = [...touches.values()]
    gesture = {
      kind: 'pinch',
      x: p.x,
      y: p.y,
      moved: 99,
      at: performance.now(),
      dist: Math.hypot(a.x - b.x, a.y - b.y),
    }
    return
  }
  if (touches.size > 1) return
  const point = space.pointAt(p.x, p.y, (r) => space!.movable(r))
  if (point) {
    e.preventDefault()
    space.grab(point, p.x, p.y)
    readout.value = space.heldAt()
    gesture = { kind: 'drag', x: p.x, y: p.y, moved: 0, at: performance.now() }
    return
  }
  gesture = { kind: 'turn', x: p.x, y: p.y, moved: 0, at: performance.now() }
}

function move(e: PointerEvent) {
  if (!space) return
  const p = local(e)
  if (!gesture) {
    if (e.pointerType === 'mouse') {
      const point = space.pointAt(p.x, p.y, (r) => space!.movable(r))
      stage.value!.style.cursor = point ? 'grab' : space.clickAt(p.x, p.y) ? 'pointer' : ''
    }
    return
  }
  if (!touches.has(e.pointerId)) return
  touches.set(e.pointerId, p)
  if (gesture.kind === 'pinch') {
    const [a, b] = [...touches.values()]
    const d = Math.hypot(a.x - b.x, a.y - b.y)
    if (gesture.dist && d > 0) space.zoomBy(gesture.dist / d)
    gesture.dist = d
    turned.value = true
    return
  }
  const dx = p.x - gesture.x
  const dy = p.y - gesture.y
  gesture.moved += Math.hypot(dx, dy)
  gesture.x = p.x
  gesture.y = p.y
  if (gesture.kind === 'drag') {
    space.drag(p.x, p.y)
    readout.value = space.heldAt()
    return
  }
  const sideways = e.pointerType === 'touch' && !full.value
  space.turn(dx * TURN, sideways ? 0 : dy * TURN)
  lastMove = { dx: dx * TURN, dy: sideways ? 0 : dy * TURN, t: performance.now() }
}

function up(e: PointerEvent) {
  touches.delete(e.pointerId)
  if (!space || !gesture) return
  const g = gesture
  if (touches.size > 0 && g.kind === 'pinch') return
  gesture = null
  if (g.kind === 'drag') {
    space.release()
    readout.value = null
    return
  }
  if (g.kind === 'turn' && g.moved < 5 && e.type === 'pointerup') {
    const p = local(e)
    space.clickAt(p.x, p.y)?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    return
  }
  // Let go while moving: the view keeps turning a moment, slowing to a stop.
  if (g.kind === 'turn' && performance.now() - lastMove.t < 60) space.turn(lastMove.dx, lastMove.dy, true)
}

function key(e: KeyboardEvent) {
  if (!space) return
  const by: Record<string, [number, number]> = {
    ArrowLeft: [-0.12, 0],
    ArrowRight: [0.12, 0],
    ArrowUp: [0, 0.1],
    ArrowDown: [0, -0.1],
  }
  const d = by[e.key]
  if (!d) return
  e.preventDefault()
  space.turn(d[0], d[1])
}

function reset() {
  space?.setView(asked.value.az, asked.value.el, true)
  turned.value = false
}

// ——— size, ranges and view from the app ———

let sizer: ResizeObserver | null = null
let modes: MutationObserver | null = null
onMounted(() => {
  space = new Space(host, stage.value!, overlay.value!, spec.value)
  space.onError = report
  space.onViewChanged = () => (turned.value = true)
  space.setView(asked.value.az, asked.value.el, false)
  sizer = new ResizeObserver(() =>
    requestAnimationFrame(() => {
      const w = box.value?.clientWidth ?? 0
      if (w > 0) space?.resize(w, H.value)
    })
  )
  if (box.value) sizer.observe(box.value)
  modes = new MutationObserver(() => {
    full.value = document.documentElement.dataset.mode === 'full'
  })
  modes.observe(document.documentElement, { attributes: true, attributeFilter: ['data-mode'] })
})
watch(spec, (b) => space?.setBox(b))
watch(asked, (v) => {
  space?.setView(v.az, v.el, true)
  turned.value = false
})
watch(H, (h) => space?.resize(box.value?.clientWidth ?? 0, h))
onBeforeUnmount(() => {
  sizer?.disconnect()
  modes?.disconnect()
  space?.dispose()
  space = null
})
</script>
<template>
  <div ref="box" class="space" :style="{ height: `${H}px` }">
    <div
      ref="stage"
      class="stage"
      :class="{ full }"
      tabindex="0"
      role="img"
      aria-roledescription="三维图"
      @pointerdown="down"
      @pointermove="move"
      @pointerup="up"
      @pointercancel="up"
      @keydown="key"
    />
    <div ref="overlay" class="overlay">
      <slot />
      <div v-if="readout" class="readout" :style="{ transform: `translate(${readout.x + 14}px, ${readout.y + 14}px)` }">
        {{ readout.text }}
      </div>
    </div>
    <button v-if="turned" class="reset" type="button" @click="reset">复位视角</button>
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
.space {
  position: relative;
  width: 100%;
  min-width: 0;
  overflow: hidden;
}
.stage {
  width: 100%;
  height: 100%;
  outline: none;
  touch-action: pan-y;
  user-select: none;
  -webkit-user-select: none;
}
.stage.full {
  touch-action: none;
}
.stage:focus-visible {
  box-shadow: inset 0 0 0 2px var(--accent);
  border-radius: var(--radius-sm);
}
.stage canvas {
  display: block;
  width: 100%;
  height: 100%;
}
.overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  font-family: var(--font-sans);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.overlay > div {
  position: absolute;
  left: 0;
  top: 0;
  white-space: nowrap;
}
.tick {
  color: var(--muted);
}
.name {
  color: var(--text);
  font-style: italic;
}
.tag {
  color: var(--text);
  text-shadow:
    0 0 2px var(--surface),
    0 0 2px var(--surface),
    0 0 3px var(--surface);
}
/* Where a draggable point takes the keyboard's focus; seen only when focused. */
.handle {
  width: 26px;
  height: 26px;
  border-radius: var(--radius-pill);
  outline: none;
}
.handle:focus-visible {
  box-shadow: 0 0 0 2px var(--accent);
}
.readout {
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  background: var(--surface);
  border: 1px solid var(--line-2);
  color: var(--text);
}
.reset {
  position: absolute;
  top: 6px;
  right: 6px;
  font: inherit;
  font-size: 13px;
  padding: 3px 10px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
}
.reset:hover {
  background: var(--line);
}
</style>
