<script setup lang="ts">
import type { DocLinkTarget } from '../../../lib/docLinks'

import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { applyDocLink, safeDocHref } from '../../../lib/docLinks'

import { t } from '@/i18n'

const props = defineProps<{ target: DocLinkTarget; top: number; left: number }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const editing = ref(false)
const draft = ref('')
const error = ref('')
const input = ref<HTMLInputElement | null>(null)
watch(
  () => props.target,
  (target) => {
    draft.value = target.href
    editing.value = !target.mark
    error.value = ''
    if (editing.value) void nextTick(() => input.value?.focus({ preventScroll: true }))
  },
  { immediate: true }
)
function submit(remove = false) {
  const result = applyDocLink(props.target, remove ? null : draft.value)
  if (result === 'ok') emit('close')
  else error.value = t(`work.room.docLink.${result}`)
}
function edit() {
  editing.value = true
  void nextTick(() => input.value?.focus({ preventScroll: true }))
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  if (event.key === 'Escape') {
    event.stopPropagation()
    event.preventDefault()
    if (editing.value && props.target.mark) editing.value = false
    else emit('close')
  } else if (event.key === 'Enter' && editing.value) {
    event.preventDefault()
    submit()
  }
}
function docChanged() {
  error.value = t('work.room.docLink.stale')
}
watch(
  () => props.target.editor,
  (editor, old) => {
    old?.off('update', docChanged)
    editor.on('update', docChanged)
  },
  { immediate: true }
)
onBeforeUnmount(() => props.target.editor.off('update', docChanged))
</script>

<template>
  <div
    role="toolbar"
    :aria-label="t('work.room.docLink.title')"
    class="doc-link-callout"
    :style="{ top: `${top}px`, left: `${left}px` }"
    @keydown="keydown"
    @mousedown.stop
  >
    <button type="button" :aria-label="t('work.room.docLink.close')" @click="emit('close')">
      <v-icon size="16">mdi-close</v-icon>
    </button>
    <template v-if="editing">
      <input ref="input" v-model="draft" autocomplete="off" type="url" :aria-label="t('work.room.docLink.url')" />
      <button type="button" @mousedown.prevent @click="submit()">{{ t('work.room.docLink.save') }}</button>
      <button type="button" @click="target.mark ? (editing = false) : emit('close')">
        {{ t('work.room.docLink.cancel') }}
      </button>
    </template>
    <template v-else>
      <a v-if="safeDocHref(target.href)" :href="target.href" target="_blank" rel="noopener noreferrer">{{
        target.href
      }}</a>
      <span v-else>{{ target.href }}</span>
      <button type="button" @click="edit">{{ t('work.room.docLink.edit') }}</button>
      <button type="button" @mousedown.prevent @click="submit(true)">{{ t('work.room.docLink.remove') }}</button>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.doc-link-callout {
  position: absolute;
  z-index: 8;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  max-width: calc(100% - 16px);
  box-sizing: border-box;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--ink);
  box-shadow: var(--shadow-2);
  font-size: 13px;
}
button {
  min-height: 28px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  cursor: pointer;
  padding: 4px 8px;
}
button:hover {
  background: var(--fill);
}
a,
span {
  max-width: 176px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
input {
  width: 208px;
  min-width: 0;
  max-width: 100%;
  background: var(--surface);
  color: var(--ink);
  border: 1px solid var(--line);
  padding: 4px;
}
p {
  flex-basis: 100%;
  margin: 4px;
}
button:focus-visible,
a:focus-visible,
input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
