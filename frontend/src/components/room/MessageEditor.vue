<script setup lang="ts">
// 改一条自己发过的消息：正文原地换成输入框，下面「取消」「保存」。房间的对话和
// 卡下的对话都用它，所以两处的手势一样：打开时放进原文、光标落在末尾；Enter 保存、
// Shift+Enter 换行、Esc 放弃。输入法组字时的 Enter 是选字，不算。
import { computed, nextTick, onMounted, ref } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  /** 打开时框里放的字。 */
  text: string
  /** 保存请求还在路上。 */
  saving: boolean
}>()

const emit = defineEmits<{
  (e: 'save', text: string): void
  (e: 'cancel'): void
}>()

const draft = ref(props.text)
const input = ref<HTMLTextAreaElement | null>(null)
const MAX_ROWS = 12
const rows = computed(() => Math.min(MAX_ROWS, draft.value.split('\n').length))

onMounted(async () => {
  await nextTick()
  const box = input.value
  if (!box) return
  box.focus()
  box.setSelectionRange(box.value.length, box.value.length)
})

function onKey(e: KeyboardEvent) {
  if (e.isComposing) return
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('cancel')
  } else if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    emit('save', draft.value)
  }
}
</script>

<template>
  <div class="msg-edit">
    <textarea
      ref="input"
      v-model="draft"
      class="msg-edit__input"
      autocomplete="off"
      :rows="rows"
      :aria-label="t('work.room.message.edit')"
      @keydown="onKey"
    />
    <div class="msg-edit__actions">
      <button type="button" class="msg-edit__btn" @click="emit('cancel')">
        {{ t('work.room.message.cancel') }}
      </button>
      <button type="button" class="msg-edit__btn" :disabled="saving || !draft.trim()" @click="emit('save', draft)">
        {{ t('work.room.message.save') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.msg-edit {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 2px;
}
.msg-edit__input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14-loose);
  color: var(--text);
  resize: none;
  transition: border-color var(--dur-quick) var(--ease-standard);
}
/* 聚焦只把边提一档，和发消息的输入框一样：边本身就是焦点的指示。 */
.msg-edit__input:focus {
  outline: none;
  border-color: var(--faint);
}
.msg-edit__actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}
/* 和发送失败那一行同一种中性小按钮。 */
.msg-edit__btn {
  flex: none;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard);
}
.msg-edit__btn:hover:not(:disabled) {
  background: var(--fill);
  border-color: var(--faint);
}
.msg-edit__btn:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
