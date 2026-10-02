<script setup lang="ts">
// 「让{agent}改」的输入框：一句要求，或者点一个常用的说法直接发出。
import { onMounted, ref } from 'vue'

import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

const props = defineProps<{ agentName: string }>()
const emit = defineEmits<{
  (e: 'send', instruction: string): void
  (e: 'cancel'): void
}>()

const text = ref('')
const input = ref<HTMLTextAreaElement | null>(null)
const PRESETS = ['concise', 'specific', 'formal', 'list'] as const

onMounted(() => input.value?.focus({ preventScroll: true }))

function send(instruction = text.value) {
  if (instruction.trim()) emit('send', instruction.trim())
}
function onKey(e: KeyboardEvent) {
  if (e.isComposing) return
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('cancel')
  } else if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}
</script>

<template>
  <div class="doc-rewrite-box" role="dialog" :aria-label="t('work.room.docEdit.boxLabel', { agent: props.agentName })">
    <label class="doc-rewrite-box__label" for="doc-rewrite-input">
      {{ t('work.room.docEdit.boxLabel', { agent: props.agentName }) }}
    </label>
    <textarea
      id="doc-rewrite-input"
      ref="input"
      v-model="text"
      autocomplete="off"
      rows="2"
      class="doc-rewrite-box__input"
      :placeholder="t('work.room.docEdit.boxPlaceholder')"
      :title="t('work.room.docEdit.boxKeyHint')"
      @keydown="onKey"
    />
    <div class="doc-rewrite-box__row">
      <button
        v-for="preset in PRESETS"
        :key="preset"
        type="button"
        class="doc-rewrite-box__chip"
        @click="send(t(`work.room.docEdit.preset.${preset}`))"
      >
        {{ t(`work.room.docEdit.preset.${preset}`) }}
      </button>
      <span class="doc-rewrite-box__spacer" />
      <DocEditButton strong :disabled="!text.trim()" @click="send()">
        {{ t('work.room.docEdit.send') }}
      </DocEditButton>
    </div>
  </div>
</template>

<style scoped>
.doc-rewrite-box {
  box-sizing: border-box;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
}
.doc-rewrite-box__label {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.doc-rewrite-box__input {
  display: block;
  box-sizing: border-box;
  width: 100%;
  margin: 6px 0 10px;
  padding: 8px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  resize: none;
}
.doc-rewrite-box__input:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -1px;
}
.doc-rewrite-box__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.doc-rewrite-box__spacer {
  flex: 1 1 auto;
}
.doc-rewrite-box__chip {
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-rewrite-box__chip:hover {
  background: var(--fill);
}
.doc-rewrite-box__chip:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
</style>
