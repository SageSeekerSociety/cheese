<script setup lang="ts">
// 看着的那一处建议下面的卡：谁提的、第几处，接受或拒绝。
import CheeseAvatar from '../../CheeseAvatar.vue'

import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

defineProps<{
  agentName: string
  /** 提建议的 AI 队友的 handle：头像的底色跟着它。 */
  agentHandle: string | null
  index: number
  total: number
  /** 它说的理由；没有就不写。 */
  reason: string | null
  editable: boolean
}>()
const emit = defineEmits<{
  (e: 'accept'): void
  (e: 'reject'): void
}>()
</script>

<template>
  <div class="doc-suggestion-card" role="group" :aria-label="t('work.room.docSuggest.title', { agent: agentName })">
    <CheeseAvatar :size="22" :name="agentName" :handle="agentHandle" class="doc-suggestion-card__avatar" />
    <div class="doc-suggestion-card__body">
      <span class="doc-suggestion-card__title">{{ t('work.room.docSuggest.title', { agent: agentName }) }}</span>
      <span class="doc-suggestion-card__count">
        · {{ t('work.room.docSuggest.position', { i: index + 1, n: total }) }}</span
      >
      <p v-if="reason" class="doc-suggestion-card__reason">{{ reason }}</p>
    </div>
    <template v-if="editable">
      <DocEditButton @click="emit('reject')">{{ t('work.room.docSuggest.reject') }}</DocEditButton>
      <DocEditButton strong @click="emit('accept')">{{ t('work.room.docSuggest.accept') }}</DocEditButton>
    </template>
  </div>
</template>

<style scoped>
.doc-suggestion-card {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
}
.doc-suggestion-card__avatar {
  flex: 0 0 auto;
  margin-top: 1px;
}
.doc-suggestion-card__body {
  flex: 1 1 160px;
  min-width: 0;
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-suggestion-card__title {
  color: var(--ink);
  font-weight: 600;
}
.doc-suggestion-card__count {
  color: var(--faint);
}
.doc-suggestion-card__reason {
  margin: 2px 0 0;
  color: var(--text);
}
</style>
