<script setup lang="ts">
// AI 队友提议的一个任务，接在对话后面等人决定。它不能自己创建任务，只能提议；房间里
// 的人点「创建任务」，点的人就是负责人。取数和创建在对话栏那一层，这里只画。
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  proposal: { id: string; title: string; summary: string }
  /** 提议的 AI 队友叫什么。 */
  proposer: string
  busy?: boolean
}>()

const emit = defineEmits<{
  (e: 'decide', decision: 'accept' | 'dismiss'): void
}>()
</script>

<template>
  <div class="task-proposal" data-testid="task-proposal">
    <div class="t-meta task-proposal__eyebrow">{{ t('work.task.proposal.byline', { name: proposer }) }}</div>
    <div class="t-title task-proposal__title">{{ proposal.title }}</div>
    <p v-if="proposal.summary.trim()" class="t-body task-proposal__summary">{{ proposal.summary.trim() }}</p>
    <div class="task-proposal__actions">
      <BaseButton kind="primary" size="sm" :loading="busy" @click="emit('decide', 'accept')">{{
        t('work.task.proposal.accept')
      }}</BaseButton>
      <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('decide', 'dismiss')">{{
        t('work.task.proposal.dismiss')
      }}</BaseButton>
    </div>
  </div>
</template>

<style scoped>
.task-proposal {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 8px 16px 0;
  padding: 12px 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.task-proposal__eyebrow {
  color: var(--muted);
}
.task-proposal__title {
  color: var(--ink);
}
.task-proposal__summary {
  margin: 0;
  color: var(--text);
  white-space: pre-wrap;
}
.task-proposal__actions {
  display: flex;
  gap: 8px;
}
</style>
