<script setup lang="ts">
// A number inside a sentence that the reader drags sideways to change (or
// steps with the arrow keys), so the app's assumptions can be changed where
// they are stated. The value is a number, kept within min and max, on step.
import { computed, useHost } from 'vue'

import { announce } from './emit'
const props = withDefaults(
  defineProps<{
    value?: number
    min?: number
    max?: number
    step?: number
    unit?: string
    format?: (v: number) => string
  }>(),
  { value: undefined, min: 0, max: 100, step: 1, unit: '' }
)
const host = useHost() as HTMLElement & { value?: unknown }
const current = computed(() => Number(props.value ?? props.min))
const shown = computed(() => (props.format ? props.format(current.value) : `${current.value}${props.unit}`))
const places = computed(() => Math.max(0, (String(props.step).split('.')[1] ?? '').length))

function set(v: number) {
  const clamped = Math.min(Math.max(v, Number(props.min)), Number(props.max))
  const stepped = Number(
    (Math.round((clamped - Number(props.min)) / Number(props.step)) * Number(props.step) + Number(props.min)).toFixed(
      places.value
    )
  )
  if (stepped !== current.value) announce(host, stepped)
}
function grab(e: PointerEvent) {
  if (e.button !== 0) return
  e.preventDefault()
  const el = e.currentTarget as HTMLElement
  el.setPointerCapture(e.pointerId)
  el.focus()
  const [x0, v0] = [e.clientX, current.value]
  // One step for every 6 pixels, but never more than 200 pixels for the whole range.
  const per = Math.max(Number(props.step), (Number(props.max) - Number(props.min)) / 200) / 6
  const move = (ev: PointerEvent) => set(v0 + (ev.clientX - x0) * per)
  const done = () => {
    el.removeEventListener('pointermove', move)
    el.removeEventListener('pointerup', done)
    el.removeEventListener('pointercancel', done)
  }
  el.addEventListener('pointermove', move)
  el.addEventListener('pointerup', done)
  el.addEventListener('pointercancel', done)
}
function key(e: KeyboardEvent) {
  const by = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key]
  if (!by) return
  e.preventDefault()
  set(current.value + by * Number(props.step) * (e.shiftKey ? 10 : 1))
}
</script>
<template>
  <span
    class="scrub"
    tabindex="0"
    role="slider"
    :aria-valuenow="current"
    :aria-valuemin="min"
    :aria-valuemax="max"
    :aria-valuetext="shown"
    @pointerdown="grab"
    @keydown="key"
    >{{ shown }}</span
  >
</template>
<style>
:host {
  display: inline;
}
.scrub {
  color: var(--accent-ink);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  border-bottom: 1px dashed var(--accent);
  cursor: ew-resize;
  touch-action: none;
  user-select: none;
  white-space: nowrap;
  outline: none;
}
.scrub:hover,
.scrub:focus-visible {
  background: var(--accent-wash);
  border-radius: var(--radius-sm);
}
</style>
