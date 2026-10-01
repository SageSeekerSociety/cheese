<script setup lang="ts">
import type { DocThreadActions, DocThreadState } from '../../../lib/docThreadTypes'

import { onBeforeUnmount, ref, watch } from 'vue'

import { relTime } from '../../../lib/relTime'

import { t } from '@/i18n'

const props = defineProps<{
  id: string
  topic: string | null
  actor: string
  state: DocThreadState
  actions: DocThreadActions
}>()
const text = ref('')
const localError = ref('')
let generation = 0
let disposed = false
const key = () => `cheese.doc-thread.draft.v1:${props.actor}:${props.topic}:${props.id}`
watch(
  () => [props.id, props.topic, props.actor],
  () => {
    generation++
    localError.value = ''
    try {
      text.value = localStorage.getItem(key()) ?? ''
    } catch {
      text.value = ''
    }
    void props.actions.load(props.id)
  },
  { immediate: true, flush: 'sync' }
)
watch(
  text,
  (value) => {
    try {
      localStorage.setItem(key(), value)
    } catch {
      localError.value = t('work.room.docThread.storage')
    }
  },
  { flush: 'sync' }
)
async function act(action: 'reply' | 'resolve' | 'reopen' | 'recover') {
  const captured = generation
  const content = text.value
  localError.value = ''
  try {
    if (action === 'reply') await props.actions.reply(props.id, content)
    else await props.actions[action](props.id)
    if (disposed || captured !== generation) return
    if (action === 'reply' && text.value === content) text.value = ''
  } catch (cause) {
    if (!disposed && captured === generation) localError.value = cause instanceof Error ? cause.message : String(cause)
  }
}
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault()
    if (!props.state.busy && !props.state.unknown && text.value.trim()) void act('reply')
  }
}
onBeforeUnmount(() => {
  disposed = true
  generation++
})
</script>

<template>
  <section class="doc-thread" :aria-label="t('work.room.docThread.title')">
    <p v-if="localError || state.errors[id]" role="alert">{{ localError || state.errors[id] }}</p>
    <template v-if="state.threads[id]">
      <div class="doc-thread-actions">
        <span>{{
          t(state.threads[id].state === 'open' ? 'work.room.docThread.open' : 'work.room.docThread.resolved')
        }}</span>
        <button
          type="button"
          :disabled="state.busy || !!state.unknown"
          @click="act(state.threads[id].state === 'open' ? 'resolve' : 'reopen')"
        >
          {{ t(state.threads[id].state === 'open' ? 'work.room.docThread.resolve' : 'work.room.docThread.reopen') }}
        </button>
        <button type="button" :disabled="state.busy" @click="act('recover')">
          {{ t('work.room.docThread.refresh') }}
        </button>
      </div>
      <p v-if="state.threads[id].anchor" class="doc-thread-history">{{ t('work.room.docThread.historicalAnchor') }}</p>
      <article v-for="reply in state.threads[id].replies" :key="reply.sequence" class="doc-thread-reply">
        <header>{{ reply.comment.author }} · {{ relTime(reply.comment.created_at) }}</header>
        <p dir="auto">{{ reply.comment.content }}</p>
      </article>
      <template v-if="state.threads[id].state === 'open'">
        <textarea
          v-model="text"
          autocomplete="off"
          rows="2"
          :aria-label="t('work.room.docThread.reply')"
          :placeholder="t('work.room.docThread.placeholder')"
          @keydown="keydown"
        />
        <button
          type="button"
          :disabled="state.busy || !!state.unknown || !text.trim()"
          @mousedown.prevent
          @click="act('reply')"
        >
          {{ t('work.room.docThread.send') }}
        </button>
      </template>
      <p v-else>{{ t('work.room.docThread.reopenToReply') }}</p>
    </template>
    <button v-else type="button" :disabled="state.busy" @click="act('recover')">
      {{ t('work.room.docThread.load') }}
    </button>
    <div v-if="state.unknown === id" role="status">
      <p>{{ t('work.room.docThread.unknown') }}</p>
      <button type="button" :disabled="state.busy" @click="act('recover')">
        {{ t('work.room.docThread.recover') }}
      </button>
    </div>
  </section>
</template>

<style scoped>
.doc-thread {
  border-block-start: 1px solid var(--line);
  padding-block-start: 8px;
  margin-block-start: 8px;
  min-width: 0;
}
.doc-thread-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.doc-thread-history,
header {
  color: var(--muted);
  font-size: 12px;
}
.doc-thread-reply {
  border-inline-start: 2px solid var(--line);
  padding-inline-start: 8px;
  margin-block: 12px;
}
p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin-block: 4px;
}
textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  padding: 8px;
  margin-block: 8px;
}
button {
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  cursor: pointer;
}
button:disabled {
  color: var(--faint);
  cursor: default;
}
button:focus-visible,
textarea:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
