<script setup lang="ts">
import type { AgentControlRequest, AgentControlResult, AgentControlState } from '../api'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { answerAgentControl, getAgentControl, getAgentControlResult, sendAgentControl } from '../api'
// 用模块里那个全局 t，而不是 useI18n()：这块面板嵌在 ChatPanel 里，父组件在若干
// 测试中是不带 i18n 插件挂载的，useI18n() 会当场抛「Need to install with
// `app.use` function」。全局 t 读的是同一个 composer，模板里照样随语言切换重渲染
// （SignIn.vue、AppBar.vue 也是这么用的）。
import { t } from '../i18n'

const props = defineProps<{ topicId: string; active: boolean; questionsOnly?: boolean }>()
const state = ref<AgentControlState | null>(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const expanded = ref(false)
const output = ref<unknown>(null)
const answers = ref<Record<string, Record<string, string>>>({})
const waiting = ref<{ sessionId: string; requestId: string } | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0

async function refresh(epoch = generation) {
  const topic = props.topicId
  try {
    const next = await getAgentControl(topic)
    if (epoch !== generation) return
    if (next.id !== state.value?.id) {
      answers.value = {}
      output.value = null
      notice.value = ''
      waiting.value = null
    }
    state.value = next
    const pendingResult = waiting.value
    if (pendingResult) {
      const result = await getAgentControlResult(topic, pendingResult.sessionId, pendingResult.requestId)
      if (epoch === generation && pendingResult === waiting.value && result.result) {
        waiting.value = null
        receiveResult(result.result)
      } else if (epoch === generation && result.status === 'uncertain') {
        notice.value = t('agentControls.deliveryUnconfirmed')
      }
    }
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : t('agentControls.loadFailed')
  }
}

async function poll(epoch: number) {
  await refresh(epoch)
  if (epoch === generation && props.active) timer = setTimeout(() => void poll(epoch), 2000)
}

watch(
  () => [props.topicId, props.active],
  () => {
    generation += 1
    clearTimeout(timer)
    state.value = null
    waiting.value = null
    error.value = ''
    if (props.active) void poll(generation)
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  generation += 1
  clearTimeout(timer)
})

const pending = computed(() => Object.values(state.value?.pending ?? {}))
const tasks = computed(() => Object.values(state.value?.tasks ?? {}))
// computed 而不是模块级常量表：常量表在 setup 时求值一次，切语言不会重算。
const taskLabels = computed<Record<string, string>>(() => ({
  running: t('agentControls.statuses.running'),
  queued: t('agentControls.statuses.queued'),
  pending: t('agentControls.statuses.pending'),
  completed: t('agentControls.statuses.completed'),
  failed: t('agentControls.statuses.failed'),
  stopped: t('agentControls.statuses.stopped'),
  task_started: t('agentControls.statuses.running'),
  task_progress: t('agentControls.statuses.running'),
}))

function receiveResult(result: NonNullable<AgentControlResult['result']>) {
  const response = result.response
  if (response.subtype === 'error') throw new Error(response.error ?? t('agentControls.incomplete'))
  output.value = response.response ?? null
  notice.value =
    response.response?.backgrounded === false
      ? t('agentControls.cannotBackground')
      : t('agentControls.commandConfirmed')
}

async function run(request: Record<string, unknown>) {
  if (!state.value?.id || busy.value) return
  const epoch = generation
  busy.value = true
  error.value = ''
  notice.value = ''
  output.value = null
  try {
    const result = await sendAgentControl(props.topicId, state.value.id, request)
    if (epoch !== generation) return
    if (!result.result) {
      notice.value = t('agentControls.sentNoResult')
      waiting.value = { sessionId: state.value.id, requestId: result.request_id }
      return
    }
    receiveResult(result.result)
    await refresh(epoch)
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : t('agentControls.runFailed')
  } finally {
    busy.value = false
  }
}

interface Question {
  question: string
  options?: { label: string; description?: string }[]
  multiSelect?: boolean
}
function questions(item: AgentControlRequest): Question[] {
  const value = item.request.input?.questions
  return Array.isArray(value) ? (value as Question[]) : []
}
function setAnswer(id: string, question: string, value: string) {
  answers.value[id] = { ...answers.value[id], [question]: value }
}
async function answer(item: AgentControlRequest, allow: boolean) {
  if (!state.value?.id || busy.value) return
  const epoch = generation
  busy.value = true
  error.value = ''
  output.value = null
  notice.value = ''
  try {
    await answerAgentControl(
      props.topicId,
      state.value.id,
      item.request_id,
      allow
        ? {
            behavior: 'allow',
            updatedInput: {
              ...item.request.input,
              ...(questions(item).length ? { answers: answers.value[item.request_id] } : {}),
            },
          }
        : { behavior: 'deny', message: t('agentControls.denyMessage') }
    )
    if (epoch !== generation) return
    notice.value = t('agentControls.answerSubmitted')
    await refresh(epoch)
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : t('agentControls.answerFailed')
  } finally {
    busy.value = false
  }
}

// 每一项都写成字面量 t('...') 调用：目录门禁只认源码里出现的字面 key，
// 拼出来的 `t('agentControls.operations.' + value)` 会被判成「没人用」。
const operations = computed(() => [
  { value: 'initialize', title: t('agentControls.operations.initialize'), fields: [] },
  { value: 'set_model', title: t('agentControls.operations.setModel'), fields: ['model'] },
  { value: 'set_permission_mode', title: t('agentControls.operations.setPermissionMode'), fields: ['mode'] },
  { value: 'apply_flag_settings', title: t('agentControls.operations.applyFlagSettings'), fields: ['effort'] },
  { value: 'set_max_thinking_tokens', title: t('agentControls.operations.setMaxThinkingTokens'), fields: ['budget'] },
  { value: 'rename_session', title: t('agentControls.operations.renameSession'), fields: ['title'] },
  { value: 'set_color', title: t('agentControls.operations.setColor'), fields: ['color'] },
  { value: 'file_suggestions', title: t('agentControls.operations.fileSuggestions'), fields: ['query'] },
  { value: 'read_file', title: t('agentControls.operations.readFile'), fields: ['path'] },
  { value: 'get_workspace_diff', title: t('agentControls.operations.getWorkspaceDiff'), fields: [] },
  { value: 'get_context_usage', title: t('agentControls.operations.getContextUsage'), fields: [] },
  { value: 'get_usage', title: t('agentControls.operations.getUsage'), fields: [] },
  { value: 'mcp_status', title: t('agentControls.operations.mcpStatus'), fields: [] },
  { value: 'mcp_reconnect', title: t('agentControls.operations.mcpReconnect'), fields: ['serverName'] },
  { value: 'mcp_authenticate', title: t('agentControls.operations.mcpAuthenticate'), fields: ['serverName'] },
  {
    value: 'mcp_oauth_callback_url',
    title: t('agentControls.operations.mcpOauthCallbackUrl'),
    fields: ['serverName', 'callbackUrl'],
  },
])
const operation = ref('initialize')
const values = ref<Record<string, string>>({ mode: 'default', effort: 'medium', budget: '2048' })
const selected = computed(() => operations.value.find((op) => op.value === operation.value)!)
const labels = computed<Record<string, string>>(() => ({
  model: t('agentControls.fields.model'),
  title: t('agentControls.fields.title'),
  color: t('agentControls.fields.color'),
  query: t('agentControls.fields.query'),
  path: t('agentControls.fields.path'),
  serverName: t('agentControls.fields.serverName'),
  callbackUrl: t('agentControls.fields.callbackUrl'),
  budget: t('agentControls.fields.budget'),
}))
const permissionOptions = computed(() => [
  { title: t('agentControls.permissions.default'), value: 'default' },
  { title: t('agentControls.permissions.acceptEdits'), value: 'acceptEdits' },
  { title: t('agentControls.permissions.plan'), value: 'plan' },
  { title: t('agentControls.permissions.bypassPermissions'), value: 'bypassPermissions' },
])
function execute() {
  const fields = Object.fromEntries(selected.value.fields.map((field) => [field, values.value[field] ?? '']))
  const request: Record<string, unknown> = { subtype: operation.value, ...fields }
  if (operation.value === 'apply_flag_settings') {
    delete request.effort
    request.settings = { effortLevel: values.value.effort }
  }
  if (operation.value === 'set_max_thinking_tokens') {
    delete request.budget
    request.max_thinking_tokens = Number(values.value.budget)
  }
  if (operation.value === 'initialize') request.supportedDialogKinds = ['ask_user_question']
  if (operation.value === 'read_file') request.encoding = 'utf8'
  void run(request)
}
const formattedOutput = computed(() => {
  if (typeof output.value === 'string') return output.value
  if (output.value && typeof output.value === 'object') {
    const data = output.value as Record<string, unknown>
    if (typeof data.contents === 'string') return data.contents
    if (typeof data.content === 'string') return data.content
    if (typeof data.diff === 'string') return data.diff
    return JSON.stringify(data, null, 2)
  }
  return ''
})
const authUrl = computed(() => {
  const value = (output.value as Record<string, unknown> | null)?.authUrl
  return typeof value === 'string' && /^https?:\/\//.test(value) ? value : undefined
})
</script>

<template>
  <section v-if="!questionsOnly || pending.length" class="agent-controls" :aria-label="t('agentControls.ariaLabel')">
    <div v-if="!questionsOnly" class="control-bar">
      <span>{{ state?.connected ? t('agentControls.connected') : t('agentControls.disconnected') }}</span>
      <v-btn
        size="small"
        variant="text"
        :disabled="!state?.connected || busy"
        @click="run({ subtype: 'background_tasks' })"
        >{{ t('agentControls.background') }}</v-btn
      >
      <v-btn size="small" variant="text" :disabled="!state?.connected || busy" @click="run({ subtype: 'interrupt' })">{{
        t('agentControls.interrupt')
      }}</v-btn>
      <v-btn size="small" variant="text" :aria-expanded="expanded" @click="expanded = !expanded">{{
        expanded ? t('agentControls.fewerControls') : t('agentControls.moreControls')
      }}</v-btn>
    </div>
    <v-alert v-if="error" type="error" density="compact" class="ma-2">{{ error }}</v-alert>
    <p v-if="notice" role="status" class="control-notice">{{ notice }}</p>
    <div v-if="pending.length || expanded" class="control-body">
      <article v-for="item in pending" :key="item.request_id" class="control-question">
        <template v-if="questions(item).length">
          <div v-for="question in questions(item)" :key="question.question">
            <p>{{ question.question }}</p>
            <v-combobox
              autocomplete="off"
              :model-value="
                question.multiSelect
                  ? answers[item.request_id]?.[question.question]?.split(', ') ?? []
                  : answers[item.request_id]?.[question.question] ?? ''
              "
              :items="question.options?.map((option) => option.label)"
              :multiple="question.multiSelect"
              :label="t('agentControls.yourAnswer')"
              density="compact"
              hide-details
              @update:model-value="
                setAnswer(
                  item.request_id,
                  question.question,
                  Array.isArray($event) ? $event.join(', ') : String($event ?? '')
                )
              "
            />
            <p v-for="option in question.options" :key="option.label" class="control-description">
              {{ t('agentControls.optionLine', { label: option.label, description: option.description ?? '' }) }}
            </p>
          </div>
        </template>
        <template v-else>
          <p>{{ t('agentControls.toolRequest', { tool: item.request.tool_name ?? t('agentControls.operation') }) }}</p>
          <pre>{{ JSON.stringify(item.request.input, null, 2) }}</pre>
        </template>
        <div class="control-bar">
          <v-btn
            size="small"
            variant="tonal"
            :disabled="busy || questions(item).some((q) => !answers[item.request_id]?.[q.question])"
            @click="answer(item, true)"
            >{{ questions(item).length ? t('agentControls.submitAnswer') : t('agentControls.allowOnce') }}</v-btn
          >
          <v-btn size="small" variant="text" :disabled="busy" @click="answer(item, false)">{{
            t('agentControls.deny')
          }}</v-btn>
        </div>
      </article>
      <template v-if="expanded">
        <div v-for="task in tasks" :key="task.task_id" class="control-bar">
          <span
            >{{ task.description ?? task.task_id }} ·
            {{ taskLabels[task.status ?? task.subtype ?? ''] ?? t('agentControls.statusUnknown') }}</span
          >
          <v-btn
            v-if="task.tool_use_id"
            size="small"
            variant="text"
            :disabled="busy || !state?.connected"
            @click="run({ subtype: 'background_tasks', tool_use_id: task.tool_use_id })"
            >{{ t('agentControls.background') }}</v-btn
          >
          <v-btn
            size="small"
            variant="text"
            :disabled="busy || !state?.connected || ['completed', 'failed', 'stopped'].includes(task.status ?? '')"
            @click="run({ subtype: 'stop_task', task_id: task.task_id })"
            >{{ t('agentControls.stop') }}</v-btn
          >
        </div>
        <form class="control-form" @submit.prevent="execute">
          <v-select
            v-model="operation"
            autocomplete="off"
            :items="operations"
            :label="t('agentControls.operation')"
            density="compact"
            hide-details
          />
          <template v-for="field in selected.fields" :key="field">
            <v-select
              v-if="field === 'mode'"
              v-model="values.mode"
              autocomplete="off"
              :label="t('agentControls.operations.setPermissionMode')"
              :items="permissionOptions"
              density="compact"
              hide-details
            />
            <v-select
              v-else-if="field === 'effort'"
              v-model="values.effort"
              autocomplete="off"
              :label="t('agentControls.operations.applyFlagSettings')"
              :items="['low', 'medium', 'high', 'max']"
              density="compact"
              hide-details
            />
            <v-text-field
              v-else
              v-model="values[field]"
              autocomplete="off"
              :label="labels[field]"
              :type="field === 'budget' ? 'number' : 'text'"
              density="compact"
              hide-details
              required
            />
          </template>
          <p v-if="operation === 'set_max_thinking_tokens'" class="control-description">
            {{ t('agentControls.effortHint') }}
          </p>
          <v-btn type="submit" size="small" variant="tonal" :disabled="busy || !state?.connected">{{
            t('agentControls.run')
          }}</v-btn>
        </form>
        <v-btn v-if="authUrl" :href="authUrl" target="_blank" rel="noopener noreferrer" variant="tonal">{{
          t('agentControls.openAuthPage')
        }}</v-btn>
        <pre v-if="formattedOutput" class="control-output">{{ formattedOutput }}</pre>
      </template>
    </div>
  </section>
</template>

<style scoped>
.agent-controls {
  font-size: 13px;
  color: var(--text);
  background: var(--surface);
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}

.control-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px;
}

.control-description {
  color: var(--muted);
}

.control-body {
  max-height: 50vh;
  padding: 8px;
  overflow: auto;
}

.control-question {
  padding: 12px;
  margin-bottom: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.control-question p {
  margin-bottom: 8px;
}

.control-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 8px;
}

.control-notice {
  padding: 8px 16px;
}

pre {
  font-size: 13px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.control-output {
  padding: 12px;
  background: var(--fill);
  border-radius: var(--radius-md);
}
</style>
