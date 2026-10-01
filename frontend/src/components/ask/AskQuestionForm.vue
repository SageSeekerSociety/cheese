<script setup lang="ts">
import type { Block } from '../../cx_types'
import type { AskAction, AskFormState } from '../../lib/askPresentation'
import type { AskDraft } from '../../lib/askState'

import { computed } from 'vue'
import { t } from '../../i18n'
import { canAnswer, validAskDraft } from '../../lib/askState'
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
const locked = computed(() => !allowed.value || !props.state?.fresh || props.state.busy || !!props.state.pending || props.state.storageBlocked)
function change(patch: Partial<AskDraft>) {
  if (!props.state || locked.value) return
  emit('action', { type: 'draft', draft: { ...props.state.draft, ...patch, later: false } })
}
</script>

<template>
  <section class="ask-form" :aria-label="t('ask.form.title')">
    <template v-if="answers.length">
      <p class="ask-form__saved">{{ t('ask.flow.answerSaved') }}</p>
      <AskAnswerHistory :answers="answers.slice(-1)" :names="names" />
      <details v-if="answers.length > 1">
        <summary>{{ t('ask.history.previous', { count: answers.length - 1 }) }}</summary>
        <AskAnswerHistory :answers="answers.slice(0, -1)" :names="names" />
      </details>
      <p class="ask-form__hint">{{ t('ask.flow.continuationUnknown') }}</p>
      <button v-if="allowed && !editing" type="button" @click="emit('action', { type: 'correct' })">{{ t('ask.form.correct') }}</button>
    </template>
    <p v-if="!allowed" class="ask-form__hint">{{ answers.length ? t('ask.form.originalOnly') : t('ask.form.waitingFor', { name: names[block.meta?.asked ?? ''] ?? block.meta?.asked ?? t('ask.form.unknownAnswerer') }) }}</p>
    <form v-if="state && editing && allowed" @submit.prevent="emit('action', { type: 'submit' })">
      <fieldset :disabled="locked">
        <legend class="ask-form__legend">{{ answers.length ? t('ask.form.correction') : t('ask.form.choose') }}</legend>
        <label v-for="(option, index) in block.meta?.options" :key="option.text" class="ask-form__option" :class="{ 'ask-form__option--picked': state.draft.kind === 'option' && state.draft.option === option.text }">
          <input type="radio" :name="`ask-${block.id}`" :value="option.text" :checked="state.draft.kind === 'option' && state.draft.option === option.text" @change="change({ kind: 'option', option: option.text })" />
          <span class="ask-form__number" aria-hidden="true">{{ index + 1 }}</span>
          <span><span class="ask-form__label">{{ option.text }}</span><span v-if="option.explain" class="ask-form__explain">{{ option.explain }}</span></span>
        </label>
        <label v-if="block.meta?.allow_other" class="ask-form__option">
          <input type="radio" :name="`ask-${block.id}`" :checked="state.draft.kind === 'note'" @change="change({ kind: 'note', option: '' })" />
          <span>{{ t('ask.form.other') }}</span>
        </label>
        <label v-if="block.meta?.reject_option" class="ask-form__option">
          <input type="radio" :name="`ask-${block.id}`" :checked="state.draft.kind === 'reject'" @change="change({ kind: 'reject', option: '' })" />
          <span>{{ t('ask.form.reject') }}</span>
        </label>
        <label class="ask-form__note" :for="`ask-note-${block.id}`">{{ state.draft.kind === 'note' ? t('ask.form.answer') : t('ask.form.note') }}</label>
        <textarea :id="`ask-note-${block.id}`" :value="state.draft.note" maxlength="2000" rows="3" @input="change({ note: ($event.target as HTMLTextAreaElement).value })" />
      </fieldset>
      <p v-if="state.saved && !state.pending" class="ask-form__hint" role="status">{{ t('ask.flow.draftSaved') }}</p>
      <p v-if="state.pending" class="ask-form__hint" role="status">{{ t('ask.flow.unconfirmed') }}</p>
      <p v-if="state.error" class="ask-form__error" role="alert">{{ state.error }}</p>
      <div class="ask-form__actions">
        <button v-if="!grouped && !block.meta?.ask_group" type="submit" class="ask-form__primary" :disabled="state.busy || !state.fresh || state.conflict || state.storageBlocked || (!state.pending && !validAskDraft(block, state.draft))">{{ state.busy ? t('ask.form.submitting') : state.pending ? t('ask.form.retry') : answers.length ? t('ask.form.submitCorrection') : t('ask.form.submit') }}</button>
        <button v-if="!state.fresh || state.pending || state.error" type="button" :disabled="state.busy" @click="emit('action', { type: 'refresh' })">{{ t('ask.form.refresh') }}</button>
        <button v-if="state.conflict && state.fresh" type="button" @click="emit('action', { type: 'resolve-conflict' })">{{ t('ask.form.revise') }}</button>
        <button v-if="answers.length && !state.pending" type="button" :disabled="state.busy" @click="emit('action', { type: 'cancel' })">{{ t('ask.form.cancel') }}</button>
      </div>
      <p v-if="block.meta?.ask_group && !grouped" class="ask-form__hint">{{ t('ask.flow.groupRequired') }}</p>
    </form>
  </section>
</template>

<style scoped>
.ask-form { margin-top: 16px; max-width: 640px; font-size: 14px; color: var(--text); }
.ask-form fieldset { border: 0; padding: 0; min-width: 0; }
.ask-form__legend { color: var(--muted); margin-bottom: 8px; font-size: 13px; }
.ask-form__option { display: flex; align-items: flex-start; gap: 8px; padding: 12px; margin-bottom: 8px; border: 1px solid var(--line); border-radius: var(--radius-md); cursor: pointer; }
.ask-form__option:hover { background: var(--fill); }
.ask-form__option--picked { background: var(--line-2); border-color: var(--muted); }
.ask-form__option input { margin-top: 4px; accent-color: var(--accent); }
.ask-form__number { color: var(--muted); }
.ask-form__label { display: block; color: var(--ink); }
.ask-form__explain { display: block; color: var(--muted); font-size: 13px; margin-top: 4px; }
.ask-form__note { display: block; margin: 16px 0 8px; }
.ask-form textarea { width: 100%; resize: vertical; padding: 8px 12px; border: 1px solid var(--line-2); border-radius: var(--radius-md); color: var(--text); background: var(--surface); font: inherit; }
.ask-form__actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.ask-form button { min-height: 36px; padding: 8px 12px; border: 1px solid var(--line); border-radius: var(--radius-md); }
.ask-form button:disabled { opacity: 0.5; cursor: not-allowed; }
.ask-form__primary { background: var(--accent); color: var(--ink); }
.ask-form__hint { color: var(--muted); font-size: 13px; margin: 8px 0; }
.ask-form__error { color: var(--danger-ink); margin: 8px 0; overflow-wrap: anywhere; }
.ask-form__saved { color: var(--ok-ink); margin-bottom: 8px; }
.ask-form summary { color: var(--muted); margin: 12px 0; cursor: pointer; }
.ask-form :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
</style>
