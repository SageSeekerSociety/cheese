<script setup lang="ts">
import { useHost } from 'vue'

import { announce } from './emit'
const props = withDefaults(
  defineProps<{
    label?: string
    value?: number
    min?: number
    max?: number
    step?: number
    unit?: string
    disabled?: boolean
  }>(),
  { step: 1, unit: '', value: undefined, min: undefined, max: undefined }
)
const host = useHost() as HTMLElement & { value?: unknown }
function clamp(n: number) {
  if (props.min !== undefined && n < props.min) return props.min
  if (props.max !== undefined && n > props.max) return props.max
  return n
}
function typed(e: Event) {
  e.stopPropagation()
  const raw = (e.target as HTMLInputElement).value
  if (raw === '' || Number.isNaN(Number(raw))) return
  announce(host, Number(raw))
}
function settle(e: Event) {
  e.stopPropagation()
  const input = e.target as HTMLInputElement
  if (input.value === '') {
    input.value = String(props.value ?? '')
    return
  }
  const n = clamp(Number(input.value))
  input.value = String(n)
  if (n !== props.value) announce(host, n)
}
</script>
<template>
  <label class="field">
    <span v-if="label" class="label">{{ label }}</span>
    <span class="box"
      ><input
        type="number"
        :value="value"
        :min="min"
        :max="max"
        :step="step"
        :disabled="disabled"
        :aria-label="label"
        @input="typed"
        @change="settle"
      /><span v-if="unit" class="unit">{{ unit }}</span></span
    >
  </label>
</template>
<style>
*,
*::before,
*::after {
  box-sizing: border-box;
}
:host {
  display: inline-block;
  margin: 8px 0;
}
.field {
  display: inline-flex;
  flex-direction: column;
  gap: 4px;
}
.label {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.box {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
input {
  width: 112px;
  min-height: 32px;
  padding: 4px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}
input:disabled {
  opacity: 0.5;
}
input:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.unit {
  font-size: 13px;
  color: var(--muted);
}
</style>
