<script setup lang="ts">
// 「这间房的会话现在什么样」那一栏的展示件：连没连上、手上有哪几个队友的会话、它在跑
// 什么任务、用的是哪些 MCP 连接，以及问一句只读的话。
//
// 取数（轮询、上一帧会话状态、展开时读 MCP 连接、把那一句问出去）都在
// `composables/useSessionInspector.ts` 里，由 `usePanelSite` 调一次、整包从 `session`
// 递进来。这一只原先自己引三个接口函数，而它渲染在「现场」那一格里 —— 那一格是场景
// （`components/panels/**` 下每个 SFC 都是），于是整格跟着它够得着接口层。
import type {
  McpDeclaringType,
  RoomMcpServer,
  SessionInspectorBundle,
  SessionRead,
} from '../composables/useSessionInspector'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import UserRef from '@/components/common/UserRef.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'
import { userRefRoute } from '@/lib/userRef'

// 现场 only watches: everything here reads the session's state, and nothing
// changes the session, the room or the machine.
const props = defineProps<{
  /** 这一栏的取数（`composables/useSessionInspector.ts` 那一包）。 */
  session: SessionInspectorBundle
  /** 「由 X 授权」那颗 chip 的去处要它：项目 ID 换来项目里的成员页。 */
  projectId?: string | null
}>()

const emit = defineEmits<{ (e: 'mention-click', handle: string): void }>()

const {
  state,
  error,
  busy,
  expanded,
  output,
  seat,
  seats,
  tasks,
  mcpServers,
  reading,
  values,
  fields,
  readItems,
  fieldLabels,
  setSeat,
  setExpanded,
  setReading,
  setValue,
  look,
} = props.session

const seatItems = computed(() => seats.value.map((held) => ({ value: held.agent, title: held.agent })))
const status = computed(() => {
  if (state.value?.connected) return t('work.room.site.session.connected')
  if (seats.value.length > 1) return t('work.room.site.session.several', { count: seats.value.length })
  return t('work.room.site.session.none')
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
        class="inspector-seat"
        autocomplete="off"
        :items="seatItems"
        :model-value="seat"
        :label="t('work.room.site.session.teammate')"
        density="compact"
        hide-details
        @update:model-value="(v: string | null) => setSeat(v)"
      />
      <BaseButton kind="ghost" size="sm" :aria-expanded="expanded" @click="setExpanded(!expanded)">{{
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
                <template #name>
                  <UserRef
                    :handle="server.authorized_by"
                    :to="server.authorized_by ? userRefRoute(server.authorized_by, projectId) : null"
                    @navigate="emit('mention-click', server.authorized_by ?? '')"
                  />
                </template>
                <template #when>{{ relTime(server.authorized_at) }}</template>
              </i18n-t>
              <template v-else>{{ mcpState(server) }}</template>
            </span>
          </li>
        </ul>
      </div>
      <form class="inspector-form" @submit.prevent="look()">
        <v-select
          autocomplete="off"
          :items="readItems"
          :model-value="reading"
          :label="t('work.room.site.session.what')"
          density="compact"
          hide-details
          @update:model-value="(v: string) => setReading(v as SessionRead)"
        />
        <v-text-field
          v-for="field in fields"
          :key="field"
          autocomplete="off"
          :model-value="values[field]"
          :label="t(fieldLabels[field])"
          density="compact"
          hide-details
          required
          @update:model-value="(v: string) => setValue(field, v)"
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
