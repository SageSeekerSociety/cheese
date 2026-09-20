<script setup lang="ts">
import { computed } from 'vue'

import i18n, { LANGUAGE_NAMES, LANGUAGE_SWITCH_LABELS, otherLocale, setLocale } from '@/i18n'

const locale = i18n.global.locale

// The button names the language it switches *to*, written in that language, so a
// visitor who cannot read the current one can still find it. `target` drives all
// three properties from one decision.
const target = computed(() => otherLocale(locale.value))
</script>

<template>
  <button
    type="button"
    class="language-toggle"
    :lang="target"
    :aria-label="LANGUAGE_SWITCH_LABELS[target]"
    @click="setLocale(target)"
  >
    {{ LANGUAGE_NAMES[target] }}
  </button>
</template>

<style scoped>
.language-toggle {
  min-height: 40px;
  padding: 6px 12px;
  font: inherit;
  font-size: 14px;
  color: var(--ink);
  white-space: nowrap;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.language-toggle:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}
</style>
