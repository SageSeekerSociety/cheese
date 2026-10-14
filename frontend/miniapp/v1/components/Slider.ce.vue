<script setup lang="ts">
// A slider drawn by the platform rather than the browser's range input, which
// is thin, small to grab, and jumps a whole step at a time. The thumb follows
// the finger continuously while the value snaps to the step; on release it
// slides onto the nearest step. A few steps (24 or fewer) are marked under
// the track. A long press on a phone holds the slider instead of selecting
// the page's text. The keyboard works as on a range input.
import { computed, ref, useHost } from 'vue'

import { announce } from './emit'
const props = withDefaults(
  defineProps<{
    label?: string
    min?: number
    max?: number
    step?: number
    value?: number
    unit?: string
    format?: (v: number) => string
    disabled?: boolean
  }>(),
  { min: 0, max: 100, step: 1, value: undefined, unit: '' }
)
const host = useHost() as HTMLElement & { value?: unknown }
const rail = ref<HTMLElement | null>(null)
const lo = computed(() => Number(props.min))
const hi = computed(() => Math.max(Number(props.max), lo.value))
const unit = computed(() => (Number(props.step) > 0 ? Number(props.step) : 1))
const current = computed(() => Number(props.value ?? lo.value))
/** As many decimals as the step has: a value the app computed shows as 0.3, not 0.30000000000000004. */
const places = computed(() => (String(unit.value).split('.')[1] ?? '').length)
const shown = computed(() =>
  props.format ? props.format(current.value) : `${Number(current.value.toFixed(places.value))}${props.unit}`
)
const steps = computed(() => Math.round((hi.value - lo.value) / unit.value))
const ticks = computed(() => (steps.value > 0 && steps.value <= 24 ? steps.value : 0))

/** Where the thumb is drawn, 0 to 1: under the finger while dragging, else at the value. */
const held = ref<number | null>(null)
const settling = ref(false)
const frac = computed(() => {
  if (held.value !== null) return held.value
  const span = hi.value - lo.value
  return span > 0 ? Math.min(1, Math.max(0, (current.value - lo.value) / span)) : 0
})

function snap(v: number) {
  const n = lo.value + Math.round((v - lo.value) / unit.value) * unit.value
  return Number(Math.min(hi.value, Math.max(lo.value, n)).toPrecision(12))
}
function send(v: number) {
  const next = snap(v)
  if (next !== current.value) announce(host, next)
}
function at(e: PointerEvent) {
  const r = rail.value!.getBoundingClientRect()
  return r.width > 0 ? Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) : 0
}
function down(e: PointerEvent) {
  if (props.disabled || e.button !== 0) return
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  ;(e.currentTarget as HTMLElement).focus()
  settling.value = true
  held.value = at(e)
  send(lo.value + held.value * (hi.value - lo.value))
}
function move(e: PointerEvent) {
  if (held.value === null) return
  settling.value = false
  held.value = at(e)
  send(lo.value + held.value * (hi.value - lo.value))
}
function up() {
  if (held.value === null) return
  settling.value = true
  held.value = null
}
function key(e: KeyboardEvent) {
  if (props.disabled) return
  const big = Math.max(unit.value, Math.round((hi.value - lo.value) / 10 / unit.value) * unit.value)
  const by: Record<string, number> = {
    ArrowRight: unit.value,
    ArrowUp: unit.value,
    ArrowLeft: -unit.value,
    ArrowDown: -unit.value,
    PageUp: big,
    PageDown: -big,
  }
  let next: number | null = null
  if (e.key in by) next = current.value + by[e.key]
  else if (e.key === 'Home') next = lo.value
  else if (e.key === 'End') next = hi.value
  if (next === null) return
  e.preventDefault()
  settling.value = true
  send(next)
}
</script>
<template>
  <div class="slider" :class="{ off: disabled }">
    <span class="top"
      ><span class="label">{{ label }}</span
      ><output>{{ shown }}</output></span
    >
    <div
      class="area"
      :class="{ settle: settling, held: held !== null }"
      role="slider"
      :tabindex="disabled ? -1 : 0"
      :aria-label="label"
      :aria-valuemin="lo"
      :aria-valuemax="hi"
      :aria-valuenow="current"
      :aria-valuetext="shown"
      :aria-disabled="disabled || undefined"
      @pointerdown="down"
      @pointermove="move"
      @pointerup="up"
      @pointercancel="up"
      @lostpointercapture="up"
      @keydown="key"
      @contextmenu.prevent
      @touchstart.prevent
    >
      <div ref="rail" class="rail">
        <div class="track"></div>
        <div class="done" :style="{ width: `${frac * 100}%` }"></div>
        <i
          v-for="i in ticks ? ticks + 1 : 0"
          :key="i"
          class="tick"
          :class="{ on: (i - 1) / ticks <= frac + 1e-9 }"
          :style="{ left: `${((i - 1) / ticks) * 100}%` }"
        ></i>
        <div class="thumb" :style="{ left: `${frac * 100}%` }"></div>
      </div>
    </div>
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
  flex: 1 1 220px;
  min-width: 0;
  margin: 8px 0;
  -webkit-user-select: none;
  user-select: none;
  -webkit-touch-callout: none;
  -webkit-tap-highlight-color: transparent;
}
.off {
  opacity: 0.5;
}
.top {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
output {
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.area {
  position: relative;
  height: 36px;
  margin-top: 2px;
  cursor: pointer;
  outline: none;
  touch-action: none;
}
.off .area {
  cursor: default;
}
/* Inset by the thumb's radius, so the thumb and the end ticks stay on the track. */
.rail {
  position: absolute;
  inset: 0 10px;
}
.track,
.done {
  position: absolute;
  left: 0;
  top: 13px;
  height: 6px;
  border-radius: var(--radius-pill);
}
.track {
  right: 0;
  background: var(--fill-2);
}
.done {
  background: var(--ink);
}
.tick {
  position: absolute;
  top: 24px;
  width: 2px;
  height: 5px;
  margin-left: -1px;
  background: var(--line-2);
}
.tick.on {
  background: var(--muted);
}
.thumb {
  position: absolute;
  top: 6px;
  width: 20px;
  height: 20px;
  margin-left: -10px;
  border-radius: 50%;
  background: var(--surface);
  border: 2px solid var(--ink);
  box-shadow: 0 0 0 0 var(--fill-2);
  transition: box-shadow var(--dur-quick) var(--ease-standard);
}
.settle .thumb,
.settle .done {
  transition:
    left var(--dur-quick) var(--ease-standard),
    width var(--dur-quick) var(--ease-standard),
    box-shadow var(--dur-quick) var(--ease-standard);
}
.area:hover .thumb {
  box-shadow: 0 0 0 6px var(--fill-2);
}
.held .thumb {
  box-shadow: 0 0 0 8px var(--fill-2);
}
.area:focus-visible .thumb {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
@media (prefers-reduced-motion: reduce) {
  .settle .thumb,
  .settle .done {
    transition: none;
  }
}
</style>
