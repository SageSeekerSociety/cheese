<script setup lang="ts">
import type { AgentControlRequest, AgentControlResult, AgentControlState } from '../api'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { answerAgentControl, getAgentControl, getAgentControlResult, sendAgentControl } from '../api'

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
        notice.value = '尚未确认送达；为避免重复执行，系统不会自动重发'
      }
    }
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : '加载会话控制失败'
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
const taskLabels: Record<string, string> = {
  running: '运行中',
  queued: '排队中',
  pending: '等待中',
  completed: '已完成',
  failed: '失败',
  stopped: '已停止',
  task_started: '运行中',
  task_progress: '运行中',
}

function receiveResult(result: NonNullable<AgentControlResult['result']>) {
  const response = result.response
  if (response.subtype === 'error') throw new Error(response.error ?? '操作未完成')
  output.value = response.response ?? null
  notice.value = response.response?.backgrounded === false ? '当前任务无法转入后台' : '指令已确认'
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
      notice.value = '已发送，尚未收到执行结果'
      waiting.value = { sessionId: state.value.id, requestId: result.request_id }
      return
    }
    receiveResult(result.result)
    await refresh(epoch)
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : '操作失败'
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
        : { behavior: 'deny', message: '用户拒绝了此操作' }
    )
    if (epoch !== generation) return
    notice.value = '回答已提交，正在等待会话确认'
    await refresh(epoch)
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : '提交回答失败'
  } finally {
    busy.value = false
  }
}

const operations = [
  { value: 'initialize', title: '会话状态', fields: [] },
  { value: 'set_model', title: '切换模型', fields: ['model'] },
  { value: 'set_permission_mode', title: '工具权限', fields: ['mode'] },
  { value: 'apply_flag_settings', title: '思考强度', fields: ['effort'] },
  { value: 'set_max_thinking_tokens', title: '思考预算', fields: ['budget'] },
  { value: 'rename_session', title: '会话名称', fields: ['title'] },
  { value: 'set_color', title: '会话颜色', fields: ['color'] },
  { value: 'file_suggestions', title: '查找文件', fields: ['query'] },
  { value: 'read_file', title: '查看文件', fields: ['path'] },
  { value: 'get_workspace_diff', title: '工作区变更', fields: [] },
  { value: 'get_context_usage', title: '上下文用量', fields: [] },
  { value: 'get_usage', title: '账号用量', fields: [] },
  { value: 'mcp_status', title: '外部工具连接', fields: [] },
  { value: 'mcp_reconnect', title: '重连外部工具', fields: ['serverName'] },
  { value: 'mcp_authenticate', title: '授权外部工具', fields: ['serverName'] },
  { value: 'mcp_oauth_callback_url', title: '完成外部工具授权', fields: ['serverName', 'callbackUrl'] },
]
const operation = ref('initialize')
const values = ref<Record<string, string>>({ mode: 'default', effort: 'medium', budget: '2048' })
const selected = computed(() => operations.find((op) => op.value === operation.value)!)
const labels: Record<string, string> = {
  model: '模型名称',
  title: '会话名称',
  color: '颜色',
  query: '文件名',
  path: '文件路径',
  serverName: '工具服务名称',
  callbackUrl: '授权完成后的地址',
  budget: '思考 token 上限',
}
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
  <section v-if="!questionsOnly || pending.length" class="agent-controls" aria-label="会话控制">
    <div v-if="!questionsOnly" class="control-bar">
      <span>{{ state?.connected ? '控制已连接' : '暂无可用控制连接' }}</span>
      <v-btn
        size="small"
        variant="text"
        :disabled="!state?.connected || busy"
        @click="run({ subtype: 'background_tasks' })"
        >转入后台</v-btn
      >
      <v-btn size="small" variant="text" :disabled="!state?.connected || busy" @click="run({ subtype: 'interrupt' })"
        >中断当前任务</v-btn
      >
      <v-btn size="small" variant="text" :aria-expanded="expanded" @click="expanded = !expanded">{{
        expanded ? '收起控制' : '更多控制'
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
              label="你的回答"
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
              {{ option.label }}：{{ option.description }}
            </p>
          </div>
        </template>
        <template v-else>
          <p>{{ item.request.tool_name ?? '操作' }} 请求执行许可</p>
          <pre>{{ JSON.stringify(item.request.input, null, 2) }}</pre>
        </template>
        <div class="control-bar">
          <v-btn
            size="small"
            variant="tonal"
            :disabled="busy || questions(item).some((q) => !answers[item.request_id]?.[q.question])"
            @click="answer(item, true)"
            >{{ questions(item).length ? '提交回答' : '允许本次' }}</v-btn
          >
          <v-btn size="small" variant="text" :disabled="busy" @click="answer(item, false)">拒绝</v-btn>
        </div>
      </article>
      <template v-if="expanded">
        <div v-for="task in tasks" :key="task.task_id" class="control-bar">
          <span
            >{{ task.description ?? task.task_id }} ·
            {{ taskLabels[task.status ?? task.subtype ?? ''] ?? '状态待更新' }}</span
          >
          <v-btn
            v-if="task.tool_use_id"
            size="small"
            variant="text"
            :disabled="busy || !state?.connected"
            @click="run({ subtype: 'background_tasks', tool_use_id: task.tool_use_id })"
            >转入后台</v-btn
          >
          <v-btn
            size="small"
            variant="text"
            :disabled="busy || !state?.connected || ['completed', 'failed', 'stopped'].includes(task.status ?? '')"
            @click="run({ subtype: 'stop_task', task_id: task.task_id })"
            >停止</v-btn
          >
        </div>
        <form class="control-form" @submit.prevent="execute">
          <v-select
            v-model="operation"
            autocomplete="off"
            :items="operations"
            label="操作"
            density="compact"
            hide-details
          />
          <template v-for="field in selected.fields" :key="field">
            <v-select
              v-if="field === 'mode'"
              v-model="values.mode"
              autocomplete="off"
              label="工具权限"
              :items="[
                { title: '逐次确认', value: 'default' },
                { title: '自动接受编辑', value: 'acceptEdits' },
                { title: '仅规划', value: 'plan' },
                { title: '自动执行', value: 'bypassPermissions' },
              ]"
              density="compact"
              hide-details
            />
            <v-select
              v-else-if="field === 'effort'"
              v-model="values.effort"
              autocomplete="off"
              label="思考强度"
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
            采用自适应思考的模型会自行决定预算
          </p>
          <v-btn type="submit" size="small" variant="tonal" :disabled="busy || !state?.connected">执行</v-btn>
        </form>
        <v-btn v-if="authUrl" :href="authUrl" target="_blank" rel="noopener noreferrer" variant="tonal"
          >打开授权页面</v-btn
        >
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
