<script setup lang="ts">
// 在桌面 app 里接入这台设备的对话框：先问一次，接入时逐步显示进度（可取消），接好后顺手设好名称、
// 提供给哪些团队、Claude Code（样稿 ①②③）。只管画和发事件，接入和存取由 views/desktop/DeviceConnect.vue 做。
import type { MyTeam } from '@/cx_types'
import type { ConnectFailure, ConnectStep } from '@/lib/desktop'

import { ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import ClaudeCodeLogin from '@/components/settings/ClaudeCodeLogin.vue'
import DeviceTeamsPicker from '@/components/settings/DeviceTeamsPicker.vue'
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
  teamIds: number[]
  claudeLoggedIn: boolean
  claudePlan: string | null
  claudeState: 'idle' | 'preparing' | 'browser'
  error: string | null
}>()

const emit = defineEmits<{
  connect: []
  later: []
  cancel: []
  retry: []
  finish: []
  rename: [name: string]
  teams: [ids: number[]]
  claudeLogin: [console: boolean]
  claudeCancel: []
}>()

const name = ref(props.deviceName)
watch(
  () => props.deviceName,
  (v) => (name.value = v)
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
  if (props.stage === 'ask') emit('connect')
  else if (props.stage === 'done') {
    emit('rename', name.value)
    emit('finish')
  } else if (props.stage === 'failed') emit('retry')
}

// 关上对话框（按钮、✕、Esc）：问的时候是「暂不接入」，接入中是取消，其余是关上。
function dismiss(open: boolean) {
  if (open) return
  if (props.stage === 'ask') emit('later')
  else if (props.stage === 'progress') emit('cancel')
  else {
    if (props.stage === 'done') emit('rename', name.value)
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
    :cancel-label="cancelLabel()"
    @update:model-value="dismiss"
    @primary="primary"
  >
    <div v-if="stage === 'ask'" class="flow">
      <p class="flow__body">{{ t('account.connectFlow.askBody') }}</p>
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
      <v-text-field
        v-model="name"
        autocomplete="off"
        :label="t('account.thisDevice.name')"
        variant="outlined"
        density="comfortable"
        hide-details
        class="mt-2"
        @blur="emit('rename', name)"
        @keyup.enter="emit('rename', name)"
      />
      <div class="flow__section">
        <div class="flow__label">{{ t('account.thisDevice.teams') }}</div>
        <div class="flow__hint">{{ t('account.thisDevice.teamsHint') }}</div>
        <DeviceTeamsPicker :teams="teams" :model-value="teamIds" @update:model-value="(ids) => emit('teams', ids)" />
      </div>
      <div class="flow__section flow__section--line">
        <div class="flow__label">Claude Code</div>
        <ClaudeCodeLogin
          :logged-in="claudeLoggedIn"
          :plan="claudePlan"
          :state="claudeState"
          @login="(console) => emit('claudeLogin', console)"
          @cancel="emit('claudeCancel')"
        />
      </div>
      <p v-if="error" class="flow__error">{{ error }}</p>
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

.flow__error {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--danger-ink);
}

.flow__section {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
}

.flow__section--line {
  padding-top: 16px;
  border-top: 1px solid var(--line);
}

.flow__label {
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
  color: var(--ink);
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
