<script setup lang="ts">
import { computed, useHost } from 'vue'

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
const current = computed(() => (props.value ?? props.min) as number)
const shown = computed(() => (props.format ? props.format(Number(current.value)) : `${current.value}${props.unit}`))
function move(e: Event) {
  e.stopPropagation()
  announce(host, Number((e.target as HTMLInputElement).value))
}
</script>
<template>
  <label class="slider" :class="{ off: disabled }">
    <span class="top"
      ><span class="label">{{ label }}</span
      ><output>{{ shown }}</output></span
    >
    <input
      type="range"
      :min="min"
      :max="max"
      :step="step"
      :value="current"
      :disabled="disabled"
      :aria-label="label"
      @input="move"
      @change.stop
    />
  </label>
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
}
.slider {
  display: block;
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
input {
  width: 100%;
  margin: 6px 0 0;
  accent-color: var(--ink);
}
input:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
