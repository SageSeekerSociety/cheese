<script setup lang="ts">
// 写一条批注的那个框：插在差异里被选中的那几行下面。
//
// 「修改建议」把选中的那几行原样填进下面的等宽框里，直接改成想要的样子；送出后芝士
// 看到的是一段确切的写法，不用再猜。回复和编辑也用这一个框，回复没有修改建议。
//
// 送出等服务器答应：`send` 成了由外面把框收起，没成就留着框和字，原因写在框里。
import type { ReviewWriteResult } from '@/types/reviewComment'

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
    /** 送出：null 是成了，否则是没成的原因。 */
    send: (body: string, suggestion: string | null) => ReviewWriteResult
  }>(),
  { lineText: '', body: '', suggestion: null, allowSuggestion: true, busy: false }
)

const emit = defineEmits<{
  (e: 'cancel'): void
}>()

const text = ref(props.body)
const sending = ref(false)
const error = ref('')
const suggesting = ref(props.suggestion !== null)
const replacement = ref(props.suggestion ?? props.lineText)

const field = ref<{ focus: () => void } | null>(null)
onMounted(() => void nextTick(() => field.value?.focus()))

function toggleSuggestion() {
  suggesting.value = !suggesting.value
  if (suggesting.value && props.suggestion === null) replacement.value = props.lineText
}

async function submit() {
  if (sending.value || props.busy) return
  const suggestion = suggesting.value ? replacement.value : null
  if (!text.value.trim() && suggestion === null) return
  sending.value = true
  error.value = ''
  try {
    error.value = (await props.send(text.value, suggestion)) ?? ''
  } finally {
    sending.value = false
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault()
    void submit()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    if (!sending.value) emit('cancel')
  }
}
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- the send and cancel shortcuts bubble here from the field -->
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
    <p v-if="error" role="alert" class="t-meta c-danger comment-box__error">{{ error }}</p>
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
      <BaseButton kind="ghost" size="sm" :disabled="busy || sending" @click="emit('cancel')">
        {{ t('work.room.accept.cancel') }}
      </BaseButton>
      <BaseButton
        kind="secondary"
        size="sm"
        :loading="busy || sending"
        :disabled="busy || sending"
        @click="void submit()"
      >
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
.comment-box__error {
  margin: 0;
}
.comment-box__grow {
  flex: 1 1 auto;
}
.comment-box__toggle--on {
  background: var(--fill);
}
</style>
