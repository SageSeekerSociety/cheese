<script setup lang="ts">
// Select one enrollment team, show progress and report completion.
import type { MyTeam } from '@/cx_types'
import type { ConnectFailure, ConnectStep } from '@/lib/desktop'
import type { DesktopExecutorStatus } from '@/lib/desktopApp'

import { ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

defineOptions({ name: 'DeviceConnectDialog' })

const props = defineProps<{
  open: boolean
  stage: 'ask' | 'progress' | 'done' | 'failed'
  steps: ConnectStep[]
  current: ConnectStep | null
  percent: number | null
  failure: ConnectFailure | null
  /** 这台 Mac 首次接入可能要装命令行开发者工具，先说一声。 */
  mac: boolean
  deviceName: string
  teams: MyTeam[]
  error: string | null
  /** 接好之后，AI 队友要用的程序在后台下到哪了；不说的应用为 null。 */
  prefetch?: DesktopExecutorStatus | null
}>()

const emit = defineEmits<{
  connect: [teamId: number]
  later: []
  cancel: []
  retry: []
  finish: []
}>()

const teamId = ref<number | null>(null)
watch(
  () => props.teams,
  (teams) => {
    if (!teams.some((team) => team.id === teamId.value))
      teamId.value = teams.find((team) => team.personal)?.id ?? teams[0]?.id ?? null
  },
  { immediate: true }
)
function title() {
  if (props.stage === 'ask') return t('account.connectFlow.askTitle')
  if (props.stage === 'progress') return t('account.connectFlow.progressTitle')
  if (props.stage === 'done') return t('account.connectFlow.doneTitle')
  return t('account.connectFlow.failedTitle')
}

function primaryLabel() {
  if (props.stage === 'ask') return t('account.connectFlow.connect')
  if (props.stage === 'done') return t('account.connectFlow.finish')
  if (props.stage === 'failed') return t('account.connectFlow.retry')
  return undefined
}

function cancelLabel() {
  if (props.stage === 'ask') return t('account.connectFlow.later')
  if (props.stage === 'progress') return t('account.connectFlow.cancel')
  return t('account.connectFlow.close')
}

function primary() {
  if (props.stage === 'ask' && teamId.value !== null) emit('connect', teamId.value)
  else if (props.stage === 'done') {
    emit('finish')
  } else if (props.stage === 'failed') emit('retry')
}

// 关上对话框（按钮、✕、Esc）：问的时候是「暂不接入」，接入中是取消，其余是关上。
function dismiss(open: boolean) {
  if (open) return
  if (props.stage === 'ask') emit('later')
  else if (props.stage === 'progress') emit('cancel')
  else {
    emit('finish')
  }
}

type StepState = 'done' | 'active' | 'wait'
function stepState(step: ConnectStep): StepState {
  if (props.current === step) return 'active'
  const at = props.current ? props.steps.indexOf(props.current) : props.steps.length
  return props.steps.indexOf(step) < at ? 'done' : 'wait'
}

const HINTS: Partial<Record<ConnectStep, string>> = {
  removeOld: 'account.connectFlow.hint.removeOld',
  tools: 'account.connectFlow.hint.tools',
  runtime: 'account.connectFlow.hint.runtime',
}

function failedAt(f: ConnectFailure) {
  if (f.step === 'tools') return t('account.connectFlow.failed.tools')
  if (f.step === 'start') return t('account.connectFlow.failed.start')
  if (f.step === 'removeOld') return t('account.connectFlow.failed.removeOld')
  if (f.step === 'busy') return t('account.connectFlow.failed.busy')
  const step = (['download', 'approve', 'runtime'] as string[]).includes(f.step) ? f.step : 'approve'
  return t('account.connectFlow.failed.at', { step: t(`account.connectFlow.step.${step}`) })
}
</script>

<template>
  <AdaptiveDialog
    :model-value="open"
    :title="title()"
    size="sm"
    persistent
    :primary-label="primaryLabel()"
    :primary-disabled="stage === 'ask' && teamId === null"
    :cancel-label="cancelLabel()"
    @update:model-value="dismiss"
    @primary="primary"
  >
    <p v-if="error" class="flow__error">{{ error }}</p>
    <div v-if="stage === 'ask'" class="flow">
      <p class="flow__body">{{ t('account.connectFlow.askBody') }}</p>
      <v-select
        v-model="teamId"
        autocomplete="off"
        :items="teams"
        item-title="name"
        item-value="id"
        :label="t('project.connect.teamLabel')"
        variant="outlined"
        hide-details
        class="mt-3"
      />
      <p v-if="mac" class="flow__hint">{{ t('account.connectFlow.askMacTools') }}</p>
    </div>

    <ol v-else-if="stage === 'progress'" class="steps" aria-live="polite">
      <li v-for="step in steps" :key="step" class="step" :class="`step--${stepState(step)}`">
        <v-icon v-if="stepState(step) === 'done'" size="20" class="step__icon step__icon--done"
          >mdi-check-circle</v-icon
        >
        <v-progress-circular
          v-else-if="stepState(step) === 'active'"
          indeterminate
          size="18"
          width="2"
          color="primary"
          class="step__icon"
        />
        <v-icon v-else size="20" class="step__icon step__icon--wait">mdi-circle-outline</v-icon>
        <div class="step__text">
          <div class="step__line">
            <span>{{ t(`account.connectFlow.step.${step}`) }}</span>
            <span v-if="stepState(step) === 'active' && step === 'download' && percent !== null" class="step__pct">
              {{ percent }}%
            </span>
          </div>
          <div v-if="stepState(step) === 'active' && HINTS[step]" class="step__hint">{{ t(HINTS[step]!) }}</div>
          <v-progress-linear
            v-if="stepState(step) === 'active' && step === 'download' && percent !== null"
            :model-value="percent"
            color="primary"
            rounded
            height="4"
          />
        </div>
      </li>
    </ol>

    <div v-else-if="stage === 'failed' && failure" class="flow">
      <p class="flow__body">{{ failedAt(failure) }}</p>
      <details v-if="failure.detail" class="flow__details">
        <summary>{{ t('account.connectFlow.details') }}</summary>
        <pre>{{ failure.detail }}</pre>
      </details>
    </div>

    <div v-else-if="stage === 'done'" class="flow">
      <p class="flow__hint">{{ t('account.connectFlow.doneHint') }}</p>
      <p class="flow__body">{{ deviceName }}</p>
      <div v-if="prefetch?.state === 'fetching'" class="flow__prefetch" aria-live="polite">
        <p class="flow__hint">
          {{
            prefetch.percent === null
              ? t('account.connectFlow.prefetch.preparing')
              : t('account.connectFlow.prefetch.fetching', { percent: prefetch.percent })
          }}
        </p>
        <v-progress-linear
          :model-value="prefetch.percent ?? undefined"
          :indeterminate="prefetch.percent === null"
          color="primary"
          rounded
          height="2"
        />
      </div>
      <p v-else-if="prefetch?.state === 'ready'" class="flow__hint">{{ t('account.connectFlow.prefetch.ready') }}</p>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
.flow {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.flow__body {
  margin: 0;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.flow__hint {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.flow__prefetch {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.flow__error {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}

.flow__details summary {
  font-size: 13px;
  color: var(--muted);
  cursor: pointer;
}

.flow__details pre {
  margin: 8px 0 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  white-space: pre-wrap;
}

.steps {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 0;
  margin: 4px 0 0;
  list-style: none;
}

.step {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.step__icon {
  flex: none;
  margin-top: 1px;
}

.step__icon--done {
  color: var(--ok);
}

.step__icon--wait {
  color: var(--faint);
}

.step__text {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
}

.step__line {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--ink);
}

.step--wait .step__line {
  color: var(--faint);
}

.step__pct {
  font-variant-numeric: tabular-nums;
  color: var(--faint);
}

.step__hint {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}
</style>
