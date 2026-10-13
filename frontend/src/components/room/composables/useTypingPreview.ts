/**
 * 队友正在写给房间的那条消息：模型一边生成 `chat_send` 的参数，房间一边看到正文
 * 长出来；真的那条落下来，它就换成那一条。
 *
 * 来源是 socket 上的 `live` 帧（types/live.ts）：每帧是那位队友此刻正在生成的块，
 * 工具调用的参数是到这一刻为止的 JSON 原文。这里只认 `chat_send`，从里面读出
 * `content` 已经写出的那一截。每位队友最多一条。
 *
 * 它从来不是一条消息，所以每一条出路都得有：
 * - 真消息落下（同一位队友署名的 assistant_block）→ 换成那一条；
 * - 这次调用从帧里消失，而正文还没写完 → 模型放弃了它，立刻撤；
 * - 正文写完了才消失 → 记录落下、工具在跑，消息马上就到：再等 LANDING_MS，
 *   等不到（被拦下、调用失败）就撤；
 * - 那一轮结束，或这个房间已经没有在跑的轮次 → 撤；
 * - STALE_MS 没有新帧 → 撤（帧只在内容变化时才来，断了不会有人说）。
 * 换房间由房间壳调 `clear()`；重连后房间壳调 `keepTurns`，只撤掉断线期间结束了的那几轮的。房间壳把它收到的每一帧在自己处理完之后交给
 * `follow`；`rows` 是时间线末尾要画的那几行。
 */

import type { ComputedRef, Ref } from 'vue'
import type { Block, WsServerFrame } from '../../../cx_types'
import type { Outgoing } from '../../../lib/composerDrafts'
import type { LiveFrame } from '../../../types/live'

import { computed, onScopeDispose, ref } from 'vue'

import { type RunEdge, runEdgeBetween } from '../../../lib/chatGrouping'
import { partialStringField } from '../../../lib/partialJson'

/** 正文写完、消息还没到时最多等多久。 */
export const LANDING_MS = 5000
/** 正在写、却这么久没有新帧：当它已经不在写了。 */
export const STALE_MS = 30000

export interface TypingPreview {
  agent: string
  turn: string | null
  /** 这次工具调用的 id；同一位队友换了一次调用，就是另一条。 */
  call: string | null
  text: string
  /** 正文的引号已经合上：不会再长了，等它落下。 */
  closed: boolean
  /** 开始显示的时刻，消息分组按它算。 */
  since: string
}

/** 发消息给房间的那个工具，不论 harness 给它挂了什么 MCP 前缀。 */
function isChatSend(name: string | null): boolean {
  return !!name && name.replace(/^mcp__[a-z0-9_]+__/, '') === 'chat_send'
}

/** 末尾一个还没写完的引用（`<@han`、`<#…`、`<&…`）先不显示：显示出来就是半截原文。 */
function withoutOpenRef(text: string): string {
  return text.replace(/<[@#&][^>\s]*$/, '')
}

function written(frame: LiveFrame): { call: string | null; text: string; closed: boolean } | null {
  for (let i = frame.blocks.length - 1; i >= 0; i--) {
    const block = frame.blocks[i]
    if (block.type !== 'tool' || !isChatSend(block.name)) continue
    if (typeof block.arguments !== 'string') return null
    const content = partialStringField(block.arguments, 'content')
    if (!content) return null
    return { call: block.id ?? null, text: withoutOpenRef(content.text), closed: content.closed }
  }
  return null
}

/** 画成一条消息要的那几个字段。id 不会和任何落库的块撞上。 */
export function previewBlock(p: TypingPreview, topicId: string): Block {
  return {
    id: `typing:${p.agent}`,
    conversation_id: topicId,
    kind: 'message',
    author_type: 'participant',
    author: p.agent,
    content: p.text,
    created_at: p.since,
  }
}

/** 房间壳那边、决定这几行画在哪、接在谁后面的东西。 */
export interface TypingView {
  topic: () => { id: string } | null | undefined
  /** 停在历史中间：时间线底部不是最新，不画。 */
  hasNewer: Ref<boolean>
  outbox: Ref<Outgoing[]>
  visible: ComputedRef<Block[]>
  /** 时间线的入场动画集合：替下预览的那条消息不再入场一次，而是从淡的那一档恢复。 */
  arrived: Set<string>
  delivered: Set<string>
}

export function useTypingPreview(view: TypingView) {
  const previews = ref<TypingPreview[]>([])
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  /** 正文写完、已经从帧里消失、在等消息落下的那几位。 */
  const landing = new Set<string>()

  function drop(agent: string) {
    clearTimeout(timers.get(agent))
    timers.delete(agent)
    landing.delete(agent)
    if (previews.value.some((p) => p.agent === agent)) {
      previews.value = previews.value.filter((p) => p.agent !== agent)
    }
  }

  function arm(agent: string, ms: number) {
    clearTimeout(timers.get(agent))
    timers.set(
      agent,
      setTimeout(() => drop(agent), ms)
    )
  }

  function onLive(frame: LiveFrame) {
    const agent = frame.agent
    if (!agent) return
    const current = previews.value.find((p) => p.agent === agent)
    const now = written(frame)
    if (!now) {
      if (!current) return
      if (!current.closed) drop(agent)
      else if (!landing.has(agent)) {
        landing.add(agent)
        arm(agent, LANDING_MS)
      }
      return
    }
    if (!now.text.trim()) {
      // 调用开始了，正文还没出来：先不画一条空消息。换了一次调用的，旧的那条撤掉。
      if (current && current.call !== now.call) drop(agent)
      return
    }
    landing.delete(agent)
    arm(agent, STALE_MS)
    const next: TypingPreview = {
      agent,
      turn: frame.turn_id,
      call: now.call,
      text: now.text,
      closed: now.closed,
      since: current && current.call === now.call ? current.since : new Date().toISOString(),
    }
    previews.value = current ? previews.value.map((p) => (p.agent === agent ? next : p)) : [...previews.value, next]
  }

  /** 一条消息落下了。是正在写的那位署名的，就是它：撤下预览，答 true。 */
  function landed(block: Block): boolean {
    if (block.kind !== 'message' || !previews.value.some((p) => p.agent === block.author)) return false
    drop(block.author)
    return true
  }

  /** 一轮结束了（帧上说了是谁的，就连那位一起认）。`idle`：这个房间已经没有在跑的轮次。 */
  function turnEnded(turnId: string | null, idle: boolean, agent?: string) {
    for (const p of [...previews.value]) {
      if (idle || (turnId && p.turn === turnId) || p.agent === agent) drop(p.agent)
    }
  }

  /** 重连后：只留下此刻还在跑的那几轮正在写的消息。 */
  function keepTurns(live: Set<string>) {
    for (const p of [...previews.value]) if (!p.turn || !live.has(p.turn)) drop(p.agent)
  }

  function clear() {
    for (const timer of timers.values()) clearTimeout(timer)
    timers.clear()
    landing.clear()
    previews.value = []
  }

  /** 房间壳处理完一帧之后交过来。`idle`：此刻这个房间已经没有在跑的轮次。 */
  function follow(frame: WsServerFrame, idle: boolean) {
    if (frame.type === 'live') onLive(frame)
    else if (frame.type === 'assistant_block' && landed(frame.block)) {
      view.arrived.delete(frame.block.id)
      view.delivered.add(frame.block.id)
    } else if (frame.type === 'turn_finished') turnEnded(frame.turn_id, idle, frame.agent)
    else if (frame.type === 'done' || frame.type === 'error') turnEnded(null, idle)
  }

  // 正在写的那几条接在发件箱后面，只在看着最新一段时画：它们是马上要落下的最新一条。
  const rows = computed<{ block: Block; edge: RunEdge }[]>(() => {
    const room = view.topic()
    if (!room || view.hasNewer.value) return []
    const queued = view.outbox.value.length > 0
    let prev = queued ? undefined : view.visible.value.at(-1)
    return previews.value.map((p, i) => {
      const block = previewBlock(p, room.id)
      const edge = runEdgeBetween(prev, block, {
        broken: i === 0 && queued,
      })
      prev = block
      return { block, edge }
    })
  })

  onScopeDispose(clear)

  return { previews, rows, onLive, landed, turnEnded, follow, clear, keepTurns }
}
