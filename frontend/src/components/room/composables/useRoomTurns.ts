/**
 * 这个房间里哪几轮在跑：谁在干、从什么时候开始、要不要显示「在处理」。
 *
 * 每条消息写完才作为一帧落下来（写的过程见 useTypingPreview）。「在等回复」从召唤开始，到每一个
 * 在跑的轮次都明确结束为止。轮次的生命周期帧（turn_active / turn_started /
 * turn_finished）在房间壳的 handleFrame 里认出来，交给这里记账；这里不认识 socket，
 * 也不往上报事件——报什么、什么时候报，是房间壳的事。
 */

import type { Ref } from 'vue'
import type { Block } from '../../../cx_types'

import { computed, onScopeDispose, ref } from 'vue'

import { agentFaces, FACE_SETTLE_MS } from '../../../lib/agentFace'
import { isAgentHandle } from '../../../lib/authorship'

export function useRoomTurns(options: {
  /** 时间线上此刻有的块：落下来的轮次是谁的，从块上认。 */
  messages: Ref<Block[]>
  /** 平台记下的运行记录（重试、整理上下文、等机器）：队友此刻在等什么，从它们读。 */
  records?: Ref<Block[]>
}) {
  const seen = computed(() => [...options.messages.value, ...(options.records?.value ?? [])])
  const awaitingReply = ref(false)
  const activeTurnIds = ref<Set<string>>(new Set())

  // 每个在跑的轮次从什么时候开始。中途连进来的，后端在 turn_active 上带着开始时间；
  // 没带的（老后端）只能从连上的这一刻算。
  const turnStarts = ref<Record<string, number>>({})

  // 每个在跑的轮次在哪个座位上（块署名的那个 handle，从 turn_started /
  // turn_active 帧学来）。一间房几个队友并行在干时，每张在动的头像认的是它。
  const turnAgents = ref<Record<string, string>>({})

  function began(id: string, at = Date.now(), agent?: string) {
    if (!(id in turnStarts.value)) turnStarts.value = { ...turnStarts.value, [id]: at }
    if (agent && turnAgents.value[id] !== agent) turnAgents.value = { ...turnAgents.value, [id]: agent }
  }

  function ended(id: string) {
    if (id in turnStarts.value) {
      const next = { ...turnStarts.value }
      delete next[id]
      turnStarts.value = next
    }
    if (id in turnAgents.value) {
      const next = { ...turnAgents.value }
      delete next[id]
      turnAgents.value = next
    }
  }

  // 每一轮是哪个队友的。在跑的轮次，帧上说了（turnAgents）；落下来的轮次，块上也说：
  // 那一轮里队友自己写的块署的就是它，人发的那条记着交给了谁、开的是哪一轮
  // （`agent_recipient` 与 `consumed_turn` / `prompted_turn`）。平台替一轮写的通知
  // （失败、重试、排队）署名是 system，在 `meta.seat` 上记着是哪位的那一轮：一轮
  // 还没开始就失败了，别的块都还没落，只有它说得出来。
  const turnOwners = computed(() => {
    const owners: Record<string, string> = {}
    for (const m of seen.value) {
      const seat = m.meta?.seat
      if (m.turn_id && typeof seat === 'string') owners[m.turn_id] = seat
    }
    for (const m of options.messages.value) {
      const recipient = (m.meta?.agent_recipient as { handle?: unknown } | undefined)?.handle
      if (typeof recipient !== 'string') continue
      for (const turn of [m.meta?.consumed_turn, m.meta?.prompted_turn]) {
        if (typeof turn === 'string') owners[turn] = recipient
      }
    }
    for (const m of options.messages.value) {
      if (m.turn_id && isAgentHandle(m.author)) owners[m.turn_id] = m.author
    }
    return { ...owners, ...turnAgents.value }
  })

  /** 这一轮那位队友的 handle；认不出是谁的轮次就是 null。 */
  function turnAgentHandle(turnId: string | null | undefined): string | null {
    return (turnId && turnOwners.value[turnId]) || null
  }

  /** 连上时 broker 报的「此刻在跑的这几轮」。 */
  function active(ids: string[], since?: Record<string, unknown>, agents?: Record<string, string>) {
    if (ids.length) activeTurnIds.value = new Set(ids)
    for (const id of ids) {
      const at = since?.[id]
      began(id, typeof at === 'number' ? at * 1000 : Date.now(), agents?.[id])
    }
    awaitingReply.value = true
  }

  function started(id: string, agent?: string) {
    const next = new Set(activeTurnIds.value)
    next.add(id)
    activeTurnIds.value = next
    began(id, Date.now(), agent)
    awaitingReply.value = true
  }

  // 刚干完一轮的队友 → 那一轮。头像在这一小会儿里做「做完了 / 卡住了」那一下，
  // 到点就撤掉回到静止。用计时器撤，不等动画结束的事件：系统关了动效时动画不播，
  // 那个事件也就永远不来。
  const recentlyEnded = ref<Record<string, string>>({})
  const settleTimers = new Map<string, ReturnType<typeof setTimeout>>()

  function noteEnded(id: string) {
    const handle = turnOwners.value[id]
    if (!handle) return
    recentlyEnded.value = { ...recentlyEnded.value, [handle]: id }
    clearTimeout(settleTimers.get(handle))
    settleTimers.set(
      handle,
      setTimeout(() => {
        settleTimers.delete(handle)
        if (recentlyEnded.value[handle] !== id) return
        const next = { ...recentlyEnded.value }
        delete next[handle]
        recentlyEnded.value = next
      }, FACE_SETTLE_MS)
    )
  }

  function clearEnded() {
    for (const timer of settleTimers.values()) clearTimeout(timer)
    settleTimers.clear()
    recentlyEnded.value = {}
  }
  onScopeDispose(clearEnded)

  /** 每位在干活（或刚干完）的队友此刻的表情，按 handle。 */
  const faces = computed(() => agentFaces(seen.value, turnStarts.value, turnOwners.value, recentlyEnded.value))

  /** 一轮结束。返回是否已经没有在跑的轮次。 */
  function finished(id: string): boolean {
    noteEnded(id)
    const next = new Set(activeTurnIds.value)
    next.delete(id)
    activeTurnIds.value = next
    ended(id)
    awaitingReply.value = next.size > 0
    return next.size === 0
  }

  /**
   * 没有生命周期帧可依的时候（老后端、一条错误、一次 done）：没有在跑的轮次就不再
   * 等了。返回是否因此停了下来。
   */
  function settleIfIdle(): boolean {
    if (activeTurnIds.value.size > 0) return false
    awaitingReply.value = false
    return true
  }

  /** 换了房间：上一个房间的轮次和这里无关。 */
  function reset() {
    awaitingReply.value = false
    activeTurnIds.value = new Set()
    turnStarts.value = {}
    turnAgents.value = {}
    clearEnded()
  }

  return {
    awaitingReply,
    turnStarts,
    faces,
    turnAgentHandle,
    active,
    started,
    finished,
    settleIfIdle,
    reset,
  }
}
