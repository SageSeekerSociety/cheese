<script setup lang="ts">
// 「查看改动」打开文档时正文上方那一条：谁让 AI 队友改的、还有几处在，上一处 / 下一处，
// 收起。
import CheeseAvatar from '../../CheeseAvatar.vue'

import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

defineProps<{
  agentName: string
  requester: string
  /** 新文还在正文里的那几处。 */
  count: number
}>()
const emit = defineEmits<{
  (e: 'step', direction: -1 | 1): void
  (e: 'close'): void
}>()
</script>

<template>
  <div class="doc-review-strip" role="region" :aria-label="t('work.room.docReview.label')">
    <CheeseAvatar :size="18" :name="agentName" />
    <span class="doc-review-strip__text">{{
      count > 0
        ? requester
          ? t('work.room.docReview.title', { requester, agent: agentName, n: count })
          : t('work.room.docReview.titleOwn', { agent: agentName, n: count })
        : requester
          ? t('work.room.docReview.allRestored', { requester, agent: agentName })
          : t('work.room.docReview.allRestoredOwn', { agent: agentName })
    }}</span>
    <template v-if="count > 0">
      <button
        type="button"
        class="doc-review-strip__nav"
        :aria-label="t('work.room.docEdit.prev')"
        :title="t('work.room.docEdit.prev')"
        @click="emit('step', -1)"
      >
        <v-icon size="16">mdi-arrow-up</v-icon>
      </button>
      <button
        type="button"
        class="doc-review-strip__nav"
        :aria-label="t('work.room.docEdit.next')"
        :title="t('work.room.docEdit.next')"
        @click="emit('step', 1)"
      >
        <v-icon size="16">mdi-arrow-down</v-icon>
      </button>
    </template>
    <span class="doc-review-strip__spacer" />
    <DocEditButton @click="emit('close')">{{ t('work.room.docReview.close') }}</DocEditButton>
  </div>
</template>

<style scoped>
.doc-review-strip {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 6px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--fill);
}
.doc-review-strip__text {
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-review-strip__spacer {
  flex: 1 1 auto;
}
.doc-review-strip__nav {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  color: var(--muted);
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-review-strip__nav:hover {
  background: var(--fill-2);
}
.doc-review-strip__nav:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
