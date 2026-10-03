<script setup lang="ts">
import type { AskReceipt } from '../../lib/askGroup'
import type { AskGroupAction, AskGroupState } from '../../lib/askGroupState'
import type { AskAction } from '../../lib/askPresentation'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { t } from '../../i18n'
import { canAnswer, emptyAskDraft, validAskDraft } from '../../lib/askState'

import AskQuestionForm from './AskQuestionForm.vue'

const props = defineProps<{
  state: AskGroupState
  viewer: string
  names: Record<string, string>
  focusBlock?: string | null
  autoFocus?: boolean
  // 接管输入框那一格时画成 composer 的样子（同一块控件），而不是对话里的卡片。
  composer?: boolean
}>()
const emit = defineEmits<{
  (e: 'action', action: AskGroupAction): void
  (e: 'dismiss'): void
}>()
const cursor = ref(0)
const navigationOpen = ref(false)
const card = ref<HTMLElement | null>(null)
const questionForm = ref<InstanceType<typeof AskQuestionForm> | null>(null)
// 点选选项之后停一小会儿再推进：这段时间里整行保持高亮、标记翻成实心，重复点选
// 一律作废（见 onPicked / onFormAction）。这就是 Codex 那 180ms 的用意——让人看清
// 自己刚点的是哪个。
const committing = ref(false)
let commitTimer: ReturnType<typeof setTimeout> | undefined
onBeforeUnmount(() => clearTimeout(commitTimer))
const autofocus = computed(() => props.autoFocus || props.composer)

watch(
  () => props.state.scope,
  () => {
    cursor.value = 0
    navigationOpen.value = false
    committing.value = false
  }
)
watch(
  () => [props.focusBlock, props.state.scope] as const,
  () => {
    const index = props.state.scope.members.indexOf(props.focusBlock ?? '')
    if (index >= 0) cursor.value = index
  },
  { immediate: true }
)
const blocks = computed(() => props.state.data?.blocks ?? [])
const block = computed(() => blocks.value[cursor.value])

// 单选默认落在第一个选项上：一进来就能直接 Enter 或点第一项，不用先手动选。
function defaultSelect() {
  const current = block.value
  if (!current || !current.meta?.options?.length || !canAnswer(current, props.viewer)) return
  const form = props.state.forms[current.id]
  if (
    !form ||
    form.draft.kind ||
    form.draft.note ||
    form.draft.later ||
    current.meta.answer_log?.length ||
    // 已经进入「确认提交」或「提交中」时不再自动落选，别去覆盖这一刻的状态。
    props.state.confirm ||
    props.state.pending
  )
    return
  const draft = { ...(form.draft ?? emptyAskDraft()), kind: 'option' as const, option: current.meta.options[0]!.text }
  emit('action', { type: 'question', blockId: current.id, action: { type: 'draft', draft } })
}
watch(
  () => block.value?.id,
  () => {
    committing.value = false
    clearTimeout(commitTimer)
    defaultSelect()
  },
  { immediate: true }
)

function focusCurrent() {
  const current = block.value
  if (!current) return
  if (!current.meta?.options?.length && current.meta?.allow_other && questionForm.value?.focusReply()) return
  card.value?.focus({ preventScroll: true })
}
watch(
  [() => autofocus.value, () => props.focusBlock, () => block.value?.id],
  () => {
    if (autofocus.value) void nextTick(focusCurrent)
  },
  { immediate: true }
)
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
function move(index: number) {
  cursor.value = Math.max(0, Math.min(blocks.value.length - 1, index))
  navigationOpen.value = false
  committing.value = false
  clearTimeout(commitTimer)
  void nextTick(focusCurrent)
}
// 非末题前进、末题提交。提交时整组一起发（见 useAskGroups 的 snapshot）。
function advance() {
  if (cursor.value < blocks.value.length - 1) move(cursor.value + 1)
  else emit('action', { type: 'submit' })
}
function onPicked() {
  if (committing.value || !currentReady.value) return
  committing.value = true
  clearTimeout(commitTimer)
  commitTimer = setTimeout(() => {
    committing.value = false
    advance()
  }, 180)
}
// 面板里所有动作都会经过这里：180ms 窗口内一律丢弃，所以再点别的选项不会改掉高亮。
function onFormAction(action: AskAction) {
  const current = block.value
  if (!current || committing.value) return
  emit('action', { type: 'question', blockId: current.id, action })
}
// 跳过：已经写了自由文本就当作提交继续；否则把这一题标为稍后再前进。末题不再前进，
// 留给下方的「提交」按钮决定。
function onSkip() {
  const current = block.value
  if (!current || committing.value) return
  if (current.meta?.allow_other && props.state.forms[current.id]?.draft.note.trim()) {
    advance()
    return
  }
  if (!props.state.forms[current.id]?.draft.later) emit('action', { type: 'later', blockId: current.id })
  if (cursor.value < blocks.value.length - 1) move(cursor.value + 1)
}
function keydown(event: KeyboardEvent) {
  if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return
  if (event.key === 'Escape') {
    // Esc 只是把面板收起来，问题不会消失：收起后「有 N 个问题待回答」那一条能叫回来。
    event.preventDefault()
    emit('dismiss')
    return
  }
  if (questionForm.value?.handleShortcut(event)) return
  const target = event.target as HTMLElement
  const typing = !!target.closest('textarea, input:not([type="radio"]), [contenteditable="true"]')
  if (
    event.key === 'Enter' &&
    !event.shiftKey &&
    !target.closest('input:not([type="radio"]), [contenteditable="true"]')
  ) {
    event.preventDefault()
    if (!committing.value && currentReady.value) advance()
    return
  }
  if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    event.preventDefault()
    questionForm.value?.moveOption(event.key === 'ArrowDown' ? 1 : -1)
    return
  }
  if (typing) return
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault()
    move(cursor.value + (event.key === 'ArrowLeft' ? -1 : 1))
  }
}
const currentReady = computed(() => !!block.value && stateFormReady(block.value.id))
function stateFormReady(id: string) {
  const form = props.state.forms[id]
  return (
    !!form && (!!block.value?.meta?.answer_log?.length || form.draft.later || validAskDraft(block.value!, form.draft))
  )
}
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
  <section
    ref="card"
    class="ask-group"
    :class="{ 'ask-group--composer': composer }"
    :aria-label="t('ask.group.title')"
    tabindex="0"
    @keydown="keydown"
  >
    <header class="ask-group-header">
      <span class="ask-group-title"
        ><svg
          aria-hidden="true"
          xmlns="http://www.w3.org/2000/svg"
          width="16"
          height="16"
          viewBox="0 0 16 16"
          fill="none"
        >
          <path
            d="M8.64198 10.689C8.64198 11.0151 8.37765 11.2794 8.05158 11.2794C7.72551 11.2794 7.46118 11.0151 7.46118 10.689C7.46118 10.363 7.72551 10.0986 8.05158 10.0986C8.37765 10.0986 8.64198 10.363 8.64198 10.689Z"
            fill="currentColor"
          />
          <path
            d="M13.4746 8C13.4746 5.18821 11.0524 2.8584 8 2.8584C4.94756 2.8584 2.52539 5.18821 2.52539 8C2.52548 9.1334 2.98018 9.88293 3.55176 11.0146C3.62017 11.1502 3.63938 11.3057 3.60645 11.4541L3.34277 12.6406L4.62598 12.3086L4.74023 12.2891C4.81669 12.2832 4.89333 12.2912 4.9668 12.3115L5.0752 12.3516L5.44238 12.5215C6.29248 12.8992 7.09158 13.1416 8 13.1416C11.0523 13.1416 13.4744 10.8116 13.4746 8ZM14.5254 8C14.5252 11.4473 11.5749 14.1914 8 14.1914C6.78477 14.1914 5.75932 13.829 4.75488 13.3594L2.9873 13.8184C2.5113 13.9417 2.07317 13.5182 2.17969 13.0381L2.5498 11.3633C2.03641 10.3597 1.4747 9.3817 1.47461 8C1.47461 4.55257 4.42502 1.80762 8 1.80762C11.575 1.80762 14.5254 4.55257 14.5254 8Z"
            fill="currentColor"
          />
          <path
            d="M7.40252 8.91806V8.54208C7.40252 8.41516 7.42153 8.28053 7.47869 8.14755C7.53676 8.01248 7.62182 7.90733 7.71502 7.82431C7.87599 7.68095 8.09566 7.57975 8.26384 7.50009C8.69088 7.29781 9.01087 7.02649 9.0158 6.58993C9.0096 6.27466 8.88987 6.0834 8.73455 5.96103C8.56492 5.82739 8.31348 5.74764 8.02654 5.75009C7.49521 5.75464 7.07313 6.02276 6.95427 6.36142C6.8584 6.63506 6.55897 6.77956 6.28533 6.68368C6.01184 6.58772 5.86819 6.2883 5.96404 6.01474C6.27928 5.11513 7.21706 4.70616 8.01775 4.69931C8.48983 4.69532 8.98861 4.82532 9.38396 5.13681C9.79368 5.45962 10.0534 5.94919 10.0656 6.56943V6.58017C10.0654 7.67549 9.21595 8.21154 8.71404 8.44931C8.61598 8.49576 8.54625 8.5286 8.49138 8.55868C8.47514 8.56759 8.46287 8.57666 8.45232 8.5831V8.91806C8.45232 9.20801 8.21688 9.44345 7.92693 9.44345C7.63703 9.44339 7.40252 9.20797 7.40252 8.91806Z"
            fill="currentColor"
          />
        </svg>
        {{ t('ask.group.heading') }}</span
      >
      <div class="ask-group-navigation">
        <template v-if="state.data && blocks.length > 1">
          <button
            type="button"
            class="ask-group-icon"
            :aria-label="t('ask.group.previous')"
            :disabled="cursor === 0"
            @click="move(cursor - 1)"
          >
            <svg class="ask-group-chevron-left" viewBox="0 0 20 21" aria-hidden="true">
              <path
                d="M15.2793 7.71101C15.539 7.45131 15.961 7.45131 16.2207 7.71101C16.4804 7.97071 16.4804 8.39272 16.2207 8.65242L10.4707 14.4024C10.211 14.6621 9.78902 14.6621 9.52932 14.4024L3.77932 8.65242L3.69436 8.54792C3.52385 8.28979 3.55205 7.93828 3.77932 7.71101C4.00659 7.48374 4.3581 7.45554 4.61623 7.62605L4.72073 7.71101L10 12.9903L15.2793 7.71101Z"
                fill="currentColor"
                stroke-width="0.6"
              />
            </svg>
          </button>
          <button
            type="button"
            class="ask-group-position"
            :aria-label="t('ask.group.questions')"
            :aria-expanded="navigationOpen"
            @click="navigationOpen = !navigationOpen"
          >
            {{ t('ask.group.position', { index: cursor + 1, total: blocks.length }) }}
          </button>
          <button
            type="button"
            class="ask-group-icon"
            :aria-label="t('ask.group.next')"
            :disabled="cursor === blocks.length - 1"
            @click="move(cursor + 1)"
          >
            <svg class="ask-group-chevron-right" viewBox="0 0 20 21" aria-hidden="true">
              <path
                d="M15.2793 7.71101C15.539 7.45131 15.961 7.45131 16.2207 7.71101C16.4804 7.97071 16.4804 8.39272 16.2207 8.65242L10.4707 14.4024C10.211 14.6621 9.78902 14.6621 9.52932 14.4024L3.77932 8.65242L3.69436 8.54792C3.52385 8.28979 3.55205 7.93828 3.77932 7.71101C4.00659 7.48374 4.3581 7.45554 4.61623 7.62605L4.72073 7.71101L10 12.9903L15.2793 7.71101Z"
                fill="currentColor"
                stroke-width="0.6"
              />
            </svg>
          </button>
        </template>
      </div>
    </header>
    <p v-if="state.error && !state.data" role="alert" class="ask-group-error">{{ state.error }}</p>
    <p v-if="state.busy" class="ask-group-notice" role="status">{{ t('ask.form.submitting') }}</p>
    <p v-if="state.error" role="alert" class="ask-group-error">{{ state.error }}</p>
    <div v-if="!state.data && !state.busy" class="ask-group-status">
      <button type="button" @click="emit('action', { type: 'refresh' })">{{ t('ask.form.refresh') }}</button>
    </div>
    <template v-if="state.data">
      <nav v-if="navigationOpen" class="ask-group-tabs" :aria-label="t('ask.group.questions')">
        <button
          v-for="(item, index) in blocks"
          :key="item.id"
          type="button"
          :aria-current="cursor === index ? 'step' : undefined"
          @click="move(index)"
        >
          {{ t('ask.group.question', { index: index + 1 }) }}
          <span v-if="item.meta?.answer_log?.length">{{ t('ask.group.answered') }}</span>
          <span v-else-if="state.forms[item.id]?.draft.later">{{ t('ask.group.later') }}</span>
        </button>
      </nav>
      <template v-if="block">
        <h3 class="ask-group-question">{{ block.content }}</h3>
        <AskQuestionForm
          ref="questionForm"
          class="ask-group-form"
          :block="block"
          :viewer="viewer"
          :names="names"
          :state="state.forms[block.id]"
          grouped
          @action="onFormAction"
          @picked="onPicked"
        >
          <template #actions>
            <div v-if="allowed && !state.confirm" class="ask-group-footer">
              <button
                v-if="canAnswer(block, viewer)"
                type="button"
                class="ask-group-outline"
                :disabled="state.busy || !!state.pending"
                @click="onSkip"
              >
                {{ t('ask.group.skip') }}
              </button>
              <button
                v-if="cursor < blocks.length - 1"
                type="button"
                class="ask-group-primary"
                :disabled="state.busy || !currentReady"
                @click="advance()"
              >
                {{ t('ask.group.continue') }}
              </button>
              <button
                v-else
                type="button"
                class="ask-group-primary"
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
            </div>
          </template>
        </AskQuestionForm>
      </template>
      <details
        v-if="
          navigationOpen ||
          answered > 0 ||
          state.data.settlement ||
          state.pending ||
          state.error ||
          state.confirm ||
          state.questionChanged ||
          state.conflict ||
          state.rejectedOperation ||
          state.confirmedOperation
        "
        class="ask-group-status"
        :open="
          !!state.data.settlement ||
          !!state.pending ||
          !!state.error ||
          !!state.confirm ||
          !!state.questionChanged ||
          !!state.conflict ||
          !!state.rejectedOperation ||
          !!state.confirmedOperation
        "
      >
        <summary>{{ t('ask.group.progress', { answered, total: state.scope.total }) }}</summary>
        <button type="button" :disabled="state.busy" @click="emit('action', { type: 'refresh' })">
          {{ t('ask.form.refresh') }}
        </button>
        <p class="ask-group-summary">
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
          <p v-if="receipt?.last_error" class="ask-group-error">{{ receipt.last_error }}</p>
        </template>
        <template
          v-if="
            state.confirmedOperation?.settlement &&
            state.confirmedOperation.settlement.client_op_id !== state.data.settlement?.client_op_id
          "
        >
          <p role="status">
            {{ t('ask.group.operationConfirmed', { version: state.confirmedOperation.settlement.v }) }}
          </p>
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
        <div v-if="state.confirm" class="ask-group-confirm">
          <p>{{ t('ask.group.incomplete', { count: unansweredCount }) }}</p>
          <button type="button" @click="emit('action', { type: 'back' })">{{ t('ask.group.back') }}</button>
          <button type="button" class="ask-group-primary" @click="emit('action', { type: 'confirm' })">
            {{ t('ask.group.submitAnyway') }}
          </button>
        </div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.ask-group {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  max-width: 680px;
  margin: 16px 0;
  overflow: hidden;
  font-family: var(--font-sans);
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--line-2);

  /* User approved matching Codex Desktop rounded-3xl (20px fallback) for this panel. */
  /* stylelint-disable-next-line declaration-property-value-allowed-list */
  border-radius: calc(var(--radius-lg) * 5 / 3);
  container-type: inline-size;
}

.ask-group-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 16px 12px 8px 16px;
}

.ask-group-title {
  display: flex;
  min-width: 0;
  font-size: 13px;
  font-weight: 400;
  line-height: var(--lh-14);
  color: var(--muted);
  align-items: center;
  gap: 8px;
}

.ask-group svg {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentcolor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.ask-group-title svg {
  stroke: none;
  flex-shrink: 0;
}

.ask-group-navigation {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
  color: var(--muted);
}

.ask-group button {
  min-height: 28px;
  padding: 0 8px;
  font: inherit;
  font-weight: 500;
  color: var(--text);
  cursor: pointer;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
}

.ask-group .ask-group-icon {
  display: flex;
  width: 24px;
  min-height: 24px;
  padding: 4px;
  color: var(--muted);
  border-radius: var(--radius-md);
  align-items: center;
  justify-content: center;
  border-color: transparent;
}

.ask-group-icon svg {
  width: 14px;
  height: 14px;
}

.ask-group-chevron-left {
  transform: rotate(90deg);
}

.ask-group-chevron-right {
  transform: rotate(-90deg);
}

.ask-group .ask-group-position {
  min-height: 0;
  padding: 0;
  font-size: 12px;
  font-weight: 400;
  line-height: calc(var(--lh-12) * 8 / 9);
  color: var(--muted);
  border: 0;
  border-radius: var(--radius-sm);
}

/* The user requested Codex Desktop panel geometry; keep these radii local. */
@supports (corner-shape: superellipse(1.5)) {
  .ask-group {
    /* stylelint-disable-next-line declaration-property-value-allowed-list */
    border-radius: calc(var(--radius-lg) * 25 / 12);

    /* Progressive enhancement supported by current Chromium, ahead of this lint schema. */
    /* stylelint-disable-next-line property-no-unknown */
    corner-shape: superellipse(1.5);
  }

  .ask-group .ask-group-icon {
    /* stylelint-disable-next-line declaration-property-value-allowed-list */
    border-radius: calc(var(--radius-md) * 5 / 4);
  }
}

.ask-group-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px 16px 12px;
}

.ask-group-tabs button[aria-current] {
  background: var(--fill);
}

.ask-group-tabs span {
  margin-left: 4px;
}

.ask-group-question {
  padding: 4px 16px 0;
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
  color: var(--ink);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ask-group-form {
  margin-right: 8px;
  margin-left: 8px;
}

.ask-group-footer {
  display: flex;
  padding: 0;
  margin-left: auto;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
}

.ask-group .ask-group-primary {
  color: var(--surface);
  background: var(--ink);
}

.ask-group .ask-group-primary:hover:not(:disabled) {
  background: var(--text);
}

.ask-group-notice,
.ask-group-error {
  padding: 0 16px;
  margin: 4px 0 8px;
  overflow-wrap: anywhere;
}

.ask-group-error {
  color: var(--danger-ink);
}

.ask-group-status {
  padding: 0 16px 12px;
  color: var(--muted);
}

.ask-group-status summary {
  padding: 4px 0;
  font-size: 12px;
  line-height: var(--lh-12);
  cursor: pointer;
}

.ask-group-status[open] summary {
  margin-bottom: 8px;
}

.ask-group-summary {
  margin: 8px 0;
}

.ask-group-confirm {
  margin-top: 8px;
}

.ask-group-confirm button {
  margin: 8px 8px 0 0;
}

.ask-group :focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.ask-group:focus-visible {
  outline-offset: 2px;
}

.ask-group button:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.ask-group button:not(.ask-group-primary):hover:not(:disabled) {
  background: var(--fill);
}

/* 接管输入框那一格时，用 composer 的样子：同一块控件、同样的描边圆角与底部
   安全区。放在最后，盖过上面卡片那圈更大的圆角和 superellipse。 */
.ask-group--composer {
  max-width: none;
  padding-bottom: env(safe-area-inset-bottom);
  margin: 0 16px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  /* 接管输入框那一格：面板比输入框高得多，两件事都要成立，否则读起来就是
     「挤在一个小框里」。① 太高时自己滚，而不是把底部的按钮裁掉（基础样式那条
     `overflow: hidden` 正是裁掉它的原因）；② 题面那一段（`.ask-form`）原本有
     640px 上限——那是给消息流里的窄卡片定的，接管时那一格就是输入框的宽度，
     要跟着走。 */
  max-height: min(60vh, 560px);
  overflow-y: auto;
  overscroll-behavior: contain;
  /* 上面那条 `max-height` 管得住「太高」，管不住「被压扁」。输入框那一格是个
     flex 列：上面那条时间线比面板长得多时，两件一起按比例压缩，面板会缩成一条
     几十像素高的缝——题面和选项都滚在那条缝里，看着还是坏的。它不是可以牺牲的
     那一件，这一格是它的：`flex: 0 0 auto` 让它守住自己的高度，压缩全落到时间线
     上；真超过上面那条上限时，出面的才是面板自己的滚动条。 */
  flex: 0 0 auto;
  /* 基础样式那条 `width: 100%` 是给消息流里的卡片定的。接管时再加这里的左右各
     16px 外边距，面板会比输入框宽 32px、右边缘探出这一栏。输入框是用内边距让开
     的（RoomComposer 的 `.composer` 只有 padding），所以这一格也只剩外边距一种
     让法，宽度才和它对齐。 */
  width: auto;
}

.ask-group--composer :deep(.ask-form) {
  max-width: none;
}

/* 这一档原本把接管那一格又限到 `--page-w`（窄屏时收窄）。它和「面板要跟输入框
   同宽」直接冲突：窗口一窄，输入框还是满列宽，面板却缩到 720 一类，看起来就是
   「没有拉伸」。接管那一格的宽度由输入栏给，不在这里再定一次。 */
</style>
