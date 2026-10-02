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
let restoring = false
const key = () => `cheese.doc-thread.draft.v1:${props.actor}:${props.topic}:${props.id}`
watch(
  () => [props.id, props.topic, props.actor],
  () => {
    generation++
    localError.value = ''
    restoring = true
    try {
      text.value = localStorage.getItem(key()) ?? ''
    } catch {
      text.value = ''
    }
    restoring = false
    void props.actions.load(props.id)
  },
  { immediate: true, flush: 'sync' }
)
watch(
  text,
  (value) => {
    if (restoring) return
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
    let submitted: string | undefined
    if (action === 'reply') {
      await props.actions.reply(props.id, content)
      submitted = content
    } else if (action === 'recover') {
      submitted = (await props.actions.recover(props.id))?.reply
    } else await props.actions[action](props.id)
    if (disposed || captured !== generation) return
    if (submitted !== undefined && text.value === submitted) text.value = ''
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
    <div class="doc-thread__history">
      <slot />
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
        <p v-if="state.threads[id].anchor" class="doc-thread-history">
          {{ t('work.room.docThread.historicalAnchor') }}
        </p>
        <article v-for="reply in state.threads[id].replies" :key="reply.sequence" class="doc-thread-reply">
          <header>{{ reply.comment.author }} · {{ relTime(reply.comment.created_at) }}</header>
          <p dir="auto">{{ reply.comment.content }}</p>
        </article>
      </template>
      <button v-else type="button" :disabled="state.busy" @click="act('recover')">
        {{ t('work.room.docThread.load') }}
      </button>
    </div>
    <template v-if="state.threads[id]">
      <template v-if="state.threads[id].state === 'open'">
        <div class="doc-thread-composer">
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
            class="doc-thread-send"
            :disabled="state.busy || !!state.unknown || !text.trim()"
            @mousedown.prevent
            @click="act('reply')"
          >
            {{ t('work.room.docThread.send') }}
          </button>
        </div>
      </template>
      <p v-else>{{ t('work.room.docThread.reopenToReply') }}</p>
    </template>
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
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
}
.doc-thread__history {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.doc-thread-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  border-block-start: 1px solid var(--line);
  padding-block-start: 8px;
}
.doc-thread-history,
header {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-thread-reply {
  margin-block: 16px;
  font-size: 14px;
  line-height: var(--lh-14);
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
  border: 0;
  background: transparent;
  color: var(--ink);
  padding: 12px;
  resize: vertical;
  min-height: 72px;
  max-height: 160px;
  font-size: 14px;
  line-height: var(--lh-14);
}
button {
  background: transparent;
  color: var(--muted);
  border-radius: var(--radius-sm);
  padding: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
button:hover:not(:disabled) {
  background: var(--fill);
}
.doc-thread-actions > span {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  margin-inline-end: auto;
}
.doc-thread-composer {
  flex: 0 0 auto;
  margin-top: 12px;
  padding-bottom: 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}
.doc-thread-composer:focus-within {
  border-color: var(--muted);
}
.doc-thread-send {
  display: block;
  margin-inline: auto 8px;
  color: var(--accent-ink);
  background: var(--accent-wash);
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
