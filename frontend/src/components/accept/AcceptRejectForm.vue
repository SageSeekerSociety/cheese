<script setup lang="ts">
// 退回：点了横条上的「退回」，输入框的位置换成这一块。
//
// 退回就是给芝士写一段话，所以它放在平时写字的地方，芝士的回复也会出现在正上方。审阅
// 的人不是负责人时，任务里平时没有输入框（#2422：只有负责人和芝士对话）；这一块只在
// 退回时出现，送出的内容走退回，不是一条普通消息。理由可以不写。
import { nextTick, onMounted, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{ busy: boolean }>()

const note = defineModel<string>('note', { required: true })

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'confirm'): void
}>()

const field = ref<{ focus: () => void } | null>(null)
onMounted(() => void nextTick(() => field.value?.focus()))

// Enter 换行，⌘/Ctrl+Enter 送出：和输入框的习惯一致，理由常常不止一行。
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault()
    emit('confirm')
  } else if (e.key === 'Escape') {
    e.preventDefault()
    emit('cancel')
  }
}
</script>

<template>
  <div class="reject-form">
    <div class="t-title reject-form__title">{{ t('work.room.accept.sendBack') }}</div>
    <v-textarea
      ref="field"
      v-model="note"
      autocomplete="off"
      variant="outlined"
      density="compact"
      rows="2"
      auto-grow
      max-rows="8"
      hide-details
      :placeholder="t('work.room.accept.sendBackReason')"
      :aria-label="t('work.room.accept.sendBackReason')"
      @keydown="onKeydown"
    />
    <div class="reject-form__actions">
      <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('cancel')">
        {{ t('work.room.accept.cancel') }}
      </BaseButton>
      <BaseButton kind="primary" size="sm" :loading="busy" :disabled="busy" @click="emit('confirm')">
        {{ t('work.room.accept.sendBack') }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped>
.reject-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
}
.reject-form__title {
  margin: 0;
}
.reject-form__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
