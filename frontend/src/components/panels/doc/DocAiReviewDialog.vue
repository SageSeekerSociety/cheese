<script setup lang="ts">
import type { DocAiCard } from '../../../lib/docAiTypes'

import { computed } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  card: DocAiCard | null
  busy: boolean
  unknown: boolean
  blocked: boolean
  version: number
}>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'after-leave'): void
  (e: 'accept', id: string): void
}>()
const canAccept = computed(
  () =>
    !!props.card?.proposal &&
    props.card.proposal.state === 'pending' &&
    props.card.request.state === 'succeeded' &&
    props.card.context?.state === 'verified' &&
    props.card.proposal.base_version === props.version &&
    !props.busy &&
    !props.unknown &&
    !props.blocked
)
function accept() {
  if (canAccept.value && props.card?.proposal) emit('accept', props.card.proposal.proposal_id)
}
</script>

<template>
  <v-dialog
    :model-value="!!card"
    :aria-label="t('work.room.docAi.review')"
    max-width="920"
    scrollable
    @update:model-value="!$event && emit('close')"
    @after-leave="emit('after-leave')"
  >
    <v-card v-if="card" class="doc-ai-review" rounded="lg">
      <header class="doc-ai-review__head">
        <div>
          <h2>{{ t('work.room.docAi.review') }}</h2>
          <p v-if="card.proposal" class="doc-ai-review__version">
            {{ t('work.room.docAi.baseVersion', { version: card.proposal.base_version }) }}
          </p>
        </div>
        <button
          type="button"
          class="doc-ai-review__close"
          :aria-label="t('work.room.docAi.closeReview')"
          @click="emit('close')"
        >
          <v-icon size="20">mdi-close</v-icon>
        </button>
      </header>
      <v-card-text class="doc-ai-review__body" tabindex="0" role="region" :aria-label="t('work.room.docAi.comparison')">
        <p v-if="card.context?.question" class="doc-ai-review__question" dir="auto">{{ card.context.question }}</p>
        <details v-if="card.request.answer" class="doc-ai-review__explanation">
          <summary>{{ t('work.room.docAi.explanation') }}</summary>
          <p class="doc-ai-review__answer" dir="auto">{{ card.request.answer }}</p>
        </details>
        <div class="doc-ai-review__comparison">
          <section :aria-label="t('work.room.docAi.original')">
            <h3>{{ t('work.room.docAi.original') }}</h3>
            <div v-if="card.context?.state === 'verified'" class="doc-ai-review__text" dir="auto">
              {{ card.context.original }}
            </div>
            <p v-else role="status">
              {{
                t(
                  card.context?.state === 'invalid'
                    ? 'work.room.docAi.contextInvalid'
                    : 'work.room.docAi.contextUnavailable'
                )
              }}
            </p>
          </section>
          <section :aria-label="t('work.room.docAi.suggested')">
            <h3>{{ t('work.room.docAi.suggested') }}</h3>
            <div class="doc-ai-review__text" dir="auto">{{ card.proposal?.replacement }}</div>
          </section>
        </div>
      </v-card-text>
      <footer class="doc-ai-review__foot">
        <p v-if="card.proposal?.state === 'accepted'" role="status">
          {{
            t('work.room.docAi.accepted', {
              actor: card.proposal.accepted_by ?? '',
              version: card.proposal.accepted_version ?? '',
            })
          }}
        </p>
        <p v-else-if="card.proposal && card.proposal.base_version !== version" role="status">
          {{ t('work.room.docAi.staleProposal') }}
        </p>
        <span v-else />
        <v-btn
          v-if="card.proposal?.state === 'pending'"
          color="primary"
          variant="flat"
          :disabled="!canAccept"
          :loading="busy"
          @click="accept"
          >{{ t('work.room.docAi.humanAccept') }}</v-btn
        >
      </footer>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.doc-ai-review {
  min-width: 0;
  color: var(--text);
}
.doc-ai-review__head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 24px 24px 16px;
}
h2 {
  color: var(--ink);
  font-size: 18px;
  line-height: var(--lh-18);
  font-weight: 600;
}
.doc-ai-review__version {
  margin-top: 4px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
.doc-ai-review__close {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  color: var(--muted);
}
.doc-ai-review__close:hover {
  background: var(--fill);
}
.doc-ai-review__close:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.doc-ai-review__body {
  padding: 0 24px 24px;
  min-height: 0;
  overflow-wrap: anywhere;
}
.doc-ai-review__body:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}
.doc-ai-review__question {
  color: var(--ink);
  font-weight: 600;
  margin-bottom: 8px;
}
.doc-ai-review__answer {
  color: var(--muted);
  margin-top: 12px;
  white-space: pre-wrap;
}
.doc-ai-review__explanation {
  margin-bottom: 16px;
}
.doc-ai-review__explanation summary {
  width: fit-content;
  padding: 8px 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
}
.doc-ai-review__explanation summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.doc-ai-review__comparison {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 24px;
}
.doc-ai-review__comparison section {
  min-width: 0;
}
h3 {
  border-bottom: 1px solid var(--line);
  margin-bottom: 12px;
  padding-bottom: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 500;
  color: var(--muted);
}
.doc-ai-review__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: var(--lh-14);
}
.doc-ai-review__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid var(--line);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
@media (width <= 600px) {
  .doc-ai-review__head {
    padding: 16px;
  }
  .doc-ai-review__body {
    padding: 0 16px 16px;
  }
  .doc-ai-review__comparison {
    grid-template-columns: minmax(0, 1fr);
    gap: 24px;
  }
  .doc-ai-review__foot {
    padding: 16px;
  }
}
</style>
