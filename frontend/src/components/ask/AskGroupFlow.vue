<script setup lang="ts">
import type { AskReceipt } from '../../lib/askGroup'
import type { AskGroupAction, AskGroupState } from '../../lib/askGroupState'

import { computed, ref, watch } from 'vue'

import { t } from '../../i18n'
import { canAnswer, validAskDraft } from '../../lib/askState'

import AskQuestionForm from './AskQuestionForm.vue'

const props = defineProps<{
  state: AskGroupState
  viewer: string
  names: Record<string, string>
  focusBlock?: string | null
}>()
const emit = defineEmits<{ (e: 'action', action: AskGroupAction): void }>()
const cursor = ref(0)
watch(
  () => props.state.scope,
  () => {
    cursor.value = 0
  }
)
watch(
  () => [props.focusBlock, props.state.data] as const,
  () => {
    const index = props.state.scope.members.indexOf(props.focusBlock ?? '')
    if (index >= 0) cursor.value = index
  },
  { immediate: true }
)
const blocks = computed(() => props.state.data?.blocks ?? [])
const block = computed(() => blocks.value[cursor.value])
const answered = computed(() => blocks.value.filter((b) => b.meta?.answer_log?.length).length)
const draftCount = computed(
  () =>
    blocks.value.filter((b) => props.state.forms[b.id]?.editing && validAskDraft(b, props.state.forms[b.id]!.draft))
      .length
)
const laterCount = computed(() => blocks.value.filter((b) => props.state.forms[b.id]?.draft.later).length)
const unansweredCount = computed(
  () =>
    blocks.value.filter(
      (b) =>
        !props.state.forms[b.id]?.draft.later &&
        !b.meta?.answer_log?.length &&
        !(props.state.forms[b.id]?.editing && validAskDraft(b, props.state.forms[b.id]!.draft))
    ).length
)
const allowed = computed(() => blocks.value.some((b) => canAnswer(b, props.viewer)))
const receipt = computed(() => props.state.data?.receipt)
const receiptText = computed(() => receiptLabel(receipt.value))
function receiptLabel(r: AskReceipt | null | undefined) {
  if (!r || r.state === 'unavailable') return t('ask.flow.continuationUnknown')
  if (r.state === 'received' && r.completed_at) return t('ask.group.completed')
  if (r.state === 'received' && r.received_at) return t('ask.group.received')
  if (r.state === 'failed') return t('ask.group.deliveryFailed')
  if (r.state === 'uncertain') return t('ask.group.uncertain')
  return t('ask.group.awaitingReceipt')
}
</script>

<template>
  <section class="ask-group" :aria-label="t('ask.group.title')">
    <header class="ask-group__header">
      <strong>{{ t('ask.group.progress', { answered, total: state.scope.total }) }}</strong>
      <button type="button" :disabled="state.busy" @click="emit('action', { type: 'refresh' })">
        {{ t('ask.form.refresh') }}
      </button>
    </header>
    <p v-if="state.busy" role="status">{{ t('ask.form.submitting') }}</p>
    <p v-if="state.error" role="alert" class="ask-group__error">{{ state.error }}</p>
    <template v-if="state.data">
      <nav class="ask-group__tabs" :aria-label="t('ask.group.questions')">
        <button
          v-for="(item, index) in blocks"
          :key="item.id"
          type="button"
          :aria-current="cursor === index ? 'step' : undefined"
          @click="cursor = index"
        >
          {{ t('ask.group.question', { index: index + 1 }) }}
          <span v-if="item.meta?.answer_log?.length">{{ t('ask.group.answered') }}</span>
          <span v-else-if="state.forms[item.id]?.draft.later">{{ t('ask.group.later') }}</span>
        </button>
      </nav>
      <template v-if="block">
        <h3 class="ask-group__question">{{ block.content }}</h3>
        <AskQuestionForm
          :block="block"
          :viewer="viewer"
          :names="names"
          :state="state.forms[block.id]"
          grouped
          @action="emit('action', { type: 'question', blockId: block!.id, action: $event })"
        />
        <button
          v-if="canAnswer(block, viewer)"
          type="button"
          :disabled="state.busy || !!state.pending"
          @click="emit('action', { type: 'later', blockId: block!.id })"
        >
          {{ state.forms[block.id]?.draft.later ? t('ask.group.resume') : t('ask.group.defer') }}
        </button>
      </template>
      <p class="ask-group__summary">
        {{ t('ask.group.summary', { submitted: draftCount, later: laterCount, unanswered: unansweredCount }) }}
      </p>
      <template v-if="state.data.settlement">
        <p>
          {{
            t('ask.group.settled', {
              submitted: state.data.settlement.answered.length,
              later: state.data.settlement.later.length,
              unanswered: state.data.settlement.unanswered.length,
            })
          }}
        </p>
        <p role="status">{{ receiptText }}</p>
        <p v-if="receipt?.last_error" class="ask-group__error">{{ receipt.last_error }}</p>
      </template>
      <template
        v-if="
          state.confirmedOperation?.settlement &&
          state.confirmedOperation.settlement.client_op_id !== state.data.settlement?.client_op_id
        "
      >
        <p role="status">{{ t('ask.group.operationConfirmed', { version: state.confirmedOperation.settlement.v }) }}</p>
        <p>{{ receiptLabel(state.confirmedOperation.receipt) }}</p>
      </template>
      <p v-if="state.questionChanged" role="alert">{{ t('ask.flow.questionChanged') }}</p>
      <template v-if="state.rejectedOperation">
        <p role="status">{{ t('ask.group.rejected') }}</p>
        <button
          type="button"
          :disabled="state.busy || !state.fresh"
          @click="emit('action', { type: 'resolve-conflict' })"
        >
          {{ t('ask.form.revise') }}
        </button>
      </template>
      <p v-else-if="state.conflict" role="alert">{{ t('ask.group.conflict') }}</p>
      <p v-if="state.pending && !state.rejectedOperation && !state.questionChanged" role="status">
        {{ t('ask.flow.unconfirmed') }}
      </p>
      <div v-if="state.confirm" class="ask-group__confirm">
        <p>{{ t('ask.group.incomplete', { count: unansweredCount }) }}</p>
        <button type="button" @click="emit('action', { type: 'back' })">{{ t('ask.group.back') }}</button>
        <button type="button" class="ask-group__primary" @click="emit('action', { type: 'confirm' })">
          {{ t('ask.group.submitAnyway') }}
        </button>
      </div>
      <button
        v-else-if="allowed"
        type="button"
        class="ask-group__primary"
        :disabled="
          state.busy ||
          !state.fresh ||
          state.storageBlocked ||
          !!state.rejectedOperation ||
          state.questionChanged ||
          (state.conflict && !state.pending)
        "
        @click="emit('action', { type: 'submit' })"
      >
        {{ state.pending ? t('ask.form.retry') : t('ask.group.submit') }}
      </button>
    </template>
  </section>
</template>

<style scoped>
.ask-group {
  max-width: 680px;
  margin: 16px 0;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
}
.ask-group__header,
.ask-group__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.ask-group__header {
  justify-content: space-between;
}
.ask-group__tabs {
  margin: 16px 0;
}
.ask-group button {
  min-height: 36px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.ask-group button:hover {
  background: var(--fill);
}
.ask-group button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.ask-group button[aria-current] {
  color: var(--accent-ink);
  border-color: var(--accent);
}
.ask-group__tabs span {
  margin-left: 4px;
  font-size: 13px;
}
.ask-group__question {
  font-size: 16px;
  color: var(--ink);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.ask-group__error {
  color: var(--danger-ink);
  overflow-wrap: anywhere;
}
.ask-group__summary {
  margin: 16px 0;
  color: var(--muted);
}
.ask-group__primary {
  background: var(--accent);
  color: var(--ink);
}
.ask-group__confirm button {
  margin: 8px 8px 0 0;
}
.ask-group :focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
