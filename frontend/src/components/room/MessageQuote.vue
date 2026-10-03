<script setup lang="ts">
import type { QuotedContext } from '../../lib/quotedContext'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{ quote: QuotedContext }>()

/** 两种引用共用一个框：文字引用说引的是哪一段，位置引用说指在哪儿。 */
const summary = computed(() =>
  props.quote.kind === 'slide-page'
    ? t(props.quote.scope === 'selection' ? 'slides.quotedSelection' : 'slides.quotedPage', {
        page: props.quote.page,
      })
    : t('slides.pinnedPage', { page: props.quote.page })
)
const pinWhere = computed(() =>
  props.quote.kind === 'page-pin'
    ? t('work.room.preview.pinWhere', {
        left: Math.round(props.quote.x * 100),
        top: Math.round(props.quote.y * 100),
      })
    : ''
)
</script>

<template>
  <details class="message-quote">
    <summary>{{ summary }}</summary>
    <div class="message-quote__path">{{ quote.path }}</div>
    <div class="message-quote__identity">
      {{ t(quote.source === 'committed' ? 'slides.sourceCommitted' : 'slides.sourceLive') }}
      · {{ t('work.room.preview.readVersion', { version: quote.version }) }}
    </div>
    <div v-if="quote.kind === 'slide-page'" class="message-quote__text">{{ quote.text }}</div>
    <div v-else class="message-quote__where">{{ pinWhere }}</div>
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
.message-quote__text,
.message-quote__where {
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
.message-quote__where {
  margin-top: 8px;
  color: var(--faint);
}
</style>
