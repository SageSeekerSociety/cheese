<script setup lang="ts">
defineProps<{ kind?: 'ok' | 'no' | 'warn' }>()
const SIGNS = { ok: '✓', no: '✗', warn: '!' }
</script>
<template>
  <span class="chip" :data-kind="kind ?? 'ok'">{{ SIGNS[kind ?? 'ok'] }} <slot /></span>
</template>
<style>
*,
*::before,
*::after {
  box-sizing: border-box;
}
:host {
  display: inline-block;
  margin: 2px 4px 2px 0;
}
.chip {
  display: inline-block;
  padding: 0 8px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: var(--lh-13);
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.chip[data-kind='no'] {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
.chip[data-kind='warn'] {
  background: var(--warn-wash);
  color: var(--warn-ink);
}
</style>
