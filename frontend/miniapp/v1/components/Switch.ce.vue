<script setup lang="ts">
import { computed, useHost } from 'vue'

import { announce } from './emit'
const props = defineProps<{ label?: string; value?: boolean; checked?: boolean; disabled?: boolean }>()
const host = useHost() as HTMLElement & { value?: unknown; checked?: unknown }
const on = computed(() => Boolean(props.value ?? props.checked))
function flip() {
  const next = !on.value
  host.checked = next
  announce(host, next)
}
</script>
<template>
  <button type="button" role="switch" class="switch" :aria-checked="on" :disabled="disabled" @click.stop="flip">
    <span class="track"><span class="knob" /></span><span v-if="label" class="label">{{ label }}</span>
  </button>
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
.switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}
.switch:disabled {
  cursor: default;
  opacity: 0.5;
}
.track {
  position: relative;
  width: 34px;
  height: 20px;
  border-radius: var(--radius-pill);
  background: var(--line-2);
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: var(--radius-pill);
  background: var(--surface);
  transition: transform var(--dur-quick) var(--ease-standard);
}
.switch[aria-checked='true'] .track {
  background: var(--ink);
}
.switch[aria-checked='true'] .knob {
  transform: translateX(14px);
}
.switch:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
