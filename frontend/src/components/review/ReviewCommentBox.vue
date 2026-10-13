<script setup lang="ts">
// 写一条批注的那个框：插在差异里被选中的那几行下面。
//
// 「修改建议」把选中的那几行原样填进下面的等宽框里，直接改成想要的样子；送出后芝士
// 看到的是一段确切的写法，不用再猜。回复和编辑也用这一个框，回复没有修改建议。
import { nextTick, onMounted, ref } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = withDefaults(
  defineProps<{
    /** 选中的那几行现在的样子：打开修改建议时的起点。 */
    lineText?: string
    body?: string
    suggestion?: string | null
    /** 回复不带修改建议。 */
    allowSuggestion?: boolean
    submitLabel: string
    busy?: boolean
  }>(),
  { lineText: '', body: '', suggestion: null, allowSuggestion: true, busy: false }
)

const emit = defineEmits<{
  (e: 'submit', body: string, suggestion: string | null): void
  (e: 'cancel'): void
}>()

const text = ref(props.body)
const suggesting = ref(props.suggestion !== null)
const replacement = ref(props.suggestion ?? props.lineText)

const field = ref<{ focus: () => void } | null>(null)
onMounted(() => void nextTick(() => field.value?.focus()))

function toggleSuggestion() {
  suggesting.value = !suggesting.value
  if (suggesting.value && props.suggestion === null) replacement.value = props.lineText
}

function submit() {
  const suggestion = suggesting.value ? replacement.value : null
  if (!text.value.trim() && suggestion === null) return
  emit('submit', text.value, suggestion)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault()
    submit()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    emit('cancel')
  }
}
</script>

<template>
  <div class="comment-box" @keydown="onKeydown">
    <v-textarea
      ref="field"
      v-model="text"
      autocomplete="off"
      variant="outlined"
      density="compact"
      rows="2"
      auto-grow
      max-rows="10"
      hide-details
      :placeholder="t('work.room.review.placeholder')"
      :aria-label="t('work.room.review.placeholder')"
    />
    <textarea
      v-if="suggesting"
      v-model="replacement"
      class="comment-box__suggestion"
      autocomplete="off"
      spellcheck="false"
      :rows="Math.min(12, Math.max(2, replacement.split('\n').length))"
      :aria-label="t('work.room.review.suggestion')"
    />
    <div class="comment-box__bar">
      <BaseButton
        v-if="allowSuggestion"
        kind="ghost"
        size="sm"
        prepend-icon="mdi-file-document-edit-outline"
        :aria-pressed="suggesting"
        :class="{ 'comment-box__toggle--on': suggesting }"
        @click="toggleSuggestion"
      >
        {{ t('work.room.review.suggestion') }}
      </BaseButton>
      <span class="comment-box__grow" />
      <BaseButton kind="ghost" size="sm" :disabled="busy" @click="emit('cancel')">
        {{ t('work.room.accept.cancel') }}
      </BaseButton>
      <BaseButton kind="secondary" size="sm" :loading="busy" :disabled="busy" @click="submit">
        {{ submitLabel }}
      </BaseButton>
    </div>
  </div>
</template>

<style scoped>
.comment-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 6px 12px 8px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  font-family: var(--font-sans);
  white-space: normal;
}
.comment-box__suggestion {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--fill);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  resize: vertical;
}
.comment-box__bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.comment-box__grow {
  flex: 1 1 auto;
}
.comment-box__toggle--on {
  background: var(--fill);
}
</style>
