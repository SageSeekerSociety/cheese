<script setup lang="ts">
import { computed, useHost } from 'vue'

import { announce, optionsOf } from './emit'
const props = defineProps<{ label?: string; options?: unknown; value?: string | number; disabled?: boolean }>()
const host = useHost() as HTMLElement & { value?: unknown }
const list = computed(() => optionsOf(props.options))
function pick(e: Event) {
  e.stopPropagation()
  const i = (e.target as HTMLSelectElement).selectedIndex
  announce(host, list.value[i]?.value)
}
</script>
<template>
  <label class="field">
    <span v-if="label" class="label">{{ label }}</span>
    <select :aria-label="label" :disabled="disabled" @change="pick" @input.stop>
      <option v-for="o in list" :key="String(o.value)" :selected="o.value === value">{{ o.label }}</option>
    </select>
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
select {
  min-height: 32px;
  padding: 4px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 14px;
}
select:disabled {
  opacity: 0.5;
}
select:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
</style>
