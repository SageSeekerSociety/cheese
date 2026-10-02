<script setup lang="ts">
import type { QuotedContext } from '../../lib/quotedContext'

import { t } from '@/i18n'

defineProps<{ quote: QuotedContext }>()
</script>

<template>
  <details class="message-quote">
    <summary>
      {{ t(quote.scope === 'selection' ? 'slides.quotedSelection' : 'slides.quotedPage', { page: quote.page }) }}
    </summary>
    <div class="message-quote__path">{{ quote.path }}</div>
    <div class="message-quote__identity">
      {{ t(quote.source === 'committed' ? 'slides.sourceCommitted' : 'slides.sourceLive') }}
      · {{ t('work.room.preview.readVersion', { version: quote.version }) }}
    </div>
    <div class="message-quote__text">{{ quote.text }}</div>
  </details>
</template>

<style scoped>
.message-quote {
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  color: var(--text);
  background: var(--surface);
  font-size: 13px;
  line-height: var(--lh-13);
}
.message-quote summary {
  cursor: pointer;
  font-weight: 500;
}
.message-quote__path,
.message-quote__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.message-quote__path {
  margin-top: 8px;
}
.message-quote__identity {
  margin-top: 4px;
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
.message-quote__text {
  margin-top: 8px;
  max-height: 240px;
  overflow-y: auto;
}
</style>
