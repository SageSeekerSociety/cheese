<script setup lang="ts">
import { computed, useHost } from 'vue'

import { announce, optionsOf } from './emit'
const props = defineProps<{ label?: string; options?: unknown; value?: string | number; disabled?: boolean }>()
const host = useHost() as HTMLElement & { value?: unknown }
const list = computed(() => optionsOf(props.options))
</script>
<template>
  <div class="field">
    <span v-if="label" class="label">{{ label }}</span>
    <div class="seg" role="group" :aria-label="label">
      <button
        v-for="o in list"
        :key="String(o.value)"
        type="button"
        :disabled="disabled"
        :aria-pressed="o.value === value"
        @click.stop="announce(host, o.value)"
      >
        {{ o.label }}
      </button>
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
.seg {
  display: inline-flex;
  flex-wrap: wrap;
  padding: 2px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--fill);
}
button {
  min-height: 28px;
  padding: 2px 12px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
button[aria-pressed='true'] {
  background: var(--surface);
  color: var(--ink);
  font-weight: 600;
}
button:disabled {
  cursor: default;
  opacity: 0.5;
}
button:focus-visible {
  outline: 2px solid var(--focus-ring);
}
</style>
