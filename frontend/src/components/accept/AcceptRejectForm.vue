<script setup lang="ts">
// 退回：点了横条上的「退回」，输入框的位置换成这一块。
//
// 退回就是给芝士写一段话，所以它放在平时写字的地方，芝士的回复也会出现在正上方。审阅
// 的人不是负责人时，任务里平时没有输入框（#2422：只有负责人和芝士对话）；这一块只在
// 退回时出现，送出的内容走退回，不是一条普通消息。理由可以不写。
//
// 在「改动」里写的批注列在理由上面，默认一起送出；移掉的那条这一次不送，留在原处还是
// 未发送，下一次退回再带上。
import { computed, nextTick, onMounted, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

export interface RejectComment {
  id: string
  /** 文件和行号，比如 `repositories.py:208`。 */
  where: string
  text: string
  suggestion: boolean
  held: boolean
}

const props = withDefaults(defineProps<{ busy: boolean; comments?: RejectComment[] }>(), { comments: () => [] })

const note = defineModel<string>('note', { required: true })

const emit = defineEmits<{
  (e: 'cancel'): void
  (e: 'confirm'): void
  (e: 'hold', id: string, held: boolean): void
}>()

const sending = computed(() => props.comments.filter((c) => !c.held).length)

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
    <div class="reject-form__head">
      <span class="t-title reject-form__title">{{ t('work.room.accept.sendBack') }}</span>
      <span v-if="comments.length" class="t-meta">{{ t('work.room.review.sendingCount', { count: sending }) }}</span>
    </div>
    <ul v-if="comments.length" class="reject-form__list">
      <li v-for="c in comments" :key="c.id" class="reject-form__item" :class="{ 'reject-form__item--held': c.held }">
        <span class="reject-form__mark" :class="{ 'reject-form__mark--suggest': c.suggestion }" aria-hidden="true" />
        <div class="reject-form__body">
          <span class="reject-form__text">{{ c.text }}</span>
          <span class="t-meta">
            {{ c.where }} · {{ c.suggestion ? t('work.room.review.suggestion') : t('work.room.review.comment') }}
          </span>
        </div>
        <BaseButton
          kind="ghost"
          size="sm"
          :icon="c.held ? 'mdi-undo' : 'mdi-close'"
          :title="c.held ? t('work.room.review.sendAgain') : t('work.room.review.hold')"
          :aria-label="c.held ? t('work.room.review.sendAgain') : t('work.room.review.hold')"
          @click="emit('hold', c.id, !c.held)"
        />
      </li>
    </ul>
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
.reject-form__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.reject-form__title {
  margin: 0;
}
.reject-form__list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 240px;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
}
.reject-form__item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 4px 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.reject-form__item--held {
  opacity: 0.55;
}
.reject-form__mark {
  flex: none;
  width: 6px;
  height: 6px;
  margin-top: 7px;
  border-radius: 50%;
  background: var(--faint);
}
.reject-form__mark--suggest {
  background: var(--ink);
}
.reject-form__body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
}
.reject-form__text {
  overflow-wrap: anywhere;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
}
.reject-form__actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
