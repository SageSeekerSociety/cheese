<script setup lang="ts">
defineProps<{ label?: string; value?: string | number; delta?: string; tone?: 'good' | 'bad' }>()
</script>
<template>
  <div class="stat">
    <div class="label">{{ label }}</div>
    <div class="value">{{ value }}<slot /></div>
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
  flex: 1 1 140px;
  min-width: 0;
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
