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
 * 换房间、重连由房间壳调 `clear()`。
 */

import type { Block } from '../../../cx_types'
import type { LiveFrame } from '../../../types/live'

import { onScopeDispose, ref } from 'vue'

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
    topic_id: topicId,
    kind: 'message',
    author_type: 'participant',
    author: p.agent,
    content: p.text,
    created_at: p.since,
  }
}

export function useTypingPreview() {
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

  function clear() {
    for (const timer of timers.values()) clearTimeout(timer)
    timers.clear()
    landing.clear()
    previews.value = []
  }

  onScopeDispose(clear)

  return { previews, onLive, landed, turnEnded, clear }
}
