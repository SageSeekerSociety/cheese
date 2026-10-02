<script setup lang="ts">
import type { DocAiCard, DocAiDisplayContext } from '../../../lib/docAiTypes'

import { computed, ref, useId, watch } from 'vue'

import DocAiContextPreview from './DocAiContextPreview.vue'
import DocAiReviewDialog from './DocAiReviewDialog.vue'

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
  docked?: boolean
  opened?: boolean
  preparedContext?: DocAiDisplayContext
}>()
const emit = defineEmits<{
  (e: 'update:question', value: string): void
  (e: 'submit', kind: 'ask' | 'propose'): void
  (e: 'accept', id: string): void
  (e: 'cancel', id: string): void
  (e: 'recover'): void
  (e: 'close'): void
}>()
const questionId = useId()
const reviewDialogId = useId()
const activeId = ref<string | null>(null)
const review = ref<{ request: string; proposal: string; revision: number } | null>(null)
let reviewOpener: HTMLElement | null = null
let pendingFocus: { request: string; element: HTMLElement } | null = null
const reviewCard = computed(() => {
  const target = review.value
  if (!target) return null
  return (
    props.cards.find(
      (card) =>
        card.request.request_id === target.request &&
        card.proposal?.proposal_id === target.proposal &&
        card.proposal.revision === target.revision
    ) ?? null
  )
})
watch(
  () => props.cards,
  (cards) => {
    if (!cards.some((card) => card.request.request_id === activeId.value))
      activeId.value = cards[0]?.request.request_id ?? null
    if (review.value && !reviewCard.value) retireReview()
  },
  { immediate: true }
)
watch(
  () => props.opened,
  (value) => {
    if (value === false) retireReview()
  }
)
function retireReview() {
  reviewOpener = null
  pendingFocus = null
  review.value = null
}
function reviewChanges(card: DocAiCard, event: MouseEvent) {
  if (card.proposal) {
    pendingFocus = null
    reviewOpener = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
    review.value = {
      request: card.request.request_id,
      proposal: card.proposal.proposal_id,
      revision: card.proposal.revision,
    }
  }
}
function closeReview() {
  pendingFocus = review.value && reviewOpener ? { request: review.value.request, element: reviewOpener } : null
  reviewOpener = null
  review.value = null
}
function restoreReviewFocus() {
  const target = pendingFocus
  pendingFocus = null
  if (
    !target ||
    review.value ||
    props.opened === false ||
    !target.element.isConnected ||
    !props.cards.some((card) => card.request.request_id === target.request)
  )
    return
  const active = document.activeElement
  if (
    active instanceof HTMLElement &&
    active !== document.body &&
    active !== target.element &&
    !document.getElementById(reviewDialogId)?.contains(active)
  )
    return
  target.element.focus({ preventScroll: true })
}
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
    <header class="doc-ai-panel__head">
      <h2 v-if="!docked">{{ t('work.room.docAi.title') }}</h2>
      <button
        type="button"
        :aria-label="t('work.room.docAi.refresh')"
        :title="t('work.room.docAi.refresh')"
        @click="emit('recover')"
      >
        <v-icon size="16">mdi-refresh</v-icon>
      </button>
      <button v-if="!docked" type="button" :aria-label="t('work.room.docAi.close')" @click="emit('close')">
        <v-icon size="16">mdi-close</v-icon>
      </button>
    </header>
    <div class="doc-ai-results">
      <article
        v-for="card in cards"
        :key="card.request.request_id"
        class="doc-ai-card"
        :class="{ 'is-active': activeId === card.request.request_id }"
      >
        <button
          type="button"
          class="doc-ai-card__summary"
          :aria-expanded="activeId === card.request.request_id"
          :aria-controls="`${questionId}-${card.request.request_id}`"
          @click="activeId = activeId === card.request.request_id ? null : card.request.request_id"
        >
          <v-icon size="16">{{
            card.request.kind === 'propose' ? 'mdi-text-box-edit-outline' : 'mdi-message-text-outline'
          }}</v-icon>
          <span class="doc-ai-card__question" dir="auto">{{
            card.context?.question || t('work.room.docAi.aiProposal')
          }}</span>
          <span class="doc-ai-card__state">{{ t(`work.room.docAi.state.${card.request.state}`) }}</span>
        </button>
        <div
          v-show="activeId === card.request.request_id"
          :id="`${questionId}-${card.request.request_id}`"
          class="doc-ai-card__content"
        >
          <DocAiContextPreview :context="card.context" hide-question collapsed />
          <div v-if="card.proposal" class="doc-ai-card__actions">
            <button type="button" class="doc-ai-review-action" @click="reviewChanges(card, $event)">
              {{ t('work.room.docAi.review') }}
            </button>
            <span v-if="card.proposal.state === 'pending'">{{
              t('work.room.docAi.baseVersion', { version: card.proposal.base_version })
            }}</span>
            <span v-else>{{
              t('work.room.docAi.accepted', {
                actor: card.proposal.accepted_by ?? '',
                version: card.proposal.accepted_version ?? '',
              })
            }}</span>
          </div>
          <p v-if="card.request.answer" dir="auto" class="doc-ai-answer">{{ card.request.answer }}</p>
          <p v-if="card.request.error" role="alert">{{ card.request.error }}</p>
          <template v-if="card.proposal">
            <details class="doc-ai-card__replacement">
              <summary>{{ t('work.room.docAi.replacement') }}</summary>
              <pre dir="auto">{{ card.proposal.replacement }}</pre>
            </details>
          </template>
          <button
            v-if="card.request.state === 'pending' || card.request.state === 'running'"
            type="button"
            :disabled="busy"
            @click="emit('cancel', card.request.request_id)"
          >
            {{ t('work.room.docAi.cancel') }}
          </button>
        </div>
      </article>
    </div>
    <div class="doc-ai-controls">
      <div class="doc-ai-notices">
        <p v-if="error" role="alert">{{ error }}</p>
        <p v-if="selectionStatus">{{ selectionStatus }}</p>
        <p v-if="blocked">{{ t('work.room.docAi.unverified') }}</p>
        <div v-if="unknown && !busy" role="status">
          <p>{{ t('work.room.docAi.unknown') }}</p>
          <button type="button" :disabled="busy" @click="emit('recover')">{{ t('work.room.docAi.recover') }}</button>
        </div>
      </div>
      <DocAiContextPreview v-if="preparedContext" :context="preparedContext" compact hide-question />
      <div class="doc-ai-composer">
        <textarea
          :id="questionId"
          :aria-label="t('work.room.docAi.question')"
          :placeholder="t('work.room.docAi.question')"
          autocomplete="off"
          :value="question"
          rows="2"
          maxlength="16000"
          @input="emit('update:question', ($event.target as HTMLTextAreaElement).value)"
          @keydown="keydown"
        />
        <div class="doc-ai-actions">
          <button
            type="button"
            class="doc-ai-ask"
            :disabled="busy || unknown || blocked || !question.trim()"
            @click="emit('submit', 'ask')"
          >
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
      </div>
    </div>
    <DocAiReviewDialog
      :id="reviewDialogId"
      :card="reviewCard"
      :busy="busy"
      :unknown="unknown"
      :blocked="blocked"
      :version="version"
      @close="closeReview"
      @after-leave="restoreReviewFocus"
      @accept="emit('accept', $event)"
    />
  </section>
</template>

<style scoped>
.doc-ai-panel {
  min-width: 0;
  min-height: 0;
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface);
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-ai-panel__head {
  flex: 0 0 auto;
  justify-content: flex-end;
  padding: 8px 12px 0;
}
.doc-ai-controls {
  flex: 0 0 auto;
  padding: 12px;
  border-top: 1px solid var(--line);
}
.doc-ai-notices {
  max-height: 96px;
  overflow-y: auto;
  overflow-wrap: anywhere;
}
.doc-ai-results {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 8px;
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
  background: transparent;
  color: var(--ink);
  border-radius: var(--radius-sm);
  padding: 8px;
  cursor: pointer;
}
button:hover:not(:disabled) {
  background: var(--fill);
}
button:disabled {
  opacity: 0.6;
}
.doc-ai-composer {
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.doc-ai-composer:focus-within {
  border-color: var(--muted);
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
  border: 0;
  background: transparent;
  color: var(--ink);
  padding: 12px;
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  max-height: 96px;
  resize: vertical;
  overflow-y: auto;
}
.doc-ai-actions {
  padding: 0 8px 8px;
  gap: 4px;
}
.doc-ai-actions button {
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-ai-actions .doc-ai-ask {
  color: var(--accent-ink);
  background: var(--accent-wash);
}
.doc-ai-actions .doc-ai-ask:hover:not(:disabled) {
  background: var(--fill-2);
}
.doc-ai-card {
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  margin-bottom: 8px;
}
.doc-ai-card.is-active {
  border-color: var(--line);
}
.doc-ai-card__summary {
  display: flex;
  align-items: flex-start;
  width: 100%;
  text-align: start;
  gap: 8px;
}
.doc-ai-card__question {
  min-width: 0;
  flex: 1;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  font-size: 14px;
  line-height: var(--lh-14);
}
.doc-ai-card__state {
  flex: 0 0 auto;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
.doc-ai-card__content {
  padding: 4px 12px 12px;
}
.doc-ai-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  color: var(--muted);
}
.doc-ai-card__actions .doc-ai-review-action {
  border: 1px solid var(--line);
}
.doc-ai-card__replacement {
  margin-block: 12px;
}
.doc-ai-card__replacement summary {
  cursor: pointer;
  color: var(--muted);
}
.doc-ai-answer,
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 160px;
  overflow: auto;
}
pre {
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-ai-answer {
  margin-block: 12px;
  font-size: 14px;
  line-height: var(--lh-14);
  max-height: none;
}
</style>
