<script setup lang="ts">
// 看着的那一处改动下面的卡：第几处，不要就还原。
import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

defineProps<{ index: number; total: number; busy: boolean; editable: boolean }>()
const emit = defineEmits<{ (e: 'restore'): void }>()
</script>

<template>
  <div class="doc-review-card" role="group" :aria-label="t('work.room.docReview.position', { i: index + 1, n: total })">
    <span class="doc-review-card__text">{{ t('work.room.docReview.position', { i: index + 1, n: total }) }}</span>
    <DocEditButton v-if="editable" :disabled="busy" @click="emit('restore')">
      {{ t('work.room.docReview.restore') }}
    </DocEditButton>
  </div>
</template>

<style scoped>
.doc-review-card {
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
.doc-review-card__text {
  flex: 1 1 auto;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
