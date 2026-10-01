<script setup lang="ts">
import type { DocAiCard } from '../../../lib/docAiTypes'

import { t } from '@/i18n'

const props = defineProps<{
  cards: DocAiCard[]
  question: string
  busy: boolean
  error: string
  selectionStatus: string
  hasSelection: boolean
  blocked: boolean
  unknown: boolean
  version: number
}>()
const emit = defineEmits<{
  (e: 'update:question', value: string): void
  (e: 'submit', kind: 'ask' | 'propose'): void
  (e: 'accept', id: string): void
  (e: 'cancel', id: string): void
  (e: 'recover'): void
  (e: 'close'): void
}>()
function keydown(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault()
    if (!props.busy && !props.unknown && !props.blocked) emit('submit', 'ask')
  }
}
</script>

<template>
  <section class="doc-ai-panel" :aria-label="t('work.room.docAi.title')" :aria-busy="busy">
    <header>
      <h2>{{ t('work.room.docAi.title') }}</h2>
      <button type="button" @click="emit('recover')">{{ t('work.room.docAi.refresh') }}</button>
      <button type="button" :aria-label="t('work.room.docAi.close')" @click="emit('close')">
        <v-icon size="16">mdi-close</v-icon>
      </button>
    </header>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="selectionStatus">{{ selectionStatus }}</p>
    <p v-if="blocked">{{ t('work.room.docAi.unverified') }}</p>
    <div v-if="unknown && !busy" role="status">
      <p>{{ t('work.room.docAi.unknown') }}</p>
      <button type="button" :disabled="busy" @click="emit('recover')">{{ t('work.room.docAi.recover') }}</button>
    </div>
    <label for="doc-ai-question">{{ t('work.room.docAi.question') }}</label>
    <textarea
      id="doc-ai-question"
      autocomplete="off"
      :value="question"
      rows="3"
      maxlength="16000"
      @input="emit('update:question', ($event.target as HTMLTextAreaElement).value)"
      @keydown="keydown"
    />
    <div class="doc-ai-actions">
      <button type="button" :disabled="busy || unknown || blocked || !question.trim()" @click="emit('submit', 'ask')">
        {{ t('work.room.docAi.ask') }}
      </button>
      <button
        type="button"
        :disabled="busy || unknown || blocked || !hasSelection || !question.trim()"
        :title="!hasSelection ? t('work.room.docAi.unverified') : undefined"
        @click="emit('submit', 'propose')"
      >
        {{ t('work.room.docAi.propose') }}
      </button>
    </div>
    <article v-for="card in cards" :key="card.request.request_id" class="doc-ai-card">
      <header>
        <span>{{ t('work.room.docAi.aiProposal') }}</span>
        <span role="status">{{ t(`work.room.docAi.state.${card.request.state}`) }}</span>
      </header>
      <p v-if="card.request.answer" dir="auto" class="doc-ai-answer">{{ card.request.answer }}</p>
      <p v-if="card.request.error" role="alert">{{ card.request.error }}</p>
      <template v-if="card.proposal">
        <details open>
          <summary>{{ t('work.room.docAi.replacement') }}</summary>
          <pre dir="auto">{{ card.proposal.replacement }}</pre>
        </details>
        <p>{{ t('work.room.docAi.baseVersion', { version: card.proposal.base_version }) }}</p>
        <button
          v-if="card.proposal.state === 'pending'"
          type="button"
          :disabled="busy || unknown || blocked || card.proposal.base_version !== version"
          @click="emit('accept', card.proposal.proposal_id)"
        >
          {{ t('work.room.docAi.humanAccept') }}
        </button>
        <p v-else>
          {{
            t('work.room.docAi.accepted', {
              actor: card.proposal.accepted_by ?? '',
              version: card.proposal.accepted_version ?? '',
            })
          }}
        </p>
      </template>
      <button
        v-if="card.request.state === 'pending' || card.request.state === 'running'"
        type="button"
        :disabled="busy"
        @click="emit('cancel', card.request.request_id)"
      >
        {{ t('work.room.docAi.cancel') }}
      </button>
    </article>
  </section>
</template>

<style scoped>
.doc-ai-panel {
  min-width: 0;
  max-height: 50vh;
  overflow-y: auto;
  padding: 12px;
  border-block: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  font-size: 13px;
}
header,
.doc-ai-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
h2 {
  margin: 0;
  font-size: 14px;
  flex: 1;
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
textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--line);
  background: var(--surface);
  color: var(--ink);
  padding: 8px;
  margin-block: 4px 8px;
}
.doc-ai-card {
  border-block-start: 1px solid var(--line);
  margin-block-start: 12px;
  padding-block-start: 12px;
}
.doc-ai-answer,
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
pre {
  max-height: 240px;
  overflow: auto;
  font-size: 12px;
}
</style>
