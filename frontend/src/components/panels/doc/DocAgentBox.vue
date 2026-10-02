<script setup lang="ts">
// 点 AI 队友开出的输入框。选中文字时（`scope: 'selection'`）点一个常用的说法，它直接
// 改；自己写一句，是问它。不能直接改这一段时（只读、选区改不了）没有常用的说法，只能
// 问。对整篇（`scope: 'document'`）时常用的说法和自己写的一句一样，都是问它。
import { onMounted, ref } from 'vue'

import CheeseAvatar from '../../CheeseAvatar.vue'

import DocEditButton from './DocEditButton.vue'

import { t } from '@/i18n'

const props = defineProps<{ agentName: string; scope: 'selection' | 'document'; editable?: boolean }>()
const emit = defineEmits<{
  (e: 'edit', instruction: string): void
  (e: 'ask', question: string): void
  (e: 'cancel'): void
}>()

const text = ref('')
const input = ref<HTMLTextAreaElement | null>(null)
const EDIT_PRESETS = ['concise', 'specific', 'formal', 'list'] as const
const ASK_PRESETS = ['conflicts', 'memory', 'structure'] as const
const label = () =>
  t(props.scope === 'document' ? 'work.room.docEdit.docBoxLabel' : 'work.room.docEdit.boxLabel', {
    agent: props.agentName,
  })

onMounted(() => input.value?.focus({ preventScroll: true }))

function send() {
  if (text.value.trim()) emit('ask', text.value.trim())
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
  <div class="doc-rewrite-box" role="dialog" :aria-label="label()">
    <label class="doc-rewrite-box__label" :for="`doc-agent-input-${scope}`">
      <CheeseAvatar :size="18" :name="props.agentName" />
      {{ label() }}
    </label>
    <textarea
      :id="`doc-agent-input-${scope}`"
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
      <template v-if="scope === 'selection'">
        <button
          v-for="preset in props.editable ? EDIT_PRESETS : []"
          :key="preset"
          type="button"
          class="doc-rewrite-box__chip"
          @click="emit('edit', t(`work.room.docEdit.preset.${preset}`))"
        >
          {{ t(`work.room.docEdit.preset.${preset}`) }}
        </button>
      </template>
      <template v-else>
        <button
          v-for="preset in ASK_PRESETS"
          :key="preset"
          type="button"
          class="doc-rewrite-box__chip"
          @click="emit('ask', t(`work.room.docEdit.docPreset.${preset}`))"
        >
          {{ t(`work.room.docEdit.docPreset.${preset}`) }}
        </button>
      </template>
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
  display: flex;
  align-items: center;
  gap: 8px;
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
