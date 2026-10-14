<script setup lang="ts">
import { computed } from 'vue'

import { tween } from '../platform/motion'

const props = defineProps<{ label?: string; value?: string | number; delta?: string; tone?: 'good' | 'bad' }>()

// The first number in the value slides to its new amount, written the way the
// app wrote it: as many decimals, thousands separated or not, the words around
// it kept. A value with no number in it is shown as it is.
const NUMBER = /-?\d[\d,]*(?:\.\d+)?/
const parts = computed(() => {
  const text = String(props.value ?? '')
  const m = NUMBER.exec(text)
  if (!m) return null
  const raw = m[0]
  return {
    before: text.slice(0, m.index),
    after: text.slice(m.index + raw.length),
    amount: Number(raw.replace(/,/g, '')),
    decimals: raw.split('.')[1]?.length ?? 0,
    grouped: raw.includes(','),
  }
})
const amount = tween(() => parts.value?.amount ?? 0)
const shown = computed(() => {
  const p = parts.value
  if (!p) return props.value ?? ''
  const n = amount.value.toFixed(p.decimals)
  const [whole, frac] = n.split('.')
  const body = p.grouped ? whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : whole
  return `${p.before}${frac ? `${body}.${frac}` : body}${p.after}`
})
</script>
<template>
  <div class="stat">
    <div class="label">{{ label }}</div>
    <div class="value">{{ shown }}<slot /></div>
    <div v-if="delta" class="delta" :data-tone="tone">{{ delta }}</div>
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
  /* As the slider: a width in a row, never a height in a column. */
  flex: 1 1 auto;
  min-width: min(140px, 100%);
  /* Cards side by side are one height, whatever the row aligns its controls to. */
  align-self: stretch;
}
.stat {
  height: 100%;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.label {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.value {
  font-size: 18px;
  line-height: var(--lh-18);
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.delta {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.delta[data-tone='good'] {
  color: var(--ok-ink);
}
.delta[data-tone='bad'] {
  color: var(--danger-ink);
}
</style>
