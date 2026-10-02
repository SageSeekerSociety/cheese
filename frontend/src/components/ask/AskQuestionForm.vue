<script setup lang="ts">
import type { Block } from '../../cx_types'
import type { AskAction, AskFormState } from '../../lib/askPresentation'
import type { AskDraft } from '../../lib/askState'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { t } from '../../i18n'
import { canAnswer, canRevisePending, validAskDraft } from '../../lib/askState'

import AskAnswerHistory from './AskAnswerHistory.vue'

const props = defineProps<{
  block: Block
  viewer: string
  names: Record<string, string>
  state?: AskFormState
  grouped?: boolean
}>()
const emit = defineEmits<{ (e: 'action', action: AskAction): void }>()
const answers = computed(() => props.block.meta?.answer_log ?? [])
const allowed = computed(() => canAnswer(props.block, props.viewer))
const editing = computed(() => !answers.value.length || props.state?.editing)
const locked = computed(
  () => !allowed.value || !props.state?.fresh || props.state.busy || !!props.state.pending || props.state.storageBlocked
)
const customReply = computed(() => props.grouped && !!props.block.meta?.allow_other)
const replyField = ref<HTMLTextAreaElement | null>(null)
const canSubmit = computed(
  () =>
    !!props.state &&
    allowed.value &&
    !props.state.busy &&
    props.state.fresh &&
    !props.state.conflict &&
    !props.state.storageBlocked &&
    (!!props.state.pending || validAskDraft(props.block, props.state.draft))
)
let resizeObserver: ResizeObserver | undefined
let replyWidth = 0
function resizeReply() {
  const field = replyField.value
  if (!props.grouped || !field) return
  field.style.height = 'auto'
  field.style.height = `${field.scrollHeight}px`
}
watch(
  () => [props.block.id, props.state?.draft.note, props.state?.draft.kind],
  () => void nextTick(resizeReply),
  { immediate: true }
)
watch(replyField, (field) => {
  resizeObserver?.disconnect()
  replyWidth = 0
  if (!field || typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(([entry]) => {
    if (!entry || replyWidth === entry.contentRect.width) return
    replyWidth = entry.contentRect.width
    resizeReply()
  })
  resizeObserver.observe(field)
  void nextTick(resizeReply)
})
onBeforeUnmount(() => resizeObserver?.disconnect())
const recommended = (text: string) => text.endsWith(' (Recommended)')
const optionLabel = (text: string) => (recommended(text) ? text.slice(0, -14) : text)
function change(patch: Partial<AskDraft>) {
  if (!props.state || locked.value) return
  emit('action', { type: 'draft', draft: { ...props.state.draft, ...patch, later: false } })
}
function reply(value: string) {
  change(customReply.value ? { kind: 'note', option: '', note: value } : { note: value })
}
function selectOption(text: string) {
  change({
    kind: 'option',
    option: text,
  })
}
function keydown(event: KeyboardEvent) {
  if (!props.grouped || locked.value || event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return
  if ((event.target as HTMLElement).closest('textarea, input:not([type="radio"]), [contenteditable="true"]')) return
  const index = Number(event.key) - 1
  const option = /^[1-9]$/.test(event.key) ? props.block.meta?.options?.[index] : undefined
  if (!option) return
  event.preventDefault()
  selectOption(option.text)
}
function submit() {
  if (!props.grouped && !props.block.meta?.ask_group && canSubmit.value) emit('action', { type: 'submit' })
}
</script>

<template>
  <section
    class="ask-form"
    :class="{ 'ask-form-grouped': grouped }"
    :aria-label="t('ask.form.title')"
    @keydown="keydown"
  >
    <template v-if="answers.length">
      <p class="ask-form-saved">{{ t('ask.flow.answerSaved') }}</p>
      <AskAnswerHistory :answers="answers.slice(-1)" :names="names" />
      <details v-if="answers.length > 1">
        <summary>{{ t('ask.history.previous', { count: answers.length - 1 }) }}</summary>
        <AskAnswerHistory :answers="answers.slice(0, -1)" :names="names" />
      </details>
      <p v-if="!grouped" class="ask-form-hint">{{ t('ask.flow.continuationUnknown') }}</p>
      <button v-if="allowed && !editing" type="button" @click="emit('action', { type: 'correct' })">
        {{ t('ask.form.correct') }}
      </button>
    </template>
    <p v-if="!allowed" class="ask-form-hint">
      {{
        answers.length
          ? t('ask.form.originalOnly')
          : t('ask.form.waitingFor', {
              name: names[block.meta?.asked ?? ''] ?? block.meta?.asked ?? t('ask.form.unknownAnswerer'),
            })
      }}
    </p>
    <form v-if="state && editing && allowed" @submit.prevent="submit">
      <fieldset :disabled="locked">
        <legend class="ask-form-legend" :class="{ 'ask-form-sr-only': grouped }">
          {{ answers.length ? t('ask.form.correction') : t('ask.form.choose') }}
        </legend>
        <label
          v-for="(option, index) in block.meta?.options"
          :key="option.text"
          class="ask-form-option"
          :class="{ 'ask-form-option-picked': state.draft.kind === 'option' && state.draft.option === option.text }"
        >
          <input
            type="radio"
            :name="`ask-${block.id}`"
            :value="option.text"
            :checked="state.draft.kind === 'option' && state.draft.option === option.text"
            @change="selectOption(option.text)"
          />
          <span v-if="!grouped || (block.meta?.options?.length ?? 0) > 1" class="ask-form-number" aria-hidden="true">{{
            index + 1
          }}</span>
          <span class="ask-form-copy">
            <span class="ask-form-label-row">
              <span class="ask-form-label">{{ grouped ? optionLabel(option.text) : option.text }}</span>
              <span v-if="grouped && recommended(option.text)" class="ask-form-recommended">{{
                t('ask.form.recommended')
              }}</span>
            </span>
            <span v-if="option.explain" class="ask-form-explain">{{ option.explain }}</span>
          </span>
          <svg v-if="grouped" class="ask-form-arrow" viewBox="0 0 20 20" aria-hidden="true">
            <path d="M4 10h12m-5-5 5 5-5 5" />
          </svg>
        </label>
        <label v-if="block.meta?.allow_other && !grouped" class="ask-form-option">
          <input
            type="radio"
            :name="`ask-${block.id}`"
            :checked="state.draft.kind === 'note'"
            @change="change({ kind: 'note', option: '' })"
          />
          <span>{{ t('ask.form.other') }}</span>
        </label>
        <label v-if="block.meta?.reject_option" class="ask-form-option">
          <input
            type="radio"
            :name="`ask-${block.id}`"
            :checked="state.draft.kind === 'reject'"
            @change="change({ kind: 'reject', option: '' })"
          />
          <span>{{ t('ask.form.reject') }}</span>
        </label>
      </fieldset>
      <div :class="{ 'ask-form-reply-row': grouped }">
        <label class="ask-form-note" :class="{ 'ask-form-sr-only': customReply }" :for="`ask-note-${block.id}`">{{
          customReply || state.draft.kind === 'note' ? t('ask.form.answer') : t('ask.form.note')
        }}</label>
        <textarea
          :id="`ask-note-${block.id}`"
          ref="replyField"
          :disabled="locked"
          :value="customReply && state.draft.kind !== 'note' ? '' : state.draft.note"
          maxlength="2000"
          autocomplete="off"
          :rows="grouped ? 1 : 3"
          :placeholder="customReply ? t('ask.form.replyPlaceholder') : undefined"
          @input="reply(($event.target as HTMLTextAreaElement).value)"
        />
        <slot name="actions" />
      </div>
      <details
        v-if="customReply && ['option', 'reject'].includes(state.draft.kind ?? '')"
        class="ask-form-supplement"
        :open="!!state.draft.note"
      >
        <summary>{{ t('ask.form.note') }}</summary>
        <textarea
          :disabled="locked"
          :aria-label="t('ask.form.note')"
          :value="state.draft.note"
          maxlength="2000"
          rows="2"
          autocomplete="off"
          @input="change({ note: ($event.target as HTMLTextAreaElement).value })"
        />
      </details>
      <p v-if="state.saved && !state.pending" class="ask-form-hint" role="status">{{ t('ask.flow.draftSaved') }}</p>
      <p v-if="state.pending" class="ask-form-hint" role="status">{{ t('ask.flow.unconfirmed') }}</p>
      <p v-if="state.error" class="ask-form-error" role="alert">{{ state.error }}</p>
      <div v-if="!grouped || state.error || !state.fresh || state.pending || answers.length" class="ask-form-actions">
        <button v-if="!grouped && !block.meta?.ask_group" type="submit" class="ask-form-primary" :disabled="!canSubmit">
          {{
            state.busy
              ? t('ask.form.submitting')
              : state.pending
                ? t('ask.form.retry')
                : answers.length
                  ? t('ask.form.submitCorrection')
                  : t('ask.form.submit')
          }}
        </button>
        <button
          v-if="!state.fresh || state.pending || state.error"
          type="button"
          :disabled="state.busy"
          @click="emit('action', { type: 'refresh' })"
        >
          {{ t('ask.form.refresh') }}
        </button>
        <button
          v-if="state.conflict && canRevisePending(block, state.pending, state.fresh)"
          type="button"
          @click="emit('action', { type: 'resolve-conflict' })"
        >
          {{ t('ask.form.revise') }}
        </button>
        <button
          v-if="answers.length && !state.pending"
          type="button"
          :disabled="state.busy"
          @click="emit('action', { type: 'cancel' })"
        >
          {{ t('ask.form.cancel') }}
        </button>
      </div>
      <p v-if="block.meta?.ask_group && !grouped" class="ask-form-hint">{{ t('ask.flow.groupRequired') }}</p>
    </form>
    <slot v-else name="actions" />
  </section>
</template>

<style scoped>
.ask-form {
  max-width: 640px;
  margin-top: 16px;
  font-size: 14px;
  color: var(--text);
}

.ask-form fieldset {
  min-width: 0;
  padding: 0;
  border: 0;
}

.ask-form-legend {
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--muted);
}

.ask-form-option {
  display: flex;
  padding: 12px;
  margin-bottom: 8px;
  cursor: pointer;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  align-items: flex-start;
  gap: 8px;
}

.ask-form-option:hover {
  background: var(--fill);
}

.ask-form-option-picked {
  background: var(--line-2);
  border-color: var(--muted);
}

.ask-form-option input {
  margin-top: 4px;
  accent-color: var(--accent);
}

.ask-form-number {
  color: var(--muted);
}

.ask-form-label {
  display: block;
  color: var(--ink);
}

.ask-form-explain {
  display: block;
  margin-top: 4px;
  font-size: 13px;
  color: var(--muted);
}

.ask-form-note {
  display: block;
  margin: 16px 0 8px;
}

.ask-form textarea {
  width: 100%;
  padding: 8px 12px;
  font: inherit;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  resize: vertical;
}

.ask-form-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.ask-form button {
  min-height: 36px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.ask-form button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.ask-form-primary {
  color: var(--ink);
  background: var(--accent);
}

.ask-form-hint {
  margin: 8px 0;
  font-size: 13px;
  color: var(--muted);
}

.ask-form-error {
  margin: 8px 0;
  color: var(--danger-ink);
  overflow-wrap: anywhere;
}

.ask-form-saved {
  margin-bottom: 8px;
  color: var(--ok-ink);
}

.ask-form summary {
  margin: 12px 0;
  color: var(--muted);
  cursor: pointer;
}

.ask-form :focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.ask-form-grouped {
  max-width: none;
  margin-top: 12px;
  font-size: 13px;
  line-height: var(--lh-13);
}

.ask-form-grouped fieldset {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.ask-form-grouped .ask-form-option {
  position: relative;
  box-sizing: border-box;
  min-height: 32px;
  padding: 6px 8px;
  margin: 0;
  border-color: transparent;
  border-radius: var(--radius-lg);
}

.ask-form-grouped .ask-form-option:hover,
.ask-form-grouped .ask-form-option-picked,
.ask-form-grouped .ask-form-option:focus-within {
  background: var(--fill);
}

.ask-form-grouped .ask-form-option input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.ask-form-grouped .ask-form-option:has(input:focus-visible) {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.ask-form-copy {
  min-width: 0;
  flex: 1;
}

.ask-form-label-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.ask-form-grouped .ask-form-label {
  min-width: 0;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.ask-form-recommended {
  padding: 2px 6px;
  font-size: 12px;
  font-weight: 430;
  line-height: calc(var(--lh-12) * 2 / 3);
  color: var(--muted);
  background: var(--fill-2);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.ask-form-grouped .ask-form-number {
  display: flex;
  box-sizing: border-box;
  width: 20px;
  height: 20px;
  font-size: 12px;
  font-weight: 500;
  line-height: calc(var(--lh-12) * 2 / 3);
  background: var(--fill);
  border: 1px solid var(--line-2);

  /* Codex Desktop uses a 4px number marker, approved as part of matching its panel. */
  /* stylelint-disable-next-line declaration-property-value-allowed-list */
  border-radius: calc(var(--radius-sm) * 2 / 3);
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
}

.ask-form-grouped .ask-form-explain {
  margin-top: 2px;
  line-height: var(--lh-13);
}

.ask-form-grouped textarea {
  box-sizing: border-box;
  max-height: calc(var(--lh-13) * 8 + 12px);
  min-height: 32px;
  padding: 6px 8px;
  line-height: var(--lh-13);
  border-radius: var(--radius-lg);
  border-color: transparent;
  resize: none;
}

.ask-form-reply-row {
  display: flex;
  padding: 6px 8px;
  margin-top: 4px;
  border-radius: var(--radius-lg);
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.ask-form-reply-row:focus-within {
  outline: 1px solid var(--focus-ring);
  outline-offset: -1px;
}

.ask-form-reply-row textarea {
  width: auto;
  min-width: 120px;
  min-height: 20px;
  padding: 0;
  line-height: var(--lh-14);
  border: 0;
  flex: 1;
}

/* The user requested Codex Desktop panel geometry; keep these radii local. */
@supports (corner-shape: superellipse(1.5)) {
  .ask-form-grouped .ask-form-option,
  .ask-form-grouped textarea,
  .ask-form-reply-row {
    /* stylelint-disable-next-line declaration-property-value-allowed-list */
    border-radius: calc(var(--radius-lg) * 5 / 4);

    /* Progressive enhancement supported by current Chromium, ahead of this lint schema. */
    /* stylelint-disable-next-line property-no-unknown */
    corner-shape: superellipse(1.5);
  }

  .ask-form-grouped .ask-form-number {
    /* stylelint-disable-next-line declaration-property-value-allowed-list */
    border-radius: calc(var(--radius-sm) * 5 / 6);

    /* stylelint-disable-next-line property-no-unknown */
    corner-shape: round;
  }

  .ask-form-recommended {
    /* stylelint-disable-next-line declaration-property-value-allowed-list */
    border-radius: calc(var(--radius-sm) * 5 / 4);

    /* stylelint-disable-next-line property-no-unknown */
    corner-shape: round;
  }
}

.ask-form-grouped textarea::placeholder {
  color: var(--muted);
}

.ask-form-grouped textarea:focus {
  outline: 1px solid var(--focus-ring);
  outline-offset: -1px;
}

.ask-form-reply-row textarea:focus {
  outline: none;
}

.ask-form-grouped .ask-form-hint,
.ask-form-supplement {
  padding: 0 8px;
}

.ask-form-supplement summary {
  margin: 4px 0;
  font-size: 13px;
  line-height: var(--lh-13);
}

.ask-form-sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.ask-form-arrow {
  flex-shrink: 0;
  align-self: center;
  width: 16px;
  height: 16px;
  fill: none;
  stroke: var(--muted);
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0;
}

.ask-form-option-picked .ask-form-arrow,
.ask-form-option:hover .ask-form-arrow,
.ask-form-option:focus-within .ask-form-arrow {
  opacity: 1;
}
</style>
