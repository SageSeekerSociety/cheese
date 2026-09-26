<script setup lang="ts">
import type { AgentControlResult, AgentControlState } from '../api'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getAgentControl, sendAgentControl } from '../api'

const props = defineProps<{
  topicId: string
  active: boolean
  /** The room's socket already carries this state. A parent that passes it puts
   * the panel on the frames and drops it to the idle cadence below; a parent
   * that does not keeps asking every two seconds. */
  pushed?: AgentControlState | null
}>()
const LIVE_POLL_MS = 2000
// Only a floor under a frame that never arrived: a socket that dropped, a room
// opened in a view with no socket. Everything this panel shows arrives pushed.
const IDLE_POLL_MS = 30000
const state = ref<AgentControlState | null>(null)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const expanded = ref(false)
const output = ref<unknown>(null)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0

function adopt(next: AgentControlState) {
  if (next.id !== state.value?.id) {
    output.value = null
    notice.value = ''
  }
  state.value = next
}

async function refresh(epoch = generation) {
  try {
    const next = await getAgentControl(props.topicId)
    if (epoch === generation) adopt(next)
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : '加载会话控制失败'
  }
}

async function poll(epoch: number) {
  await refresh(epoch)
  const every = props.pushed === undefined ? LIVE_POLL_MS : IDLE_POLL_MS
  if (epoch === generation && props.active) timer = setTimeout(() => void poll(epoch), every)
}

// A frame lands: take it as the whole state, the same shape the request returns.
watch(
  () => props.pushed,
  (next) => {
    if (next) adopt(next)
  }
)

watch(
  () => props.active,
  () => {
    generation += 1
    clearTimeout(timer)
    state.value = null
    error.value = ''
    if (props.active) void poll(generation)
  },
  { immediate: true }
)
onBeforeUnmount(() => {
  generation += 1
  clearTimeout(timer)
})

const tasks = computed(() => Object.values(state.value?.tasks ?? {}))
const taskLabels: Record<string, string> = {
  running: '运行中',
  queued: '排队中',
  pending: '等待中',
  completed: '已完成',
  failed: '失败',
  killed: '已停止',
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
    if (result.result) receiveResult(result.result)
    await refresh(epoch)
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : '操作失败'
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
const values = ref<Record<string, string>>({ mode: 'bypassPermissions', effort: 'medium', budget: '2048' })
const selected = computed(() => operations.find((op) => op.value === operation.value)!)
const labels: Record<string, string> = {
  model: '模型名称',
  title: '会话名称',
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
  <section class="agent-controls" aria-label="会话控制">
    <div class="control-bar">
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
    <div v-if="expanded" class="control-body">
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
          :disabled="
            busy || !state?.connected || ['completed', 'failed', 'killed', 'stopped'].includes(task.status ?? '')
          "
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
              { title: '自动执行', value: 'bypassPermissions' },
              { title: '仅规划', value: 'plan' },
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
        <v-btn type="submit" size="small" color="primary" variant="tonal" :disabled="busy || !state?.connected"
          >执行</v-btn
        >
      </form>
      <v-btn v-if="authUrl" :href="authUrl" target="_blank" rel="noopener noreferrer" variant="tonal"
        >打开授权页面</v-btn
      >
      <pre v-if="formattedOutput" class="control-output">{{ formattedOutput }}</pre>
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
