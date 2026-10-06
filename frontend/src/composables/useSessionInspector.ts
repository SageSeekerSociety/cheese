// 「这间房的会话现在什么样」：连没连上、手上有哪几个队友的会话、它在跑什么任务、
// 用的是哪些 MCP 连接，以及往会话里问一句只读的话。
//
// 和画的那一半（`components/SessionInspector.vue`）分家，理由和别处一样：那一只原先
// 自己引三个接口函数，而它渲染在「现场」那一格里，于是整格跟着它够得着接口层。
//
// 这里只有读：会话状态大多由房间的 socket 推上来，没推的地方才兜底轮询，而且是推着的
// 房间放慢到 30 秒一次。
import type { AgentControlState, McpDeclaringType, RoomMcpServer } from '../api'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { getAgentControl, getRoomMcpServers, sendAgentControl } from '../api'

import { t } from '@/i18n'

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
/** 那一句只读的话问的是哪一项。 */
export type SessionRead = (typeof reads)[number]['value']
const fieldLabels: Record<string, string> = {
  query: 'work.room.site.session.fields.query',
  path: 'work.room.site.session.fields.path',
}

export interface SessionInspectorScope {
  /** 哪间房的会话；没有房间时不问。 */
  topicId: () => string | null
  /** 这一栏在不在屏幕上：不在就不轮询。 */
  active: () => boolean
  /** 房间 socket 上最近一帧会话状态。给了就按它走、只在它没来的时候兜底轮询；
   *  没给（`undefined`）就一直按快的那档问。 */
  pushed: () => AgentControlState | null | undefined
}

export function useSessionInspector(scope: SessionInspectorScope) {
  const LIVE_POLL_MS = 2000
  // Only a floor under a frame that never arrived: a socket that dropped, a room
  // opened in a view with no socket.
  const IDLE_POLL_MS = 30000

  const state = ref<AgentControlState | null>(null)
  const error = ref('')
  const busy = ref(false)
  const expanded = ref(false)
  const output = ref<unknown>(null)
  // The teammate whose session is shown, once the room has several working: the
  // platform never picks one of them, so the person does.
  const seat = ref<string | null>(null)
  const mcpServers = ref<RoomMcpServer[]>([])
  let timer: ReturnType<typeof setTimeout> | undefined
  let generation = 0

  const seats = computed(() => state.value?.seats ?? [])
  const tasks = computed(() => Object.values(state.value?.tasks ?? {}))

  function adopt(next: AgentControlState) {
    if (next.id !== state.value?.id) output.value = null
    state.value = next
    if (seat.value && !seats.value.some((held) => held.agent === seat.value)) seat.value = null
  }

  watch(seat, () => void refresh())

  async function refresh(epoch = generation) {
    const topicId = scope.topicId()
    if (!topicId) return
    try {
      const next = await getAgentControl(topicId, seat.value)
      if (epoch === generation) adopt(next)
    } catch (e) {
      if (epoch === generation) error.value = e instanceof Error ? e.message : t('work.room.site.session.loadFailed')
    }
  }

  async function poll(epoch: number) {
    await refresh(epoch)
    const every = scope.pushed() === undefined ? LIVE_POLL_MS : IDLE_POLL_MS
    if (epoch === generation && scope.active()) timer = setTimeout(() => void poll(epoch), every)
  }

  // A frame lands: take it as the whole state, the same shape the request returns.
  // It speaks for the room as a whole, so with a teammate picked it is only the
  // cue to read that teammate's session again.
  watch(
    () => scope.pushed(),
    (next) => {
      if (!next) return
      if (seat.value) void refresh()
      else adopt(next)
    }
  )

  watch(
    () => scope.active(),
    () => {
      generation += 1
      clearTimeout(timer)
      state.value = null
      error.value = ''
      if (scope.active()) void poll(generation)
    },
    { immediate: true }
  )
  onBeforeUnmount(() => {
    generation += 1
    clearTimeout(timer)
  })

  // 这间房的会话用的是项目的连接：用谁的账号授权的，房间里的人都看得到（#1909）。
  // 只读；连接和断开在项目设置里。展开才读，收着的时候不必问。
  watch(expanded, async (open) => {
    const topicId = scope.topicId()
    if (!open || !topicId) return
    try {
      mcpServers.value = (await getRoomMcpServers(topicId)).servers
    } catch {
      mcpServers.value = []
    }
  })

  const reading = ref<SessionRead>('initialize')
  const values = ref<Record<string, string>>({})
  const fields = computed(() => reads.find((read) => read.value === reading.value)!.fields)
  const readItems = computed(() => reads.map((read) => ({ value: read.value, title: t(read.label) })))

  /** 往会话里问一句只读的话（`reads` 那几项）。写和改不在这里。 */
  async function look() {
    const topicId = scope.topicId()
    if (!topicId || !state.value?.id || busy.value) return
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
        ? await sendAgentControl(topicId, id, request, undefined, agent)
        : await sendAgentControl(topicId, id, request)
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

  // 这些是读者手上的开关，写回这一层：展示组件不碰别人的 ref。
  function setSeat(v: string | null) {
    seat.value = v
  }
  function setExpanded(v: boolean) {
    expanded.value = v
  }
  function setReading(v: SessionRead) {
    reading.value = v
  }
  function setValue(field: string, v: string) {
    values.value[field] = v
  }

  return {
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
    refresh,
    look,
    setSeat,
    setExpanded,
    setReading,
    setValue,
  }
}

/** 会话那一栏的取数，由外壳调一次整个递进展示组件。 */
export type SessionInspectorBundle = ReturnType<typeof useSessionInspector>
/** 展示组件画 MCP 那一块时用到的服务器形状。 */
export type { McpDeclaringType, RoomMcpServer }
