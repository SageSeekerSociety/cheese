<script setup lang="ts">
import type { AgentControlState, McpDeclaringType, RoomMcpServer } from '../api'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getAgentControl, getRoomMcpServers, sendAgentControl } from '../api'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRefLink.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

// 现场 only watches: everything here reads the session's state, and nothing
// changes the session, the room or the machine.
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
const busy = ref(false)
const expanded = ref(false)
const output = ref<unknown>(null)
// The teammate whose session is shown, once the room has several working: the
// platform never picks one of them, so the person does.
const seat = ref<string | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0

const seats = computed(() => state.value?.seats ?? [])
const seatItems = computed(() => seats.value.map((held) => ({ value: held.agent, title: held.agent })))
const status = computed(() => {
  if (state.value?.connected) return t('work.room.site.session.connected')
  if (seats.value.length > 1) return t('work.room.site.session.several', { count: seats.value.length })
  return t('work.room.site.session.none')
})

function adopt(next: AgentControlState) {
  if (next.id !== state.value?.id) output.value = null
  state.value = next
  if (seat.value && !seats.value.some((held) => held.agent === seat.value)) seat.value = null
}

watch(seat, () => void refresh())

async function refresh(epoch = generation) {
  try {
    const next = await getAgentControl(props.topicId, seat.value)
    if (epoch === generation) adopt(next)
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : t('work.room.site.session.loadFailed')
  }
}

async function poll(epoch: number) {
  await refresh(epoch)
  const every = props.pushed === undefined ? LIVE_POLL_MS : IDLE_POLL_MS
  if (epoch === generation && props.active) timer = setTimeout(() => void poll(epoch), every)
}

// A frame lands: take it as the whole state, the same shape the request returns.
// It speaks for the room as a whole, so with a teammate picked it is only the
// cue to read that teammate's session again.
watch(
  () => props.pushed,
  (next) => {
    if (!next) return
    if (seat.value) void refresh()
    else adopt(next)
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

// 这间房的会话用的是项目的连接：用谁的账号授权的，房间里的人都看得到（#1909）。
// 只读；连接和断开在项目设置里。
const mcpServers = ref<RoomMcpServer[]>([])
watch(expanded, async (open) => {
  if (!open) return
  try {
    mcpServers.value = (await getRoomMcpServers(props.topicId)).servers
  } catch {
    mcpServers.value = []
  }
})
// 和项目设置里同一句：项目的 .mcp.json（模板里画），或声明它的那几个队友类型。
function declaredBy(types: McpDeclaringType[]) {
  const titles = types.map((type) => type.title || type.name).join(t('work.mcp.listSeparator'))
  return t('work.mcp.source.types', { types: titles }, types.length)
}

function mcpState(server: RoomMcpServer) {
  const keys: Record<RoomMcpServer['status'], string> = {
    connected: 'work.mcp.status.connected',
    ready: 'work.mcp.status.ready',
    needs_reconnect: 'work.mcp.status.needsReconnect',
    missing_values: 'work.mcp.room.setUp',
    disconnected: 'work.mcp.room.setUp',
  }
  return t(keys[server.status])
}

const tasks = computed(() => Object.values(state.value?.tasks ?? {}))
const taskKeys: Record<string, string> = {
  running: 'work.room.site.session.task.running',
  queued: 'work.room.site.session.task.queued',
  pending: 'work.room.site.session.task.pending',
  completed: 'work.room.site.session.task.completed',
  failed: 'work.room.site.session.task.failed',
  killed: 'work.room.site.session.task.stopped',
  stopped: 'work.room.site.session.task.stopped',
  task_started: 'work.room.site.session.task.running',
  task_progress: 'work.room.site.session.task.running',
}
function taskLabel(task: { status?: string; subtype?: string }) {
  return t(taskKeys[task.status ?? task.subtype ?? ''] ?? 'work.room.site.session.task.unknown')
}

// Each of these only reads. `initialize` answers with what the session was
// started with (its commands, models, account) and changes nothing.
const reads = [
  { value: 'initialize', label: 'work.room.site.session.reads.initialize', fields: [] },
  { value: 'file_suggestions', label: 'work.room.site.session.reads.file_suggestions', fields: ['query'] },
  { value: 'read_file', label: 'work.room.site.session.reads.read_file', fields: ['path'] },
  { value: 'get_workspace_diff', label: 'work.room.site.session.reads.get_workspace_diff', fields: [] },
  { value: 'get_context_usage', label: 'work.room.site.session.reads.get_context_usage', fields: [] },
  { value: 'get_usage', label: 'work.room.site.session.reads.get_usage', fields: [] },
  { value: 'mcp_status', label: 'work.room.site.session.reads.mcp_status', fields: [] },
] as const
type Read = (typeof reads)[number]['value']
const readItems = computed(() => reads.map((read) => ({ value: read.value, title: t(read.label) })))
const fieldLabels: Record<string, string> = {
  query: 'work.room.site.session.fields.query',
  path: 'work.room.site.session.fields.path',
}
const reading = ref<Read>('initialize')
const values = ref<Record<string, string>>({})
const fields = computed(() => reads.find((read) => read.value === reading.value)!.fields)

async function look() {
  if (!state.value?.id || busy.value) return
  const epoch = generation
  const request: Record<string, unknown> = {
    subtype: reading.value,
    ...Object.fromEntries(fields.value.map((field) => [field, values.value[field] ?? ''])),
  }
  if (reading.value === 'read_file') request.encoding = 'utf8'
  busy.value = true
  error.value = ''
  output.value = null
  try {
    const { id, agent } = state.value
    const result = agent
      ? await sendAgentControl(props.topicId, id, request, undefined, agent)
      : await sendAgentControl(props.topicId, id, request)
    if (epoch !== generation) return
    const response = result.result?.response
    if (response?.subtype === 'error') throw new Error(response.error ?? t('work.room.site.session.askFailed'))
    output.value = response?.response ?? null
  } catch (e) {
    if (epoch === generation) error.value = e instanceof Error ? e.message : t('work.room.site.session.askFailed')
  } finally {
    busy.value = false
  }
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
</script>

<template>
  <section class="session-inspector" :aria-label="t('work.room.site.session.label')">
    <div class="inspector-bar">
      <span>{{ status }}</span>
      <v-select
        v-if="seats.length > 1"
        v-model="seat"
        class="inspector-seat"
        autocomplete="off"
        :items="seatItems"
        :label="t('work.room.site.session.teammate')"
        density="compact"
        hide-details
      />
      <BaseButton kind="ghost" size="sm" :aria-expanded="expanded" @click="expanded = !expanded">{{
        t(expanded ? 'work.room.site.session.collapse' : 'work.room.site.session.expand')
      }}</BaseButton>
    </div>
    <v-alert v-if="error" type="error" density="compact" class="ma-2">{{ error }}</v-alert>
    <div v-if="expanded" class="inspector-body">
      <ul v-if="tasks.length" class="inspector-tasks">
        <li v-for="task in tasks" :key="task.task_id">
          {{ task.description ?? task.task_id }} · {{ taskLabel(task) }}
        </li>
      </ul>
      <div v-if="mcpServers.length" class="inspector-mcp" data-testid="room-mcp-servers">
        <div class="t-eyebrow">{{ t('work.mcp.room.heading') }}</div>
        <ul>
          <li v-for="server in mcpServers" :key="server.name">
            <span class="c-ink">{{ server.name }}</span>
            <span class="c-muted">
              ·
              <i18n-t v-if="!server.declared_by" keypath="work.mcp.source.project" tag="span" data-testid="mcp-source">
                <template #file><code class="mcp-file">.mcp.json</code></template>
              </i18n-t>
              <span v-else data-testid="mcp-source">{{ declaredBy(server.declared_by) }}</span>
              ·
              <i18n-t
                v-if="server.status === 'connected' && server.authorized_by"
                keypath="work.mcp.status.connectedBy"
                tag="span"
              >
                <template #name><UserRef :handle="server.authorized_by" /></template>
                <template #when>{{ relTime(server.authorized_at) }}</template>
              </i18n-t>
              <template v-else>{{ mcpState(server) }}</template>
            </span>
          </li>
        </ul>
      </div>
      <form class="inspector-form" @submit.prevent="look">
        <v-select
          v-model="reading"
          autocomplete="off"
          :items="readItems"
          :label="t('work.room.site.session.what')"
          density="compact"
          hide-details
        />
        <v-text-field
          v-for="field in fields"
          :key="field"
          v-model="values[field]"
          autocomplete="off"
          :label="t(fieldLabels[field])"
          density="compact"
          hide-details
          required
        />
        <BaseButton kind="primary" size="sm" type="submit" :disabled="busy || !state?.connected">{{
          t('work.room.site.session.view')
        }}</BaseButton>
      </form>
      <pre v-if="formattedOutput" class="inspector-output">{{ formattedOutput }}</pre>
    </div>
  </section>
</template>

<style scoped>
.session-inspector {
  font-size: 13px;
  color: var(--text);
  background: var(--surface);
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}

.inspector-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px;
}

.inspector-seat {
  flex: 0 1 200px;
  min-width: 140px;
}

.inspector-body {
  max-height: 50vh;
  padding: 8px;
  overflow: auto;
}

.inspector-tasks {
  margin: 0;
  padding: 0 8px 8px;
  list-style: none;
}

.inspector-mcp {
  padding: 0 8px 8px;
}

.mcp-file {
  font-family: var(--font-mono);
}

.inspector-mcp ul {
  margin: 4px 0 0;
  padding: 0;
  list-style: none;
}

.inspector-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 8px;
}

pre {
  font-size: 13px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.inspector-output {
  padding: 12px;
  background: var(--fill);
  border-radius: var(--radius-md);
}
</style>
