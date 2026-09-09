<script setup lang="ts">
import i18n, { setLocale } from '@/i18n'

const locale = i18n.global.locale
</script>

<template>
  <button
    type="button"
    class="language-toggle"
    :lang="locale === 'en' ? 'zh-CN' : 'en'"
    :aria-label="locale === 'en' ? '切换到中文' : 'Switch to English'"
    @click="setLocale(locale === 'en' ? 'zh-CN' : 'en')"
  >
    {{ locale === 'en' ? '中文' : 'English' }}
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
