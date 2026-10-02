<script setup lang="ts">
import type { Block } from '../../../cx_types'

import { t } from '@/i18n'

defineProps<{
  comment: Block
  anchor: Block | null
  quoteStatus: 'unique' | 'missing' | 'ambiguous'
  expanded: boolean
  overflowing: boolean
}>()
const emit = defineEmits<{
  (e: 'locate'): void
  (e: 'expand'): void
}>()
function nodeLabel(content: string): string {
  const label = content.replace(/^#+\s*/, '').trim()
  return label.length > 22 ? label.slice(0, 22) + '…' : label || t('work.room.comments.emptyParagraph')
}
</script>

<template>
  <div class="doc-comment-body">
    <button
      v-if="comment.reply_to && anchor"
      type="button"
      class="doc-comment-body__quote"
      :title="t('work.room.comments.locate')"
      dir="auto"
      @click="emit('locate')"
    >
      {{ comment.anchor_quote || nodeLabel(anchor.content) }}
    </button>
    <div v-else-if="comment.reply_to || comment.anchor_quote" class="doc-comment-body__stale">
      {{ t('work.room.comments.anchorChanged') }}
    </div>
    <div
      v-if="comment.anchor_quote && quoteStatus !== 'unique' && comment.reply_to && anchor"
      class="doc-comment-body__stale"
    >
      {{ t(quoteStatus === 'ambiguous' ? 'work.room.comments.anchorAmbiguous' : 'work.room.comments.anchorChanged') }}
    </div>
    <div class="doc-comment-body__text" :class="{ 'is-expanded': expanded }" :data-comment-body="comment.id" dir="auto">
      {{ comment.content }}
    </div>
    <button
      v-if="overflowing"
      type="button"
      class="doc-comment-body__expand"
      :aria-expanded="expanded"
      @click="emit('expand')"
    >
      {{ t(expanded ? 'work.room.comments.showLess' : 'work.room.comments.showMore') }}
    </button>
  </div>
</template>

<style scoped>
.doc-comment-body {
  min-width: 0;
  padding-bottom: 8px;
}
.doc-comment-body__text {
  display: block;
  max-height: calc(16 * var(--lh-14));
  overflow: hidden;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.doc-comment-body__text.is-expanded {
  max-height: none;
}
.doc-comment-body__quote {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  width: 100%;
  text-align: start;
  border-inline-start: 2px solid var(--line-2);
  padding: 4px 8px;
  margin-bottom: 12px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: pre-wrap;
}
.doc-comment-body__quote:hover {
  background: var(--fill);
}
.doc-comment-body__stale {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
  margin-block: 4px 8px;
}
.doc-comment-body__expand {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  margin-block: 8px;
  padding: 4px;
}
.doc-comment-body__quote:focus-visible,
.doc-comment-body__expand:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
</style>
