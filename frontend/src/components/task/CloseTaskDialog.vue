<script setup lang="ts">
// 关闭一件任务前的那张确认框：可以留一句结论（写了是做完了，不写是不做了）。任务页、
// 任务信息、侧栏任务行的右键都开它，点了「关闭任务」把结论交出去，关不关由外面做。
import { ref, watch } from 'vue'

import ConfirmDialog from '@/components/base/ConfirmDialog.vue'
import { t } from '@/i18n'

const open = defineModel<boolean>({ default: false })
defineProps<{ loading?: boolean }>()
const emit = defineEmits<{ (e: 'confirm', conclusion: string): void }>()

const conclusion = ref('')
watch(open, (now) => {
  if (now) conclusion.value = ''
})
</script>

<template>
  <ConfirmDialog
    v-model="open"
    :title="t('work.task.closeTitle')"
    :confirm-label="t('work.task.close')"
    :loading="loading"
    @confirm="emit('confirm', conclusion)"
  >
    <p class="t-body mb-2">{{ t('work.task.closeBody') }}</p>
    <textarea
      v-model="conclusion"
      rows="3"
      autocomplete="off"
      class="close-task__input t-body"
      :aria-label="t('work.task.conclusion')"
      :placeholder="t('work.task.conclusion')"
    />
  </ConfirmDialog>
</template>

<style scoped>
.close-task__input {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--ink);
  resize: vertical;
}
</style>
