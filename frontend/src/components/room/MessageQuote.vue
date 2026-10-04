<script setup lang="ts">
import type { QuotedContext } from '../../lib/quotedContext'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{ quote: QuotedContext }>()

/** 各种引用共用一个框：文字引用说引的是哪一段，位置引用说指在哪儿。
 *
 *  按 `kind` 分派，最后一支兜住这个版本还不认识的 kind——库里可能已经躺着更新的
 *  客户端发来的引用，读到它时画不出细节，但至少要说得出「这里有一份引用」，不能
 *  把 kind 当页码算出一串 undefined。 */
const summary = computed(() => {
  const quote = props.quote
  switch (quote.kind) {
    case 'slide-page':
      return t(quote.scope === 'selection' ? 'slides.quotedSelection' : 'slides.quotedPage', {
        page: quote.page,
      })
    case 'page-pin':
      return t('slides.pinnedPage', { page: quote.page })
    case 'sheet-cell':
      return t('slides.quotedCell', {
        address: quote.sheet ? `${quote.sheet}!${quote.address}` : quote.address,
      })
    case 'text-range':
      return t('slides.quotedPassage')
    case 'web-element':
      return t('slides.quotedWebElement')
    case 'web-text':
      return t('slides.quotedWebText')
    case 'web-region':
      return t('slides.quotedWebRegion')
    default:
      return t('slides.quotedSource')
  }
})
/** 文件身份那一半（路径、来源、版本）。网页框选的区域只带网址，没有这几样 —— 在
 *  这里先收窄再取字段：`quote.path` 在只带网址的那一支上不存在，拿联集直接取连编译
 *  都过不去。 */
const path = computed(() => ('path' in props.quote ? props.quote.path : ''))
const identityLine = computed(() => {
  const quote = props.quote
  if (!('path' in quote)) return ''
  return (
    t(quote.source === 'committed' ? 'slides.sourceCommitted' : 'slides.sourceLive') +
    ' · ' +
    t('work.room.preview.readVersion', { version: quote.version })
  )
})
/** 网页引用说明在哪：有选择器就说选择器，否则说网址。 */
const webWhere = computed(() => {
  const quote = props.quote
  if (quote.kind === 'web-element' || quote.kind === 'web-text') return quote.selector
  if (quote.kind === 'web-region') return quote.url
  return ''
})
const pinWhere = computed(() =>
  props.quote.kind === 'page-pin'
    ? t('work.room.preview.pinWhere', {
        left: Math.round(props.quote.x * 100),
        top: Math.round(props.quote.y * 100),
      })
    : ''
)
/** 正文那一段属于哪一节；没有标题就是文件开头。 */
const rangeWhere = computed(() =>
  props.quote.kind === 'text-range'
    ? props.quote.heading
      ? t('work.room.preview.mdHeading', { heading: props.quote.heading })
      : t('work.room.preview.mdTop')
    : ''
)
</script>

<template>
  <details class="message-quote">
    <summary>{{ summary }}</summary>
    <div v-if="path" class="message-quote__path">{{ path }}</div>
    <div v-if="identityLine" class="message-quote__identity">{{ identityLine }}</div>
    <div v-if="quote.kind === 'text-range'" class="message-quote__where">{{ rangeWhere }}</div>
    <div v-if="quote.kind === 'slide-page' || quote.kind === 'text-range'" class="message-quote__text">
      {{ quote.text }}
    </div>
    <div v-else-if="quote.kind === 'sheet-cell'" class="message-quote__text">
      {{ quote.value || t('work.room.preview.emptyCell') }}
    </div>
    <div v-else-if="quote.kind === 'page-pin'" class="message-quote__where">{{ pinWhere }}</div>
    <template v-else-if="quote.kind === 'web-element' || quote.kind === 'web-text'">
      <div class="message-quote__where">{{ webWhere }}</div>
      <div class="message-quote__text">{{ quote.text }}</div>
    </template>
    <div v-else-if="quote.kind === 'web-region'" class="message-quote__where">{{ webWhere }}</div>
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
